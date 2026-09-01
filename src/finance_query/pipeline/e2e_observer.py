"""Independent E2E observer for canonical ``ResolvedPrediction`` rows.

The observer is intentionally downstream of the resolver.  It adapts the
current grounded-E2E engine to the new hand-off, then emits one compact
``E2EReceipt`` per question.  The engine may still use the declared source
closure to rebuild exact bindings; this adapter records that compatibility
fact instead of presenting it as a completed architectural migration.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any

import yaml

from ..e2e.deterministic_replay import load_deterministic_replay_inputs, run_deterministic_replay
from .contracts import canonical_sha256


E2E_RECEIPT_PROTOCOL = "vifinqa_e2e_receipt_v1"
E2E_OBSERVER_MANIFEST_PROTOCOL = "vifinqa_e2e_observer_manifest_v1"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class E2EObserverResult:
    """Files emitted by one independent observer run."""

    output_dir: Path
    receipts_path: Path
    run_receipt_path: Path
    manifest_path: Path
    question_count: int
    verified_count: int


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _rows(path: Path) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path}:{line_number} must contain an object")
        values.append(value)
    return values


def _index(rows: Iterable[Mapping[str, Any]], *, key: str, label: str) -> dict[int, dict[str, Any]]:
    indexed: dict[int, dict[str, Any]] = {}
    for row in rows:
        try:
            identifier = int(row[key])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(f"{label} has an invalid question id") from error
        if identifier in indexed:
            raise ValueError(f"{label} has duplicate question id {identifier}")
        indexed[identifier] = dict(row)
    return indexed


def _without_id(value: Mapping[str, Any], identifier: str) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key != identifier}


def _validate_resolved_row(row: Mapping[str, Any]) -> int:
    """Validate the resolver's content hash before invoking the E2E engine."""

    if (
        row.get("schema_version") != 1
        or row.get("protocol") != "vifinqa_resolved_prediction_v1"
    ):
        raise ValueError("resolved prediction has an unexpected protocol")
    try:
        question_id = int(row["question_id"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("resolved prediction has an invalid question id") from error
    identifier = _text(row.get("resolved_prediction_id"))
    if not _SHA256_RE.fullmatch(identifier) or canonical_sha256(
        _without_id(row, "resolved_prediction_id")
    ) != identifier:
        raise ValueError(f"resolved prediction id mismatch for question {question_id}")
    return question_id


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _text(value: object) -> str:
    return str(value or "").strip()


def _decimal(value: object) -> Decimal | None:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() else None


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )


def _candidate_ledger(resolved_rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for row in resolved_rows:
        answer = _decimal(row.get("answer_decimal"))
        if answer is None:
            continue
        sources = [
            dict(operand.get("source") or {})
            for operand in row.get("operand_receipts") or []
            if isinstance(operand, Mapping) and _mapping(operand.get("source"))
        ]
        candidates.append(
            {
                "schema_version": 1,
                "protocol": "vifinqa_best_surviving_candidate_v1",
                "question_id": int(row["question_id"]),
                "candidate_id": f"resolved:{row.get('resolved_prediction_id')}",
                "answer_decimal": format(answer, "f"),
                "filter_status": "SURVIVED_FILTER",
                "filter_passed": True,
                "selection_method": "resolved_prediction_handoff",
                "source": sources,
                "resolved_prediction_id": row.get("resolved_prediction_id"),
                "operation_ast_sha256": (
                    _mapping(row.get("execution_receipt")).get("operation_ast_sha256")
                ),
                "canonical_decimal_reexecution": (
                    _mapping(row.get("execution_receipt")).get("canonical_decimal_reexecution")
                    is True
                ),
            }
        )
    return candidates


def _generated_config(base_config: Path, *, candidate_path: Path, output_dir: Path) -> Path:
    payload = yaml.safe_load(base_config.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping) or not isinstance(payload.get("paths"), Mapping):
        raise ValueError("E2E config must contain a paths mapping")
    paths: dict[str, str] = {}
    for name, raw_value in payload["paths"].items():
        if str(name).startswith("expected_"):
            continue
        if not isinstance(raw_value, str) or not raw_value.strip():
            continue
        path = Path(raw_value)
        if not path.is_absolute():
            path = (base_config.parent / path).resolve()
        paths[str(name)] = str(path)
    paths["best_candidate_predictions"] = str(candidate_path.resolve())
    generated_payload = {
        "schema_version": payload.get("schema_version"),
        "protocol": payload.get("protocol"),
        "run_name": "canonical-resolved-prediction-observer",
        "paths": paths,
    }
    generated_path = output_dir / "e2e_input.yaml"
    generated_path.write_text(
        yaml.safe_dump(generated_payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return generated_path


def _field_status(binding: Mapping[str, Any], field: str) -> str:
    statuses = _mapping(binding.get("field_statuses"))
    value = _text(statuses.get(f"{field}_status")).upper()
    return value or "UNRESOLVED"


def _certificate_id_valid(certificate: Mapping[str, Any]) -> bool:
    identifier = _text(certificate.get("answer_certificate_id"))
    return bool(
        _SHA256_RE.fullmatch(identifier)
        and canonical_sha256(_without_id(certificate, "answer_certificate_id")) == identifier
    )


def _coordinate_ready(source: Mapping[str, Any]) -> bool:
    return bool(
        _text(source.get("document_id") or source.get("document_uid"))
        and _text(source.get("internal_table_uid"))
        and isinstance(source.get("row_index"), int)
        and not isinstance(source.get("row_index"), bool)
        and source.get("row_index") >= 0
        and isinstance(source.get("column_index"), int)
        and not isinstance(source.get("column_index"), bool)
        and source.get("column_index") >= 0
    )


def _document_key(value: object) -> str:
    return _text(value).removesuffix(".txt")


def _resolved_source_closure_ready(resolved: Mapping[str, Any]) -> bool:
    operands = [
        operand
        for operand in resolved.get("operand_receipts") or []
        if isinstance(operand, Mapping)
    ]
    if not operands:
        return False
    return all(
        operand.get("binding_status") in {"BOUND_CANDIDATE", "BOUND"}
        and (
            operand.get("source_value_status") == "AVAILABLE"
            or _decimal(operand.get("source_value_decimal")) is not None
        )
        and _coordinate_ready(_mapping(operand.get("source")))
        for operand in operands
    )


def _resolved_coordinates(resolved: Mapping[str, Any]) -> set[tuple[str, str, int, int]]:
    coordinates: set[tuple[str, str, int, int]] = set()
    for operand in resolved.get("operand_receipts") or []:
        if not isinstance(operand, Mapping):
            continue
        source = _mapping(operand.get("source"))
        if not _coordinate_ready(source):
            continue
        coordinates.add(
            (
                _document_key(source.get("document_id") or source.get("document_uid")),
                _text(source.get("internal_table_uid")),
                int(source["row_index"]),
                int(source["column_index"]),
            )
        )
    return coordinates


def _certificate_coordinates(certificate: Mapping[str, Any]) -> set[tuple[str, str, int, int]]:
    coordinates: set[tuple[str, str, int, int]] = set()
    for binding in certificate.get("binding_receipts") or []:
        if not isinstance(binding, Mapping):
            continue
        cell = _mapping(binding.get("source_value_cell"))
        source = {
            "document_uid": binding.get("document_uid"),
            "internal_table_uid": binding.get("internal_table_uid"),
            "row_index": cell.get("row_index"),
            "column_index": cell.get("column_index"),
        }
        if not _coordinate_ready(source):
            continue
        coordinates.add(
            (
                _document_key(source.get("document_id") or source.get("document_uid")),
                _text(source.get("internal_table_uid")),
                int(source["row_index"]),
                int(source["column_index"]),
            )
        )
    return coordinates


def _resolved_operand_ids(resolved: Mapping[str, Any]) -> list[str]:
    return [
        _text(operand.get("operand_id"))
        for operand in resolved.get("operand_receipts") or []
        if isinstance(operand, Mapping)
    ]


def _certificate_operand_ids(certificate: Mapping[str, Any]) -> list[str]:
    return [
        _text(binding.get("operand_id"))
        for binding in certificate.get("binding_receipts") or []
        if isinstance(binding, Mapping)
    ]


def _certificate_source_and_semantics_ready(certificate: Mapping[str, Any]) -> bool:
    """Require actual binding receipts, not merely a certificate status label."""

    raw_bindings = certificate.get("binding_receipts")
    if not isinstance(raw_bindings, list) or not raw_bindings:
        return False
    if any(not isinstance(binding, Mapping) for binding in raw_bindings):
        return False
    bindings = [binding for binding in raw_bindings if isinstance(binding, Mapping)]
    required_fields = {
        "variable_status",
        "period_status",
        "unit_status",
        "entity_status",
        "scope_status",
        "source_integrity_status",
    }
    for binding in bindings:
        source_cell = binding.get("source_value_cell")
        if not _text(binding.get("document_uid")) or not _text(binding.get("internal_table_uid")):
            return False
        if not isinstance(source_cell, Mapping):
            return False
        if not _coordinate_ready(
            {
                "document_uid": binding.get("document_uid"),
                "internal_table_uid": binding.get("internal_table_uid"),
                "row_index": source_cell.get("row_index"),
                "column_index": source_cell.get("column_index"),
            }
        ):
            return False
        statuses = _mapping(binding.get("field_statuses"))
        if not required_fields.issubset(statuses):
            return False
        if any(statuses[field] not in {"PASS", "NOT_APPLICABLE"} for field in required_fields):
            return False
    checks = certificate.get("counterfactual_checks")
    return isinstance(checks, list) and bool(checks) and all(
        isinstance(item, Mapping) and item.get("status") in {"EXHAUSTED", "REJECTED"}
        for item in checks
    )


def _evaluate_certificate(
    *,
    resolved: Mapping[str, Any],
    certificate: Mapping[str, Any],
) -> tuple[str, list[str], bool]:
    """Return status, failure codes, and whether the certificate is complete."""

    resolved_answer = _decimal(resolved.get("answer_decimal"))
    certificate_answer = _decimal(certificate.get("answer"))
    answer_matches = (
        resolved_answer is not None
        and certificate_answer is not None
        and resolved_answer == certificate_answer
    )
    complete = (
        certificate.get("status") == "ANSWER_CERTIFICATE_COMPLETE_CAMPAIGN_ONLY"
        and certificate.get("answer_status") == "ANSWER"
        and certificate.get("answer_authorized") is True
    )
    reasons: list[str] = []
    if not certificate:
        return (
            "PARTIAL" if resolved_answer is not None else "UNRESOLVED",
            ["E2E_CERTIFICATE_MISSING"],
            False,
        )
    if (
        certificate.get("schema_version") != 1
        or certificate.get("protocol") != "vifinqa_answer_certificate_v1"
    ):
        reasons.append("ANSWER_CERTIFICATE_PROTOCOL_INVALID")
    if not _certificate_id_valid(certificate):
        reasons.append("ANSWER_CERTIFICATE_ID_MISMATCH")
    certificate_question_id = certificate.get("question_id")
    if certificate_question_id is None:
        reasons.append("ANSWER_CERTIFICATE_QUESTION_ID_MISSING")
    else:
        try:
            question_id_matches = int(certificate_question_id) == int(resolved["question_id"])
        except (TypeError, ValueError):
            question_id_matches = False
        if not question_id_matches:
            reasons.append("ANSWER_CERTIFICATE_QUESTION_ID_MISMATCH")
    if not answer_matches:
        reasons.append("RESOLVED_ANSWER_CERTIFICATE_MISMATCH")
    canonical_execution = _mapping(resolved.get("execution_receipt"))
    canonical_answer = _decimal(canonical_execution.get("answer_decimal"))
    if (
        resolved.get("resolution_status") != "RESOLVED_CANDIDATE"
        or canonical_execution.get("canonical_decimal_reexecution") is not True
        or canonical_execution.get("status") != "PASS"
        or canonical_answer is None
        or resolved_answer is None
        or canonical_answer != resolved_answer
    ):
        reasons.append("CANONICAL_REEXECUTION_NOT_PROVEN")
    if not _resolved_source_closure_ready(resolved):
        reasons.append("RESOLVED_SOURCE_CLOSURE_NOT_READY")
    if _certificate_coordinates(certificate) != _resolved_coordinates(resolved):
        reasons.append("E2E_SOURCE_COORDINATE_MISMATCH")
    if Counter(_certificate_operand_ids(certificate)) != Counter(_resolved_operand_ids(resolved)):
        reasons.append("E2E_OPERAND_ID_MISMATCH")
    expected_ast_sha = _text(canonical_execution.get("operation_ast_sha256"))
    certificate_ast_sha = _text(certificate.get("operation_ast_sha256"))
    if not expected_ast_sha or certificate_ast_sha != expected_ast_sha:
        reasons.append("CANONICAL_OPERATION_AST_HASH_MISMATCH")
    handoff = _mapping(certificate.get("canonical_handoff"))
    handoff_id = _text(
        certificate.get("resolved_prediction_id") or handoff.get("resolved_prediction_id")
    )
    if handoff_id and handoff_id != _text(resolved.get("resolved_prediction_id")):
        reasons.append("RESOLVED_PREDICTION_ID_MISMATCH")
    certificate_execution = _mapping(certificate.get("execution_receipt"))
    if not certificate_execution:
        reasons.append("E2E_EXECUTION_RECEIPT_MISSING")
    else:
        if _text(certificate_execution.get("status")) not in {"PASS", "execution_replay_ready"}:
            reasons.append("E2E_EXECUTION_NOT_PASSED")
        if _decimal(certificate_execution.get("answer_decimal")) != canonical_answer:
            reasons.append("E2E_EXECUTION_ANSWER_MISMATCH")
        if _text(certificate_execution.get("operation_ast_sha256")) != expected_ast_sha:
            reasons.append("E2E_EXECUTION_AST_HASH_MISMATCH")
    if not _certificate_source_and_semantics_ready(certificate):
        reasons.append("E2E_SOURCE_OR_SEMANTIC_PROOF_MISSING")
    identity_or_value_mismatch = {
        "ANSWER_CERTIFICATE_ID_MISMATCH",
        "ANSWER_CERTIFICATE_PROTOCOL_INVALID",
        "ANSWER_CERTIFICATE_QUESTION_ID_MISSING",
        "ANSWER_CERTIFICATE_QUESTION_ID_MISMATCH",
        "RESOLVED_ANSWER_CERTIFICATE_MISMATCH",
        "CANONICAL_OPERATION_AST_HASH_MISMATCH",
        "RESOLVED_PREDICTION_ID_MISMATCH",
        "E2E_EXECUTION_ANSWER_MISMATCH",
        "E2E_EXECUTION_AST_HASH_MISMATCH",
        "E2E_SOURCE_COORDINATE_MISMATCH",
        "E2E_OPERAND_ID_MISMATCH",
    }
    if reasons:
        return (
            "REJECTED" if complete or identity_or_value_mismatch.intersection(reasons) else "PARTIAL",
            sorted(set(reasons)),
            complete,
        )
    if complete:
        return "VERIFIED", [], True
    if certificate.get("answer_status") == "PREDICTED_CANDIDATE":
        return "PARTIAL", ["E2E_CERTIFICATE_NOT_COMPLETE"], False
    return "UNRESOLVED", ["E2E_CERTIFICATE_NOT_COMPLETE"], False


def _receipt(
    *,
    resolved: Mapping[str, Any],
    certificate_row: Mapping[str, Any] | None,
    evidence_rows: list[Mapping[str, Any]],
) -> dict[str, Any]:
    question_id = int(resolved["question_id"])
    certificate = _mapping((certificate_row or {}).get("answer_certificate"))
    status, evaluation_reasons, _complete = _evaluate_certificate(
        resolved=resolved,
        certificate=certificate,
    )
    wrapper_handoff_id = _text((certificate_row or {}).get("resolved_prediction_id"))
    if wrapper_handoff_id and wrapper_handoff_id != _text(resolved.get("resolved_prediction_id")):
        status = "REJECTED"
        evaluation_reasons = sorted(
            set(evaluation_reasons) | {"RESOLVED_PREDICTION_ID_MISMATCH"}
        )
    certificate_answer = _decimal(certificate.get("answer"))
    resolved_answer = _decimal(resolved.get("answer_decimal"))
    answer_matches = (
        resolved_answer is not None
        and certificate_answer is not None
        and resolved_answer == certificate_answer
    )
    operand_receipts: list[dict[str, Any]] = []
    for binding in evidence_rows:
        field_statuses = _mapping(binding.get("field_statuses"))
        operand_receipts.append(
            {
                "operand_id": binding.get("operand_id"),
                "binding_status": binding.get("binding_status") or "UNRESOLVED",
                "semantic_status": _field_status(binding, "variable"),
                "temporal_status": _field_status(binding, "period"),
                "scope_entity_status": _field_status(binding, "entity"),
                "unit_status": _field_status(binding, "unit"),
                "field_statuses": dict(field_statuses),
                "source": {
                    key: binding.get(key)
                    for key in ("document_uid", "internal_table_uid", "source_value_cell")
                    if binding.get(key) is not None
                },
                "failure_reason_codes": [],
            }
        )
    if not operand_receipts:
        operand_receipts = [
            {
                "operand_id": operand.get("operand_id"),
                "binding_status": operand.get("binding_status") or "UNRESOLVED",
                "semantic_status": operand.get("semantic_status") or "UNRESOLVED",
                "temporal_status": operand.get("temporal_status") or "UNRESOLVED",
                "scope_entity_status": operand.get("scope_entity_status") or "UNRESOLVED",
                "unit_status": operand.get("unit_status") or "UNRESOLVED",
                "field_statuses": {},
                "source": dict(operand.get("source") or {}),
                "failure_reason_codes": list(operand.get("failure_reason_codes") or []),
            }
            for operand in resolved.get("operand_receipts") or []
            if isinstance(operand, Mapping)
        ]
    reason_codes = [str(value) for value in certificate.get("abstain_reason_codes") or [] if value]
    reason_codes.extend(evaluation_reasons)
    binding_plan = _mapping(certificate.get("binding_plan"))
    formula = _mapping(binding_plan.get("formula_compatibility"))
    canonical_execution = _mapping(resolved.get("execution_receipt"))
    payload: dict[str, Any] = {
        "schema_version": 1,
        "protocol": E2E_RECEIPT_PROTOCOL,
        "question_id": question_id,
        "resolved_prediction_id": resolved.get("resolved_prediction_id"),
        "e2e_status": status,
        "certificate": {
            "answer_certificate_id": certificate.get("answer_certificate_id"),
            "status": certificate.get("status"),
            "answer_status": certificate.get("answer_status"),
            "answer_authorized": certificate.get("answer_authorized") is True,
            "answer_matches_resolved_prediction": answer_matches,
            "binding_plan_sha256": certificate.get("binding_plan_sha256"),
            "operation_ast_sha256": certificate.get("operation_ast_sha256"),
            "content_hash_valid": _certificate_id_valid(certificate) if certificate else False,
        },
        "answer_certificate": dict(certificate),
        "operand_receipts": operand_receipts,
        "formula_receipt": {
            "contract_status": "PASS" if formula and status == "VERIFIED" else "UNRESOLVED",
            "compatibility_status": "PASS" if formula and status == "VERIFIED" else "UNRESOLVED",
            "formula_compatibility": dict(formula),
        },
        "execution_receipt": {
            "status": canonical_execution.get("status") or "UNRESOLVED",
            "answer_decimal": canonical_execution.get("answer_decimal"),
            "operation_ast_sha256": canonical_execution.get("operation_ast_sha256"),
            "canonical_decimal_reexecution": canonical_execution.get(
                "canonical_decimal_reexecution"
            ) is True,
        },
        "first_failure": reason_codes[0] if reason_codes else None,
        "failure_reason_codes": sorted(set(reason_codes)),
        "authority_boundary": "e2e_receipt_proof_only_release_policy_still_required",
    }
    return {**payload, "e2e_receipt_id": canonical_sha256(payload)}


def run_e2e_observer(
    *,
    resolved_predictions_path: Path,
    base_config: Path,
    output_dir: Path,
) -> E2EObserverResult:
    """Run the independent E2E engine from a resolved prediction hand-off."""

    resolved_predictions_path = resolved_predictions_path.resolve()
    base_config = base_config.resolve()
    output_dir = output_dir.resolve()
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite E2E observer output: {output_dir}")
    resolved_rows = _rows(resolved_predictions_path)
    if not resolved_rows:
        raise ValueError("resolved prediction ledger is empty")
    resolved_ids = [_validate_resolved_row(row) for row in resolved_rows]
    if len(resolved_ids) != len(set(resolved_ids)):
        raise ValueError("resolved prediction ledger has duplicate question ids")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.tmp-", dir=output_dir.parent))
    try:
        candidate_path = staging / "resolved_candidate_ledger_v1.jsonl"
        _write_jsonl(candidate_path, _candidate_ledger(resolved_rows))
        generated_config = _generated_config(base_config, candidate_path=candidate_path, output_dir=staging)
        e2e_dir = staging / "grounded_e2e"
        receipt = run_deterministic_replay(
            load_deterministic_replay_inputs(generated_config),
            output_dir=e2e_dir,
        )
        certificate_path = e2e_dir / "answer_certificates_v1.jsonl"
        evidence_path = e2e_dir / "evidence_bindings_v1.jsonl"
        certificates = _index(_rows(certificate_path), key="question_id", label="E2E certificates")
        evidence_by_question: dict[int, list[dict[str, Any]]] = {}
        for row in _rows(evidence_path):
            evidence_by_question.setdefault(int(row["question_id"]), []).append(row.get("evidence_binding") or {})
        receipts = [
            _receipt(
                resolved=row,
                certificate_row=certificates.get(int(row["question_id"])),
                evidence_rows=evidence_by_question.get(int(row["question_id"]), []),
            )
            for row in resolved_rows
        ]
        receipts_path = staging / "e2e_receipts_v1.jsonl"
        _write_jsonl(receipts_path, receipts)
        run_receipt_path = staging / "grounded_e2e_run_v1.json"
        # Keep the engine's authoritative raw receipt at a stable nested path,
        # while the observer wrapper exposes the canonical hand-off hashes.
        shutil.copy2(e2e_dir / "grounded_e2e_run_v1.json", run_receipt_path)
        status_counts = Counter(str(row["e2e_status"]) for row in receipts)
        manifest = {
            "schema_version": 1,
            "protocol": E2E_OBSERVER_MANIFEST_PROTOCOL,
            "inputs": {
                "resolved_predictions": {
                    "path": str(resolved_predictions_path),
                    "sha256": _sha256_file(resolved_predictions_path),
                },
                "base_config": {"path": str(base_config), "sha256": _sha256_file(base_config)},
            },
            "outputs": {
                "e2e_receipts": {
                    "path": str(output_dir / receipts_path.name),
                    "sha256": _sha256_file(receipts_path),
                },
                "grounded_e2e_run": {
                    "path": str(output_dir / run_receipt_path.name),
                    "sha256": _sha256_file(run_receipt_path),
                },
            },
            "counts": {
                "question_count": len(receipts),
                "e2e_status_counts": dict(sorted(status_counts.items())),
                "certificate_content_hash_valid_count": sum(
                    1
                    for row in receipts
                    if row["certificate"].get("content_hash_valid") is True
                ),
                "canonical_execution_count": sum(
                    1
                    for row in receipts
                    if row["execution_receipt"].get("canonical_decimal_reexecution") is True
                ),
            },
            "authority": {
                "e2e_observer_can_generate_answer": False,
                "e2e_observer_can_authorize_without_canonical_execution": False,
                "verified_requires_matching_certificate": True,
                "verified_requires_source_closure": True,
                "verified_requires_semantic_proof": True,
                "hash_mismatch_is_rejected": True,
                "release_gate_required": True,
            },
            "migration": {
                "source_engine": "finance_query.e2e.grounded_e2e",
                "resolved_prediction_is_explicit_input": True,
                "engine_rebuilds_compatibility_bindings": True,
                "canonical_execution_is_resolver_owned": True,
                "legacy_claim_used_as_e2e_proof": False,
                "verified_requires_handoff_ast_hash_match": True,
            },
            "engine_receipt": receipt,
        }
        manifest_path = staging / "e2e_observer.manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        staging.rename(output_dir)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return E2EObserverResult(
        output_dir=output_dir,
        receipts_path=output_dir / "e2e_receipts_v1.jsonl",
        run_receipt_path=output_dir / "grounded_e2e_run_v1.json",
        manifest_path=output_dir / "e2e_observer.manifest.json",
        question_count=len(resolved_rows),
        verified_count=sum(row["e2e_status"] == "VERIFIED" for row in receipts),
    )


__all__ = [
    "E2EObserverResult",
    "E2E_RECEIPT_PROTOCOL",
    "run_e2e_observer",
]
