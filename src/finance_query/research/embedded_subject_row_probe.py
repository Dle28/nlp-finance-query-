"""Find ownership-table row candidates whose subject is outside column zero.

Financial-statement ownership notes often put the investee/company name in a
later column and use a serial number in column zero.  The normal row matcher is
deliberately label-first, so it correctly abstains in that situation.  This
module measures a narrow alternative: subject-token coverage across a row in
an already retrieved table.  It is navigation-only and cannot create an exact
cell binding, evidence, answer, training datum, release, or submission.
"""

from __future__ import annotations

from collections import Counter, defaultdict
import json
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any, Iterable, Mapping

from finance_query.e2e.core.table_retrieval import WORD_RE
from finance_query.research.machine_exact_cell_proposals import sha256_file


PROTOCOL = "vifinqa_embedded_subject_row_probe_v1"
CONTRACT = {
    "research_only": True,
    "navigation_metadata_only": True,
    "evidence_eligible": False,
    "may_materialize_answer": False,
    "training_eligible": False,
    "submission_eligible": False,
    "promotion_allowed": False,
}
FORBIDDEN = frozenset(
    {
        "answer",
        "answer_decimal",
        "raw_value",
        "raw_values",
        "cell_value",
        "pandas_query",
        "human_verified",
        "rows",
        "row_label",
        "raw_source_row",
        "raw_source_cell",
        "subject_text",
        "source_label",
    }
)
OWNERSHIP_RE = re.compile(r"^\s*t(?:ỷ|ỉ)\s+lệ\s+sở\s+hữu\b", re.IGNORECASE)
SUBJECT_RE = re.compile(
    r"\btại\s+(?P<subject>.+?)(?=\s+(?:vào\s+(?:cuối\s+năm|ngày)|đến\s+(?:ngày|cuối\s+năm)|cuối\s+năm)\b)",
    re.IGNORECASE,
)


def _rows(path: Path) -> Iterable[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number} must contain JSON objects")
            yield value


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain one JSON object")
    return value


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, values: Iterable[Mapping[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for value in values),
        encoding="utf-8",
    )


def _contains_forbidden(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(str(key) in FORBIDDEN or _contains_forbidden(child) for key, child in value.items())
    return any(_contains_forbidden(child) for child in value) if isinstance(value, list) else False


def _require_output(manifest_path: Path, key: str, path: Path) -> None:
    descriptor = (_json(manifest_path).get("outputs") or {}).get(key) or {}
    if sha256_file(path) != descriptor.get("sha256"):
        raise ValueError(f"SHA-256 mismatch for {key}")


def _tokens(value: str) -> set[str]:
    return {token.casefold() for token in WORD_RE.findall(value) if len(token) > 1}


def _ownership_subject(question: str) -> set[str] | None:
    if not OWNERSHIP_RE.search(question):
        return None
    match = SUBJECT_RE.search(question)
    if match is None:
        return None
    tokens = _tokens(match.group("subject"))
    # A company subject must contain at least one non-generic identifying token.
    generic = {"công", "ty", "tnhh", "ctcp", "tập", "đoàn"}
    return tokens if len(tokens - generic) >= 1 else None


def _coverage(subject_tokens: set[str], row: Iterable[object]) -> float:
    row_tokens = _tokens(" ".join(str(cell) for cell in row))
    return len(subject_tokens & row_tokens) / len(subject_tokens) if subject_tokens else 0.0


def build_embedded_subject_row_probe(
    *,
    reclassification_dir: Path,
    review_items_path: Path,
    table_candidates_path: Path,
    table_candidates_manifest_path: Path,
    full_assets_path: Path,
    full_assets_manifest_path: Path,
    output_dir: Path,
    expected_question_count: int = 1012,
    minimum_subject_token_coverage: float = 1.0,
) -> dict[str, Any]:
    """Measure exact-subject row navigation within already retrieved tables."""
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite output directory: {output_dir}")
    if not 0.0 <= minimum_subject_token_coverage <= 1.0:
        raise ValueError("minimum subject token coverage must be between zero and one")
    reclassification_manifest = reclassification_dir / "manifest.json"
    _require_output(reclassification_manifest, "plans", reclassification_dir / "typed_operand_plans.jsonl")
    _require_output(reclassification_manifest, "target_bridge", reclassification_dir / "direct_lookup_target_bridge_v1.jsonl")
    _require_output(table_candidates_manifest_path, "table_candidates_v1.jsonl", table_candidates_path)
    assets_descriptor = (_json(full_assets_manifest_path).get("outputs") or {}).get(full_assets_path.name) or {}
    if sha256_file(full_assets_path) != assets_descriptor.get("sha256"):
        raise ValueError("full assets do not match their manifest")

    plans = {int(row["question_id"]): row for row in _rows(reclassification_dir / "typed_operand_plans.jsonl")}
    target_ids = sorted(int(row["question_id"]) for row in _rows(reclassification_dir / "direct_lookup_target_bridge_v1.jsonl"))
    items = {int(row["id"]): row for row in _rows(review_items_path)}
    expected_ids = set(range(1, expected_question_count + 1))
    if set(plans) != expected_ids or set(items) != expected_ids or len(target_ids) != len(set(target_ids)):
        raise ValueError("reclassification inputs must cover the expected question population")

    tables_by_question: defaultdict[int, list[dict[str, Any]]] = defaultdict(list)
    needed_uids: set[str] = set()
    for candidate in _rows(table_candidates_path):
        question_id = int(candidate.get("question_id") or 0)
        if question_id not in target_ids:
            continue
        uid = str(candidate.get("internal_table_uid") or "")
        if uid:
            tables_by_question[question_id].append(candidate)
            needed_uids.add(uid)
    assets = {
        str(asset.get("internal_table_uid") or ""): asset
        for asset in _rows(full_assets_path)
        if str(asset.get("internal_table_uid") or "") in needed_uids
    }
    if needed_uids - set(assets):
        raise ValueError("retrieved table candidate is missing from full assets")

    records: list[dict[str, Any]] = []
    for question_id in target_ids:
        plan = plans[question_id]
        question = str(items[question_id].get("question") or "")
        subject_tokens = _ownership_subject(question)
        operand = (plan.get("operands") or [None])[0]
        requested_scope = operand.get("scope") if isinstance(operand, Mapping) else None
        accepted: list[dict[str, Any]] = []
        if subject_tokens is not None:
            seen_tables: set[str] = set()
            for candidate in tables_by_question[question_id]:
                uid = str(candidate.get("internal_table_uid") or "")
                if uid in seen_tables or candidate.get("scope") != requested_scope:
                    continue
                seen_tables.add(uid)
                asset = assets[uid]
                for row_index, row in enumerate(asset.get("rows") or []):
                    if not isinstance(row, list):
                        continue
                    coverage = _coverage(subject_tokens, row)
                    if coverage >= minimum_subject_token_coverage:
                        accepted.append(
                            {
                                "internal_table_uid": uid,
                                "document_id": str(asset.get("document_id") or ""),
                                "local_ordinal": int(asset.get("local_ordinal") or 0),
                                "row_index": row_index,
                                "subject_token_coverage": coverage,
                            }
                        )
        if subject_tokens is None:
            status = "NOT_OWNERSHIP_SUBJECT_FORM"
        elif len(accepted) == 1:
            status = "UNIQUE_EXACT_SUBJECT_ROW_NAVIGATION"
        elif len(accepted) > 1:
            status = "MULTIPLE_EXACT_SUBJECT_ROW_NAVIGATION"
        else:
            status = "NO_EXACT_SUBJECT_ROW_NAVIGATION"
        record = {
            "schema_version": 1,
            "protocol": PROTOCOL,
            "question_id": question_id,
            "status": status,
            "retrieved_table_count": len({str(row.get("internal_table_uid") or "") for row in tables_by_question[question_id]}),
            "subject_row_candidate_count": len(accepted),
            "subject_row_candidates": accepted,
            "source_contract": dict(CONTRACT),
        }
        if _contains_forbidden(record):
            raise ValueError("embedded-subject probe leaked unsafe content")
        records.append(record)
    summary = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "target_question_count": len(records),
        "status_counts": dict(sorted(Counter(str(row["status"]) for row in records).items())),
        "minimum_subject_token_coverage": minimum_subject_token_coverage,
        "source_contract": dict(CONTRACT),
    }
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.tmp-", dir=output_dir.parent))
    try:
        records_path = temporary / "embedded_subject_row_probe_v1.jsonl"
        summary_path = temporary / "embedded_subject_row_probe_summary_v1.json"
        _write_jsonl(records_path, records)
        _write_json(summary_path, summary)
        inputs = {
            "reclassification_manifest": reclassification_manifest,
            "review_items": review_items_path,
            "table_candidates": table_candidates_path,
            "table_candidates_manifest": table_candidates_manifest_path,
            "full_assets": full_assets_path,
            "full_assets_manifest": full_assets_manifest_path,
        }
        _write_json(
            temporary / "manifest.json",
            {
                "schema_version": 1,
                "protocol": PROTOCOL,
                "inputs": {name: {"path": str(path), "sha256": sha256_file(path)} for name, path in inputs.items()},
                "outputs": {
                    records_path.name: {"path": records_path.name, "sha256": sha256_file(records_path)},
                    summary_path.name: {"path": summary_path.name, "sha256": sha256_file(summary_path)},
                },
                "source_contract": dict(CONTRACT),
            },
        )
        temporary.rename(output_dir)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return {"status": "BUILT", **summary, "answer_eligible": False, "submission_eligible": False}


def validate_embedded_subject_row_probe(artifact_dir: Path, *, expected_question_count: int = 1012) -> dict[str, Any]:
    manifest = _json(artifact_dir / "manifest.json")
    if manifest.get("protocol") != PROTOCOL or manifest.get("source_contract") != CONTRACT:
        raise ValueError("unexpected embedded-subject probe contract")
    for descriptor in (manifest.get("inputs") or {}).values():
        path = Path(str(descriptor.get("path") or ""))
        if not path.is_file() or sha256_file(path) != descriptor.get("sha256"):
            raise ValueError("embedded-subject probe input hash mismatch")
    for descriptor in (manifest.get("outputs") or {}).values():
        path = artifact_dir / str(descriptor.get("path") or "")
        if not path.is_file() or sha256_file(path) != descriptor.get("sha256"):
            raise ValueError("embedded-subject probe output hash mismatch")
    records = list(_rows(artifact_dir / "embedded_subject_row_probe_v1.jsonl"))
    summary = _json(artifact_dir / "embedded_subject_row_probe_summary_v1.json")
    if not records or len({int(row["question_id"]) for row in records}) != len(records):
        raise ValueError("embedded-subject probe IDs are invalid")
    if len(records) > expected_question_count or any(_contains_forbidden(row) or row.get("source_contract") != CONTRACT for row in records):
        raise ValueError("embedded-subject probe lost navigation-only boundary")
    counts = dict(sorted(Counter(str(row.get("status") or "") for row in records).items()))
    if int(summary.get("target_question_count") or 0) != len(records) or summary.get("status_counts") != counts:
        raise ValueError("embedded-subject probe summary mismatch")
    return {"status": "PASS", "target_question_count": len(records), "answer_eligible": False, "submission_eligible": False}
