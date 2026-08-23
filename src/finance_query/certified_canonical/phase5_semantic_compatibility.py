"""Fail-closed source-profile compatibility checks for Phase 5 table claims.

Phase 4 provides an exact literal heading span and Phase 5 turns a small,
explicit subset of those headings into source profiles.  This module checks
whether the *model-proposed* ``table_semantics`` string is compatible with one
of those narrow financial-statement profiles.  It intentionally does not turn
that agreement into a certified table meaning, a repair, or a training row.

The point of the gate is diagnostic: distinguish a proposal that names a
controlled statement kind from relation labels or unconstrained prose that the
current request schema allowed the model to emit.  Numbered financial-note
headings remain context only, because a note heading alone does not define a
complete table semantic type.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping
import unicodedata

from finance_query.table_structure import sha256_file

from .phase4_layout import PHASE4_LAYOUT_PROTOCOL
from .phase45 import PHASE45_PROTOCOL
from .phase5_profiles import PHASE5_PROFILE_PROTOCOL
from .pipeline import CertifiedCanonicalError


PHASE5_SEMANTIC_COMPATIBILITY_PROTOCOL = "vifinqa_ccl_phase5_source_profile_compatibility_v1"
PHASE5_SEMANTIC_COMPATIBILITY_SCHEMA_VERSION = 1
_COMPATIBILITY_NAME = "phase5_source_profile_compatibility_v1.jsonl"
_REPORT_NAME = "phase5_source_profile_compatibility_report.json"
_MANIFEST_NAME = "phase5_source_profile_compatibility_manifest.json"
_OUTPUT_NAMES = (_COMPATIBILITY_NAME, _REPORT_NAME, _MANIFEST_NAME)

# These are controlled label values, not fuzzy aliases.  A Vietnamese literal
# remains accepted because Phase 5 has independently proved it exists in the
# immutable source heading for the matching statement family.
_PROFILE_PROPOSAL_LITERALS: dict[str, frozenset[str]] = {
    "balance_sheet": frozenset({"balance_sheet", "bảng cân đối kế toán"}),
    "income_statement": frozenset({"income_statement", "báo cáo kết quả hoạt động kinh doanh"}),
    "cash_flow_statement": frozenset({"cash_flow_statement", "báo cáo lưu chuyển tiền tệ"}),
}


@dataclass(frozen=True, slots=True)
class Phase5SemanticCompatibilityResult:
    output_dir: Path
    compatibility_count: int
    compatible_context_count: int
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
        raise CertifiedCanonicalError("output-dir cannot be a Phase 5 compatibility input")
    if output_dir.exists():
        raise FileExistsError("output-dir must be new; Phase 5 compatibility output is immutable")


def _fold(value: object) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).casefold().split())


def _index_unique(rows: Iterable[Mapping[str, Any]], *, key: str, label: str) -> dict[str, Mapping[str, Any]]:
    indexed: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        value = str(row.get(key) or "")
        if not value or value in indexed:
            raise CertifiedCanonicalError(f"{label} has a missing or duplicate {key}")
        indexed[value] = row
    return indexed


def _require_manifest(
    manifest: Mapping[str, Any],
    *,
    protocol: str,
    run_status: str,
    label: str,
) -> None:
    if (
        manifest.get("protocol") != protocol
        or manifest.get("run_status") != run_status
        or manifest.get("training_eligible") is not False
        or manifest.get("certification_allowed") is not False
    ):
        raise CertifiedCanonicalError(f"{label} manifest is unsupported or promotable")


def _require_manifest_input(
    manifest: Mapping[str, Any], path: Path, *, label: str
) -> None:
    _require_hash(path, ((manifest.get("inputs") or {}).get(path.name) or {}).get("sha256"), label=label)


def _assert_shared_identity(
    assertion: Mapping[str, Any], span: Mapping[str, Any], profile: Mapping[str, Any]
) -> None:
    assertion_id = str(assertion.get("assertion_id") or "")
    if span.get("phase45_assertion_id") != assertion_id or profile.get("phase45_assertion_id") != assertion_id:
        raise CertifiedCanonicalError("compatibility inputs do not share a Phase 4-5 assertion identity")
    for field in ("phase3_request_id", "internal_table_uid", "route_id"):
        expected = assertion.get(field)
        if not expected or span.get(field) != expected or profile.get(field) != expected:
            raise CertifiedCanonicalError(f"compatibility inputs have mismatched {field}")
    if profile.get("phase4_heading_span_id") != span.get("heading_span_id"):
        raise CertifiedCanonicalError("profile does not bind the selected Phase 4 heading span")


def _compatible_record(
    *, assertion: Mapping[str, Any], span: Mapping[str, Any], profile: Mapping[str, Any]
) -> dict[str, Any]:
    profile_payload = profile.get("profile") if isinstance(profile.get("profile"), Mapping) else None
    profile_kind = str((profile_payload or {}).get("profile_kind") or "")
    payload: dict[str, Any] = {
        "schema_version": PHASE5_SEMANTIC_COMPATIBILITY_SCHEMA_VERSION,
        "protocol": PHASE5_SEMANTIC_COMPATIBILITY_PROTOCOL,
        "phase45_assertion_id": assertion["assertion_id"],
        "phase4_heading_span_id": span["heading_span_id"],
        "phase5_table_topic_profile_id": profile["table_topic_profile_id"],
        "phase3_request_id": assertion["phase3_request_id"],
        "phase3_proposal_sha256": assertion.get("phase3_proposal_sha256"),
        "internal_table_uid": assertion["internal_table_uid"],
        "route_id": assertion["route_id"],
        "field": "table_semantics",
        "proposed_value": assertion.get("proposed_value"),
        "source_heading": span.get("source_heading"),
        "source_heading_sha256": span.get("source_heading_sha256"),
        "source_context_sha256": span.get("source_context_sha256"),
        "source_profile": profile_payload,
        "status": "UNRESOLVED",
        "evidence_class": "E3",
        "reason_codes": [],
        "derivation_rule": "none_phase5_source_profile_incompatible",
        # Compatibility is a diagnostic signal only: no row created here can
        # enter training, repair, certification, or a future campaign queue.
        "campaign_candidate_allowed": False,
        "training_eligible": False,
        "certification_allowed": False,
    }
    if span.get("status") != "SOURCE_HEADING_SPAN_MATERIALIZED":
        payload["reason_codes"] = ["no_materialized_source_heading_span"]
    elif profile.get("status") != "SOURCE_PROFILED_LAYOUT_CONTEXT" or profile_payload is None:
        payload["reason_codes"] = ["no_narrow_source_profile"]
    elif str(profile_payload.get("profile_family") or "") != "financial_statement":
        payload["reason_codes"] = ["source_profile_is_not_a_statement_type"]
    elif _fold(assertion.get("proposed_value")) not in _PROFILE_PROPOSAL_LITERALS.get(profile_kind, frozenset()):
        payload["reason_codes"] = ["model_table_semantic_value_not_compatible_with_source_profile"]
    else:
        payload.update(
            {
                "status": "SOURCE_PROFILE_COMPATIBLE_CONTEXT_ONLY",
                "evidence_class": "E0",
                "derivation_rule": "phase5_controlled_statement_profile_compatibility_v1",
            }
        )
    return {"source_profile_compatibility_id": _sha_json(payload), **payload}


def _statement_profile_comparison(records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Summarize a model enum against the independently-derived source profile.

    This is an evaluation metric, not a source of labels.  It only counts
    profiles classified as literal financial-statement headings, so note
    sections and unresolved spans cannot inflate or reduce the rate.
    """
    source_count = 0
    compatible_count = 0
    confusion: dict[str, Counter[str]] = {}
    for record in records:
        profile = record.get("source_profile")
        if not isinstance(profile, Mapping) or profile.get("profile_family") != "financial_statement":
            continue
        profile_kind = str(profile.get("profile_kind") or "")
        proposed_value = str(record.get("proposed_value") or "")
        if not profile_kind or not proposed_value:
            continue
        source_count += 1
        confusion.setdefault(profile_kind, Counter())[proposed_value] += 1
        compatible_count += int(record.get("status") == "SOURCE_PROFILE_COMPATIBLE_CONTEXT_ONLY")
    mismatch_count = source_count - compatible_count
    return {
        "source_profiled_statement_count": source_count,
        "model_enum_compatible_context_count": compatible_count,
        "model_enum_mismatch_count": mismatch_count,
        "model_enum_compatible_context_rate": compatible_count / source_count if source_count else None,
        "source_profile_to_model_value_counts": {
            source: dict(sorted(values.items())) for source, values in sorted(confusion.items())
        },
    }


def verify_phase5_source_profile_compatibility(
    *,
    phase45_assertions: Path,
    phase45_manifest: Path,
    phase4_heading_spans: Path,
    phase4_manifest: Path,
    phase5_profiles: Path,
    phase5_manifest: Path,
    output_dir: Path,
    route_id: str,
) -> Phase5SemanticCompatibilityResult:
    """Emit hash-bound, non-promotable model/source profile compatibility diagnostics."""
    inputs = [
        path.resolve()
        for path in (
            phase45_assertions,
            phase45_manifest,
            phase4_heading_spans,
            phase4_manifest,
            phase5_profiles,
            phase5_manifest,
        )
    ]
    if any(not path.is_file() for path in inputs):
        raise FileNotFoundError("missing Phase 5 source-profile compatibility input")
    _require_new_output(output_dir, inputs)
    before_hashes = {path.name: sha256_file(path) for path in inputs}

    phase45 = _json(phase45_manifest)
    _require_manifest(
        phase45,
        protocol=PHASE45_PROTOCOL,
        run_status="phase_4_5_deterministic_verification_complete_not_certified",
        label="Phase 4-5",
    )
    _require_hash(
        phase45_assertions,
        ((phase45.get("outputs") or {}).get(phase45_assertions.name) or {}).get("sha256"),
        label="Phase 4-5 assertions",
    )

    phase4 = _json(phase4_manifest)
    _require_manifest(
        phase4,
        protocol=PHASE4_LAYOUT_PROTOCOL,
        run_status="phase_4_heading_span_materialization_complete_not_certified",
        label="Phase 4 heading span",
    )
    _require_manifest_input(phase4, phase45_assertions, label="Phase 4 bound Phase 4-5 assertions")
    _require_manifest_input(phase4, phase45_manifest, label="Phase 4 bound Phase 4-5 manifest")
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
    _require_manifest_input(phase5, phase4_heading_spans, label="Phase 5 bound Phase 4 heading spans")
    _require_manifest_input(phase5, phase4_manifest, label="Phase 5 bound Phase 4 manifest")
    _require_hash(
        phase5_profiles,
        ((phase5.get("outputs") or {}).get(phase5_profiles.name) or {}).get("sha256"),
        label="Phase 5 table profiles",
    )

    assertions = _index_unique(
        (
            row
            for row in _jsonl(phase45_assertions)
            if row.get("route_id") == route_id and row.get("field") == "table_semantics"
        ),
        key="assertion_id",
        label="Phase 4-5 table-semantic assertions",
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
    if not assertions:
        raise CertifiedCanonicalError("requested route has no table-semantic assertions")
    if set(assertions) != set(spans) or set(assertions) != set(profiles):
        raise CertifiedCanonicalError("compatibility inputs do not provide a one-to-one assertion/span/profile join")

    records: list[dict[str, Any]] = []
    for assertion_id in sorted(assertions):
        assertion, span, profile = assertions[assertion_id], spans[assertion_id], profiles[assertion_id]
        _require_non_promotable(assertion, label="Phase 4-5 assertion")
        _require_non_promotable(span, label="Phase 4 heading span")
        _require_non_promotable(profile, label="Phase 5 source profile")
        _assert_shared_identity(assertion, span, profile)
        records.append(_compatible_record(assertion=assertion, span=span, profile=profile))

    after_hashes = {path.name: sha256_file(path) for path in inputs}
    if after_hashes != before_hashes:
        raise CertifiedCanonicalError("Phase 5 source-profile compatibility changed a hash-bound input")
    output_dir.mkdir(parents=True)
    records_path = output_dir / _COMPATIBILITY_NAME
    with records_path.open("x", encoding="utf-8") as file:
        for record in records:
            file.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    status_counts = Counter(str(row["status"]) for row in records)
    reason_counts = Counter(str(reason) for row in records for reason in (row.get("reason_codes") or []))
    statement_profile_comparison = _statement_profile_comparison(records)
    report = {
        "schema_version": PHASE5_SEMANTIC_COMPATIBILITY_SCHEMA_VERSION,
        "protocol": PHASE5_SEMANTIC_COMPATIBILITY_PROTOCOL,
        "run_status": "phase_5_source_profile_compatibility_complete_not_certified",
        "route_id": route_id,
        "compatibility_count": len(records),
        "status_counts": dict(sorted(status_counts.items())),
        "unresolved_reason_counts": dict(sorted(reason_counts.items())),
        "statement_profile_comparison": statement_profile_comparison,
        "input_hashes_unchanged": True,
        "campaign_candidate_allowed_count": 0,
        "training_eligible_output_count": 0,
        "certification_allowed": False,
        "next_gate": "schema_redesign_and_smoke_only;_do_not_promote_this_route",
    }
    report_path = output_dir / _REPORT_NAME
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    output_manifest = {
        "schema_version": PHASE5_SEMANTIC_COMPATIBILITY_SCHEMA_VERSION,
        "protocol": PHASE5_SEMANTIC_COMPATIBILITY_PROTOCOL,
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
        "campaign_candidate_allowed": False,
        "training_eligible": False,
        "certification_allowed": False,
    }
    (output_dir / _MANIFEST_NAME).write_text(
        json.dumps(output_manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return Phase5SemanticCompatibilityResult(
        output_dir=output_dir,
        compatibility_count=len(records),
        compatible_context_count=status_counts["SOURCE_PROFILE_COMPATIBLE_CONTEXT_ONLY"],
        unresolved_count=status_counts["UNRESOLVED"],
    )
