#!/usr/bin/env python3
"""Run the population-level source-bound plan-union experiment.

This runner deliberately consumes existing immutable proposal ledgers and
writes a new candidate directory.  It does not alter a builder output, the
control submission, or any historical artifact.
"""

from __future__ import annotations

import argparse
import csv
from decimal import Decimal
import json
from pathlib import Path
import zipfile
from typing import Any, Mapping, Sequence

from finance_query.research.source_bound_plan_union import (
    HYPOTHESIS,
    MAX_PLANS_PER_QUESTION,
    SOURCE_BOUND_PLAN_UNION_PROTOCOL,
    SourceBoundPlanUnionError,
    answer_diff_counts,
    canonical_sha256,
    collect_ledger_rows,
    load_jsonl,
    load_review_items,
    load_tables_for_uids,
    select_population_union,
    sha256_file,
)


def _path(value: object, *, name: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty path")
    return Path(value).expanduser().resolve()


def _int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _decimal(value: object) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = Decimal(str(value).strip())
    except Exception:  # noqa: BLE001 - input ledgers are external artifacts
        return None
    return result if result.is_finite() else None


def _json_safe(value: object) -> object:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, Mapping):
        return {str(key): _json_safe(nested) for key, nested in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(nested) for nested in value]
    return value


def _load_submission(path: Path) -> list[dict[str, Any]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise SourceBoundPlanUnionError(f"control submission must be a list of objects: {path}")
    return value


def _index_rows(rows: Sequence[Mapping[str, Any]], *, key: str) -> dict[int, dict[str, Any]]:
    indexed: dict[int, dict[str, Any]] = {}
    for row in rows:
        question_id = _int(row.get(key))
        if question_id is None or question_id in indexed:
            raise SourceBoundPlanUnionError(f"rows must contain unique integer {key}")
        indexed[question_id] = dict(row)
    return indexed


def _collect_uids(
    *,
    control_rows: Mapping[int, Mapping[str, Any]],
    candidate_rows: Mapping[int, Sequence[tuple[str, Mapping[str, Any]]]],
) -> set[str]:
    uids: set[str] = set()
    rows: list[Mapping[str, Any]] = list(control_rows.values())
    rows.extend(row for values in candidate_rows.values() for _label, row in values)
    for row in rows:
        evidence = row.get("evidence")
        if not isinstance(evidence, (list, tuple)):
            continue
        for item in evidence:
            if isinstance(item, Mapping):
                uid = str(item.get("internal_table_uid") or item.get("table_uid") or "").strip()
                if uid:
                    uids.add(uid)
    return uids


def _load_line_map(path: Path | None) -> dict[str, int]:
    if path is None:
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise SourceBoundPlanUnionError("source line map must be a JSON object")
    result: dict[str, int] = {}
    for key, raw in value.items():
        line = _int(raw)
        if line is not None and line > 0:
            result[str(key)] = line
    return result


def _selected_evidence(plan: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    value = plan.get("selection_evidence")
    if not isinstance(value, (list, tuple)):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _table_row(table: Mapping[str, Any], row_index: object) -> list[object] | None:
    index = _int(row_index)
    rows = table.get("rows")
    if index is None or not isinstance(rows, list) or index < 0 or index >= len(rows):
        return None
    row = rows[index]
    return row if isinstance(row, list) else None


def _candidate_tables(
    *,
    selected: Mapping[str, Any],
    tables_by_uid: Mapping[str, Mapping[str, Any]],
    line_map: Mapping[str, int],
    fallback_record: Mapping[str, Any],
) -> tuple[list[str], list[str]]:
    docs: list[str] = []
    refs: list[str] = []
    for evidence in _selected_evidence(selected):
        uid = str(evidence.get("internal_table_uid") or evidence.get("table_uid") or "").strip()
        table = tables_by_uid.get(uid)
        if table is None:
            continue
        document = str(table.get("document_id") or evidence.get("document_id") or "").removesuffix(".txt")
        if document and document not in docs:
            docs.append(document)
        line = line_map.get(uid)
        if document and line is not None:
            ref = f"{document}|{line}"
            if ref not in refs:
                refs.append(ref)
    if not docs:
        docs = [str(value) for value in fallback_record.get("relevant_docs") or []]
    if not refs:
        refs = [str(value) for value in fallback_record.get("relevant_tables") or []]
    return docs, refs


def _write_candidate_csv(
    path: Path,
    *,
    selected: Mapping[str, Any],
    tables_by_uid: Mapping[str, Mapping[str, Any]],
) -> None:
    plan_operands = [
        item for item in selected.get("operands") or [] if isinstance(item, Mapping)
    ]
    evidence = _selected_evidence(selected)
    fields = [
        "value",
        "operand_value",
        "raw_value",
        "operand_role",
        "document_id",
        "internal_table_uid",
        "row_index",
        "column_index",
        "row_label",
        "source_multiplier",
        "confidence_tier",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for index, operand in enumerate(plan_operands):
            source = evidence[index] if index < len(evidence) else {}
            uid = str(source.get("internal_table_uid") or source.get("table_uid") or "").strip()
            table = tables_by_uid.get(uid) or {}
            row_index = source.get("row_index")
            column_index = source.get("column_index")
            table_row = _table_row(table, row_index)
            raw_value: object = ""
            if table_row is not None:
                column = _int(column_index)
                if column is not None and 0 <= column < len(table_row):
                    raw_value = table_row[column]
            operand_value = operand.get("raw_value")
            writer.writerow(
                {
                    "value": operand_value if operand_value is not None else "",
                    "operand_value": operand_value if operand_value is not None else "",
                    "raw_value": raw_value,
                    "operand_role": source.get("role") or "source_bound_plan_union",
                    "document_id": table.get("document_id") or source.get("document_id") or "",
                    "internal_table_uid": uid,
                    "row_index": row_index if row_index is not None else "",
                    "column_index": column_index if column_index is not None else "",
                    "row_label": source.get("row_label")
                    or (table_row[0] if table_row else ""),
                    "source_multiplier": source.get("source_to_vnd_multiplier")
                    or source.get("source_multiplier")
                    or "1",
                    "confidence_tier": selected.get("route_family") or "source_bound_plan_union",
                }
            )


def _build_submission(
    *,
    control_submission: Sequence[Mapping[str, Any]],
    selected_rows: Sequence[Mapping[str, Any]],
    tables_by_uid: Mapping[str, Mapping[str, Any]],
    line_map: Mapping[str, int],
    output_dir: Path,
) -> list[dict[str, Any]]:
    selected_by_id = _index_rows(selected_rows, key="question_id")
    records: list[dict[str, Any]] = []
    data_dir = output_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    for control in control_submission:
        question_id = _int(control.get("id"))
        if question_id is None or question_id not in selected_by_id:
            raise SourceBoundPlanUnionError("control/candidate submission populations differ")
        selected = selected_by_id[question_id]
        plan = selected.get("selected_plan")
        plan = plan if isinstance(plan, Mapping) else {}
        answer = _decimal(selected.get("selected_answer_decimal"))
        if answer is None:
            answer = _decimal(control.get("answer")) or Decimal("0")
        docs, refs = _candidate_tables(
            selected=plan,
            tables_by_uid=tables_by_uid,
            line_map=line_map,
            fallback_record=control,
        )
        filename = f"q{question_id:04d}_evidence.csv"
        _write_candidate_csv(
            data_dir / filename,
            selected=plan,
            tables_by_uid=tables_by_uid,
        )
        records.append(
            {
                "id": question_id,
                "question": control.get("question"),
                "answer": float(answer),
                "relevant_docs": docs,
                "relevant_tables": refs,
                "evidence": [{"variable": "df1", "csv_path": f"data/{filename}"}],
                "pandas_query": str(
                    selected.get("pandas_query")
                    or control.get("pandas_query")
                    or "float(df1.loc[0, 'value'])"
                ),
                "model_reranked": bool(control.get("model_reranked", False)),
                "prediction_tier": selected.get("selected_route_family")
                or control.get("prediction_tier"),
                "candidate_union_source": selected.get("selected_source_run"),
                "candidate_union_plan_complete": bool(
                    selected.get("selected_plan_complete")
                ),
            }
        )
    records.sort(key=lambda row: int(row["id"]))
    return records


def _write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(_json_safe(payload), ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.write_text(
        "".join(
            json.dumps(_json_safe(row), ensure_ascii=False, sort_keys=True) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )


def _zip_submission(output_dir: Path, records: Sequence[Mapping[str, Any]]) -> Path:
    submission_path = output_dir / "submission.json"
    submission_path.write_text(
        json.dumps(_json_safe(list(records)), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    archive_path = output_dir / "submission.zip"
    with zipfile.ZipFile(
        archive_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
    ) as archive:
        archive.write(submission_path, "submission.json")
        for csv_path in sorted((output_dir / "data").glob("*.csv")):
            archive.write(csv_path, f"data/{csv_path.name}")
    return archive_path


def run(args: argparse.Namespace) -> dict[str, Any]:
    control_ledger_path = _path(args.control_ledger, name="control-ledger")
    control_submission_path = _path(args.control_submission, name="control-submission")
    review_items_path = _path(args.review_items, name="review-items")
    structured_tables_path = _path(args.structured_tables, name="structured-tables")
    output_dir = _path(args.output_dir, name="output-dir")
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite output directory: {output_dir}")
    if not args.candidate_ledger:
        raise ValueError("at least one --candidate-ledger is required")
    candidate_ledger_paths = [
        _path(value, name="candidate-ledger") for value in args.candidate_ledger
    ]
    line_map_path = (
        _path(args.source_line_map, name="source-line-map")
        if args.source_line_map
        else None
    )

    review_items = load_review_items(review_items_path)
    control_rows = _index_rows(load_jsonl(control_ledger_path), key="question_id")
    control_submission = _load_submission(control_submission_path)
    control_submission_by_id = _index_rows(control_submission, key="id")
    population = sorted(review_items)
    expected_count = args.expected_question_count
    if expected_count is not None and len(population) != int(expected_count):
        raise SourceBoundPlanUnionError(
            f"review population count {len(population)} != expected {expected_count}"
        )
    if set(control_rows) != set(population) or set(control_submission_by_id) != set(population):
        raise SourceBoundPlanUnionError("control/review/submission populations do not match")

    ledger_rows, ledger_stats = collect_ledger_rows(candidate_ledger_paths)
    candidate_uids = _collect_uids(
        control_rows=control_rows,
        candidate_rows=ledger_rows,
    )
    tables_by_uid = load_tables_for_uids(structured_tables_path, candidate_uids)
    line_map = _load_line_map(line_map_path)
    selected_rows, selection_stats = select_population_union(
        question_ids=population,
        review_items=review_items,
        ledger_rows=ledger_rows,
        tables_by_uid=tables_by_uid,
        control_rows=control_rows,
        max_plans=int(args.max_plans),
        metric_row_binding_gate=bool(args.metric_row_binding_gate),
    )
    diff_counts = answer_diff_counts(
        control_submission=control_submission,
        candidate_rows=selected_rows,
    )

    output_dir.mkdir(parents=True, exist_ok=False)
    candidate_submission = _build_submission(
        control_submission=control_submission,
        selected_rows=selected_rows,
        tables_by_uid=tables_by_uid,
        line_map=line_map,
        output_dir=output_dir,
    )
    _write_jsonl(output_dir / "source_bound_plan_union_selected_v1.jsonl", selected_rows)
    archive_path = _zip_submission(output_dir, candidate_submission)

    complete_count = sum(
        bool(row.get("selected_plan_complete")) for row in selected_rows
    )
    replay_pass_count = sum(
        str((row.get("selected_plan") or {}).get("replay_status")) == "PASS"
        for row in selected_rows
        if isinstance(row.get("selected_plan"), Mapping)
    )
    source_closure_pass_count = sum(
        str(row.get("selected_source_closure_status")) == "PASS"
        for row in selected_rows
    )
    errors: list[str] = []
    if len(candidate_submission) != len(population):
        errors.append("candidate_submission_population_mismatch")
    if len({int(row["id"]) for row in candidate_submission}) != len(population):
        errors.append("candidate_submission_duplicate_id")
    report = {
        "schema_version": 1,
        "protocol": SOURCE_BOUND_PLAN_UNION_PROTOCOL,
        "status": "COMPLETE_DIAGNOSTIC" if not errors else "FAILED",
        "hypothesis": HYPOTHESIS,
        "active_candidate_gates": {
            "metric_row_binding_gate": bool(args.metric_row_binding_gate),
            "scope": "direct_lookup_only",
            "review_and_unknown_remain_eligible": True,
        },
        "control": {
            "ledger_path": str(control_ledger_path),
            "ledger_sha256": sha256_file(control_ledger_path),
            "submission_path": str(control_submission_path),
            "submission_sha256": sha256_file(control_submission_path),
            "fingerprint": canonical_sha256(
                {
                    "ledger": sha256_file(control_ledger_path),
                    "submission": sha256_file(control_submission_path),
                }
            ),
        },
        "candidate": {
            "fingerprint": canonical_sha256(
                {
                    "protocol": SOURCE_BOUND_PLAN_UNION_PROTOCOL,
                    "hypothesis": HYPOTHESIS,
                    "candidate_ledgers": [sha256_file(path) for path in candidate_ledger_paths],
                    "structured_tables": sha256_file(structured_tables_path),
                    "review_items": sha256_file(review_items_path),
                    "max_plans": int(args.max_plans),
                    "metric_row_binding_gate": bool(args.metric_row_binding_gate),
                }
            ),
            "candidate_ledgers": [
                {"path": str(path), "sha256": sha256_file(path)}
                for path in candidate_ledger_paths
            ],
            "plan_cap": int(args.max_plans),
            "source_table_uid_count": len(tables_by_uid),
        },
        "population": {
            "expected_question_count": expected_count,
            "question_count": len(population),
            "processed_records": len(selected_rows),
            "missing_records": len(population) - len(selected_rows),
            "errors": errors,
            "split_policy": "full_population_frozen_control_candidate_union_no_tuning",
        },
        "scorer_gold": {
            "identity": "NOT_AVAILABLE",
            "command": "NOT_RUN",
            "exit_status": "NOT_RUN",
        },
        "ANSWER_ACCURACY": {
            "control": "NOT_MEASURED",
            "candidate": "NOT_MEASURED",
            "delta": "NOT_MEASURED",
        },
        "EXECUTION_ACCURACY": {
            "control": "NOT_MEASURED",
            "candidate": "NOT_MEASURED",
            "delta": "NOT_MEASURED",
        },
        "answer_diff": diff_counts,
        "diagnostic_metrics": {
            **ledger_stats,
            **selection_stats,
            "candidate_structurally_complete_selected": complete_count,
            "candidate_ast_replay_pass_selected": replay_pass_count,
            "candidate_source_closure_pass_selected": source_closure_pass_count,
            "candidate_structural_rate": complete_count / len(population)
            if population
            else 0.0,
            "answer_diff_is_not_accuracy": True,
            "replay_is_not_answer_accuracy": True,
        },
        "family_level_results": "NOT_MEASURED_WITHOUT_INDEPENDENT_GOLD_OR_SCORER",
        "decision": {
            "value": "INVESTIGATE_FURTHER",
            "reason": (
                "The union rule is complete on the frozen population only as a "
                "structural/replay candidate; no independent answer scorer or gold "
                "set was available to measure correctness or regressions."
            ),
        },
        "authority": {
            "status": "CANDIDATE_ONLY",
            "answer_authorized": False,
            "evidence_authorized": False,
            "submission_eligible": False,
            "promotion_allowed": False,
            "release_authorized": False,
        },
        "outputs": {
            "selected_ledger": str(output_dir / "source_bound_plan_union_selected_v1.jsonl"),
            "submission": str(output_dir / "submission.json"),
            "archive": str(archive_path),
        },
    }
    report_path = output_dir / "source_bound_plan_union_ab_report_v1.json"
    _write_json(report_path, report)
    manifest = {
        "schema_version": 1,
        "protocol": f"{SOURCE_BOUND_PLAN_UNION_PROTOCOL}_manifest",
        "report": {"path": str(report_path), "sha256": sha256_file(report_path)},
        "outputs": {},
    }
    for output in (
        output_dir / "source_bound_plan_union_selected_v1.jsonl",
        output_dir / "submission.json",
        archive_path,
        report_path,
    ):
        manifest["outputs"][output.name] = {
            "path": str(output),
            "sha256": sha256_file(output),
            "size_bytes": output.stat().st_size,
        }
    _write_json(output_dir / "manifest.json", manifest)
    return {**report, "manifest_path": str(output_dir / "manifest.json")}


def configure_parser(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    parser.add_argument("--control-ledger", required=True)
    parser.add_argument("--control-submission", required=True)
    parser.add_argument("--candidate-ledger", action="append", required=True)
    parser.add_argument("--review-items", required=True)
    parser.add_argument("--structured-tables", required=True)
    parser.add_argument("--source-line-map", default=None)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--expected-question-count", type=int, default=None)
    parser.add_argument(
        "--max-plans",
        type=int,
        default=MAX_PLANS_PER_QUESTION,
        help="bounded plans per question after structural hydration",
    )
    parser.add_argument(
        "--metric-row-binding-gate",
        action="store_true",
        help=(
            "research gate: reject only direct-lookup plans with a metric-row "
            "FAIL; REVIEW/UNKNOWN remain eligible"
        ),
    )
    return parser


def main() -> None:
    parser = configure_parser(argparse.ArgumentParser())
    args = parser.parse_args()
    try:
        report = run(args)
    except (FileNotFoundError, ValueError, SourceBoundPlanUnionError) as error:
        parser.error(str(error))
    print(json.dumps(_json_safe(report), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
