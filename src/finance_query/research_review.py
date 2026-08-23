"""Fail-closed validation for auditable research-review matrices.

This module governs research notes, not financial answers.  A passing matrix
can support a design discussion, but it can never promote labels, training
data, model outputs, or a submission.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any


PROTOCOL = "vifinqa_auditable_research_review_v1"
SCHEMA_VERSION = 1

REQUIRED_COLUMNS = (
    "record_id",
    "record_type",
    "title",
    "year",
    "source_url",
    "source_kind",
    "review_status",
    "claim",
    "claim_type",
    "evidence_locator",
    "supporting_record_ids",
    "counterexample_record_ids",
    "project_axis",
    "provenance_status",
    "promotion_allowed",
    "notes",
)

_RECORD_TYPES = {"evidence", "synthesis", "gap", "hypothesis"}
_CLAIM_TYPES = {"direct_evidence", "reviewer_synthesis", "research_hypothesis"}
_REVIEW_STATUSES = {"included", "secondary", "excluded", "unverified", "not_applicable"}
_EVIDENCE_PROVENANCE = {"source_verified", "needs_source_verification"}
_LOCATOR_TERMS = re.compile(
    r"\b(?:page|p\.|section|table|figure|appendix|chapter|trang|mục|bảng|hình)\b",
    flags=re.IGNORECASE,
)


class ResearchReviewValidationError(ValueError):
    """Raised when a review matrix violates the frozen review contract."""

    def __init__(self, issues: list[str]) -> None:
        self.issues = issues
        super().__init__("\n".join(issues))


def _text(value: str | None) -> str:
    return (value or "").strip()


def _record_ids(value: str | None) -> list[str]:
    return [item.strip() for item in _text(value).split(";") if item.strip()]


def _is_false(value: str | None) -> bool:
    return _text(value).lower() == "false"


def _specific_locator(value: str | None) -> bool:
    locator = _text(value)
    markdown_heading = re.search(r"\b[\w-]+\.md\b", locator, flags=re.IGNORECASE)
    return bool(_LOCATOR_TERMS.search(locator) or markdown_heading) and len(locator) >= 12


def load_protocol(path: Path) -> dict[str, Any]:
    """Load and validate the frozen research-only protocol JSON."""

    try:
        protocol = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Cannot load research-review protocol {path}: {error}") from error

    required = {
        "protocol",
        "schema_version",
        "question",
        "year_min",
        "allowed_source_kinds",
        "primary_source_kinds",
        "allowed_project_axes",
        "policy",
    }
    missing = sorted(required - set(protocol))
    if missing:
        raise ValueError(f"Research-review protocol is missing fields: {', '.join(missing)}")
    if protocol["protocol"] != PROTOCOL:
        raise ValueError(f"Unexpected research-review protocol: {protocol['protocol']!r}")
    if protocol["schema_version"] != SCHEMA_VERSION:
        raise ValueError("Unsupported research-review schema version")
    if not isinstance(protocol["year_min"], int):
        raise ValueError("Research-review year_min must be an integer")
    for field in ("allowed_source_kinds", "primary_source_kinds", "allowed_project_axes"):
        if not isinstance(protocol[field], list) or not protocol[field]:
            raise ValueError(f"Research-review {field} must be a non-empty list")

    expected_policy = {
        "research_only": True,
        "training_eligible": False,
        "submission_eligible": False,
        "promotion_allowed": False,
    }
    if protocol["policy"] != expected_policy:
        raise ValueError("Research-review policy must remain explicitly research-only")
    return protocol


def _read_matrix(path: Path) -> list[dict[str, str]]:
    try:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            headers = set(reader.fieldnames or [])
            missing = [column for column in REQUIRED_COLUMNS if column not in headers]
            if missing:
                raise ValueError(f"Research-review matrix is missing columns: {', '.join(missing)}")
            return list(reader)
    except OSError as error:
        raise ValueError(f"Cannot read research-review matrix {path}: {error}") from error


def _issue(issues: list[str], line: int, message: str) -> None:
    issues.append(f"row {line}: {message}")


def validate_matrix(protocol: dict[str, Any], matrix_path: Path) -> dict[str, Any]:
    """Validate a matrix and return a hash-bound, research-only report.

    The matrix has two planes.  ``evidence`` rows point to sources.  Synthesis,
    gaps, and hypotheses may only point back to verified evidence row IDs; they
    do not become source facts themselves.
    """

    rows = _read_matrix(matrix_path)
    if not rows:
        raise ResearchReviewValidationError(["matrix contains no records"])

    issues: list[str] = []
    identifiers: dict[str, dict[str, str]] = {}
    verified_evidence_ids: set[str] = set()
    allowed_source_kinds = set(protocol["allowed_source_kinds"])
    primary_source_kinds = set(protocol["primary_source_kinds"])
    allowed_axes = set(protocol["allowed_project_axes"])
    current_year = date.today().year

    for offset, row in enumerate(rows, start=2):
        record_id = _text(row.get("record_id"))
        record_type = _text(row.get("record_type"))
        title = _text(row.get("title"))
        claim = _text(row.get("claim"))
        claim_type = _text(row.get("claim_type"))
        axis = _text(row.get("project_axis"))

        if not record_id:
            _issue(issues, offset, "record_id is required")
        elif record_id in identifiers:
            _issue(issues, offset, f"duplicate record_id {record_id!r}")
        else:
            identifiers[record_id] = row
        if record_type not in _RECORD_TYPES:
            _issue(issues, offset, f"record_type must be one of {sorted(_RECORD_TYPES)}")
        if not title:
            _issue(issues, offset, "title is required")
        if not claim:
            _issue(issues, offset, "claim is required")
        if claim_type not in _CLAIM_TYPES:
            _issue(issues, offset, f"claim_type must be one of {sorted(_CLAIM_TYPES)}")
        if axis not in allowed_axes:
            _issue(issues, offset, f"project_axis {axis!r} is outside the frozen taxonomy")
        if not _is_false(row.get("promotion_allowed")):
            _issue(issues, offset, "promotion_allowed must be false for every research row")

        if record_type == "evidence":
            year = _text(row.get("year"))
            source_url = _text(row.get("source_url"))
            source_kind = _text(row.get("source_kind"))
            review_status = _text(row.get("review_status"))
            provenance_status = _text(row.get("provenance_status"))

            try:
                numeric_year = int(year)
            except ValueError:
                _issue(issues, offset, "evidence year must be an integer")
            else:
                if not protocol["year_min"] <= numeric_year <= current_year:
                    _issue(
                        issues,
                        offset,
                        f"evidence year must be between {protocol['year_min']} and {current_year}",
                    )
            if not source_url.startswith("https://"):
                _issue(issues, offset, "evidence source_url must use HTTPS")
            if source_kind not in allowed_source_kinds:
                _issue(issues, offset, f"source_kind {source_kind!r} is not allowed")
            if review_status not in _REVIEW_STATUSES - {"not_applicable"}:
                _issue(issues, offset, f"invalid evidence review_status {review_status!r}")
            if provenance_status not in _EVIDENCE_PROVENANCE:
                _issue(issues, offset, f"invalid evidence provenance_status {provenance_status!r}")
            if review_status == "included" and source_kind not in primary_source_kinds:
                _issue(issues, offset, "included evidence requires a primary source kind")
            if claim_type == "direct_evidence":
                if provenance_status != "source_verified":
                    _issue(issues, offset, "direct_evidence requires source_verified provenance")
                if not _specific_locator(row.get("evidence_locator")):
                    _issue(issues, offset, "direct_evidence requires a specific evidence_locator")
            if provenance_status == "source_verified" and record_id:
                verified_evidence_ids.add(record_id)
        elif record_type in {"synthesis", "gap", "hypothesis"}:
            _validate_derived_row(issues, offset, row, record_type)

    for offset, row in enumerate(rows, start=2):
        record_type = _text(row.get("record_type"))
        if record_type not in {"synthesis", "gap", "hypothesis"}:
            continue
        supporting_ids = _record_ids(row.get("supporting_record_ids"))
        counterexample_ids = _record_ids(row.get("counterexample_record_ids"))
        for supporting_id in supporting_ids:
            if supporting_id not in identifiers:
                _issue(issues, offset, f"unknown supporting_record_id {supporting_id!r}")
            elif supporting_id not in verified_evidence_ids:
                _issue(issues, offset, f"supporting_record_id {supporting_id!r} is not verified evidence")
        for counterexample_id in counterexample_ids:
            if counterexample_id not in identifiers:
                _issue(issues, offset, f"unknown counterexample_record_id {counterexample_id!r}")
            elif counterexample_id not in verified_evidence_ids:
                _issue(issues, offset, f"counterexample_record_id {counterexample_id!r} is not verified evidence")

    if issues:
        raise ResearchReviewValidationError(issues)

    matrix_bytes = matrix_path.read_bytes()
    record_types = Counter(_text(row.get("record_type")) for row in rows)
    review_statuses = Counter(_text(row.get("review_status")) for row in rows)
    return {
        "protocol": PROTOCOL,
        "schema_version": SCHEMA_VERSION,
        "validation_status": "VALIDATION PASSED",
        "matrix": {
            "path": str(matrix_path),
            "sha256": hashlib.sha256(matrix_bytes).hexdigest(),
            "record_count": len(rows),
        },
        "counts": {
            "record_types": dict(sorted(record_types.items())),
            "review_statuses": dict(sorted(review_statuses.items())),
            "source_verified_evidence": len(verified_evidence_ids),
        },
        "policy": protocol["policy"],
    }


def _validate_derived_row(
    issues: list[str], line: int, row: dict[str, str], record_type: str
) -> None:
    """Validate synthesis/gap/hypothesis rows that must not masquerade as sources."""

    expected_claim_type = {
        "synthesis": "reviewer_synthesis",
        "gap": "reviewer_synthesis",
        "hypothesis": "research_hypothesis",
    }[record_type]
    expected_provenance = {
        "synthesis": "derived_from_verified_records",
        "gap": "derived_from_verified_records",
        "hypothesis": "proposal_only",
    }[record_type]
    if _text(row.get("claim_type")) != expected_claim_type:
        _issue(issues, line, f"{record_type} requires claim_type {expected_claim_type!r}")
    if _text(row.get("provenance_status")) != expected_provenance:
        _issue(issues, line, f"{record_type} requires provenance_status {expected_provenance!r}")
    if _text(row.get("review_status")) != "not_applicable":
        _issue(issues, line, f"{record_type} review_status must be 'not_applicable'")
    for field in ("year", "source_url", "source_kind", "evidence_locator"):
        if _text(row.get(field)):
            _issue(issues, line, f"{record_type} {field} must be blank; cite evidence rows instead")

    supporting_ids = _record_ids(row.get("supporting_record_ids"))
    required_supports = 2 if record_type in {"synthesis", "gap"} else 1
    if len(supporting_ids) < required_supports:
        _issue(issues, line, f"{record_type} requires at least {required_supports} supporting_record_ids")
    counterexample_ids = _record_ids(row.get("counterexample_record_ids"))
    if record_type == "gap" and not counterexample_ids:
        _issue(issues, line, "gap requires at least one counterexample_record_id")
    if record_type != "gap" and counterexample_ids:
        _issue(issues, line, f"{record_type} must not declare counterexample_record_ids")


def write_report(report: dict[str, Any], path: Path) -> None:
    """Write a new validation report without overwriting prior research artifacts."""

    if path.exists():
        raise FileExistsError(f"Refusing to overwrite existing research-review report: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
