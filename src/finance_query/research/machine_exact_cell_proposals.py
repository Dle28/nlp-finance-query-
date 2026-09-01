"""Build conservative, value-blind exact-cell proposals for E2E repair.

The previous triage identifies questions where the calculation plan and route
are present but the period/source-navigation packet is absent.  This module
does one narrow piece of follow-up work: it intersects a high-quality table
diagnostic, a high-quality row match, and the immutable V2 source table.

Its output is deliberately a *machine proposal*, never an approval.  In
particular, it does not copy a numeric cell, does not fabricate an E2E period
packet, and cannot make a question answer- or submission-eligible.  A later
source-binding producer must independently revalidate any selected proposal.
"""

from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
from typing import Any, Iterable, Mapping


PROTOCOL = "vifinqa_machine_exact_cell_proposals_v1"
SCHEMA_VERSION = 1
TARGET_BLOCKER = "SOURCE_NAVIGATION_PACKET_MISSING"
SOURCE_CONTRACT = {
    "research_only": True,
    "machine_review_only": True,
    "evidence_eligible": False,
    "may_materialize_answer": False,
    "training_eligible": False,
    "submission_eligible": False,
    "promotion_allowed": False,
}
FORBIDDEN_KEYS = frozenset(
    {
        "answer",
        "raw_value",
        "raw_values",
        "cell_value",
        "pandas_query",
        "rows",
        "raw_source_row",
        "raw_source_cell",
        "cell_provenance",
    }
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_sha(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        record = json.loads(line)
        if not isinstance(record, dict):
            raise ValueError(f"{path}:{line_number} is not a JSON object")
        records.append(record)
    return records


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _contains_forbidden_key(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(
            str(key) in FORBIDDEN_KEYS or _contains_forbidden_key(child)
            for key, child in value.items()
        )
    if isinstance(value, list):
        return any(_contains_forbidden_key(child) for child in value)
    return False


def _int(value: object, *, label: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{label} is not an integer")
    try:
        return int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} is not an integer") from error


def _index_unique(rows: Iterable[Mapping[str, Any]], *, field: str, label: str) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for row in rows:
        value = str(row.get(field) or "")
        if not value or value in indexed:
            raise ValueError(f"{label} has a missing or duplicate {field}")
        indexed[value] = dict(row)
    return indexed


def _source_matches_diagnostic(table: Mapping[str, Any], diagnostic: Mapping[str, Any]) -> bool:
    """Check the candidate table is the hash-bound source seen by diagnostics."""
    locator = diagnostic.get("exact_table_locator")
    provenance = table.get("source_provenance")
    if not isinstance(locator, Mapping) or not isinstance(provenance, Mapping):
        return False
    if str(table.get("document_id") or "") != str(diagnostic.get("document_id") or ""):
        return False
    if _int(table.get("local_ordinal"), label="source table local_ordinal") != _int(
        locator.get("local_ordinal"), label="diagnostic local_ordinal"
    ):
        return False
    for field in ("source_path", "source_sha256", "table_sha256", "char_start"):
        if provenance.get(field) != locator.get(field):
            return False
    return True


def _selected_column(diagnostic: Mapping[str, Any]) -> tuple[int, Mapping[str, Any]] | None:
    columns = diagnostic.get("matching_year_column_indices") or []
    if not isinstance(columns, list) or len(columns) != 1:
        return None
    column_index = _int(columns[0], label="matching year column")
    records = [
        record
        for record in diagnostic.get("column_headers") or []
        if isinstance(record, Mapping) and _int(record.get("column_index"), label="column header index") == column_index
    ]
    if len(records) != 1 or not str(records[0].get("header_sha256") or ""):
        return None
    return column_index, records[0]


def _expected_table_types(period_packet: Mapping[str, Any]) -> set[str]:
    """Collect the question's already-authored source-table constraints."""
    expected: set[str] = set()
    for stage in period_packet.get("stages") or []:
        if not isinstance(stage, Mapping):
            continue
        for operand in stage.get("required_operands") or []:
            if not isinstance(operand, Mapping):
                continue
            expected.update(
                str(value)
                for value in operand.get("expected_table_types") or []
                if str(value)
            )
    return expected


def _matches_expected_table_type(
    diagnostic: Mapping[str, Any], *, expected_table_types: set[str]
) -> bool:
    """Reject a lexical row hit whose source table conflicts with the plan.

    ``notes`` is a family, because the V2 extractor distinguishes broad and
    detailed financial-note tables.  This is a compatibility filter only; it
    never infers a new source-table type.
    """
    table_function = diagnostic.get("table_function")
    kind = str(table_function.get("kind") or "") if isinstance(table_function, Mapping) else ""
    compatible = {kind}
    if kind in {"financial_note", "financial_note_detail"}:
        compatible.add("notes")
    return bool(expected_table_types & compatible)


def _structural_rows(
    rows: list[Mapping[str, Any]],
    *,
    selected_column_index: int,
    minimum_row_jaccard: float,
    minimum_margin: float,
) -> list[dict[str, Any]]:
    """Return at most one row when its lexical match is unambiguous.

    The margin compares only rows in the same table/route diagnostic.  This is
    a navigation check, not a claim that a label is semantically equivalent to
    the requested financial concept.
    """
    usable = [
        row
        for row in rows
        if selected_column_index
        in {_int(value, label="numeric column index") for value in row.get("numeric_column_indices") or []}
    ]
    usable.sort(
        key=lambda row: (
            -float(row.get("row_label_token_jaccard") or 0.0),
            _int(row.get("row_rank"), label="row rank"),
            _int(row.get("row_index"), label="row index"),
        )
    )
    if not usable:
        return []
    top = usable[0]
    top_score = float(top.get("row_label_token_jaccard") or 0.0)
    if top_score < minimum_row_jaccard:
        return []
    second_score = float(usable[1].get("row_label_token_jaccard") or 0.0) if len(usable) > 1 else 0.0
    if top_score - second_score < minimum_margin:
        return []
    return [
        {
            "row_candidate": top,
            "row_score": top_score,
            "row_score_margin": top_score - second_score,
        }
    ]


def _proposal(
    *,
    diagnostic: Mapping[str, Any],
    row: Mapping[str, Any],
    table: Mapping[str, Any],
    selected_column_index: int,
    header: Mapping[str, Any],
    row_score: float,
    row_score_margin: float,
    expected_table_types: set[str],
) -> dict[str, Any]:
    row_index = _int(row.get("row_index"), label="row index")
    table_rows = table.get("rows") or []
    provenance = table.get("cell_provenance") or []
    if (
        row_index < 0
        or selected_column_index < 0
        or row_index >= len(table_rows)
        or row_index >= len(provenance)
        or selected_column_index >= len(table_rows[row_index])
        or selected_column_index >= len(provenance[row_index])
    ):
        raise ValueError("candidate coordinate is outside the immutable V2 table")
    cell_provenance = provenance[row_index][selected_column_index]
    if not isinstance(cell_provenance, Mapping):
        raise ValueError("candidate V2 cell has no provenance mapping")
    proposal_identity = {
        "diagnostic_id": diagnostic["diagnostic_id"],
        "row_candidate_id": row["row_candidate_id"],
        "row_index": row_index,
        "column_index": selected_column_index,
        "exact_table_locator_sha256": diagnostic["exact_table_locator_sha256"],
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "protocol": PROTOCOL,
        "proposal_id": _canonical_sha(proposal_identity),
        "question_id": _int(diagnostic.get("question_id"), label="question id"),
        "route_id": diagnostic["route_id"],
        "operand_id": diagnostic["operand_id"],
        "expected_table_types": sorted(expected_table_types),
        "machine_review_status": "STRUCTURAL_CANDIDATE_NOT_AUTHORIZED",
        "selection_basis": {
            "unique_explicit_year_header": True,
            "unique_header_unit": True,
            "scope_match": True,
            "top_row_label_match": True,
            "minimum_row_jaccard": row_score,
            "row_score_margin": row_score_margin,
        },
        "source_navigation": {
            "document_id": diagnostic["document_id"],
            "internal_table_uid": diagnostic["internal_table_uid"],
            "exact_table_locator": diagnostic["exact_table_locator"],
            "exact_table_locator_sha256": diagnostic["exact_table_locator_sha256"],
            "table_source_match": True,
            "row_index": row_index,
            "column_index": selected_column_index,
            "requested_year": _int(diagnostic.get("requested_year"), label="requested year"),
            "period_header_sha256": header["header_sha256"],
            "source_unit_candidate": diagnostic["source_unit_candidate"],
            "row_label": row["row_label"],
            "row_label_sha256": row["row_label_sha256"],
            "source_cell_provenance_sha256": _canonical_sha(cell_provenance),
        },
        "raw_numeric_values_included": False,
        "source_contract": dict(SOURCE_CONTRACT),
    }


def build_machine_exact_cell_proposals(
    *,
    triage_path: Path,
    period_packets_path: Path,
    table_diagnostics_path: Path,
    row_candidates_path: Path,
    structured_tables_path: Path,
    output_dir: Path,
    minimum_row_jaccard: float = 0.5,
    minimum_row_margin: float = 0.2,
) -> dict[str, Any]:
    """Create an immutable non-authorizing candidate artifact.

    Only the source-navigation gaps selected by the triage input are covered.
    All other E2E blockers are intentionally outside this narrow repair batch.
    """
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite output directory: {output_dir}")
    if not 0.0 <= minimum_row_jaccard <= 1.0 or not 0.0 <= minimum_row_margin <= 1.0:
        raise ValueError("machine proposal thresholds must be between zero and one")

    triage_rows = _load_jsonl(triage_path)
    target_ids = {
        _int(row.get("question_id"), label="triage question_id")
        for row in triage_rows
        if row.get("primary_blocker") == TARGET_BLOCKER
    }
    if not target_ids:
        raise ValueError("triage input has no source-navigation targets")
    period_packets = {
        _int(row.get("question_id"), label="period-packet question_id"): row
        for row in _load_jsonl(period_packets_path)
    }
    if not target_ids <= set(period_packets):
        raise ValueError("period packets do not cover all source-navigation targets")
    diagnostics = _load_jsonl(table_diagnostics_path)
    diagnostic_by_id = _index_unique(diagnostics, field="diagnostic_id", label="table diagnostics")
    rows_by_diagnostic: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in _load_jsonl(row_candidates_path):
        diagnostic_id = str(row.get("diagnostic_id") or "")
        if diagnostic_id not in diagnostic_by_id:
            raise ValueError("row candidate references an unknown diagnostic")
        rows_by_diagnostic[diagnostic_id].append(row)
    tables = _index_unique(
        _load_jsonl(structured_tables_path), field="internal_table_uid", label="structured tables"
    )

    proposals: list[dict[str, Any]] = []
    route_summaries: list[dict[str, Any]] = []
    for question_id in sorted(target_ids):
        expected_table_types = _expected_table_types(period_packets[question_id])
        if not expected_table_types:
            raise ValueError("source-navigation target has no expected table type")
        question_diagnostics = sorted(
            (
                diagnostic
                for diagnostic in diagnostics
                if _int(diagnostic.get("question_id"), label="diagnostic question_id") == question_id
            ),
            key=lambda diagnostic: (
                str(diagnostic.get("route_id") or ""),
                _int(diagnostic.get("review_priority_rank"), label="review priority rank"),
                str(diagnostic.get("diagnostic_id") or ""),
            ),
        )
        if not question_diagnostics:
            route_summaries.append(
                {
                    "schema_version": SCHEMA_VERSION,
                    "protocol": PROTOCOL,
                    "question_id": question_id,
                    "expected_table_types": sorted(expected_table_types),
                    "machine_route_status": "NO_DIAGNOSTIC_FOR_TARGET",
                    "proposal_count": 0,
                    "raw_numeric_values_included": False,
                    "source_contract": dict(SOURCE_CONTRACT),
                }
            )
            continue
        question_proposals: list[dict[str, Any]] = []
        for diagnostic in question_diagnostics:
            if (
                diagnostic.get("period_status") != "UNIQUE_YEAR_HEADER_CANDIDATE"
                or diagnostic.get("unit_status") != "UNIQUE_HEADER_UNIT_CANDIDATE"
                or diagnostic.get("scope_status") != "SCOPE_MATCH"
                or not str(diagnostic.get("source_unit_candidate") or "")
                or not _matches_expected_table_type(
                    diagnostic, expected_table_types=expected_table_types
                )
            ):
                continue
            selected = _selected_column(diagnostic)
            if selected is None:
                continue
            selected_column_index, header = selected
            table = tables.get(str(diagnostic.get("internal_table_uid") or ""))
            if table is None or not _source_matches_diagnostic(table, diagnostic):
                continue
            for candidate in _structural_rows(
                rows_by_diagnostic[str(diagnostic["diagnostic_id"])],
                selected_column_index=selected_column_index,
                minimum_row_jaccard=minimum_row_jaccard,
                minimum_margin=minimum_row_margin,
            ):
                question_proposals.append(
                    _proposal(
                        diagnostic=diagnostic,
                        row=candidate["row_candidate"],
                        table=table,
                        selected_column_index=selected_column_index,
                        header=header,
                        row_score=candidate["row_score"],
                        row_score_margin=candidate["row_score_margin"],
                        expected_table_types=expected_table_types,
                    )
                )
        question_proposals.sort(key=lambda row: (str(row["route_id"]), str(row["proposal_id"])))
        proposals.extend(question_proposals)
        status = (
            "NO_MACHINE_STRUCTURAL_CANDIDATE"
            if not question_proposals
            else "ONE_MACHINE_STRUCTURAL_CANDIDATE"
            if len(question_proposals) == 1
            else "MULTIPLE_MACHINE_STRUCTURAL_CANDIDATES"
        )
        route_summaries.append(
            {
                "schema_version": SCHEMA_VERSION,
                "protocol": PROTOCOL,
                "question_id": question_id,
                "expected_table_types": sorted(expected_table_types),
                "machine_route_status": status,
                "proposal_count": len(question_proposals),
                "proposal_ids": [row["proposal_id"] for row in question_proposals],
                "raw_numeric_values_included": False,
                "source_contract": dict(SOURCE_CONTRACT),
            }
        )

    if len({row["proposal_id"] for row in proposals}) != len(proposals):
        raise ValueError("machine proposal IDs are not unique")
    if any(_contains_forbidden_key(row) for row in [*proposals, *route_summaries]):
        raise ValueError("machine proposal output contains a forbidden value field")

    status_counts = Counter(row["machine_route_status"] for row in route_summaries)
    summary = {
        "schema_version": SCHEMA_VERSION,
        "protocol": PROTOCOL,
        "target_question_count": len(target_ids),
        "route_summary_count": len(route_summaries),
        "proposal_count": len(proposals),
        "machine_route_status_counts": dict(sorted(status_counts.items())),
        "thresholds": {
            "minimum_row_jaccard": minimum_row_jaccard,
            "minimum_row_margin": minimum_row_margin,
        },
        "raw_numeric_values_included": False,
        "source_contract": dict(SOURCE_CONTRACT),
    }

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary_dir = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.tmp-", dir=output_dir.parent))
    try:
        proposals_path = temporary_dir / "machine_exact_cell_proposals_v1.jsonl"
        routes_path = temporary_dir / "machine_exact_cell_route_summary_v1.jsonl"
        summary_path = temporary_dir / "machine_exact_cell_proposal_summary_v1.json"
        _write_jsonl(proposals_path, proposals)
        _write_jsonl(routes_path, route_summaries)
        _write_json(summary_path, summary)
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "protocol": PROTOCOL,
            "inputs": {
                name: {"path": str(path), "sha256": sha256_file(path)}
                for name, path in {
                    "triage": triage_path,
                    "period_packets": period_packets_path,
                    "table_diagnostics": table_diagnostics_path,
                    "row_candidates": row_candidates_path,
                    "structured_tables": structured_tables_path,
                }.items()
            },
            "outputs": {
                name: {"path": path.name, "sha256": sha256_file(path)}
                for name, path in {
                    "proposals": proposals_path,
                    "route_summary": routes_path,
                    "summary": summary_path,
                }.items()
            },
            "source_contract": dict(SOURCE_CONTRACT),
        }
        _write_json(temporary_dir / "manifest.json", manifest)
        temporary_dir.rename(output_dir)
    except Exception:
        shutil.rmtree(temporary_dir, ignore_errors=True)
        raise
    return summary


def validate_machine_exact_cell_proposals(
    artifact_dir: Path,
    *,
    expected_target_question_count: int | None = None,
) -> dict[str, Any]:
    """Validate hashes, source consistency, and non-authorizing boundaries."""
    manifest_path = artifact_dir / "manifest.json"
    if not manifest_path.is_file():
        raise ValueError("machine proposal manifest is missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("protocol") != PROTOCOL or manifest.get("source_contract") != SOURCE_CONTRACT:
        raise ValueError("unexpected machine proposal protocol or contract")
    for descriptor in (manifest.get("outputs") or {}).values():
        if not isinstance(descriptor, Mapping):
            raise ValueError("invalid output descriptor")
        path = artifact_dir / str(descriptor.get("path") or "")
        if not path.is_file() or sha256_file(path) != descriptor.get("sha256"):
            raise ValueError("machine proposal output hash mismatch")
    for descriptor in (manifest.get("inputs") or {}).values():
        if not isinstance(descriptor, Mapping):
            raise ValueError("invalid input descriptor")
        path = Path(str(descriptor.get("path") or ""))
        if not path.is_file() or sha256_file(path) != descriptor.get("sha256"):
            raise ValueError("machine proposal input hash mismatch")

    proposals = _load_jsonl(artifact_dir / "machine_exact_cell_proposals_v1.jsonl")
    routes = _load_jsonl(artifact_dir / "machine_exact_cell_route_summary_v1.jsonl")
    summary = json.loads((artifact_dir / "machine_exact_cell_proposal_summary_v1.json").read_text(encoding="utf-8"))
    if expected_target_question_count is not None and len(routes) != expected_target_question_count:
        raise ValueError("machine proposal target-question count mismatch")
    proposal_ids = [str(row.get("proposal_id") or "") for row in proposals]
    route_ids = [_int(row.get("question_id"), label="route summary question_id") for row in routes]
    if len(proposal_ids) != len(set(proposal_ids)) or any(not value for value in proposal_ids):
        raise ValueError("machine proposal IDs are invalid")
    if len(route_ids) != len(set(route_ids)):
        raise ValueError("machine route summaries are not unique")
    if any(
        _contains_forbidden_key(row)
        or row.get("raw_numeric_values_included") is not False
        or row.get("source_contract") != SOURCE_CONTRACT
        or "human_verified" in row
        for row in [*proposals, *routes]
    ):
        raise ValueError("machine proposal boundary was violated")
    known_proposals = set(proposal_ids)
    expected_status_counts = Counter()
    for route in routes:
        ids = route.get("proposal_ids") or []
        if not isinstance(ids, list) or any(str(value) not in known_proposals for value in ids):
            raise ValueError("machine route summary references an unknown proposal")
        count = _int(route.get("proposal_count"), label="proposal count")
        if count != len(ids):
            raise ValueError("machine route proposal count mismatch")
        expected_status = (
            "NO_MACHINE_STRUCTURAL_CANDIDATE"
            if count == 0
            else "ONE_MACHINE_STRUCTURAL_CANDIDATE"
            if count == 1
            else "MULTIPLE_MACHINE_STRUCTURAL_CANDIDATES"
        )
        if route.get("machine_route_status") != expected_status:
            raise ValueError("machine route status mismatch")
        expected_status_counts[expected_status] += 1
    if summary.get("target_question_count") != len(routes) or summary.get("proposal_count") != len(proposals):
        raise ValueError("machine proposal summary count mismatch")
    if summary.get("machine_route_status_counts") != dict(sorted(expected_status_counts.items())):
        raise ValueError("machine proposal summary status mismatch")
    return {
        "status": "PASS",
        "target_question_count": len(routes),
        "proposal_count": len(proposals),
        "machine_route_status_counts": dict(sorted(expected_status_counts.items())),
        "answer_eligible": False,
        "submission_eligible": False,
    }
