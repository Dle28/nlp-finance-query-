#!/usr/bin/env python3
"""Run the strict row-identity follow-up to the exact ratio lane.

Version 1 demonstrated that a typed divide contract can replace the broad
ratio heuristic, but its shared semantic selector could still accept a row
that only overlapped a few generic tokens.  This wrapper keeps the v1
candidate/Decimal/replay implementation and adds two narrow fail-closed
guards:

* typed explicit scope is isolated; the adapter never falls through from a
  requested separate report to a consolidated report (or vice versa);
* phenomenon-specific row anchors must survive hydration, while known
  accounting look-alikes such as retained earnings, cash-flow debt movement,
  and revenue deductions are rejected.

The canonical builder remains unchanged and still owns proposal verification,
evidence writing, submission validation, and authority policy.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any, Mapping


V1_MODULE_NAME = "_vifinqa_ratio_formula_variant_v1_for_v2"
VARIANT_PROTOCOL = "vifinqa_exact_ratio_formula_v2"
RATIO_TIER = "program_exact_ratio_formula_v2"
KNOWN_SCOPES = {"separate", "consolidated", "aggregated"}


def _load_v1() -> Any:
    runner_path = Path(__file__).resolve().with_name("run_ratio_formula_variant_v1.py")
    spec = importlib.util.spec_from_file_location(V1_MODULE_NAME, runner_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load v1 ratio runner: {runner_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


V1 = _load_v1()
ARG = V1.ARG
BUILDER = V1.BUILDER
_ORIGINAL_SELECT_YEAR_ROWS = ARG._select_year_rows
_ORIGINAL_SCOPE_CANDIDATES = V1._scope_candidates

# The v1 runner's functions read these globals when they build trace/report
# records, so changing them here keeps the wrapper's output self-describing.
V1.VARIANT_PROTOCOL = VARIANT_PROTOCOL
V1.RATIO_TIER = RATIO_TIER


def _normal(value: Any) -> str:
    return " ".join(BUILDER.normalize(value).split())


def _table_kinds(table: Mapping[str, Any]) -> set[str]:
    kinds: set[str] = set()
    for key in ("table_function", "table_section", "table_purpose"):
        value = table.get(key)
        if isinstance(value, Mapping):
            for field in ("kind", "label"):
                text = _normal(value.get(field))
                if text:
                    kinds.add(text)
    return kinds


def _scope_request(item: Mapping[str, Any], typed: Mapping[str, Any]) -> str | None:
    """Return the explicit scope, including an operand-level declaration."""

    plan = item.get("question_plan")
    plan = plan if isinstance(plan, Mapping) else {}
    values = [
        typed.get("scope"),
        plan.get("scope"),
        plan.get("reporting_scope"),
    ]
    operand_scopes = {
        str(operand.get("scope") or "").strip().lower()
        for operand in typed.get("operands") or []
        if isinstance(operand, Mapping)
        and str(operand.get("scope") or "").strip().lower() in KNOWN_SCOPES
    }
    if len(operand_scopes) == 1:
        values.append(next(iter(operand_scopes)))
    values.append(ARG._explicit_question_scope(str(item.get("question") or "")))
    for value in values:
        scope = str(value or "").strip().lower()
        if scope in KNOWN_SCOPES:
            return scope
    return None


def _strict_scope_candidates(
    item: Mapping[str, Any],
    typed: Mapping[str, Any],
    ticker: str,
    year: int,
    tables_by_uid: Mapping[str, Mapping[str, Any]],
) -> list[str]:
    requested = _scope_request(item, typed)
    if requested is None:
        return _ORIGINAL_SCOPE_CANDIDATES(
            item, typed, ticker, year, tables_by_uid
        )
    index = ARG._table_index(tables_by_uid)
    if index.get((ticker, year, requested)):
        return [requested]
    V1._STATS["strict_scope_coverage_missing"] += 1
    V1._TRACE.append(
        {
            "question_id": V1._question_id(item),
            "status": "REJECTED_EXPLICIT_SCOPE_COVERAGE_MISSING",
            "ticker": ticker,
            "year": year,
            "requested_scope": requested,
        }
    )
    return []


def _row_identity_accepts(
    metric: str,
    row_label: str,
    table: Mapping[str, Any] | None = None,
    raw_metric_hint: str | None = None,
) -> tuple[bool, str]:
    """Check high-information row anchors for a typed operand.

    The rules are family-level constraints, not question-ID fixes.  They only
    reject a hydrated row when a qualifier explicitly present in the typed
    metric disappears or a known accounting look-alike is selected.
    """

    metric_norm = _normal(metric)
    label_norm = _normal(row_label)
    raw_metric_norm = _normal(raw_metric_hint)
    if not metric_norm or not label_norm:
        return False, "empty_identity"

    required_phrases = (
        ("cam ket", "cam ket"),
        ("ngoai bang", "ngoai bang"),
        ("nguyen gia", "nguyen gia"),
        ("ngan hang", "ngan hang"),
        ("dau tu tai chinh", "dau tu tai chinh"),
        ("lai vay phai tra", "phai tra"),
        ("tong no phai tra tai chinh", "tai chinh"),
        ("von gop", "von gop"),
    )
    for trigger, required in required_phrases:
        if trigger in metric_norm and required not in label_norm:
            return False, f"missing_{required.replace(' ', '_')}"

    if "phai thu" in metric_norm and "phai thu" not in label_norm:
        return False, "missing_phai_thu"
    if "phai tra" in metric_norm and "phai tra" not in label_norm:
        return False, "missing_phai_tra"

    # Keep horizon qualifiers attached to the requested accounting line.
    for horizon in ("ngan han", "dai han"):
        if horizon in metric_norm and horizon not in label_norm:
            return False, f"missing_{horizon.replace(' ', '_')}"

    if "du phong chung" in metric_norm and "du phong chung" not in label_norm:
        return False, "missing_du_phong_chung"

    if "trich lap" in metric_norm:
        if "du phong" not in label_norm:
            return False, "missing_du_phong"
        if "tang chi phi" in label_norm or "giam nguon" in label_norm:
            return False, "expense_change_lookalike"

    if "rui ro" in metric_norm and "rui ro" not in label_norm:
        return False, "missing_risk_anchor"

    if "chi phi tra truoc" in metric_norm and "tra truoc" not in label_norm:
        return False, "missing_prepaid_expense_anchor"

    if "dong tien" in metric_norm and not any(
        phrase in label_norm for phrase in ("dong tien", "luu chuyen tien")
    ):
        return False, "missing_cash_flow_anchor"

    if "cho vay dai han" in metric_norm and not (
        "cho vay" in label_norm and "dai han" in label_norm
    ):
        return False, "wrong_lending_horizon"

    if "phai thu" in metric_norm and "khac" in metric_norm:
        # Preserve qualifiers in between the core phrase and ``khác``:
        # ``phải thu ngắn hạn khác`` is valid, while ``các khoản khác phải
        # thu Nhà nước`` is a different receivable family.
        phai_thu_at = label_norm.find("phai thu")
        khac_at = label_norm.find("khac")
        if phai_thu_at < 0 or khac_at < phai_thu_at:
            return False, "missing_receivable_other_anchor"

    # ``_clean_metric`` may remove ``tổng`` while generating a bounded
    # candidate variant.  Recover only high-information total qualifiers from
    # the matching typed hint; do not infer them from a numeric value.
    if "tong doanh thu" in raw_metric_norm and "tong doanh thu" not in label_norm:
        return False, "missing_total_revenue_anchor"
    if "tong khoan phai thu khac" in raw_metric_norm:
        if "tong" not in label_norm and "cac khoan" not in label_norm:
            return False, "missing_total_other_receivable_anchor"
    if "tong no phai tra tai chinh" in raw_metric_norm:
        if "no phai tra" not in label_norm:
            return False, "missing_total_financial_debt_anchor"
        if "ngan han" in label_norm:
            return False, "horizon_subtotal_lookalike"

    if (
        "phai thu" in metric_norm
        and "du phong" in label_norm
        and "du phong" not in metric_norm
        and "du phong" not in raw_metric_norm
    ):
        return False, "provision_receivable_lookalike"

    if "tong phai thu khach hang" in metric_norm and "khach hang" not in label_norm:
        return False, "missing_customer_anchor"

    if "tong doanh thu" in metric_norm and "giam tru doanh thu" in label_norm:
        return False, "revenue_deduction_lookalike"
    if "tong doanh thu" in metric_norm and "tong doanh thu" not in label_norm:
        return False, "missing_total_revenue_anchor"

    if "loi nhuan sau thue" in metric_norm:
        if "loi nhuan sau thue" not in label_norm:
            return False, "missing_net_profit_anchor"
        if "chua phan phoi" in label_norm:
            return False, "retained_earnings_lookalike"

    if "lai vay phai tra" in metric_norm and "thu nhap lai" in label_norm:
        return False, "interest_income_lookalike"

    if any(
        phrase in metric_norm for phrase in ("no vay", "du no vay")
    ) and table is not None:
        kinds = _table_kinds(table)
        if any("cash_flow" in kind or "cash flow" in kind for kind in kinds):
            return False, "cash_flow_debt_lookalike"
    if "no vay" in metric_norm and any(
        phrase in label_norm for phrase in ("tien tra no goc vay", "tra no goc vay")
    ):
        return False, "cash_flow_debt_lookalike"

    if (
        "tong no phai tra tai chinh" in metric_norm
        and "ngan han" in label_norm
        and "ngan han" not in metric_norm
    ):
        return False, "horizon_subtotal_lookalike"

    return True, "accepted"


def _strict_select_year_rows(**kwargs: Any) -> list[dict[str, Any]]:
    selections = _ORIGINAL_SELECT_YEAR_ROWS(**kwargs)
    tables_by_uid = kwargs.get("tables_by_uid") or {}
    metric = str(kwargs.get("metric") or "")
    typed = V1._typed_plan(kwargs.get("item") or {}) or {}
    metric_hints = [
        str(hint)
        for operand in typed.get("operands") or []
        if isinstance(operand, Mapping)
        for hint in operand.get("metric_hints") or []
        if str(hint).strip()
    ]
    metric_tokens = set(BUILDER.content_tokens(metric))
    raw_metric_hint = None
    if metric_hints:
        raw_metric_hint = max(
            metric_hints,
            key=lambda hint: (
                len(metric_tokens & set(BUILDER.content_tokens(hint))),
                -abs(
                    len(set(BUILDER.content_tokens(hint)))
                    - len(metric_tokens)
                ),
                -len(hint),
            ),
        )
    kept: list[dict[str, Any]] = []
    for selection in selections:
        table = tables_by_uid.get(str(selection.get("internal_table_uid") or ""))
        accepted, reason = _row_identity_accepts(
            metric,
            str(selection.get("row_label") or ""),
            table,
            raw_metric_hint,
        )
        if accepted:
            kept.append(selection)
        else:
            V1._STATS[f"strict_row_rejected_{reason}"] += 1
    V1._STATS["strict_row_selections_kept"] += len(kept)
    if selections and not kept:
        V1._STATS["strict_row_window_all_rejected"] += 1
    return kept


ARG._select_year_rows = _strict_select_year_rows
V1._scope_candidates = _strict_scope_candidates


if __name__ == "__main__":
    V1.main()
