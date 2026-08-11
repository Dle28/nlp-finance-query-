#!/usr/bin/env python3
"""Build non-production submission-contract candidates from an audited Qwen run.

The resulting directory intentionally contains only the audited question
subset.  It is validated with the same CSV/pandas contract as a contest
submission, but its manifest marks it as non-promotable and it is never
archived.  A later full-corpus production ledger must independently approve
every question before ``compile_vifinqa_submission.py`` can create a ZIP.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from finance_query.binding import row_label  # noqa: E402
from finance_query.execution import RELIABLE_BINDING_WARNINGS, parse_decimal  # noqa: E402
from finance_query.corpus import infer_unit  # noqa: E402
from finance_query.evidence_context import validate_evidence_context_sidecar  # noqa: E402
from finance_query.schemas import DirectBinding  # noqa: E402
from finance_query.staged_submission import (  # noqa: E402
    SUPPORTED_STAGED_SUBMISSION_FAMILIES,
    write_staged_execution_record,
)
from finance_query.submission import validate_submission_directory  # noqa: E402
from finance_query.table_structure import validate_structure_sidecar  # noqa: E402


PROTOCOL = "qwen_staged_submission_candidate_subset_v1"
SOURCE_LEDGER_PROTOCOL = "qwen_staged_execution_ledger_v1"


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8-sig") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(payload: Mapping[str, Any]) -> str:
    value = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def atomic_write_json(path: Path, payload: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def atomic_write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def index_rows(rows: Iterable[Mapping[str, Any]], *, key: str, name: str) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    for row in rows:
        try:
            question_id = int(row[key])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(f"{name} has invalid id") from error
        if question_id in result:
            raise ValueError(f"{name} has duplicate Q{question_id}")
        result[question_id] = dict(row)
    return result


def validate_source_ledger(ledger: Path, bundle: Path) -> dict[int, dict[str, Any]]:
    manifest_path = ledger.with_suffix(".manifest.json")
    if not manifest_path.is_file():
        raise FileNotFoundError("Qwen staged execution ledger manifest is missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (
        manifest.get("protocol") != SOURCE_LEDGER_PROTOCOL
        or manifest.get("submission_eligible") is not False
        or manifest.get("training_eligible") is not False
        or manifest.get("provenance_promotion_allowed") is not False
        or manifest.get("sidecar_sha256") != sha256_file(ledger)
        or manifest.get("bundle_review_items_sha256")
        != sha256_file(bundle / "review_items.jsonl")
    ):
        raise ValueError("Qwen staged execution ledger provenance is invalid")
    rows = index_rows(load_jsonl(ledger), key="id", name="Qwen staged execution ledger")
    if manifest.get("question_ids") != sorted(rows):
        raise ValueError("Qwen staged execution ledger manifest question IDs differ")
    if int(manifest.get("record_count") or -1) != len(rows):
        raise ValueError("Qwen staged execution ledger manifest record count differs")
    return rows


def _canonical_header(context: Mapping[str, Any], column_index: int) -> str:
    matches = [
        str(value.get("source_label") or "")
        for value in ((context.get("canonical_headers") or {}).get("columns") or [])
        if value.get("column_index") == column_index
    ]
    if len(matches) != 1 or not matches[0]:
        raise ValueError("canonical V3 header is absent or ambiguous")
    return matches[0]


def load_source_tables(bundle: Path) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    structured_path = bundle / "tables_structured_v2.jsonl"
    context_path = bundle / "tables_evidence_context_v3.jsonl"
    validate_structure_sidecar(bundle, structured_path)
    validate_evidence_context_sidecar(bundle, structured_path, context_path)
    raw_tables = {
        str(row["internal_table_uid"]): row for row in load_jsonl(bundle / "tables.jsonl")
    }
    tables: dict[str, dict[str, Any]] = {}
    for row in load_jsonl(structured_path):
        uid = str(row.get("internal_table_uid") or "")
        if uid not in raw_tables:
            raise ValueError(f"V2 table is absent from raw bundle: {uid}")
        if str((row.get("structure_quality") or {}).get("status") or "") != "reconstructed_from_raw_html":
            continue
        tables[uid] = {**raw_tables[uid], **row}
    contexts = {
        str(row["internal_table_uid"]): row for row in load_jsonl(context_path)
    }
    return tables, contexts


def _replayed_bindings(record: Mapping[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    result: list[tuple[str, dict[str, Any]]] = []
    gates = record.get("direct_replay_gate") or {}
    if not isinstance(gates, Mapping) or not gates:
        raise ValueError("staged ledger has no direct replay gates")
    for gate in gates.values():
        if not isinstance(gate, Mapping):
            raise ValueError("staged replay gate is malformed")
        status = str(gate.get("status") or "")
        if status == "source_free_deterministic_transition":
            continue
        if status != "direct_replay_ready":
            raise ValueError("staged direct replay gate is not ready")
        stage_id = str(gate.get("stage_id") or "")
        if not stage_id:
            raise ValueError("staged direct replay gate lacks a stage ID")
        cells = gate.get("replayed_bindings")
        if not isinstance(cells, list) or not cells:
            raise ValueError("staged direct replay gate lacks replayed cells")
        valid_cells = [dict(cell) for cell in cells if isinstance(cell, Mapping)]
        if len(valid_cells) != len(cells):
            raise ValueError("staged replayed binding is malformed")
        result.extend((stage_id, cell) for cell in valid_cells)
    return result


def _require_independent_gates(record: Mapping[str, Any]) -> None:
    gates = record.get("independent_critic_gate") or {}
    if not isinstance(gates, Mapping) or not gates:
        raise ValueError("staged ledger has no independent-critic gates")
    for gate in gates.values():
        if not isinstance(gate, Mapping):
            raise ValueError("staged independent-critic gate is malformed")
        if str(gate.get("status") or "") not in {
            "independent_critic_ready",
            "independent_transition_ready",
        }:
            raise ValueError("staged independent-critic gate is not ready")


def _binding_from_replay(
    *,
    question_id: int,
    stage_id: str,
    replay: Mapping[str, Any],
    tables: Mapping[str, Mapping[str, Any]],
    contexts: Mapping[str, Mapping[str, Any]],
) -> tuple[str, DirectBinding, Mapping[str, Any]]:
    company = str(replay.get("company") or "")
    variable_id = str(replay.get("variable_id") or "")
    uid = str(replay.get("internal_table_uid") or "")
    row_index = replay.get("row_index")
    column_index = replay.get("column_index")
    if not company or not variable_id or uid not in tables:
        raise ValueError(f"Q{question_id}: replayed binding identity is invalid")
    if not isinstance(row_index, int) or isinstance(row_index, bool) or not isinstance(column_index, int) or isinstance(column_index, bool):
        raise ValueError(f"Q{question_id}: replayed binding coordinates are invalid")
    table, context = tables[uid], contexts.get(uid)
    if context is None:
        raise ValueError(f"Q{question_id}: replayed V2 table lacks V3 context")
    rows = table.get("rows") or []
    if not 0 <= row_index < len(rows) or not 0 <= column_index < len(rows[row_index]):
        raise ValueError(f"Q{question_id}: replayed binding is outside V2 table")
    raw_value = str(rows[row_index][column_index])
    canonical_header = _canonical_header(context, column_index)
    if (
        raw_value != str(replay.get("raw_value") or "")
        or canonical_header != str(replay.get("canonical_header") or "")
        or str(table.get("document_id") or "") != str(replay.get("document_id") or "")
    ):
        raise ValueError(f"Q{question_id}: replayed cell no longer matches immutable V2/V3 source")
    parsed = parse_decimal(raw_value)
    if parsed.value is None or any(
        warning not in RELIABLE_BINDING_WARNINGS for warning in parsed.warnings
    ):
        raise ValueError(f"Q{question_id}: replayed source value is not safely numeric")
    source_unit = infer_unit(
        " ".join(
            [canonical_header, *[str(value) for value in replay.get("unit_labels") or []]]
        ),
        "",
    )
    binding = DirectBinding(
        internal_table_uid=uid,
        document_id=str(table["document_id"]),
        row_index=row_index,
        column_index=column_index,
        row_text=row_label([str(value) for value in rows[row_index]]),
        column_text=canonical_header,
        raw_value=raw_value,
        parsed_value=parsed.value,
        source_unit=source_unit,
        target_unit=None,
        converted_value=parsed.value,
        binding_score=1.0,
        warnings=list(parsed.warnings),
    )
    operand_id = f"{stage_id}.{company}.{variable_id}"
    return operand_id, binding, table


def build_candidate_package(
    *,
    bundle_dir: Path,
    staged_ledger: Path,
    output_dir: Path,
) -> dict[str, Any]:
    bundle_dir = bundle_dir.resolve()
    staged_ledger = staged_ledger.resolve()
    output_dir = output_dir.resolve()
    if output_dir.exists():
        raise FileExistsError(f"Refusing to overwrite candidate output directory: {output_dir}")
    records = validate_source_ledger(staged_ledger, bundle_dir)
    questions = index_rows(load_jsonl(bundle_dir / "review_items.jsonl"), key="id", name="review items")
    tables, contexts = load_source_tables(bundle_dir)
    output_dir.mkdir(parents=True, exist_ok=False)
    candidate_records: list[dict[str, Any]] = []
    provenance_rows: list[dict[str, Any]] = []
    compile_rows: list[dict[str, Any]] = []
    for question_id, ledger_record in sorted(records.items()):
        if question_id not in questions:
            raise ValueError(f"Q{question_id}: absent from review items")
        if (
            str(ledger_record.get("execution_status") or "") != "grounded"
            or str(ledger_record.get("grounding_status") or "") != "staged_exact_cells_replayed"
            or str(ledger_record.get("provenance_status") or "") != "machine_calibrated"
            or ledger_record.get("submission_eligible") is not False
            or ledger_record.get("training_eligible") is not False
            or not bool(ledger_record.get("requires_production_audit"))
        ):
            raise ValueError(f"Q{question_id}: staged ledger row is not an audited non-production result")
        family = str(ledger_record.get("route_family") or "")
        if family not in SUPPORTED_STAGED_SUBMISSION_FAMILIES:
            raise ValueError(f"Q{question_id}: unsupported staged route family")
        _require_independent_gates(ledger_record)
        bindings: dict[str, tuple[DirectBinding, Mapping[str, Any]]] = {}
        for stage_id, replay in _replayed_bindings(ledger_record):
            operand_id, binding, asset = _binding_from_replay(
                question_id=question_id,
                stage_id=stage_id,
                replay=replay,
                tables=tables,
                contexts=contexts,
            )
            if operand_id in bindings:
                raise ValueError(f"Q{question_id}: duplicate replayed staged operand {operand_id}")
            bindings[operand_id] = (binding, asset)
        record = write_staged_execution_record(
            question_id=question_id,
            question=str(questions[question_id].get("question") or ""),
            route_family=family,
            operand_bindings=bindings,
            expected_result_value=str(ledger_record.get("result_value") or ""),
            expected_result_unit=str(ledger_record.get("result_unit") or ""),
            package_root=output_dir,
        )
        candidate_records.append(record)
        compile_rows.append(
            {
                "id": question_id,
                "protocol": PROTOCOL,
                "provenance_status": "machine_calibrated",
                "annotation_status": "machine_calibrated",
                "execution_status": "grounded",
                "grounding_status": "staged_exact_cells_replayed",
                "execution_mode": "exact_staged_contract",
                "formula_definition_status": "confirmed",
                "route_family": family,
                "result_value": ledger_record["result_value"],
                "result_unit": ledger_record["result_unit"],
                "operand_bindings": [
                    {
                        "operand_id": operand_id,
                        "internal_table_uid": binding.internal_table_uid,
                        "binding": binding.to_dict(),
                    }
                    for operand_id, (binding, _asset) in sorted(bindings.items())
                ],
                "source_ledger_record_sha256": canonical_sha256(ledger_record),
                "candidate_submission_record_sha256": canonical_sha256(record),
                "submission_eligible": False,
                "training_eligible": False,
                "requires_full_production_audit": True,
                "provenance_promotion_allowed": False,
            }
        )
        provenance_rows.append(
            {
                "id": question_id,
                "route_family": family,
                "result_value": ledger_record["result_value"],
                "result_unit": ledger_record["result_unit"],
                "source_ledger_sha256": sha256_file(staged_ledger),
                "submission_eligible": False,
                "requires_full_production_audit": True,
            }
        )
    subset_questions = [questions[question_id] for question_id in sorted(records)]
    atomic_write_json(output_dir / "submission.json", candidate_records)
    validation = validate_submission_directory(output_dir, subset_questions)
    atomic_write_jsonl(output_dir / "staged_candidate_provenance.jsonl", provenance_rows)
    atomic_write_jsonl(output_dir / "staged_compile_candidates.jsonl", compile_rows)
    manifest = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "coverage_mode": "validated_nonproduction_subset",
        "question_count": len(candidate_records),
        "question_ids": sorted(records),
        "submission_contract_validated": True,
        "submission_contract_validation": validation.to_dict(),
        "full_corpus_submission_eligible": False,
        "submission_eligible": False,
        "training_eligible": False,
        "provenance_promotion_allowed": False,
        "requires_full_production_audit": True,
        "bundle_review_items_sha256": sha256_file(bundle_dir / "review_items.jsonl"),
        "raw_tables_sha256": sha256_file(bundle_dir / "tables.jsonl"),
        "structured_tables_sha256": sha256_file(bundle_dir / "tables_structured_v2.jsonl"),
        "evidence_context_sha256": sha256_file(bundle_dir / "tables_evidence_context_v3.jsonl"),
        "source_staged_ledger_sha256": sha256_file(staged_ledger),
        "submission_json_sha256": sha256_file(output_dir / "submission.json"),
        "provenance_sidecar_sha256": sha256_file(output_dir / "staged_candidate_provenance.jsonl"),
        "compile_candidates_sha256": sha256_file(output_dir / "staged_compile_candidates.jsonl"),
    }
    atomic_write_json(output_dir / "CANDIDATE_MANIFEST.json", manifest)
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle-dir", type=Path, required=True)
    parser.add_argument("--staged-ledger", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    manifest = build_candidate_package(
        bundle_dir=args.bundle_dir,
        staged_ledger=args.staged_ledger,
        output_dir=args.output_dir,
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
