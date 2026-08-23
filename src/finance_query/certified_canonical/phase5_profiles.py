"""Deterministic, source-heading table-context profiles for CCL Phase 5.

The profile is deliberately narrow: it recognizes only literal financial
statement titles and numbered financial-note headings from a Phase 4 material-
ized source span.  It does not interpret a table value, accept an LLM label,
or certify the table's complete semantic meaning.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping
import unicodedata

from finance_query.table_structure import sha256_file

from .phase4_layout import PHASE4_LAYOUT_PROTOCOL
from .pipeline import CertifiedCanonicalError


PHASE5_PROFILE_PROTOCOL = "vifinqa_ccl_phase5_table_topic_profile_v1"
PHASE5_PROFILE_SCHEMA_VERSION = 1
_PROFILES_NAME = "phase5_table_topic_profiles_v1.jsonl"
_REPORT_NAME = "phase5_table_topic_profile_report.json"
_MANIFEST_NAME = "phase5_table_topic_profile_manifest.json"
_OUTPUT_NAMES = (_PROFILES_NAME, _REPORT_NAME, _MANIFEST_NAME)
_DECIMAL_NOTE_RE = re.compile(r"^\s*\d{1,3}(?:\.\d+)*[.)]?\s+", re.UNICODE)
_ENUMERATED_NOTE_RE = re.compile(
    r"^\s*(?:[ivxlcdm]+[.)]|[IVXLCDM]+[.)]|[a-z][.)])\s+", re.UNICODE
)


@dataclass(frozen=True, slots=True)
class Phase5TableTopicProfileResult:
    output_dir: Path
    profile_count: int
    source_profiled_count: int
    unresolved_count: int


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha_json(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise CertifiedCanonicalError(f"invalid JSON input: {path}") from error
    if not isinstance(value, dict):
        raise CertifiedCanonicalError(f"expected JSON object: {path}")
    return value


def _jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
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
            records.append(value)
    return records


def _require_hash(path: Path, expected: object, *, label: str) -> None:
    if not isinstance(expected, str) or sha256_file(path) != expected:
        raise CertifiedCanonicalError(f"SHA-256 mismatch: {label}")


def _require_non_promotable(value: Mapping[str, Any], *, label: str) -> None:
    if value.get("training_eligible") is not False or value.get("certification_allowed") is not False:
        raise CertifiedCanonicalError(f"{label} violates the non-promotable contract")


def _require_new_output(output_dir: Path, inputs: Iterable[Path]) -> None:
    if output_dir.resolve() in {path.resolve() for path in inputs}:
        raise CertifiedCanonicalError("output-dir cannot be a Phase 5 input")
    if output_dir.exists():
        raise FileExistsError("output-dir must be new; Phase 5 profile output is immutable")


def _fold(value: object) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).casefold().split())


def _heading_profile(heading: str) -> tuple[dict[str, str] | None, str | None]:
    folded = _fold(heading)
    statement_rules = (
        ("bảng cân đối kế toán", "balance_sheet", "literal_balance_sheet_heading_v1"),
        ("báo cáo kết quả hoạt động kinh doanh", "income_statement", "literal_income_statement_heading_v1"),
        ("báo cáo lưu chuyển tiền tệ", "cash_flow_statement", "literal_cash_flow_heading_v1"),
    )
    for literal, kind, rule in statement_rules:
        if literal in folded:
            return {"profile_kind": kind, "profile_family": "financial_statement", "rule": rule}, None
    if _DECIMAL_NOTE_RE.match(heading):
        return {
            "profile_kind": "financial_note_section",
            "profile_family": "financial_note",
            "rule": "literal_decimal_financial_note_heading_v1",
        }, None
    if _ENUMERATED_NOTE_RE.match(heading):
        return {
            "profile_kind": "financial_note_section",
            "profile_family": "financial_note",
            "rule": "literal_enumerated_financial_note_heading_v1",
        }, None
    return None, "source_heading_does_not_match_a_narrow_profile_rule"


def _profile_record(span: Mapping[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": PHASE5_PROFILE_SCHEMA_VERSION,
        "protocol": PHASE5_PROFILE_PROTOCOL,
        "phase4_heading_span_id": span["heading_span_id"],
        "phase45_assertion_id": span["phase45_assertion_id"],
        "phase3_request_id": span["phase3_request_id"],
        "internal_table_uid": span["internal_table_uid"],
        "route_id": span["route_id"],
        "status": "UNRESOLVED",
        "evidence_class": "E3",
        "source_heading_sha256": span.get("source_heading_sha256"),
        "source_context_sha256": span.get("source_context_sha256"),
        "profile": None,
        "reason_codes": [],
        "derivation_rule": "none_phase5_insufficient_source_profile_rule",
        "training_eligible": False,
        "certification_allowed": False,
    }
    if span.get("status") != "SOURCE_HEADING_SPAN_MATERIALIZED":
        payload["reason_codes"] = ["heading_span_unresolved"]
        return {"table_topic_profile_id": _sha_json(payload), **payload}
    heading = str(span.get("source_heading") or "")
    profile, reason = _heading_profile(heading)
    if profile is None:
        payload["reason_codes"] = [str(reason)]
        return {"table_topic_profile_id": _sha_json(payload), **payload}
    payload.update(
        {
            "status": "SOURCE_PROFILED_LAYOUT_CONTEXT",
            "evidence_class": "E0",
            "profile": profile,
            "derivation_rule": profile["rule"],
        }
    )
    return {"table_topic_profile_id": _sha_json(payload), **payload}


def build_phase5_table_topic_profiles(
    *,
    phase4_heading_spans: Path,
    phase4_manifest: Path,
    output_dir: Path,
    route_id: str,
) -> Phase5TableTopicProfileResult:
    """Profile materialized source headings without certifying table semantics."""
    inputs = [phase4_heading_spans.resolve(), phase4_manifest.resolve()]
    if any(not path.is_file() for path in inputs):
        raise FileNotFoundError("missing Phase 5 table-topic profile input")
    _require_new_output(output_dir, inputs)
    before_hashes = {path.name: sha256_file(path) for path in inputs}
    manifest = _json(phase4_manifest)
    if (
        manifest.get("protocol") != PHASE4_LAYOUT_PROTOCOL
        or manifest.get("run_status") != "phase_4_heading_span_materialization_complete_not_certified"
        or manifest.get("training_eligible") is not False
        or manifest.get("certification_allowed") is not False
    ):
        raise CertifiedCanonicalError("Phase 4 heading-span manifest is unsupported or promotable")
    _require_hash(
        phase4_heading_spans,
        ((manifest.get("outputs") or {}).get(phase4_heading_spans.name) or {}).get("sha256"),
        label="Phase 4 heading spans",
    )
    spans = [row for row in _jsonl(phase4_heading_spans) if row.get("route_id") == route_id]
    if not spans:
        raise CertifiedCanonicalError("Phase 4 heading spans contain no rows for the requested route")
    seen: set[str] = set()
    for span in spans:
        _require_non_promotable(span, label="Phase 4 heading span")
        span_id = str(span.get("heading_span_id") or "")
        if not span_id or span_id in seen or span.get("internal_table_uid") is None:
            raise CertifiedCanonicalError("Phase 4 heading-span identity is invalid")
        seen.add(span_id)
    profiles = [_profile_record(span) for span in sorted(spans, key=lambda row: str(row["heading_span_id"]))]
    after_hashes = {path.name: sha256_file(path) for path in inputs}
    if after_hashes != before_hashes:
        raise CertifiedCanonicalError("Phase 5 table-topic profiling changed a hash-bound input")
    output_dir.mkdir(parents=True)
    profiles_path = output_dir / _PROFILES_NAME
    with profiles_path.open("x", encoding="utf-8") as file:
        for profile in profiles:
            file.write(json.dumps(profile, ensure_ascii=False, sort_keys=True) + "\n")
    status_counts = Counter(str(row["status"]) for row in profiles)
    kind_counts = Counter(
        str((row.get("profile") or {}).get("profile_kind"))
        for row in profiles
        if row.get("profile")
    )
    reason_counts = Counter(str(reason) for row in profiles for reason in (row.get("reason_codes") or []))
    report = {
        "schema_version": PHASE5_PROFILE_SCHEMA_VERSION,
        "protocol": PHASE5_PROFILE_PROTOCOL,
        "run_status": "phase_5_table_topic_profile_complete_not_certified",
        "route_id": route_id,
        "profile_count": len(profiles),
        "status_counts": dict(sorted(status_counts.items())),
        "profile_kind_counts": dict(sorted(kind_counts.items())),
        "unresolved_reason_counts": dict(sorted(reason_counts.items())),
        "input_hashes_unchanged": True,
        "training_eligible_output_count": 0,
        "certification_allowed": False,
        "next_gate": "profile_to_table_semantic_assertion_compatibility",
    }
    report_path = output_dir / _REPORT_NAME
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    output_manifest = {
        "schema_version": PHASE5_PROFILE_SCHEMA_VERSION,
        "protocol": PHASE5_PROFILE_PROTOCOL,
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
        json.dumps(output_manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return Phase5TableTopicProfileResult(
        output_dir=output_dir,
        profile_count=len(profiles),
        source_profiled_count=status_counts["SOURCE_PROFILED_LAYOUT_CONTEXT"],
        unresolved_count=status_counts["UNRESOLVED"],
    )
