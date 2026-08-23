"""Materialize literal numbered-note context components for CCL Phase 5.

The source-profile stage already recognizes a numbered financial-note heading.
This module separates its literal numeric locator from the literal topic text,
without paraphrase or model inference.  The result supplies report context for
later table interpretation but never claims that a table's full semantic type
is the note topic.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

from finance_query.table_structure import sha256_file

from .phase4_layout import PHASE4_LAYOUT_PROTOCOL
from .phase5_profiles import PHASE5_PROFILE_PROTOCOL
from .pipeline import CertifiedCanonicalError


PHASE5_NOTE_CONTEXT_PROTOCOL = "vifinqa_ccl_phase5_numbered_note_context_v1"
PHASE5_NOTE_CONTEXT_SCHEMA_VERSION = 1
_CONTEXT_NAME = "phase5_numbered_note_context_v1.jsonl"
_REPORT_NAME = "phase5_numbered_note_context_report.json"
_MANIFEST_NAME = "phase5_numbered_note_context_manifest.json"
_OUTPUT_NAMES = (_CONTEXT_NAME, _REPORT_NAME, _MANIFEST_NAME)
_NOTE_LOCATOR_RE = r"(?:\d{1,3}(?:\.\d+)*[.)]?|[ivxlcdm]+[.)]|[IVXLCDM]+[.)]|[a-z][.)])"
_NUMBERED_HEADING_RE = re.compile(
    rf"^\s*(?P<locator>{_NOTE_LOCATOR_RE})\s+(?P<topic>\S(?:.*\S)?)\s*$", re.UNICODE
)


@dataclass(frozen=True, slots=True)
class Phase5NumberedNoteContextResult:
    output_dir: Path
    context_count: int
    materialized_count: int
    unresolved_count: int


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha_json(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _text_sha(value: object) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise CertifiedCanonicalError(f"invalid JSON input: {path}") from error
    if not isinstance(value, dict):
        raise CertifiedCanonicalError(f"expected JSON object: {path}")
    return value


def _jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8-sig") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as error:
                raise CertifiedCanonicalError(f"invalid JSONL input: {path}:{line_number}") from error
            if not isinstance(value, dict):
                raise CertifiedCanonicalError(f"expected JSONL object: {path}:{line_number}")
            rows.append(value)
    return rows


def _require_hash(path: Path, expected: object, *, label: str) -> None:
    if not isinstance(expected, str) or sha256_file(path) != expected:
        raise CertifiedCanonicalError(f"SHA-256 mismatch: {label}")


def _require_non_promotable(value: Mapping[str, Any], *, label: str) -> None:
    if value.get("training_eligible") is not False or value.get("certification_allowed") is not False:
        raise CertifiedCanonicalError(f"{label} violates the non-promotable contract")


def _require_new_output(output_dir: Path, inputs: Iterable[Path]) -> None:
    if output_dir.resolve() in {path.resolve() for path in inputs}:
        raise CertifiedCanonicalError("output-dir cannot be a Phase 5 note-context input")
    if output_dir.exists():
        raise FileExistsError("output-dir must be new; Phase 5 note-context output is immutable")


def _require_manifest(manifest: Mapping[str, Any], *, protocol: str, run_status: str, label: str) -> None:
    if (
        manifest.get("protocol") != protocol
        or manifest.get("run_status") != run_status
        or manifest.get("training_eligible") is not False
        or manifest.get("certification_allowed") is not False
    ):
        raise CertifiedCanonicalError(f"{label} manifest is unsupported or promotable")


def _index_unique(rows: Iterable[Mapping[str, Any]], *, key: str, label: str) -> dict[str, Mapping[str, Any]]:
    indexed: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        value = str(row.get(key) or "")
        if not value or value in indexed:
            raise CertifiedCanonicalError(f"{label} has a missing or duplicate {key}")
        indexed[value] = row
    return indexed


def _shared_identity(span: Mapping[str, Any], profile: Mapping[str, Any]) -> None:
    assertion_id = str(span.get("phase45_assertion_id") or "")
    if not assertion_id or profile.get("phase45_assertion_id") != assertion_id:
        raise CertifiedCanonicalError("note profile does not bind the Phase 4-5 assertion")
    if profile.get("phase4_heading_span_id") != span.get("heading_span_id"):
        raise CertifiedCanonicalError("note profile does not bind the Phase 4 heading span")
    for field in ("phase3_request_id", "internal_table_uid", "route_id"):
        value = span.get(field)
        if not value or profile.get(field) != value:
            raise CertifiedCanonicalError(f"note profile and heading span have mismatched {field}")


def _context_record(*, span: Mapping[str, Any], profile: Mapping[str, Any]) -> dict[str, Any]:
    source_heading = str(span.get("source_heading") or "")
    payload: dict[str, Any] = {
        "schema_version": PHASE5_NOTE_CONTEXT_SCHEMA_VERSION,
        "protocol": PHASE5_NOTE_CONTEXT_PROTOCOL,
        "phase45_assertion_id": span["phase45_assertion_id"],
        "phase4_heading_span_id": span["heading_span_id"],
        "phase5_table_topic_profile_id": profile["table_topic_profile_id"],
        "phase3_request_id": span["phase3_request_id"],
        "internal_table_uid": span["internal_table_uid"],
        "route_id": span["route_id"],
        "source_heading": span.get("source_heading"),
        "source_heading_sha256": span.get("source_heading_sha256"),
        "source_context_sha256": span.get("source_context_sha256"),
        "note_locator_literal": None,
        "note_topic_literal": None,
        "note_topic_sha256": None,
        "status": "UNRESOLVED",
        "evidence_class": "E3",
        "reason_codes": [],
        "derivation_rule": "none_phase5_numbered_note_component_parse_failed",
        "training_eligible": False,
        "certification_allowed": False,
    }
    if span.get("status") != "SOURCE_HEADING_SPAN_MATERIALIZED":
        payload["reason_codes"] = ["no_materialized_source_heading_span"]
        return {"numbered_note_context_id": _sha_json(payload), **payload}
    match = _NUMBERED_HEADING_RE.match(source_heading)
    if match is None:
        payload["reason_codes"] = ["profiled_note_heading_does_not_parse_as_numbered_literal"]
        return {"numbered_note_context_id": _sha_json(payload), **payload}
    topic = str(match.group("topic"))
    payload.update(
        {
            "note_locator_literal": str(match.group("locator")),
            "note_topic_literal": topic,
            "note_topic_sha256": _text_sha(topic),
            "status": "SOURCE_NUMBERED_NOTE_CONTEXT_COMPONENTS_ONLY",
            "evidence_class": "E0",
            "derivation_rule": "phase5_literal_numbered_note_heading_split_v1",
        }
    )
    return {"numbered_note_context_id": _sha_json(payload), **payload}


def materialize_phase5_numbered_note_context(
    *,
    phase4_heading_spans: Path,
    phase4_manifest: Path,
    phase5_profiles: Path,
    phase5_manifest: Path,
    output_dir: Path,
    route_id: str,
) -> Phase5NumberedNoteContextResult:
    """Split literal numbered-note headings without inferring table semantics."""
    inputs = [
        path.resolve()
        for path in (phase4_heading_spans, phase4_manifest, phase5_profiles, phase5_manifest)
    ]
    if any(not path.is_file() for path in inputs):
        raise FileNotFoundError("missing Phase 5 numbered-note context input")
    _require_new_output(output_dir, inputs)
    before_hashes = {path.name: sha256_file(path) for path in inputs}
    phase4 = _json(phase4_manifest)
    _require_manifest(
        phase4,
        protocol=PHASE4_LAYOUT_PROTOCOL,
        run_status="phase_4_heading_span_materialization_complete_not_certified",
        label="Phase 4 heading span",
    )
    _require_hash(
        phase4_heading_spans,
        ((phase4.get("outputs") or {}).get(phase4_heading_spans.name) or {}).get("sha256"),
        label="Phase 4 heading spans",
    )
    phase5 = _json(phase5_manifest)
    _require_manifest(
        phase5,
        protocol=PHASE5_PROFILE_PROTOCOL,
        run_status="phase_5_table_topic_profile_complete_not_certified",
        label="Phase 5 table profile",
    )
    _require_hash(
        phase4_heading_spans,
        ((phase5.get("inputs") or {}).get(phase4_heading_spans.name) or {}).get("sha256"),
        label="Phase 5 bound Phase 4 heading spans",
    )
    _require_hash(
        phase4_manifest,
        ((phase5.get("inputs") or {}).get(phase4_manifest.name) or {}).get("sha256"),
        label="Phase 5 bound Phase 4 manifest",
    )
    _require_hash(
        phase5_profiles,
        ((phase5.get("outputs") or {}).get(phase5_profiles.name) or {}).get("sha256"),
        label="Phase 5 table profiles",
    )
    spans = _index_unique(
        (row for row in _jsonl(phase4_heading_spans) if row.get("route_id") == route_id),
        key="phase45_assertion_id",
        label="Phase 4 heading spans",
    )
    profiles = _index_unique(
        (row for row in _jsonl(phase5_profiles) if row.get("route_id") == route_id),
        key="phase45_assertion_id",
        label="Phase 5 table profiles",
    )
    if set(spans) != set(profiles):
        raise CertifiedCanonicalError("note-context input does not provide a one-to-one heading span/profile join")
    selected = [
        (spans[assertion_id], profiles[assertion_id])
        for assertion_id in sorted(spans)
        if isinstance(profiles[assertion_id].get("profile"), Mapping)
        and profiles[assertion_id]["profile"].get("profile_family") == "financial_note"
    ]
    if not selected:
        raise CertifiedCanonicalError("requested route has no literal financial-note profiles")
    records: list[dict[str, Any]] = []
    for span, profile in selected:
        _require_non_promotable(span, label="Phase 4 heading span")
        _require_non_promotable(profile, label="Phase 5 source profile")
        _shared_identity(span, profile)
        records.append(_context_record(span=span, profile=profile))
    after_hashes = {path.name: sha256_file(path) for path in inputs}
    if before_hashes != after_hashes:
        raise CertifiedCanonicalError("Phase 5 numbered-note materialization changed a hash-bound input")
    output_dir.mkdir(parents=True)
    context_path = output_dir / _CONTEXT_NAME
    with context_path.open("x", encoding="utf-8") as file:
        for record in records:
            file.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    status_counts = Counter(str(record["status"]) for record in records)
    reason_counts = Counter(str(reason) for record in records for reason in (record.get("reason_codes") or []))
    report = {
        "schema_version": PHASE5_NOTE_CONTEXT_SCHEMA_VERSION,
        "protocol": PHASE5_NOTE_CONTEXT_PROTOCOL,
        "run_status": "phase_5_numbered_note_context_complete_not_certified",
        "route_id": route_id,
        "context_count": len(records),
        "status_counts": dict(sorted(status_counts.items())),
        "reason_counts": dict(sorted(reason_counts.items())),
        "input_hashes_unchanged": True,
        "training_eligible_output_count": 0,
        "certification_allowed": False,
        "next_gate": "combine_source_note_context_with_table_structure_before_any_bounded_llm_task",
    }
    report_path = output_dir / _REPORT_NAME
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest = {
        "schema_version": PHASE5_NOTE_CONTEXT_SCHEMA_VERSION,
        "protocol": PHASE5_NOTE_CONTEXT_PROTOCOL,
        "run_status": report["run_status"],
        "inputs": {path.name: {"path": str(path), "sha256": digest} for path, digest in zip(inputs, before_hashes.values())},
        "outputs": {
            name: {
                "path": str(output_dir / name),
                "sha256": sha256_file(output_dir / name),
                "bytes": (output_dir / name).stat().st_size,
            }
            for name in _OUTPUT_NAMES[:-1]
        },
        "rule_version": "1.0.0",
        "training_eligible": False,
        "certification_allowed": False,
    }
    (output_dir / _MANIFEST_NAME).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return Phase5NumberedNoteContextResult(
        output_dir=output_dir,
        context_count=len(records),
        materialized_count=status_counts["SOURCE_NUMBERED_NOTE_CONTEXT_COMPONENTS_ONLY"],
        unresolved_count=status_counts["UNRESOLVED"],
    )
