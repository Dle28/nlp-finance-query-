#!/usr/bin/env python3
"""Materialize exact-answer execution evidence from V4 reviews and FormulaSet.

This is the first answer-generation stage, not a fallback answer guesser.  It
only emits an executable record when V4's independent agents selected a
``machine_calibrated`` direct-lookup candidate and its exact V2 row/cell still
matches the immutable raw-HTML sidecar.  Every other question is retained in
the ledger with an explicit non-executable reason; it cannot enter a final
submission through ``compile_vifinqa_submission.py``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from finance_query.binding import row_label  # noqa: E402
from finance_query.corpus import infer_unit  # noqa: E402
from finance_query.execution import convert_unit, execute_ast, parse_decimal  # noqa: E402
from finance_query.financial_metrics import fold_text  # noqa: E402
from finance_query.evidence_context import (  # noqa: E402
    AUTONOMOUS_REVIEW_PROTOCOL,
    validate_evidence_context_sidecar,
)
from finance_query.schemas import DirectBinding  # noqa: E402
from finance_query.table_structure import validate_structure_sidecar  # noqa: E402


EQUIVALENT_CRITIC_POLICY = "strict_equivalent_critic_answer"
EQUIVALENT_CRITIC_MIN_SEMANTIC = 0.90
EQUIVALENT_CRITIC_MIN_EVIDENCE = 0.85
EQUIVALENT_CRITIC_MARGIN = Decimal("0.05")
EQUIVALENT_CRITIC_TIE_EPSILON = Decimal("0.000000000001")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle-dir", type=Path, required=True)
    parser.add_argument("--machine-reviews", type=Path, required=True)
    parser.add_argument(
        "--evidence-context",
        type=Path,
        default=None,
        help="Canonical context sidecar; defaults to tables_evidence_context_v3.jsonl in the bundle.",
    )
    parser.add_argument(
        "--formula-evidence",
        type=Path,
        default=None,
        help=(
            "Audited Formula EvidenceSet V3. Only complete, defined allow-listed "
            "sets may add exact_formula execution records; the controlled CFO period "
            "argmax record is shadow-only and cannot enter a submission."
        ),
    )
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8-sig") as file:
        return [json.loads(line) for line in file if line.strip()]


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")
        file.flush()
        os.fsync(file.fileno())
    temporary.replace(path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_formula_evidence_sidecar(
    bundle: Path,
    sidecar: Path,
    context_path: Path,
) -> dict[str, Any]:
    """Accept only the numeric-safe, source-discovery Formula EvidenceSet V3."""
    if sidecar.parent != bundle:
        raise ValueError("Formula evidence sidecar must reside in the review bundle")
    manifest_path = sidecar.with_suffix(".manifest.json")
    if not sidecar.is_file() or not manifest_path.is_file():
        raise FileNotFoundError("Formula evidence sidecar or manifest is missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if int(manifest.get("schema_version") or 0) != 3:
        raise ValueError("Formula execution requires Formula EvidenceSet schema_version=3")
    expected = {
        "bundle_review_items_sha256": bundle / "review_items.jsonl",
        "bundle_tables_sha256": bundle / "tables.jsonl",
        "structured_tables_sha256": bundle / "tables_structured_v2.jsonl",
        "evidence_context_sha256": context_path,
    }
    for key, source_path in expected.items():
        if str(manifest.get(key) or "") != sha256_file(source_path):
            raise ValueError(f"Formula evidence manifest does not match {source_path.name}")
    if str(manifest.get("evidence_context_file") or "") != context_path.name:
        raise ValueError("Formula evidence manifest names a different canonical context")
    if str(manifest.get("numeric_binding_policy") or "") != "one_reliable_raw_v2_number_per_operand":
        raise ValueError("Formula evidence lacks the strict numeric binding policy")
    discovery = manifest.get("source_discovery") or {}
    if not bool(discovery.get("enabled")):
        raise ValueError("Formula execution requires source-discovery provenance")
    if str(manifest.get("sidecar_sha256") or "") != sha256_file(sidecar):
        raise ValueError("Formula evidence sidecar hash does not match its manifest")
    return manifest


def index_by_id(rows: Iterable[dict[str, Any]], name: str) -> dict[int, dict[str, Any]]:
    output: dict[int, dict[str, Any]] = {}
    for row in rows:
        qid = int(row["id"])
        if qid in output:
            raise ValueError(f"Duplicate Q{qid} in {name}")
        output[qid] = row
    return output


def load_v2_tables(bundle: Path) -> dict[str, dict[str, Any]]:
    base = {
        str(row["internal_table_uid"]): row for row in load_jsonl(bundle / "tables.jsonl")
    }
    sidecar = bundle / "tables_structured_v2.jsonl"
    validate_structure_sidecar(bundle, sidecar)
    tables: dict[str, dict[str, Any]] = {}
    for row in load_jsonl(sidecar):
        uid = str(row.get("internal_table_uid") or "")
        if uid not in base:
            raise ValueError(f"V2 sidecar UID absent from bundle: {uid}")
        if str((row.get("structure_quality") or {}).get("status") or "") != "reconstructed_from_raw_html":
            continue
        tables[uid] = {**base[uid], **row}
    return tables


def load_evidence_contexts(bundle: Path, context_path: Path) -> dict[str, dict[str, Any]]:
    structured = bundle / "tables_structured_v2.jsonl"
    validate_evidence_context_sidecar(bundle, structured, context_path)
    contexts = {
        str(row["internal_table_uid"]): row for row in load_jsonl(context_path)
    }
    if not contexts:
        raise ValueError("Canonical evidence-context sidecar is empty")
    return contexts


def canonical_unit(table: dict[str, Any]) -> str | None:
    unit = table.get("unit_hint")
    if isinstance(unit, str) and unit:
        return unit
    context = " ".join(
        [
            str(table.get("context_before") or ""),
            str((table.get("context_trace") or {}).get("source_title") or ""),
            " ".join((table.get("context_trace") or {}).get("unit_labels") or []),
        ]
    )
    rows = table.get("rows") or []
    return infer_unit(context, "\n".join(" | ".join(map(str, row)) for row in rows))


def _non_executable(item: dict[str, Any], review: dict[str, Any] | None, reason: str) -> dict[str, Any]:
    return {
        "id": int(item["id"]),
        "provenance_status": str((review or {}).get("consensus_status") or "needs_human"),
        "execution_status": "not_executable",
        "grounding_status": "not_bound",
        "execution_mode": None,
        "reason": reason,
        "operation_ast": (item.get("question_plan") or {}).get("operation_ast") or {},
        "operand_bindings": [],
    }


def _profile_for_row(context: dict[str, Any], row_index: int) -> dict[str, Any]:
    return next(
        (
            profile
            for profile in context.get("row_profiles") or []
            if isinstance(profile.get("row_index"), int)
            and not isinstance(profile.get("row_index"), bool)
            and profile["row_index"] == row_index
        ),
        {},
    )


def _candidate_score(assessment: dict[str, Any]) -> Decimal:
    return (
        Decimal("0.55") * Decimal(str(assessment.get("semantic_score") or 0))
        + Decimal("0.35") * Decimal(str(assessment.get("evidence_score") or 0))
        + Decimal("0.10") * Decimal(str(assessment.get("metadata_score") or 0))
    )


def _has_tied_best(assessments: list[dict[str, Any]], key: str) -> bool:
    if len(assessments) < 2:
        return False
    scores = sorted(
        (Decimal(str(assessment.get(key) or 0)) for assessment in assessments),
        reverse=True,
    )
    return abs(scores[0] - scores[1]) <= EQUIVALENT_CRITIC_TIE_EPSILON


def _revalidate_equivalent_critic_policy(
    review: dict[str, Any],
    self_review: dict[str, Any],
    selected_table: dict[str, Any],
    selected_context: dict[str, Any],
    *,
    selected_uid: str,
    selected_row_index: int,
    selected_column_index: int,
    selected_parsed_value: str,
    selected_source_unit: str,
    tables: dict[str, dict[str, Any]],
    contexts: dict[str, dict[str, Any]],
) -> bool:
    """Reprove V4's critic-equivalence exception directly from V2 source.

    This exception is executable only when the review's whole near-tie set is
    recorded and each alternate still resolves to the same numeric V2 cell
    value in the same source unit.  It intentionally does not trust a review
    status or a natural-language explanation alone.
    """
    if str(self_review.get("selection_policy") or "") != EQUIVALENT_CRITIC_POLICY:
        return False
    if bool(self_review.get("critic_accepts")):
        return False
    selected_assessment = self_review.get("selected_assessment") or {}
    assessments = self_review.get("candidate_assessments") or []
    if (
        str(selected_assessment.get("uid") or "") != selected_uid
        or not bool((selected_assessment.get("raw_metric_identity") or {}).get("exact"))
        or Decimal(str(selected_assessment.get("semantic_score") or 0))
        < Decimal(str(EQUIVALENT_CRITIC_MIN_SEMANTIC))
        or Decimal(str(selected_assessment.get("evidence_score") or 0))
        < Decimal(str(EQUIVALENT_CRITIC_MIN_EVIDENCE))
        or Decimal(str(selected_assessment.get("source_score") or 0))
        < Decimal("1") - EQUIVALENT_CRITIC_TIE_EPSILON
        or Decimal(str(selected_assessment.get("metadata_score") or 0))
        < Decimal("1") - EQUIVALENT_CRITIC_TIE_EPSILON
        or not isinstance(assessments, list)
    ):
        return False
    selected_binding = selected_assessment.get("value_binding") or {}
    if (
        selected_binding.get("status") != "cell_bound"
        or selected_binding.get("row_index") != selected_row_index
        or selected_binding.get("column_index") != selected_column_index
    ):
        return False
    if not _has_tied_best(assessments, "source_score") or not _has_tied_best(
        assessments, "metadata_score"
    ):
        return False
    votes = review.get("agent_votes") or {}
    if not all(
        str(votes.get(agent) or "") == selected_uid
        for agent in ("semantic_agent", "evidence_agent", "challenger_agent")
    ):
        return False
    selected_score = _candidate_score(selected_assessment)
    expected = {
        str(assessment.get("uid") or "")
        for assessment in assessments
        if str(assessment.get("uid") or "") != selected_uid
        and selected_score - _candidate_score(assessment) < EQUIVALENT_CRITIC_MARGIN
    }
    recorded = self_review.get("equivalent_critic_alternatives") or []
    if not expected or not isinstance(recorded, list):
        return False
    recorded_by_uid = {
        str(alternative.get("internal_table_uid") or ""): alternative
        for alternative in recorded
        if isinstance(alternative, dict) and str(alternative.get("internal_table_uid") or "")
    }
    if set(recorded_by_uid) != expected or len(recorded_by_uid) != len(recorded):
        return False

    # The selected table/context arguments have already passed the regular
    # direct-execution revalidation.  Keep explicit references here to make
    # the equality contract clear and avoid accepting a disconnected audit.
    if tables.get(selected_uid) is not selected_table or contexts.get(selected_uid) is not selected_context:
        return False
    for uid, alternative in recorded_by_uid.items():
        table, context = tables.get(uid), contexts.get(uid)
        if table is None or context is None:
            return False
        row_index, column_index = alternative.get("row_index"), alternative.get("column_index")
        rows = table.get("rows") or []
        if (
            not isinstance(row_index, int)
            or isinstance(row_index, bool)
            or not isinstance(column_index, int)
            or isinstance(column_index, bool)
            or not 0 <= row_index < len(rows)
            or not 0 <= column_index < len(rows[row_index])
        ):
            return False
        profile = _profile_for_row(context, row_index)
        if str(profile.get("role") or "") != "data" or column_index not in {
            int(value) for value in profile.get("numeric_columns") or []
        }:
            return False
        raw_value = str(rows[row_index][column_index])
        if raw_value != str(alternative.get("raw_value") or ""):
            return False
        if row_label(rows[row_index]) != str(alternative.get("raw_row_label") or ""):
            return False
        headers = (context.get("canonical_headers") or {}).get("columns") or []
        column_label = str(
            next(
                (
                    column.get("source_label")
                    for column in headers
                    if isinstance(column.get("column_index"), int)
                    and not isinstance(column.get("column_index"), bool)
                    and column["column_index"] == column_index
                ),
                "",
            )
            or ""
        )
        if column_label != str(alternative.get("column_label") or ""):
            return False
        provenance_rows = table.get("cell_provenance") or []
        if (
            row_index >= len(provenance_rows)
            or column_index >= len(provenance_rows[row_index] or [])
            or provenance_rows[row_index][column_index] != alternative.get("source_cell")
        ):
            return False
        parsed = parse_decimal(raw_value)
        if (
            parsed.value is None
            or any(warning != "percent_value_not_scaled" for warning in parsed.warnings)
            or parsed.value != str(alternative.get("parsed_value") or "")
            or parsed.value != selected_parsed_value
        ):
            return False
        source_unit = canonical_unit(table)
        if (
            source_unit != selected_source_unit
            or source_unit != str(alternative.get("source_unit") or "")
        ):
            return False
    return True


def _formula_binding(
    match: dict[str, Any],
    table: dict[str, Any],
    context: dict[str, Any],
    *,
    confidence: float,
) -> DirectBinding | None:
    """Revalidate one stored Formula EvidenceSet match against raw V2 again."""
    binding = match.get("binding") or {}
    row_index, column_index = binding.get("row_index"), binding.get("column_index")
    rows = table.get("rows") or []
    if (
        str(binding.get("status") or "") != "cell_bound"
        or not isinstance(row_index, int)
        or not isinstance(column_index, int)
        or not 0 <= row_index < len(rows)
        or not 0 <= column_index < len(rows[row_index])
    ):
        return None
    profile = _profile_for_row(context, row_index)
    if str(profile.get("role") or "") != "data" or column_index not in {
        int(value) for value in profile.get("numeric_columns") or []
    }:
        return None
    raw_value = str(rows[row_index][column_index])
    if raw_value != str(binding.get("raw_value") or ""):
        return None
    if [str(value) for value in rows[row_index]] != [
        str(value) for value in match.get("source_row") or []
    ]:
        return None
    headers = (context.get("canonical_headers") or {}).get("columns") or []
    column_label = str(
        next(
            (
                column.get("source_label")
                for column in headers
                if isinstance(column.get("column_index"), int)
                and not isinstance(column.get("column_index"), bool)
                and column["column_index"] == column_index
            ),
            "",
        )
        or ""
    )
    if not column_label or column_label != str(binding.get("column_label") or ""):
        return None
    provenance_rows = table.get("cell_provenance") or []
    if row_index >= len(provenance_rows) or column_index >= len(provenance_rows[row_index] or []):
        return None
    provenance = provenance_rows[row_index][column_index]
    if binding.get("source_cell") != provenance:
        return None
    parsed = parse_decimal(raw_value)
    if parsed.value is None or any(
        warning != "percent_value_not_scaled" for warning in parsed.warnings
    ):
        return None
    if str(binding.get("parsed_value") or "") != parsed.value:
        return None
    source_unit = canonical_unit(table)
    if source_unit is None:
        return None
    return DirectBinding(
        internal_table_uid=str(table["internal_table_uid"]),
        document_id=str(table["document_id"]),
        row_index=row_index,
        column_index=column_index,
        row_text=row_label(rows[row_index]),
        column_text=column_label,
        raw_value=raw_value,
        parsed_value=parsed.value,
        source_unit=source_unit,
        target_unit=None,
        converted_value=parsed.value,
        binding_score=confidence,
        warnings=list(parsed.warnings),
    )


def _operating_cash_flow_argmax_period_execution(
    item: dict[str, Any],
    evidence: dict[str, Any],
    formula: dict[str, Any],
    tables: dict[str, dict[str, Any]],
    contexts: dict[str, dict[str, Any]],
    *,
    manifest: dict[str, Any],
    confidence: float,
    review: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Execute one declared single-entity CFO argmax only after all raw gates.

    This is not a generic ranking executor. It accepts exactly the controlled
    formula emitted for questions such as "which listed year had the highest
    operating cash flow", and keeps the review state unchanged. The result is
    an auditable execution-ledger value, not a training label.
    """
    entity = str(formula.get("entity") or "")
    operands = list(formula.get("operands") or [])
    selected = evidence.get("selected_operand_matches") or {}
    if not entity or len(operands) < 2 or set(selected) != {
        str(operand.get("operand_id") or "") for operand in operands
    }:
        return None
    years: set[int] = set()
    bindings: dict[str, DirectBinding] = {}
    scopes: set[str] = set()
    for operand in operands:
        operand_id = str(operand.get("operand_id") or "")
        operand_years = [value for value in operand.get("years") or [] if isinstance(value, int)]
        if (
            not operand_id
            or operand.get("role") != "period_argmax_value"
            or str(operand.get("entity") or "") != entity
            or operand.get("allowed_table_functions") != ["cash_flow_statement"]
            or len(operand_years) != 1
            or operand_years[0] in years
        ):
            return None
        year = operand_years[0]
        years.add(year)
        match = selected.get(operand_id) or {}
        uid = str(match.get("internal_table_uid") or "")
        table, context = tables.get(uid), contexts.get(uid)
        if table is None or context is None:
            return None
        if (
            str(table.get("ticker") or "") != entity
            or int(table.get("report_year") or 0) != year
            or str((context.get("table_function") or {}).get("kind") or "")
            != "cash_flow_statement"
        ):
            return None
        scope = str(table.get("scope") or "")
        if not scope or scope != str(match.get("scope") or ""):
            return None
        scopes.add(scope)
        binding = _formula_binding(match, table, context, confidence=confidence)
        if binding is None or str(year) not in binding.column_text:
            return None
        if "luu chuyen tien thuan tu hoat dong kinh doanh" not in fold_text(binding.row_text):
            return None
        bindings[operand_id] = binding
    if len(scopes) != 1 or len({binding.source_unit for binding in bindings.values()}) != 1:
        return None
    values = {
        operand_id: Decimal(binding.parsed_value)
        for operand_id, binding in bindings.items()
    }
    maximum = max(values.values())
    winners = [operand_id for operand_id, value in values.items() if value == maximum]
    if len(winners) != 1:
        return None
    winner = winners[0]
    winner_year = next(
        int(operand["years"][0]) for operand in operands if operand["operand_id"] == winner
    )
    operation_ast = {"op": "argmax_year", "args": [str(operand["operand_id"]) for operand in operands]}
    return {
        "id": int(item["id"]),
        "provenance_status": "machine_calibrated",
        "execution_status": "grounded",
        "grounding_status": "exact_rows_validated",
        "execution_mode": "exact_formula_period_argmax",
        "formula_definition_status": "defined",
        "operation_ast": operation_ast,
        "normalize_operands_to_vnd": False,
        "submission_eligible": False,
        "operand_bindings": [
            {
                "operand_id": operand_id,
                "internal_table_uid": binding.internal_table_uid,
                "binding": binding.to_dict(),
            }
            for operand_id, binding in bindings.items()
        ],
        "formula_provenance": {
            "protocol": "formula_evidence_v3_exact_operating_cash_flow_argmax_period",
            "formula_id": formula["formula_id"],
            "formula_confidence": confidence,
            "scope": next(iter(scopes)),
            "review_consensus_status": str((review or {}).get("consensus_status") or "not_used"),
            "review_status_promoted": False,
            "formula_evidence_sha256": manifest["sidecar_sha256"],
            "evidence_context_file": manifest["evidence_context_file"],
            "source_discovery": evidence.get("source_discovery") or {},
        },
        "computed_answer": str(winner_year),
    }


def exact_formula_execution_row(
    item: dict[str, Any],
    evidence: dict[str, Any] | None,
    tables: dict[str, dict[str, Any]],
    contexts: dict[str, dict[str, Any]],
    *,
    manifest: dict[str, Any],
    review: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Materialize a controlled exact Formula EvidenceSet execution.

    This is intentionally narrower than Formula EvidenceSet collection. It
    requires complete evidence, a defined formula, no outstanding reason code,
    one exact V2 number per operand and identical known source units. Only an
    explicitly enumerated formula implementation can proceed; all generic
    selection/ranking programs remain evidence-only.
    """
    if not evidence or int(evidence.get("id") or -1) != int(item["id"]):
        return None
    formula = evidence.get("formula") or {}
    try:
        confidence = float(formula.get("confidence"))
    except (TypeError, ValueError):
        return None
    if (
        str(evidence.get("evidence_completeness") or "") != "complete"
        or str(formula.get("definition_status") or "") != "defined"
        or evidence.get("reason_codes")
        or not 0.95 <= confidence <= 1.0
    ):
        return None
    formula_id = str(formula.get("formula_id") or "")
    if formula_id == "operating_cash_flow_argmax_period":
        return _operating_cash_flow_argmax_period_execution(
            item,
            evidence,
            formula,
            tables,
            contexts,
            manifest=manifest,
            confidence=confidence,
            review=review,
        )
    if formula_id != "percentage_change":
        return None
    selected = evidence.get("selected_operand_matches") or {}
    if set(selected) != {"x_old", "x_new"}:
        return None
    bindings: dict[str, DirectBinding] = {}
    for operand_id in ("x_old", "x_new"):
        match = selected[operand_id]
        uid = str(match.get("internal_table_uid") or "")
        table, context = tables.get(uid), contexts.get(uid)
        if table is None or context is None:
            return None
        binding = _formula_binding(match, table, context, confidence=confidence)
        if binding is None:
            return None
        bindings[operand_id] = binding
    if len({binding.source_unit for binding in bindings.values()}) != 1:
        return None
    operation_ast = {"op": "percentage_change", "args": ["x_new", "x_old"]}
    try:
        computed = execute_ast(
            operation_ast,
            {operand_id: Decimal(binding.parsed_value) for operand_id, binding in bindings.items()},
        )
    except (ArithmeticError, ValueError):
        return None
    if not computed.is_finite():
        return None
    return {
        "id": int(item["id"]),
        "provenance_status": "machine_calibrated",
        "execution_status": "grounded",
        "grounding_status": "exact_rows_validated",
        "execution_mode": "exact_formula",
        "formula_definition_status": "defined",
        "operation_ast": operation_ast,
        "normalize_operands_to_vnd": False,
        "operand_bindings": [
            {
                "operand_id": operand_id,
                "internal_table_uid": binding.internal_table_uid,
                "binding": binding.to_dict(),
            }
            for operand_id, binding in bindings.items()
        ],
        "formula_provenance": {
            "protocol": "formula_evidence_v3_exact_percentage_change",
            "formula_id": formula["formula_id"],
            "formula_confidence": confidence,
            "review_consensus_status": str((review or {}).get("consensus_status") or "not_used"),
            "review_status_promoted": False,
            "formula_evidence_sha256": manifest["sidecar_sha256"],
            "evidence_context_file": manifest["evidence_context_file"],
            "source_discovery": evidence.get("source_discovery") or {},
        },
        "computed_answer": format(computed, "f"),
    }


def direct_execution_row(
    item: dict[str, Any],
    review: dict[str, Any] | None,
    tables: dict[str, dict[str, Any]],
    contexts: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if review is None:
        return _non_executable(item, review, "no_autonomous_review")
    if str(review.get("consensus_status") or "") != "machine_calibrated":
        return _non_executable(item, review, "review_not_machine_calibrated")
    self_review = review.get("machine_self_review") or {}
    if str(self_review.get("protocol") or "") != AUTONOMOUS_REVIEW_PROTOCOL:
        return _non_executable(item, review, "review_protocol_not_numeric_safe_v2")
    if not bool(self_review.get("training_eligible")):
        return _non_executable(item, review, "self_review_or_critic_not_accepted")
    critic_accepts = bool(self_review.get("critic_accepts"))
    equivalent_critic_policy = (
        str(self_review.get("selection_policy") or "") == EQUIVALENT_CRITIC_POLICY
    )
    if not critic_accepts and not equivalent_critic_policy:
        return _non_executable(item, review, "self_review_or_critic_not_accepted")
    # V4 may carry a hash-bound local plan override for a disclosed source
    # row.  The immutable bundle plan remains available in the review record's
    # provenance; execution must use the plan that V4 actually reviewed.
    plan = review.get("effective_question_plan") or item.get("question_plan") or {}
    if str(plan.get("family") or item.get("weak_family") or "") != "direct_lookup":
        return _non_executable(item, review, "family_requires_composed_executor")
    uid = str(review.get("machine_candidate_uid") or "")
    table = tables.get(uid)
    if table is None:
        return _non_executable(item, review, "selected_table_not_in_valid_v2_sidecar")
    context = (contexts or {}).get(uid)
    if contexts is not None and context is None:
        return _non_executable(item, review, "selected_table_not_in_canonical_context_sidecar")
    selected = self_review.get("selected_value_binding") or {}
    if str(selected.get("status") or "") != "cell_bound":
        return _non_executable(item, review, "selected_value_cell_not_bound")
    row_index = selected.get("row_index")
    column_index = selected.get("column_index")
    rows = table.get("rows") or []
    if not isinstance(row_index, int) or not isinstance(column_index, int):
        return _non_executable(item, review, "selected_value_coordinates_invalid")
    if not 0 <= row_index < len(rows) or not 0 <= column_index < len(rows[row_index]):
        return _non_executable(item, review, "selected_value_coordinates_out_of_bounds")
    raw_value = str(rows[row_index][column_index])
    if raw_value != str(selected.get("value") or ""):
        return _non_executable(item, review, "selected_value_differs_from_v2_source")
    column_label = str(selected.get("column_label") or "")
    if context is not None:
        headers = (context.get("canonical_headers") or {}).get("columns") or []
        expected_label = str(
            next(
                (
                    column.get("source_label")
                    for column in headers
                    if int(column.get("column_index") or -1) == column_index
                ),
                "",
            )
            or ""
        )
        if not expected_label or column_label != expected_label:
            return _non_executable(item, review, "selected_column_label_differs_from_canonical_source")
    else:
        # Compatibility path for a small unit-level caller.  Production always
        # supplies the hash-validated canonical context above.
        column_labels = table.get("column_labels") or []
        if column_index >= len(column_labels) or column_label != str(column_labels[column_index]):
            return _non_executable(item, review, "selected_column_label_differs_from_v2_source")
    parsed = parse_decimal(raw_value)
    if parsed.value is None or any(
        warning != "percent_value_not_scaled" for warning in parsed.warnings
    ):
        return _non_executable(item, review, "selected_value_is_not_a_reliable_number")
    source_unit = canonical_unit(table)
    requested_unit = plan.get("requested_unit")
    monetary_targets = {"vnd", "thousand_vnd", "million_vnd", "billion_vnd", "trillion_vnd"}
    if requested_unit in monetary_targets and source_unit is None:
        return _non_executable(item, review, "source_monetary_unit_unresolved")
    if not critic_accepts and not _revalidate_equivalent_critic_policy(
        review,
        self_review,
        table,
        context or {},
        selected_uid=uid,
        selected_row_index=row_index,
        selected_column_index=column_index,
        selected_parsed_value=parsed.value,
        selected_source_unit=str(source_unit or ""),
        tables=tables,
        contexts=contexts or {},
    ):
        return _non_executable(item, review, "critic_equivalence_not_revalidated")
    try:
        converted_value = convert_unit(
            Decimal(parsed.value), source_unit, str(requested_unit) if requested_unit else None
        )
    except ValueError:
        return _non_executable(item, review, "source_and_requested_units_are_incompatible")
    operands = plan.get("operands") or []
    operand_id = str(operands[0].get("operand_id") or "x0") if operands else "x0"
    binding = DirectBinding(
        internal_table_uid=uid,
        document_id=str(table["document_id"]),
        row_index=row_index,
        column_index=column_index,
        row_text=row_label(rows[row_index]),
        column_text=column_label,
        raw_value=raw_value,
        parsed_value=parsed.value,
        source_unit=source_unit,
        target_unit=str(requested_unit) if requested_unit else None,
        converted_value=format(converted_value, "f"),
        binding_score=float(review.get("machine_confidence") or 0.0),
        warnings=list(parsed.warnings),
    )
    return {
        "id": int(item["id"]),
        "provenance_status": "machine_calibrated",
        "execution_status": "grounded",
        "grounding_status": "exact_rows_validated",
        "execution_mode": "direct_lookup",
        "formula_definition_status": "confirmed",
        "operation_ast": plan.get("operation_ast") or {"op": "lookup", "args": [operand_id]},
        "operand_bindings": [
            {
                "operand_id": operand_id,
                "internal_table_uid": uid,
                "binding": binding.to_dict(),
            }
        ],
        "review_provenance": {
            "review_protocol": self_review.get("protocol"),
            "machine_confidence": review.get("machine_confidence"),
            "agreement": review.get("agreement"),
            "critic_accepts": critic_accepts,
            "selection_policy": self_review.get("selection_policy"),
            "critic_equivalence_revalidated": not critic_accepts,
        },
    }


def main() -> None:
    args = parse_args()
    bundle = args.bundle_dir.resolve()
    items = load_jsonl(bundle / "review_items.jsonl")
    reviews = index_by_id(load_jsonl(args.machine_reviews.resolve()), "machine reviews")
    item_ids = {int(item["id"]) for item in items}
    unexpected = sorted(set(reviews) - item_ids)
    if unexpected:
        raise ValueError(f"Machine reviews contain IDs outside bundle: {unexpected[:10]}")
    tables = load_v2_tables(bundle)
    context_path = (args.evidence_context or bundle / "tables_evidence_context_v3.jsonl").resolve()
    if context_path.parent != bundle:
        raise ValueError("Execution context sidecar must reside in the review bundle")
    contexts = load_evidence_contexts(bundle, context_path)
    formula_manifest: dict[str, Any] | None = None
    formula_by_id: dict[int, dict[str, Any]] = {}
    if args.formula_evidence is not None:
        formula_path = args.formula_evidence.resolve()
        formula_manifest = validate_formula_evidence_sidecar(bundle, formula_path, context_path)
        formula_by_id = index_by_id(load_jsonl(formula_path), "formula evidence")
        unexpected_formula_ids = sorted(set(formula_by_id) - item_ids)
        if unexpected_formula_ids:
            raise ValueError(
                f"Formula evidence contains IDs outside bundle: {unexpected_formula_ids[:10]}"
            )
    rows = []
    for item in items:
        direct_row = direct_execution_row(item, reviews.get(int(item["id"])), tables, contexts)
        formula_row = (
            exact_formula_execution_row(
                item,
                formula_by_id.get(int(item["id"])),
                tables,
                contexts,
                manifest=formula_manifest,
                review=reviews.get(int(item["id"])),
            )
            if formula_manifest is not None and direct_row["execution_status"] != "grounded"
            else None
        )
        rows.append(formula_row or direct_row)
    write_jsonl(args.output.resolve(), rows)
    counts = Counter(str(row["execution_status"]) for row in rows)
    reasons = Counter(str(row.get("reason") or "") for row in rows if row.get("reason"))
    print("Execution ledger:", args.output.resolve())
    print("Status counts:", dict(counts))
    print("Top non-executable reasons:", dict(reasons.most_common(12)))


if __name__ == "__main__":
    main()
