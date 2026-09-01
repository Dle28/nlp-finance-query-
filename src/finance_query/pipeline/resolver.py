"""Deterministic resolver boundary for the canonical submission flow.

The historical submission builder already produces a selected proposal and a
replayable competition row.  During the migration this module converts that
legacy output into the canonical ``ResolvedPrediction`` contract.  It does
not pretend that a proposal is semantic proof: source/semantic/temporal/unit
checks remain visible and are completed only by the independent E2E observer.

The compatibility marker in the manifest is intentional.  It makes the
remaining duplicate execution in the old builder auditable while giving the
new flow one stable hand-off for the next refactor.
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

from ..e2e.core.currency_units import (
    CurrencyUnitContractError,
    validate_output_divisor,
    validate_source_multiplier,
)
from ..e2e.decimal_sandbox import (
    DecimalSandboxPolicy,
    SandboxViolation,
    execute_decimal_ast,
)
from .contracts import canonical_sha256


RESOLVED_PREDICTION_PROTOCOL = "vifinqa_resolved_prediction_v1"
RESOLVED_PREDICTIONS_FILENAME = "resolved_predictions_v1.jsonl"
RESOLVER_MANIFEST_PROTOCOL = "vifinqa_deterministic_resolver_manifest_v1"
CANONICAL_DECIMAL_BACKEND = "finance_query.e2e.decimal_sandbox.execute_decimal_ast"


@dataclass(frozen=True, slots=True)
class ResolverResult:
    """Files emitted by one immutable resolver run."""

    output_dir: Path
    predictions_path: Path
    manifest_path: Path
    question_count: int
    ready_count: int
    blocked_count: int


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_jsonl(path: Path, *, required: bool = True) -> list[dict[str, Any]]:
    if not path.is_file():
        if required:
            raise FileNotFoundError(path)
        return []
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path}:{line_number} must contain an object")
        rows.append(value)
    return rows


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


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _text(value: object) -> str:
    return str(value or "").strip()


def _is_coordinate(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _decimal(value: object) -> Decimal | None:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() else None


def _source_decimal(value: object) -> Decimal | None:
    """Parse a source-cell literal without consulting a proposal answer."""

    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    text = str(value or "").strip()
    if not text:
        return None
    text = text.replace("\u00a0", "").replace(" ", "")
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]
    text = text.replace("%", "")
    text = re.sub(r"[^0-9,.+\-]", "", text)
    if not text or text in {"+", "-"}:
        return None
    commas = text.count(",")
    dots = text.count(".")
    if commas and dots:
        decimal_separator = "," if text.rfind(",") > text.rfind(".") else "."
        thousands_separator = "." if decimal_separator == "," else ","
        text = text.replace(thousands_separator, "").replace(decimal_separator, ".")
    elif commas > 1 or dots > 1:
        text = text.replace(",", "").replace(".", "")
    elif commas or dots:
        separator = "," if commas else "."
        before, after = text.split(separator)
        text = before + after if len(after) == 3 else before + "." + after
    try:
        result = Decimal(text)
    except InvalidOperation:
        return None
    if not result.is_finite():
        return None
    return -result if negative else result


def _source_mapping(evidence: Mapping[str, Any]) -> dict[str, Any]:
    """Flatten an optional nested source object without losing direct claims."""

    nested = evidence.get("source")
    source = dict(nested) if isinstance(nested, Mapping) else {}
    source.update({key: value for key, value in evidence.items() if value is not None})
    return source


def _status(value: object, *, default: str = "UNRESOLVED") -> str:
    text = _text(value).upper()
    return text if text in {"PASS", "FAIL", "UNRESOLVED", "NOT_APPLICABLE", "DEFERRED"} else default


def _verification(diagnostic: Mapping[str, Any], audit: Mapping[str, Any]) -> Mapping[str, Any]:
    value = diagnostic.get("verification")
    if isinstance(value, Mapping):
        return value
    value = audit.get("verification")
    return value if isinstance(value, Mapping) else {}


def _source_ref(evidence: Mapping[str, Any]) -> str:
    """Hash a coordinate claim without calling it a source-file hash."""

    return canonical_sha256(
        {
            "document_id": evidence.get("document_id") or evidence.get("document_uid"),
            "internal_table_uid": evidence.get("internal_table_uid"),
            "row_index": evidence.get("row_index"),
            "column_index": evidence.get("column_index"),
        }
    )


def _evidence_for_operand(
    evidence_rows: list[Mapping[str, Any]],
    *,
    role: str | None,
    ordinal: int,
) -> Mapping[str, Any] | None:
    if role:
        for evidence in evidence_rows:
            source = _source_mapping(evidence)
            if _text(source.get("role")) == role:
                return evidence
    return evidence_rows[ordinal] if ordinal < len(evidence_rows) else None


def _operand_receipt(
    *,
    question_id: int,
    operand: Mapping[str, Any],
    evidence: Mapping[str, Any] | None,
    checks: Mapping[str, Any],
) -> dict[str, Any]:
    role = _text(operand.get("role") or operand.get("operand_id")) or "operand"
    source = _source_mapping(evidence or {})
    source_value = next(
        (
            _source_decimal(source.get(key))
            for key in (
                "canonical_value_decimal",
                "raw_value_decimal",
                "value_decimal",
                "raw_value",
                "source_value",
                "raw_source_cell",
            )
            if source.get(key) is not None and _source_decimal(source.get(key)) is not None
        ),
        None,
    )
    coordinate_ready = bool(
        source
        and _text(source.get("document_id") or source.get("document_uid"))
        and _text(source.get("internal_table_uid"))
        and _is_coordinate(source.get("row_index"))
        and _is_coordinate(source.get("column_index"))
    )
    binding_status = "BOUND_CANDIDATE" if coordinate_ready else "UNRESOLVED"
    return {
        "operand_id": _text(operand.get("operand_id")) or f"x{question_id}:{role}",
        "role": role,
        "binding_status": binding_status,
        "semantic_status": _status(checks.get("semantic_binding")),
        "temporal_status": _status(checks.get("temporal_binding")),
        "scope_entity_status": _status(checks.get("scope_entity_validation")),
        "unit_status": _status(checks.get("unit_validation")),
        "source": {
            key: source.get(key)
            for key in (
                "document_id",
                "document_uid",
                "internal_table_uid",
                "row_index",
                "column_index",
                "row_label",
            )
            if source.get(key) is not None
        },
        "coordinate_ref_sha256": _source_ref(source) if coordinate_ready else None,
        "source_value_status": "AVAILABLE" if source_value is not None else "UNRESOLVED",
        "_canonical_source_value_decimal": (
            format(source_value, "f") if source_value is not None else None
        ),
        "_canonical_source_to_vnd_multiplier": source.get("source_to_vnd_multiplier"),
        "_canonical_source_unit": source.get("source_unit"),
        "failure_reason_codes": [],
        "authority": "candidate_only_until_e2e",
    }


def _operand_claims(audit: Mapping[str, Any], evidence_rows: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    claims = _mapping(audit.get("claims"))
    raw_operands = claims.get("operands")
    if isinstance(raw_operands, list) and raw_operands:
        return [dict(value) for value in raw_operands if isinstance(value, Mapping)]
    # A direct lookup proposal may not have copied the typed operand plan.
    # Preserve one explicit positional operand rather than inventing semantics.
    return [
        {
            "operand_id": _text(_source_mapping(evidence).get("role")) or f"x{index}",
            "role": _text(_source_mapping(evidence).get("role")) or f"x{index}",
        }
        for index, evidence in enumerate(evidence_rows)
    ]


def _canonicalize_ast(node: object, *, role_to_operand_id: Mapping[str, str]) -> object:
    if isinstance(node, str):
        return role_to_operand_id.get(node, node)
    if isinstance(node, list):
        return [_canonicalize_ast(value, role_to_operand_id=role_to_operand_id) for value in node]
    if isinstance(node, Mapping):
        return {
            str(key): _canonicalize_ast(value, role_to_operand_id=role_to_operand_id)
            for key, value in node.items()
        }
    return node


def _ast_references(node: object) -> set[str]:
    """Return operand leaves while ignoring scalar-literal metadata."""

    if isinstance(node, str):
        return {node}
    if isinstance(node, list):
        result: set[str] = set()
        for value in node:
            result.update(_ast_references(value))
        return result
    if isinstance(node, Mapping):
        if node.get("kind") == "dimensionless_scalar":
            return set()
        args = node.get("args")
        return _ast_references(args) if isinstance(args, list) else set()
    return set()


def _requested_output_unit(
    audit: Mapping[str, Any],
    diagnostic: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> Mapping[str, Any] | None:
    for source in (audit, diagnostic, candidate):
        for key in ("requested_output_unit", "output_unit"):
            value = source.get(key)
            if isinstance(value, Mapping):
                return value
    return None


def _canonical_decimal_reexecute(
    *,
    operation_ast: Mapping[str, Any],
    operands: list[Mapping[str, Any]],
    requested_output_unit: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Execute a source-bound AST using the existing Decimal sandbox.

    Resolver input is a proposal-side handoff, not the complete V2 binding
    packet.  This function therefore proves only deterministic arithmetic over
    source-preserved literals.  Semantic source closure and authorization stay
    with the independent E2E observer.
    """

    ast = dict(operation_ast)
    if (
        set(ast) != {"op", "args"}
        or not isinstance(ast.get("op"), str)
        or not isinstance(ast.get("args"), list)
    ):
        return {
            "status": "BLOCKED",
            "canonical_decimal_reexecution": False,
            "failure_reason_codes": ["OPERATION_AST_INVALID"],
            "operation_ast_sha256": canonical_sha256(ast),
        }
    role_pairs = [
        (_text(operand.get("role")), _text(operand.get("operand_id")))
        for operand in operands
        if _text(operand.get("role")) and _text(operand.get("operand_id"))
    ]
    if len({role for role, _operand_id in role_pairs}) != len(role_pairs):
        return {
            "status": "BLOCKED",
            "canonical_decimal_reexecution": False,
            "failure_reason_codes": ["DUPLICATE_OPERAND_ROLE"],
            "operation_ast_sha256": canonical_sha256(ast),
        }
    role_to_operand_id = dict(role_pairs)
    canonical_ast = _canonicalize_ast(ast, role_to_operand_id=role_to_operand_id)
    operand_ids = {
        _text(operand.get("operand_id"))
        for operand in operands
        if _text(operand.get("operand_id"))
    }
    references = _ast_references(canonical_ast)
    reasons: list[str] = []
    if len(operand_ids) != len(operands):
        reasons.append("DUPLICATE_OPERAND_ID")
    if references - operand_ids:
        reasons.append("OPERATION_AST_UNKNOWN_OPERAND")
    if operand_ids - references:
        reasons.append("DECLARED_OPERAND_NOT_USED")
    if not operands:
        reasons.append("OPERAND_SOURCE_MISSING")
    if reasons:
        return {
            "status": "BLOCKED",
            "canonical_decimal_reexecution": False,
            "failure_reason_codes": sorted(set(reasons)),
            "operation_ast_sha256": canonical_sha256(canonical_ast),
            "canonical_operation_ast": canonical_ast,
        }

    policy = DecimalSandboxPolicy()
    values: dict[str, Decimal] = {}
    normalization: list[dict[str, Any]] = []
    for operand in operands:
        operand_id = _text(operand.get("operand_id"))
        source = _mapping(operand.get("source"))
        if (
            operand.get("binding_status") != "BOUND_CANDIDATE"
            or not _text(source.get("document_id") or source.get("document_uid"))
            or not _text(source.get("internal_table_uid"))
            or not _is_coordinate(source.get("row_index"))
            or not _is_coordinate(source.get("column_index"))
        ):
            reasons.append(f"SOURCE_COORDINATE_MISSING:{operand_id}")
            continue
        raw = _source_decimal(
            operand.get("_canonical_source_value_decimal", operand.get("source_value_decimal"))
        )
        if raw is None:
            reasons.append(f"SOURCE_VALUE_MISSING:{operand_id}")
            continue
        multiplier = _source_decimal(
            operand.get(
                "_canonical_source_to_vnd_multiplier",
                source.get("source_to_vnd_multiplier"),
            )
        )
        if multiplier is None:
            multiplier = Decimal("1")
        source_unit = _text(
            operand.get("_canonical_source_unit", source.get("source_unit"))
        )
        if source_unit:
            try:
                multiplier = validate_source_multiplier(
                    source_unit=source_unit,
                    multiplier=multiplier,
                )
            except CurrencyUnitContractError:
                reasons.append(f"SOURCE_UNIT_CONTRACT_INVALID:{operand_id}")
                continue
        elif multiplier != Decimal("1"):
            reasons.append(f"SOURCE_UNIT_CONTRACT_MISSING:{operand_id}")
            continue
        try:
            if multiplier == Decimal("1"):
                normalized = raw
                telemetry = None
            else:
                result = execute_decimal_ast(
                    {"op": "multiply", "args": ["raw", "multiplier"]},
                    {"raw": raw, "multiplier": multiplier},
                    policy=policy,
                )
                normalized = result.value
                telemetry = result.telemetry()
        except (SandboxViolation, InvalidOperation, TypeError, ValueError):
            reasons.append(f"SOURCE_NORMALIZATION_REJECTED:{operand_id}")
            continue
        values[operand_id] = normalized
        normalization.append(
            {
                "operand_id": operand_id,
                "source_value_status": "AVAILABLE",
                "sandbox": telemetry,
            }
        )
    if reasons:
        return {
            "status": "BLOCKED",
            "canonical_decimal_reexecution": False,
            "failure_reason_codes": sorted(set(reasons)),
            "operation_ast_sha256": canonical_sha256(canonical_ast),
            "canonical_operation_ast": canonical_ast,
            "operand_normalization": normalization,
        }
    try:
        formula_result = execute_decimal_ast(canonical_ast, values, policy=policy)
        output_result = formula_result
        output_divisor = None
        if requested_output_unit is not None and requested_output_unit.get("kind") == "currency":
            output_divisor = validate_output_divisor(requested_output_unit)
            output_result = execute_decimal_ast(
                {"op": "divide", "args": ["base", "divisor"]},
                {"base": formula_result.value, "divisor": output_divisor},
                policy=policy,
            )
    except (
        CurrencyUnitContractError,
        SandboxViolation,
        InvalidOperation,
        TypeError,
        ValueError,
        ArithmeticError,
    ) as error:
        return {
            "status": "BLOCKED",
            "canonical_decimal_reexecution": False,
            "failure_reason_codes": [
                f"CANONICAL_DECIMAL_EXECUTION_REJECTED:{type(error).__name__}"
            ],
            "operation_ast_sha256": canonical_sha256(canonical_ast),
            "canonical_operation_ast": canonical_ast,
            "operand_normalization": normalization,
        }
    return {
        "status": "PASS",
        "canonical_decimal_reexecution": True,
        "answer_decimal": format(output_result.value, "f"),
        "operation_ast_sha256": formula_result.ast_sha256,
        "canonical_operation_ast": canonical_ast,
        "operand_normalization": normalization,
        "formula_sandbox": formula_result.telemetry(),
        "output_conversion_sandbox": (
            output_result.telemetry() if output_result is not formula_result else None
        ),
        "output_conversion_applied": output_divisor is not None,
        "backend": CANONICAL_DECIMAL_BACKEND,
    }


def _build_prediction(
    *,
    question: Mapping[str, Any],
    diagnostic: Mapping[str, Any],
    audit: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> dict[str, Any]:
    question_id = int(question["id"])
    checks = _mapping(_verification(diagnostic, audit).get("checks"))
    evidence_rows = [
        value for value in audit.get("evidence") or [] if isinstance(value, Mapping)
    ]
    proposal_answer = _decimal(audit.get("answer_decimal"))
    answer = proposal_answer
    operation_ast = audit.get("operation_ast")
    if not isinstance(operation_ast, Mapping):
        operation_ast = {}
    operand_claims = _operand_claims(audit, evidence_rows)
    operands = [
        _operand_receipt(
            question_id=question_id,
            operand=operand,
            evidence=_evidence_for_operand(
                evidence_rows,
                role=_text(operand.get("role") or operand.get("operand_id")) or None,
                ordinal=index,
            ),
            checks=checks,
        )
        for index, operand in enumerate(operand_claims)
    ]
    if not operands and evidence_rows:
        operands = [
            _operand_receipt(
                question_id=question_id,
                operand={"operand_id": f"x{index}", "role": f"x{index}"},
                evidence=evidence,
                checks=checks,
            )
            for index, evidence in enumerate(evidence_rows)
        ]
    requested_output_unit = _requested_output_unit(audit, diagnostic, candidate)
    canonical_execution = _canonical_decimal_reexecute(
        operation_ast=operation_ast,
        operands=operands,
        requested_output_unit=requested_output_unit,
    )
    serialized_operands = [
        {key: value for key, value in operand.items() if not key.startswith("_")}
        for operand in operands
    ]
    canonical_answer = _decimal(canonical_execution.get("answer_decimal"))
    answer_origin = "legacy_proposal_claim" if proposal_answer is not None else None
    resolution_reasons = list(canonical_execution.get("failure_reason_codes") or [])
    if canonical_execution.get("status") == "PASS":
        if answer is None and canonical_answer is not None:
            answer = canonical_answer
            answer_origin = "canonical_decimal_reexecution"
        elif answer is not None and canonical_answer is not None and answer != canonical_answer:
            resolution_reasons.append("CANONICAL_RESULT_MISMATCH_PROPOSAL")
            canonical_execution = {
                **canonical_execution,
                "status": "REJECTED",
                "failure_reason_codes": sorted(set(resolution_reasons)),
                "candidate_answer_decimal": format(answer, "f"),
            }
    formula_status = _status(checks.get("formula_contract"))
    compatibility_status = _status(checks.get("operand_compatibility"))
    hard_failure = any(
        _text(value.get("reason")) in {"PROVENANCE_BROKEN", "RESULT_INCONSISTENT"}
        for value in _verification(diagnostic, audit).get("failures") or []
        if isinstance(value, Mapping)
    )
    if answer is None:
        resolution_reasons.append("PROPOSAL_ANSWER_MISSING")
    if not operands:
        resolution_reasons.append("OPERAND_SOURCE_MISSING")
    if hard_failure:
        resolution_reasons.append("LEGACY_PROPOSAL_VERIFICATION_FAILURE")
    if canonical_execution.get("status") != "PASS":
        resolution_reasons.append("CANONICAL_REEXECUTION_NOT_PASSED")
    resolution_status = "RESOLVED_CANDIDATE" if not resolution_reasons else "BLOCKED"
    claims = _mapping(audit.get("claims"))
    documents = sorted(
        {
            _text(
                _source_mapping(value).get("document_id")
                or _source_mapping(value).get("document_uid")
            )
            for value in evidence_rows
            if _text(
                _source_mapping(value).get("document_id")
                or _source_mapping(value).get("document_uid")
            )
        }
    )
    payload: dict[str, Any] = {
        "schema_version": 1,
        "protocol": RESOLVED_PREDICTION_PROTOCOL,
        "question_id": question_id,
        "proposal_id": audit.get("proposal_id") or candidate.get("proposal_id"),
        "source_scope": {
            "source_universe": documents,
            "source_scope_status": "DECLARED_FROM_PROPOSAL_EVIDENCE",
            "source_scope_authority": "candidate_only_until_e2e",
        },
        "reporting_scope": claims.get("reporting_scope"),
        "proposal": {
            "answer_route": audit.get("answer_route") or diagnostic.get("tier"),
            "operation_ast": dict(operation_ast),
            "claims": dict(claims),
            "pandas_query": audit.get("pandas_query") or diagnostic.get("pandas_query"),
            "candidate_id": candidate.get("candidate_id"),
            "answer_origin": answer_origin,
        },
        "answer_decimal": format(answer, "f") if answer is not None else None,
        "operand_receipts": serialized_operands,
        "formula_receipt": {
            "contract_status": formula_status,
            "compatibility_status": compatibility_status,
            "operation_ast_sha256": canonical_execution.get(
                "operation_ast_sha256", canonical_sha256(dict(operation_ast))
            ),
            "canonical_ast_status": (
                "PASS" if canonical_execution.get("canonical_operation_ast") else "UNRESOLVED"
            ),
            "verification_backend": CANONICAL_DECIMAL_BACKEND,
        },
        "execution_receipt": {
            **canonical_execution,
            "answer_decimal": canonical_execution.get("answer_decimal"),
            "candidate_answer_decimal": (
                format(proposal_answer, "f") if proposal_answer is not None else None
            ),
            "execution_method": (
                CANONICAL_DECIMAL_BACKEND
                if canonical_execution.get("canonical_decimal_reexecution")
                else "not_run_fail_closed"
            ),
            "legacy_claim_used_for_execution": False,
        },
        "resolution_status": resolution_status,
        "resolution_failure_reason_codes": sorted(set(resolution_reasons)),
        "e2e_status": "NOT_RUN",
        "authority_boundary": "resolved_candidate_not_authorized_until_e2e",
    }
    return {**payload, "resolved_prediction_id": canonical_sha256(payload)}


def resolve_submission_artifacts(
    submission_dir: Path,
    *,
    output_dir: Path,
) -> ResolverResult:
    """Convert one compatibility submission build into ResolvedPredictions."""

    submission_dir = submission_dir.resolve()
    output_dir = output_dir.resolve()
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite resolver output: {output_dir}")
    questions = _index(_load_json(submission_dir / "submission.json"), key="id", label="submission")
    diagnostics = _index(_load_jsonl(submission_dir / "diagnostics.jsonl"), key="id", label="diagnostics")
    audits = _index(
        _load_jsonl(submission_dir / "prediction_audit_ledger_v1.jsonl"),
        key="question_id",
        label="proposal audit",
    )
    candidates = _index(
        _load_jsonl(submission_dir / "best_surviving_candidates_v1.jsonl", required=False),
        key="question_id",
        label="candidate ledger",
    ) if (submission_dir / "best_surviving_candidates_v1.jsonl").is_file() else {}
    if set(questions) != set(diagnostics):
        raise ValueError("submission and diagnostics must cover the same question ids")
    records = [
        _build_prediction(
            question=questions[question_id],
            diagnostic=diagnostics[question_id],
            audit=audits.get(question_id, {}),
            candidate=candidates.get(question_id, {}),
        )
        for question_id in sorted(questions)
    ]

    input_names = (
        "submission.json",
        "diagnostics.jsonl",
        "prediction_audit_ledger_v1.jsonl",
        "best_surviving_candidates_v1.jsonl",
        "build_report.json",
    )
    inputs: dict[str, Any] = {}
    for name in input_names:
        path = submission_dir / name
        if path.is_file():
            inputs[name] = {"path": str(path), "sha256": _sha256_file(path)}
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.tmp-", dir=output_dir.parent))
    try:
        predictions_path = staging / RESOLVED_PREDICTIONS_FILENAME
        predictions_path.write_text(
            "".join(
                json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
                for value in records
            ),
            encoding="utf-8",
        )
        status_counts = Counter(str(value["resolution_status"]) for value in records)
        canonical_status_counts = Counter(
            str(_mapping(value.get("execution_receipt")).get("status") or "UNRESOLVED")
            for value in records
        )
        canonical_reexecuted_count = sum(
            1
            for value in records
            if _mapping(value.get("execution_receipt")).get("canonical_decimal_reexecution") is True
        )
        if not records:
            canonical_reexecution_status = "NOT_RUN"
        elif canonical_reexecuted_count == len(records) and all(
            status == "PASS" for status in canonical_status_counts
        ):
            canonical_reexecution_status = "COMPLETE"
        elif canonical_reexecuted_count:
            canonical_reexecution_status = "PARTIAL"
        else:
            canonical_reexecution_status = "BLOCKED"
        operand_counts = Counter(
            str(operand["binding_status"])
            for value in records
            for operand in value["operand_receipts"]
        )
        manifest = {
            "schema_version": 1,
            "protocol": RESOLVER_MANIFEST_PROTOCOL,
            "inputs": inputs,
            "outputs": {
                "resolved_predictions": {
                    "path": str(output_dir / predictions_path.name),
                    "sha256": _sha256_file(predictions_path),
                }
            },
            "counts": {
                "question_count": len(records),
                "resolution_status_counts": dict(sorted(status_counts.items())),
                "operand_binding_status_counts": dict(sorted(operand_counts.items())),
                "canonical_execution_status_counts": dict(sorted(canonical_status_counts.items())),
                "canonical_reexecuted_count": canonical_reexecuted_count,
            },
            "authority": {
                "resolved_prediction_is_authoritative": False,
                "e2e_required": True,
                "submission_compiler_must_consume_e2e_receipt": True,
            },
            "migration": {
                "backend": "legacy_submission_builder_adapter",
                "duplicate_execution_legacy": True,
                "legacy_claim_used_for_canonical_proof": False,
                "legacy_pandas_query_used_for_canonical_execution": False,
                "canonical_decimal_reexecution": canonical_reexecuted_count > 0,
                "canonical_decimal_reexecution_status": canonical_reexecution_status,
                "canonical_decimal_backend": CANONICAL_DECIMAL_BACKEND,
                "next_refactor": "move_formula_hydration_and_decimal_execution_here",
            },
        }
        manifest_path = staging / "resolver_run.manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        staging.rename(output_dir)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return ResolverResult(
        output_dir=output_dir,
        predictions_path=output_dir / RESOLVED_PREDICTIONS_FILENAME,
        manifest_path=output_dir / "resolver_run.manifest.json",
        question_count=len(records),
        ready_count=sum(value["resolution_status"] == "RESOLVED_CANDIDATE" for value in records),
        blocked_count=sum(value["resolution_status"] == "BLOCKED" for value in records),
    )


__all__ = [
    "CANONICAL_DECIMAL_BACKEND",
    "RESOLVED_PREDICTION_PROTOCOL",
    "RESOLVED_PREDICTIONS_FILENAME",
    "ResolverResult",
    "resolve_submission_artifacts",
]
