#!/usr/bin/env python3
"""Materialize a narrowly scoped, replay-validated ViFinQA route overlay.

The full-corpus route build is useful for discovering source-first answers, but
hydrating the complete corpus also changes unrelated generic candidate ranking.
This adapter keeps the accepted local submission as the base and replaces only
rows whose source route was independently replayed in the same full-corpus
build.  The preferred independent-replay mode derives the replacement from
the verified source-table UID closure, so a question ID does not control the
predictor or answer selection.  The older explicit-ID mode remains for legacy
artifacts and is not used for the new population-level experiments.

This is a best-effort submission artifact, not a strict certificate.  The
script never computes a new numeric value: every replacement record and its
evidence CSV is copied from the source build, then the complete 1,012-row
submission is replayed again before the ZIP is written.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import shutil
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping


DEFAULT_PERIOD_IDS = {
    815,
    835,
    838,
    845,
    847,
    859,
    903,
    911,
    914,
    969,
    972,
    988,
    996,
}
DEFAULT_ROUTE_IDS = {
    465,
    536,
    553,
    819,
    918,
    949,
    973,
    1003,
} | DEFAULT_PERIOD_IDS
LEASE_THRESHOLD_IDS = {932}
CONDITIONAL_TEMPORAL_IDS = {505, 514, 521, 523, 526}
NAMED_COMPENSATION_IDS = {15, 266, 311}
LOAN_PROVISION_IDS = {324}
SUPPLIER_PAYABLE_IDS = {338}
# Keep independently audited accounting-alias rows separate from the
# canonical argmax cohort.  Unscoped rows with a separate cross-scope winner
# consensus use an explicit allow-list below; scope ranking alone never
# materializes them.
ACCOUNTING_ALIAS_IDS = {822, 900, 921, 971, 989}
SCOPE_CONSENSUS_IDS = {829}
ARGMAX_STRICT_IDS = {
    813,
    832,
    841,
    850,
    860,
    874,
    876,
    878,
    890,
    897,
    904,
    906,
    910,
    929,
    933,
    936,
    946,
    948,
    953,
    974,
    981,
    997,
    999,
    1000,
    879,
    1008,
    928,
}
# Packaging-only contract for the independently replayed ratio argmax family.
# The runtime adapter recognizes the reusable question family; these IDs are
# used here only to select already-built rows for an auditable overlay.
RATIO_ARGMAX_STRICT_IDS = {826, 877, 982}

EXPECTED_ROUTE_BY_ID = {
    465: "source_first_multi_entity_ratio_selector_v1",
    553: "source_first_multi_entity_ratio_selector_v1",
    536: "source_first_multi_entity_selector_v1",
    819: "source_first_multi_entity_direct_aggregation_v1",
    827: "source_first_multi_entity_direct_aggregation_v1",
    918: "source_first_multi_entity_direct_aggregation_v1",
    927: "source_first_multi_entity_direct_aggregation_v1",
    1003: "source_first_multi_entity_direct_aggregation_v1",
    949: "source_first_multi_entity_threshold_v1",
    973: "source_first_multi_entity_conditional_count_v1",
    1002: "source_first_multi_entity_interest_threshold_v1",
    76: "program_subsidiary_investment_v1",
    77: "program_subsidiary_investment_v1",
    238: "program_subsidiary_investment_v1",
    290: "program_subsidiary_investment_v1",
    815: "source_first_period_extreme_v1",
    835: "source_first_period_extreme_v1",
    838: "source_first_period_extreme_v1",
    845: "source_first_period_extreme_v1",
    847: "source_first_period_extreme_v1",
    859: "source_first_period_extreme_v1",
    903: "source_first_period_extreme_v1",
    911: "source_first_period_extreme_v1",
    914: "source_first_period_extreme_v1",
    969: "source_first_period_extreme_v1",
    972: "source_first_period_extreme_v1",
    988: "source_first_period_extreme_v1",
    996: "source_first_period_extreme_v1",
    994: "source_first_period_extreme_v1",
    37: "source_first_exact_row_v1",
    125: "source_first_exact_row_v1",
    128: "source_first_exact_row_v1",
    151: "source_first_exact_row_v1",
    858: "source_first_multi_entity_direct_aggregation_v1",
    932: "source_first_multi_entity_lease_threshold_v1",
}
EXPECTED_ROUTE_BY_ID.update(
    {
        question_id: "source_first_conditional_temporal_v1"
        for question_id in CONDITIONAL_TEMPORAL_IDS
    }
)
EXPECTED_ROUTE_BY_ID.update(
    {
        question_id: "program_arg_extreme_period_v1"
        for question_id in ARGMAX_STRICT_IDS
    }
)
EXPECTED_ROUTE_BY_ID.update(
    {
        question_id: "program_named_governance_compensation_v1"
        for question_id in NAMED_COMPENSATION_IDS
    }
)
EXPECTED_ROUTE_BY_ID.update(
    {
        question_id: "program_arg_extreme_period_v1"
        for question_id in ACCOUNTING_ALIAS_IDS
    }
)
EXPECTED_ROUTE_BY_ID.update(
    {
        question_id: "program_arg_extreme_period_v1"
        for question_id in SCOPE_CONSENSUS_IDS
    }
)
EXPECTED_ROUTE_BY_ID.update(
    {
        question_id: "program_arg_extreme_ratio_period_v1"
        for question_id in RATIO_ARGMAX_STRICT_IDS
    }
)
EXPECTED_ROUTE_BY_ID.update(
    {
        question_id: "program_loan_provision_balance_v1"
        for question_id in LOAN_PROVISION_IDS
    }
)
EXPECTED_ROUTE_BY_ID.update(
    {
        question_id: "program_supplier_payable_v1"
        for question_id in SUPPLIER_PAYABLE_IDS
    }
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"{path}:{line_number} is not a JSON object")
            rows.append(row)
    return rows


def write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(dict(row), ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def row_id(row: Mapping[str, Any]) -> int | None:
    for key in ("id", "question_id"):
        value = row.get(key)
        if value is None or value == "":
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    return None


def merge_jsonl_selected(
    base_path: Path,
    source_path: Path,
    output_path: Path,
    selected_ids: set[int],
) -> None:
    """Replace selected keyed rows while preserving every other base row."""

    base_rows = read_jsonl(base_path)
    source_rows = read_jsonl(source_path)
    source_by_id = {row_id(row): row for row in source_rows if row_id(row) is not None}
    missing = sorted(selected_ids - set(source_by_id))
    if missing:
        raise ValueError(f"{source_path} is missing selected ids: {missing}")
    replaced: list[dict[str, Any]] = []
    seen: set[int] = set()
    for row in base_rows:
        question_id = row_id(row)
        if question_id in selected_ids:
            replacement = source_by_id[question_id]
            replaced.append(dict(replacement))
            seen.add(question_id)
        else:
            replaced.append(row)
    missing_base = sorted(selected_ids - seen)
    if missing_base:
        raise ValueError(f"{base_path} is missing selected ids: {missing_base}")
    write_jsonl(output_path, replaced)


def import_validator(builder_path: Path) -> Any:
    spec = importlib.util.spec_from_file_location("vifinqa_submission_builder", builder_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load validator from {builder_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def copy_selected_evidence(
    source_submission: list[dict[str, Any]],
    source_dir: Path,
    output_dir: Path,
    selected_ids: set[int],
) -> dict[int, list[str]]:
    copied: dict[int, list[str]] = {}
    source_by_id = {int(row["id"]): row for row in source_submission}
    for question_id in sorted(selected_ids):
        row = source_by_id[question_id]
        paths: list[str] = []
        for evidence in row.get("evidence") or []:
            csv_path = str(evidence.get("csv_path") or "")
            if not csv_path.startswith("data/"):
                raise ValueError(f"Q{question_id}: invalid source csv_path={csv_path!r}")
            source_path = source_dir / csv_path
            target_path = output_dir / csv_path
            if not source_path.is_file():
                raise FileNotFoundError(source_path)
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, target_path)
            paths.append(csv_path)
        copied[question_id] = paths
    return copied


def zip_submission(output_dir: Path, zip_path: Path) -> None:
    with zipfile.ZipFile(
        zip_path,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=6,
    ) as archive:
        archive.write(output_dir / "submission.json", "submission.json")
        for csv_path in sorted((output_dir / "data").glob("*.csv")):
            archive.write(csv_path, f"data/{csv_path.name}")


def parse_ids(
    raw_ids: list[str],
    *,
    include_expense_magnitude: bool,
    include_lease_threshold: bool,
    only_selected_routes: bool,
) -> set[int]:
    selected = set() if only_selected_routes else set(DEFAULT_ROUTE_IDS)
    if include_expense_magnitude:
        selected.add(858)
    if include_lease_threshold:
        selected.update(LEASE_THRESHOLD_IDS)
    for raw in raw_ids:
        for token in raw.split(","):
            token = token.strip()
            if token:
                selected.add(int(token))
    unknown = sorted(selected - set(EXPECTED_ROUTE_BY_ID))
    if unknown:
        raise ValueError(f"selected ids have no expected route contract: {unknown}")
    return selected


def derive_ids_from_independent_replay(
    source_dir: Path,
    replay_path: Path,
    *,
    expected_route: str,
) -> tuple[set[int], dict[int, str], dict[str, Any]]:
    """Select source rows by verified table/cell closures, not by question ID.

    A replay report may contain one or more independently replayed question
    groups.  A tracking question ID is used only to keep those UID closures
    separate in the audit; the source submission row is selected by an exact
    source-cell-set match when row/column coordinates are present, and by an
    exact UID-set match for legacy replay records.  The generic route tier is
    supplied by the caller.  No question ID is used as a predictor, routing
    rule, parser rule or answer selector.
    """

    replay = read_json(replay_path.expanduser().resolve())
    if not isinstance(replay, dict) or replay.get("status") != "PASS":
        raise ValueError("independent replay must be a PASS report")
    records = replay.get("records") or []
    if not isinstance(records, list) or not records:
        raise ValueError("independent replay has no records")

    def record_uids(record: Mapping[str, Any]) -> set[str]:
        uids: set[str] = set()
        for key in ("internal_table_uid", "uid"):
            uid = str(record.get(key) or "").strip()
            if uid:
                uids.add(uid)
        for key in ("sources", "operands", "cells"):
            entries = record.get(key) or []
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, Mapping):
                    continue
                for uid_key in ("internal_table_uid", "uid"):
                    uid = str(entry.get(uid_key) or "").strip()
                    if uid:
                        uids.add(uid)
        return uids

    def int_or_none(value: Any) -> int | None:
        try:
            if value is None or str(value).strip() == "":
                return None
            return int(value)
        except (TypeError, ValueError):
            return None

    def record_cells(record: Mapping[str, Any]) -> set[tuple[str, int, int]]:
        """Read the immutable table-cell closure from a replay record."""

        cells: set[tuple[str, int, int]] = set()

        def add_entry(entry: Mapping[str, Any]) -> None:
            uid = str(
                entry.get("internal_table_uid") or entry.get("uid") or ""
            ).strip()
            row_index = int_or_none(entry.get("row_index"))
            column_index = int_or_none(entry.get("column_index"))
            if uid and row_index is not None and column_index is not None:
                cells.add((uid, row_index, column_index))

        add_entry(record)
        for key in ("sources", "operands", "cells"):
            entries = record.get(key) or []
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if isinstance(entry, Mapping):
                    add_entry(entry)
        return cells

    # Keep closures grouped by the replay's tracking identity when present.
    # The identity is never used to find the source row; only the exact UID
    # set below can do that.  A single ungrouped record is also supported for
    # older replay formats that contain no question_id field.
    uid_closures: dict[str, set[str]] = {}
    cell_closures: dict[str, set[tuple[str, int, int]]] = {}
    closure_modes: dict[str, str] = {}
    for index, record in enumerate(records):
        if not isinstance(record, Mapping):
            raise ValueError(f"independent replay record {index} is not an object")
        record_uid_set = record_uids(record)
        if not record_uid_set:
            raise ValueError(
                f"independent replay record {index} has no verified table UIDs"
            )
        tracking_id = record.get("question_id")
        closure_key = (
            f"question:{tracking_id}"
            if tracking_id is not None
            else "ungrouped"
        )
        if closure_key in uid_closures:
            uid_closures[closure_key].update(record_uid_set)
        else:
            uid_closures[closure_key] = set(record_uid_set)
        record_cell_set = record_cells(record)
        if record_cell_set:
            if closure_key in cell_closures:
                cell_closures[closure_key].update(record_cell_set)
            else:
                cell_closures[closure_key] = set(record_cell_set)
            closure_modes[closure_key] = "source_cell_exact_set"
        else:
            closure_modes[closure_key] = "source_uid_exact_set_legacy"
    if "ungrouped" in uid_closures and len(uid_closures) > 1:
        raise ValueError(
            "independent replay mixes grouped and ungrouped records; "
            "cannot establish UID closures"
        )
    if len(uid_closures) > 1 and any(
        key == "ungrouped" for key in uid_closures
    ):
        raise ValueError("independent replay has ambiguous UID closures")
    verified_uids = set().union(*uid_closures.values())

    source_submission = read_json(source_dir / "submission.json")
    if not isinstance(source_submission, list):
        raise ValueError("source submission must contain an array")
    source_diagnostics = {
        int(row["id"]): row
        for row in read_jsonl(source_dir / "diagnostics.jsonl")
        if row.get("id") is not None
    }
    source_uids_by_id: dict[int, set[str]] = {}
    source_cells_by_id: dict[int, set[tuple[str, int, int]]] = {}
    for row in source_submission:
        question_id = row_id(row)
        if question_id is None:
            continue
        row_uids: set[str] = set()
        row_cells: set[tuple[str, int, int]] = set()
        for evidence in row.get("evidence") or []:
            csv_path = str(evidence.get("csv_path") or "")
            if not csv_path.startswith("data/"):
                raise ValueError(
                    f"source Q{question_id}: invalid evidence path {csv_path!r}"
                )
            evidence_path = source_dir / csv_path
            if not evidence_path.is_file():
                raise FileNotFoundError(evidence_path)
            with evidence_path.open("r", encoding="utf-8", newline="") as handle:
                for evidence_row in csv.DictReader(handle):
                    uid = str(
                        evidence_row.get("internal_table_uid") or ""
                    ).strip()
                    if uid:
                        row_uids.add(uid)
                    row_index = int_or_none(evidence_row.get("row_index"))
                    column_index = int_or_none(evidence_row.get("column_index"))
                    if uid and row_index is not None and column_index is not None:
                        row_cells.add((uid, row_index, column_index))
        source_uids_by_id[question_id] = row_uids
        source_cells_by_id[question_id] = row_cells

    selected_route_by_id: dict[int, str] = {}
    matching_uid_sets: dict[int, set[str]] = {}
    matching_cell_sets: dict[int, set[tuple[str, int, int]]] = {}
    matched_tracking_keys: dict[str, int] = {}
    for closure_key, closure_uids in sorted(uid_closures.items()):
        closure_cells = cell_closures.get(closure_key, set())
        if closure_cells:
            matches = sorted(
                question_id
                for question_id, source_cells in source_cells_by_id.items()
                if source_cells == closure_cells
            )
        else:
            matches = sorted(
                question_id
                for question_id, source_uids in source_uids_by_id.items()
                if source_uids == closure_uids
            )
        if len(matches) != 1:
            raise ValueError(
                "independent replay source closure must match exactly one source "
                f"row ({closure_key}, matches={matches})"
            )
        question_id = matches[0]
        if question_id in selected_route_by_id:
            raise ValueError(
                "independent replay UID closures map to the same source row: "
                f"Q{question_id}"
            )
        source_tier = str(source_diagnostics.get(question_id, {}).get("tier") or "")
        if source_tier != expected_route:
            raise ValueError(
                f"Q{question_id}: source tier={source_tier!r}, "
                f"expected generic route={expected_route!r}"
            )
        selected_route_by_id[question_id] = expected_route
        matching_uid_sets[question_id] = closure_uids
        if closure_cells:
            matching_cell_sets[question_id] = closure_cells
        matched_tracking_keys[closure_key] = question_id

    if not selected_route_by_id:
        raise ValueError("independent replay has no source row matches")
    replay = dict(replay)
    replay["selection_audit"] = {
        "basis": "source_evidence_cell_exact_set_when_coordinates_present",
        "closure_modes": dict(sorted(closure_modes.items())),
        "verified_source_uid_count": len(verified_uids),
        "verified_source_uid_closure_count": len(uid_closures),
        "verified_source_cell_count": sum(
            len(cells) for cells in cell_closures.values()
        ),
        "verified_source_uid_closure_counts": {
            key: len(uids) for key, uids in sorted(uid_closures.items())
        },
        "verified_source_cell_closure_counts": {
            key: len(cells) for key, cells in sorted(cell_closures.items())
        },
        "matched_submission_row_count": len(selected_route_by_id),
        "matched_submission_row_ids_tracking_only": sorted(selected_route_by_id),
        "matched_tracking_keys": matched_tracking_keys,
        "matched_uid_counts": {
            str(question_id): len(uids)
            for question_id, uids in matching_uid_sets.items()
        },
        "matched_cell_counts": {
            str(question_id): len(cells)
            for question_id, cells in matching_cell_sets.items()
        },
    }
    return set(selected_route_by_id), selected_route_by_id, replay


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-dir", type=Path, required=True)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--builder", type=Path, required=True)
    parser.add_argument(
        "--route-id",
        action="append",
        default=[],
        help="additional route id(s), comma-separated values are accepted",
    )
    parser.add_argument(
        "--include-expense-magnitude",
        action="store_true",
        help="also replace Q858 with the absolute-expense sensitivity route",
    )
    parser.add_argument(
        "--include-lease-threshold",
        action="store_true",
        help="also replace Q932 with the source-first operating-lease threshold route",
    )
    parser.add_argument(
        "--only-selected-routes",
        action="store_true",
        help="start with no default route ids; use --route-id to select an explicit delta",
    )
    parser.add_argument(
        "--independent-replay",
        type=Path,
        default=None,
        help=(
            "PASS replay JSON used to derive the overlay from exact source-table "
            "UID closure; mutually exclusive with explicit route IDs"
        ),
    )
    parser.add_argument(
        "--independent-replay-route",
        default=None,
        help="generic route tier required by --independent-replay",
    )
    args = parser.parse_args()

    base_dir = args.base_dir.expanduser().resolve()
    source_dir = args.source_dir.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()
    builder_path = args.builder.expanduser().resolve()
    for directory in (base_dir, source_dir):
        if not directory.is_dir():
            raise FileNotFoundError(directory)
    if not builder_path.is_file():
        raise FileNotFoundError(builder_path)
    if output_dir.exists() or output_dir.with_suffix(".zip").exists():
        raise FileExistsError(f"refusing to overwrite {output_dir} or its ZIP")

    base_submission_path = base_dir / "submission.json"
    source_submission_path = source_dir / "submission.json"
    base_submission = read_json(base_submission_path)
    source_submission = read_json(source_submission_path)
    if not isinstance(base_submission, list) or not isinstance(source_submission, list):
        raise ValueError("submission.json must contain an array")
    base_by_id = {int(row["id"]): row for row in base_submission}
    source_by_id = {int(row["id"]): row for row in source_submission}
    if set(base_by_id) != set(source_by_id):
        raise ValueError("base/source question populations differ")

    independent_replay_report: dict[str, Any] | None = None
    selected_route_by_id: dict[int, str]
    if args.independent_replay is not None:
        if (
            args.route_id
            or args.include_expense_magnitude
            or args.include_lease_threshold
            or not args.only_selected_routes
            or not str(args.independent_replay_route or "").strip()
        ):
            raise ValueError(
                "--independent-replay requires --only-selected-routes, "
                "--independent-replay-route, and no explicit route selectors"
            )
        (
            selected_ids,
            selected_route_by_id,
            independent_replay_report,
        ) = derive_ids_from_independent_replay(
            source_dir,
            args.independent_replay,
            expected_route=str(args.independent_replay_route).strip(),
        )
    else:
        selected_ids = parse_ids(
            args.route_id,
            include_expense_magnitude=bool(args.include_expense_magnitude),
            include_lease_threshold=bool(args.include_lease_threshold),
            only_selected_routes=bool(args.only_selected_routes),
        )
        selected_route_by_id = {
            question_id: EXPECTED_ROUTE_BY_ID[question_id]
            for question_id in selected_ids
        }
    missing = sorted(selected_ids - set(base_by_id))
    if missing:
        raise ValueError(f"selected ids absent from submissions: {missing}")

    source_diagnostics = {
        int(row["id"]): row
        for row in read_jsonl(source_dir / "diagnostics.jsonl")
        if row.get("id") is not None
    }
    route_contract_errors: list[str] = []
    for question_id in sorted(selected_ids):
        expected_route = selected_route_by_id[question_id]
        source_tier = str(source_diagnostics.get(question_id, {}).get("tier") or "")
        if source_tier != expected_route:
            route_contract_errors.append(
                f"Q{question_id}: source tier={source_tier!r}, expected={expected_route!r}"
            )
    if route_contract_errors:
        raise ValueError("route contract failed: " + "; ".join(route_contract_errors))

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(base_dir, output_dir)

    overlay_submission = []
    for row in base_submission:
        question_id = int(row["id"])
        overlay_submission.append(
            dict(source_by_id[question_id] if question_id in selected_ids else row)
        )
    (output_dir / "submission.json").write_text(
        json.dumps(overlay_submission, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    copied_evidence = copy_selected_evidence(
        source_submission,
        source_dir,
        output_dir,
        selected_ids,
    )

    for filename in (
        "diagnostics.jsonl",
        "best_surviving_candidates_v1.jsonl",
        "prediction_audit_ledger_v1.jsonl",
    ):
        base_path = base_dir / filename
        source_path = source_dir / filename
        if base_path.is_file() and source_path.is_file():
            merge_jsonl_selected(base_path, source_path, output_dir / filename, selected_ids)

    validator = import_validator(builder_path)
    validation = validator.validate_submission(output_dir, base_submission)
    if not validation.get("valid"):
        raise SystemExit(json.dumps(validation, ensure_ascii=False, indent=2))

    archive_path = output_dir.with_suffix(".zip")
    zip_submission(output_dir, archive_path)
    with zipfile.ZipFile(archive_path) as archive:
        names = set(archive.namelist())
    expected_csv_names = {f"data/q{int(row['id']):04d}_evidence.csv" for row in overlay_submission}
    zip_errors = []
    if "submission.json" not in names:
        zip_errors.append("submission.json missing")
    if expected_csv_names - names:
        zip_errors.append(f"missing_csv_count={len(expected_csv_names - names)}")
    if zip_errors:
        raise ValueError("ZIP contract failed: " + "; ".join(zip_errors))

    source_report = read_json(source_dir / "build_report.json")
    base_report = read_json(base_dir / "build_report.json")
    answer_changes = []
    source_route_counts: Counter[str] = Counter()
    used_uids: set[str] = set()
    for question_id in sorted(selected_ids):
        base_row = base_by_id[question_id]
        source_row = source_by_id[question_id]
        source_diag = source_diagnostics[question_id]
        source_route_counts[str(source_diag.get("tier"))] += 1
        for evidence in source_row.get("evidence") or []:
            csv_path = output_dir / str(evidence.get("csv_path") or "")
            with csv_path.open("r", encoding="utf-8", newline="") as handle:
                for row in csv.DictReader(handle):
                    uid = str(row.get("internal_table_uid") or "").strip()
                    if uid:
                        used_uids.add(uid)
        answer_changes.append(
            {
                "id": question_id,
                "route": selected_route_by_id[question_id],
                "base_answer": base_row.get("answer"),
                "source_answer": source_row.get("answer"),
                "overlay_answer": source_row.get("answer"),
                "source_prediction_tier": source_row.get("prediction_tier"),
                "source_diagnostic_answer_decimal": source_diag.get("answer_decimal"),
                "evidence_csv_paths": copied_evidence[question_id],
            }
        )

    inherited_manifest_path = base_dir / "route_overlay_manifest.json"
    inherited_manifest: dict[str, Any] | None = None
    if inherited_manifest_path.is_file():
        loaded_manifest = read_json(inherited_manifest_path)
        if isinstance(loaded_manifest, dict):
            inherited_manifest = loaded_manifest
    inherited_changes = list((inherited_manifest or {}).get("route_overrides") or [])
    inherited_counts = Counter(
        str(row.get("route") or "")
        for row in inherited_changes
        if row.get("route")
    )
    total_route_counts = inherited_counts + source_route_counts
    total_replacement_count = len(inherited_changes) + len(answer_changes)
    overlay_report = {
        "schema_version": "vifinqa_route_overlay_report_v1",
        "primary_model": "materialized_validated_route_overlay_v1",
        "overlay_policy": {
            "base_submission": str(base_submission_path),
            "base_submission_sha256": sha256_file(base_submission_path),
            "source_full_corpus_build": str(source_submission_path),
            "source_submission_sha256": sha256_file(source_submission_path),
            "replacement_ids_are_explicit": independent_replay_report is None,
            "replacement_selection_basis": (
                "independent_replay_source_cell_exact_set_when_coordinates_present"
                if independent_replay_report is not None
                else "legacy_explicit_route_id_allowlist"
            ),
            "independent_replay_path": (
                str(args.independent_replay.expanduser().resolve())
                if args.independent_replay is not None
                else None
            ),
            "independent_replay_status": (
                independent_replay_report.get("status")
                if independent_replay_report is not None
                else None
            ),
            "verified_source_uid_count": (
                independent_replay_report.get("selection_audit", {}).get(
                    "verified_source_uid_count"
                )
                if independent_replay_report is not None
                else None
            ),
            "verified_source_uid_closure_count": (
                independent_replay_report.get("selection_audit", {}).get(
                    "verified_source_uid_closure_count"
                )
                if independent_replay_report is not None
                else None
            ),
            "independent_replay_selection_audit": (
                independent_replay_report.get("selection_audit")
                if independent_replay_report is not None
                else None
            ),
            "replacement_count": len(selected_ids),
            "inherited_replacement_count": len(inherited_changes),
            "total_replacement_count": total_replacement_count,
            "inherited_overlay_manifest": (
                str(inherited_manifest_path) if inherited_manifest is not None else None
            ),
            "unselected_rows_preserved_bytewise_at_json_value_level": True,
            "numeric_values_copied_from_source_build": True,
            "new_numeric_arithmetic_invented": False,
            "human_verified": False,
            "promotion_allowed": False,
            "lane": "authorized_best_effort_submission_candidate",
        },
        "route_overrides": [*inherited_changes, *answer_changes],
        "route_counts": dict(sorted(total_route_counts.items())),
        "source_evidence": {
            "builder_report_path": str(source_dir / "build_report.json"),
            "builder_sha256": source_report.get("implementation", {}).get("builder_sha256"),
            "source_first_lookup_sha256": source_report.get("implementation", {}).get(
                "source_first_lookup_sha256"
            ),
            "structured_table_filter": source_report.get("structured_table_asset", {}).get(
                "filter_mode"
            ),
            "structured_table_count": source_report.get("structured_table_asset", {}).get(
                "table_count"
            ),
            "source_route_validation": source_report.get("validation"),
            "used_table_uid_count": len(used_uids),
            "used_table_uids_sha256": hashlib.sha256(
                "\n".join(sorted(used_uids)).encode("utf-8")
            ).hexdigest(),
        },
        "validation": validation,
        "zip": {
            "path": str(archive_path),
            "sha256": sha256_file(archive_path),
            "size_bytes": archive_path.stat().st_size,
            "member_count": len(names),
            "csv_member_count": len([name for name in names if name.startswith("data/")]),
        },
        "base_report_snapshot": {
            "schema_version": base_report.get("schema_version"),
            "question_count": base_report.get("question_count"),
            "validation": base_report.get("validation"),
        },
    }
    (output_dir / "build_report.json").write_text(
        json.dumps(overlay_report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output_dir / "route_overlay_manifest.json").write_text(
        json.dumps(overlay_report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(overlay_report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
