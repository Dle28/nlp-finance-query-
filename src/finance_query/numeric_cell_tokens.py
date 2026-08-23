"""Hash-bound numeric cell tokens for model/executor separation.

The public view contains token IDs and immutable coordinates but no literal
number.  The executor-only registry retains the Decimal candidate and must not
be exposed to an LLM prompt.  Neither artifact is evidence-eligible.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping


NUMERIC_CELL_TOKEN_PROTOCOL = "vifinqa_numeric_cell_tokens_v1"
NUMERIC_CELL_TOKEN_SCHEMA_VERSION = 1
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_TOKEN_RE = re.compile(r"^CELL_[0-9a-f]{32}$")


class NumericCellTokenError(ValueError):
    """Raised when a token registry loses exact-cell lineage."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stable_hash(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _rows(path: Path):
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise NumericCellTokenError(f"{path}:{line_number} must be a JSON object")
            yield value


def _write_jsonl(path: Path, values: list[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(
            json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
            for value in values
        ),
        encoding="utf-8",
    )


def _contract(*, executor_private: bool) -> dict[str, bool]:
    return {
        "candidate_only": True,
        "executor_private": executor_private,
        "model_prompt_eligible": not executor_private,
        "evidence_eligible": False,
        "training_eligible": False,
        "submission_eligible": False,
        "promotion_allowed": False,
        "may_select_value": False,
    }


def _token_key(question_id: object, stage_id: object, role: object) -> tuple[int, str, str]:
    try:
        normalized_question_id = int(question_id)
    except (TypeError, ValueError) as exc:
        raise NumericCellTokenError("numeric cell token requires an integer question_id") from exc
    return normalized_question_id, str(stage_id or ""), str(role or "")


def _selected_table_index(path: Path, required_uids: set[str]) -> dict[str, Mapping[str, Any]]:
    selected: dict[str, Mapping[str, Any]] = {}
    for table in _rows(path):
        uid = str(table.get("internal_table_uid") or "")
        if uid in required_uids:
            if uid in selected:
                raise NumericCellTokenError(f"duplicate structured table UID: {uid}")
            selected[uid] = table
    missing = sorted(required_uids - set(selected))
    if missing:
        raise NumericCellTokenError(f"structured tables missing token UIDs: {missing[:5]}")
    return selected


def materialize_numeric_cell_tokens(
    *,
    bindings: Path,
    bindings_manifest: Path,
    structured_tables: Path,
    registry_output: Path,
    public_view_output: Path,
) -> dict[str, Any]:
    """Create an executor-private registry and literal-free public token view."""

    manifest = json.loads(bindings_manifest.read_text(encoding="utf-8"))
    expected_bindings = ((manifest.get("outputs") or {}).get("bindings") or {}).get("sha256")
    if not isinstance(expected_bindings, str) or sha256_file(bindings) != expected_bindings:
        raise NumericCellTokenError("SHA-256 mismatch for exact-cell bindings")
    expected_tables = ((manifest.get("inputs") or {}).get("structured_tables") or {}).get("sha256")
    if not isinstance(expected_tables, str) or sha256_file(structured_tables) != expected_tables:
        raise NumericCellTokenError("exact-cell bindings do not derive from supplied structured tables")

    binding_rows = list(_rows(bindings))
    operands: list[tuple[Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]]] = []
    required_uids: set[str] = set()
    for question in binding_rows:
        for stage in question.get("stages") or []:
            for operand in stage.get("required_operands") or []:
                if not isinstance(operand, Mapping) or operand.get("binding_status") != "binding_ready":
                    continue
                uid = str(operand.get("internal_table_uid") or "")
                if not uid:
                    raise NumericCellTokenError("binding-ready operand is missing internal_table_uid")
                required_uids.add(uid)
                operands.append((question, stage, operand))
    tables = _selected_table_index(structured_tables, required_uids)

    registry: list[dict[str, Any]] = []
    public: list[dict[str, Any]] = []
    seen_keys: set[tuple[int, str, str]] = set()
    seen_tokens: set[str] = set()
    for question, stage, operand in operands:
        key = _token_key(question.get("question_id"), stage.get("stage_id"), operand.get("role"))
        if key in seen_keys:
            raise NumericCellTokenError(f"duplicate numeric token key: {key}")
        seen_keys.add(key)
        table = tables[str(operand["internal_table_uid"])]
        row_index, column_index = operand.get("row_index"), operand.get("column_index")
        rows = table.get("rows") or []
        if (
            not isinstance(row_index, int)
            or not isinstance(column_index, int)
            or row_index < 0
            or column_index < 0
            or row_index >= len(rows)
            or not isinstance(rows[row_index], list)
            or column_index >= len(rows[row_index])
        ):
            raise NumericCellTokenError(f"invalid exact-cell coordinate for token key: {key}")
        raw_cell = str(rows[row_index][column_index])
        if raw_cell != str(operand.get("raw_source_cell") or ""):
            raise NumericCellTokenError(f"raw source cell drift for token key: {key}")
        provenance = table.get("source_provenance") or {}
        lineage = {
            "document_id": str(table.get("document_id") or ""),
            "document_sha256": str(provenance.get("source_sha256") or ""),
            "internal_table_uid": str(table.get("internal_table_uid") or ""),
            "table_sha256": str(provenance.get("table_sha256") or ""),
            "row_index": row_index,
            "column_index": column_index,
            "raw_cell_sha256": hashlib.sha256(raw_cell.encode("utf-8")).hexdigest(),
        }
        if any(len(lineage[field]) != 64 for field in ("document_sha256", "table_sha256", "raw_cell_sha256")):
            raise NumericCellTokenError(f"incomplete source hashes for token key: {key}")
        token_payload = {
            "question_id": key[0],
            "stage_id": key[1],
            "role": key[2],
            "lineage": lineage,
        }
        token_id = "CELL_" + _stable_hash(token_payload)[:32]
        if token_id in seen_tokens:
            raise NumericCellTokenError(f"numeric token collision: {token_id}")
        seen_tokens.add(token_id)
        common = {
            "schema_version": NUMERIC_CELL_TOKEN_SCHEMA_VERSION,
            "protocol": NUMERIC_CELL_TOKEN_PROTOCOL,
            "token_id": token_id,
            **token_payload,
        }
        public.append({**common, "source_contract": _contract(executor_private=False)})
        registry.append(
            {
                **common,
                "raw_decimal_candidate": str(operand.get("raw_decimal_candidate") or ""),
                "source_contract": _contract(executor_private=True),
            }
        )

    _write_jsonl(registry_output, registry)
    _write_jsonl(public_view_output, public)
    result = {
        "schema_version": NUMERIC_CELL_TOKEN_SCHEMA_VERSION,
        "protocol": NUMERIC_CELL_TOKEN_PROTOCOL,
        "inputs": {
            "bindings": {"path": str(bindings), "sha256": sha256_file(bindings)},
            "bindings_manifest": {
                "path": str(bindings_manifest),
                "sha256": sha256_file(bindings_manifest),
            },
            "structured_tables": {
                "path": str(structured_tables),
                "sha256": sha256_file(structured_tables),
            },
        },
        "outputs": {
            "executor_registry": {
                "path": str(registry_output),
                "sha256": sha256_file(registry_output),
            },
            "public_view": {
                "path": str(public_view_output),
                "sha256": sha256_file(public_view_output),
            },
        },
        "counts": {
            "token_count": len(registry),
            "binding_status_counts": dict(Counter({"binding_ready": len(registry)})),
        },
        "source_contract": _contract(executor_private=True),
    }
    manifest_output = registry_output.with_suffix(".manifest.json")
    manifest_output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {**result, "manifest_path": str(manifest_output)}


def load_numeric_cell_tokens(
    registry_path: Path,
    manifest_path: Path,
    *,
    bindings_path: Path | None = None,
    bindings_manifest_path: Path | None = None,
) -> dict[tuple[int, str, str], Mapping[str, Any]]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (
        manifest.get("protocol") != NUMERIC_CELL_TOKEN_PROTOCOL
        or manifest.get("schema_version") != NUMERIC_CELL_TOKEN_SCHEMA_VERSION
    ):
        raise NumericCellTokenError("unexpected numeric cell token manifest protocol")
    expected = ((manifest.get("outputs") or {}).get("executor_registry") or {}).get("sha256")
    if not isinstance(expected, str) or sha256_file(registry_path) != expected:
        raise NumericCellTokenError("SHA-256 mismatch for numeric cell token registry")
    manifest_inputs = manifest.get("inputs") or {}
    for name, path in (
        ("bindings", bindings_path),
        ("bindings_manifest", bindings_manifest_path),
    ):
        if path is None:
            continue
        expected_input = (manifest_inputs.get(name) or {}).get("sha256")
        if not isinstance(expected_input, str) or sha256_file(path) != expected_input:
            raise NumericCellTokenError(f"numeric cell token registry is stale for {name}")
    index: dict[tuple[int, str, str], Mapping[str, Any]] = {}
    for token in _rows(registry_path):
        key = _token_key(token.get("question_id"), token.get("stage_id"), token.get("role"))
        if key in index:
            raise NumericCellTokenError(f"duplicate numeric token key: {key}")
        contract = token.get("source_contract") or {}
        if contract.get("executor_private") is not True or contract.get("model_prompt_eligible") is not False:
            raise NumericCellTokenError(f"unsafe numeric token contract: {key}")
        if (
            token.get("protocol") != NUMERIC_CELL_TOKEN_PROTOCOL
            or token.get("schema_version") != NUMERIC_CELL_TOKEN_SCHEMA_VERSION
            or not _TOKEN_RE.fullmatch(str(token.get("token_id") or ""))
        ):
            raise NumericCellTokenError(f"numeric token identity is invalid: {key}")
        index[key] = token
    return index


def verify_operand_token(
    token: Mapping[str, Any],
    *,
    question_id: object,
    stage_id: object,
    operand: Mapping[str, Any],
) -> str:
    """Verify token/operand identity and return the private Decimal literal."""

    key = _token_key(question_id, stage_id, operand.get("role"))
    observed = _token_key(token.get("question_id"), token.get("stage_id"), token.get("role"))
    lineage = token.get("lineage") or {}
    expected = {
        "document_id": str(operand.get("document_id") or ""),
        "internal_table_uid": str(operand.get("internal_table_uid") or ""),
        "row_index": operand.get("row_index"),
        "column_index": operand.get("column_index"),
    }
    actual = {field: lineage.get(field) for field in expected}
    if observed != key or actual != expected:
        raise NumericCellTokenError(f"numeric token lineage mismatch: {key}")
    for field in ("document_sha256", "table_sha256", "raw_cell_sha256"):
        if not _SHA256_RE.fullmatch(str(lineage.get(field) or "")):
            raise NumericCellTokenError(f"numeric token source hash is invalid: {key}")
    raw_source_cell = str(operand.get("raw_source_cell") or "")
    if hashlib.sha256(raw_source_cell.encode("utf-8")).hexdigest() != lineage["raw_cell_sha256"]:
        raise NumericCellTokenError(f"numeric token raw cell hash mismatch: {key}")
    token_payload = {
        "question_id": key[0],
        "stage_id": key[1],
        "role": key[2],
        "lineage": dict(lineage),
    }
    if str(token.get("token_id") or "") != "CELL_" + _stable_hash(token_payload)[:32]:
        raise NumericCellTokenError(f"numeric token ID mismatch: {key}")
    decimal_literal = str(token.get("raw_decimal_candidate") or "")
    if decimal_literal != str(operand.get("raw_decimal_candidate") or ""):
        raise NumericCellTokenError(f"numeric token Decimal mismatch: {key}")
    return decimal_literal
