"""Source-bound local question-plan overrides for an immutable review bundle.

The bundle's original retrieval output is never rewritten.  An override is a
small, hash-bound sidecar used only when a high-precision planner correction
is available.  This keeps the initial plan, the reason for the correction and
the effective plan independently auditable.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable, Mapping

from .questions import metric_hint, reported_value_lookup_reason


PLAN_OVERRIDE_SCHEMA_VERSION = 1


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def reported_direct_override(item: Mapping[str, Any]) -> dict[str, Any] | None:
    """Create one deterministic direct-lookup correction, if warranted."""
    question = str(item.get("question") or "")
    original = item.get("question_plan") or {}
    if str(original.get("family") or "") == "direct_lookup":
        return None
    reason = reported_value_lookup_reason(question)
    if not reason:
        return None
    years = [int(value) for value in original.get("years") or [] if isinstance(value, int)]
    tickers = [str(value) for value in original.get("tickers") or [] if str(value)]
    warnings = list(dict.fromkeys(
        [
            *[str(value) for value in original.get("warnings") or []],
            "Plan override: question requests one disclosed source row; no arithmetic is implied.",
        ]
    ))
    effective = {
        **original,
        "question_id": int(item["id"]),
        "original_question": question,
        "family": "direct_lookup",
        "family_confidence": 0.98,
        "operation_ast": {"op": "lookup", "args": ["x0"]},
        "operands": [
            {
                "operand_id": "x0",
                "metric": metric_hint(question),
                "ticker": tickers[0] if len(tickers) == 1 else None,
                "period": years[0] if len(years) == 1 else None,
                "scope": original.get("scope"),
                "entity": None,
                "qualifiers": [],
            }
        ],
        "warnings": warnings,
    }
    return {
        "schema_version": PLAN_OVERRIDE_SCHEMA_VERSION,
        "id": int(item["id"]),
        "question_sha256": canonical_sha256(question),
        "original_question_plan_sha256": canonical_sha256(original),
        "reason_code": reason,
        "effective_question_plan": effective,
    }


def validate_plan_overrides(
    items: Iterable[Mapping[str, Any]], overrides: Iterable[Mapping[str, Any]]
) -> dict[int, dict[str, Any]]:
    """Validate an override sidecar against the exact bundle questions/plans."""
    source = {int(item["id"]): item for item in items}
    output: dict[int, dict[str, Any]] = {}
    for position, override in enumerate(overrides, start=1):
        qid = int(override.get("id"))
        if qid in output:
            raise ValueError(f"Duplicate plan override for Q{qid}")
        item = source.get(qid)
        if item is None:
            raise ValueError(f"Plan override Q{qid} is absent from the review bundle")
        if int(override.get("schema_version") or 0) != PLAN_OVERRIDE_SCHEMA_VERSION:
            raise ValueError(f"Q{qid}: unsupported plan-override schema")
        if str(override.get("question_sha256") or "") != canonical_sha256(str(item.get("question") or "")):
            raise ValueError(f"Q{qid}: plan override question hash does not match bundle")
        original = item.get("question_plan") or {}
        if str(override.get("original_question_plan_sha256") or "") != canonical_sha256(original):
            raise ValueError(f"Q{qid}: plan override original-plan hash does not match bundle")
        effective = override.get("effective_question_plan")
        if not isinstance(effective, dict):
            raise ValueError(f"Q{qid}: plan override has no effective plan")
        operands = effective.get("operands") or []
        if (
            effective.get("family") != "direct_lookup"
            or (effective.get("operation_ast") or {}).get("op") != "lookup"
            or len(operands) != 1
            or str((operands[0] or {}).get("operand_id") or "") != "x0"
        ):
            raise ValueError(f"Q{qid}: plan override is not a single disclosed-row lookup")
        if not str(override.get("reason_code") or ""):
            raise ValueError(f"Q{qid}: plan override has no reason code")
        output[qid] = dict(override)
    return output


def apply_plan_overrides(
    items: Iterable[Mapping[str, Any]], overrides: Mapping[int, Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """Return effective in-memory items, preserving original bundle records."""
    output: list[dict[str, Any]] = []
    for item in items:
        copied = dict(item)
        qid = int(copied["id"])
        override = overrides.get(qid)
        if override is None:
            copied["_question_plan_provenance"] = {
                "source": "original_bundle_plan",
                "question_plan_sha256": canonical_sha256(copied.get("question_plan") or {}),
            }
        else:
            copied["question_plan"] = dict(override["effective_question_plan"])
            copied["_question_plan_provenance"] = {
                "source": "source_bound_plan_override_v1",
                "reason_code": override["reason_code"],
                "question_sha256": override["question_sha256"],
                "original_question_plan_sha256": override["original_question_plan_sha256"],
                "effective_question_plan_sha256": canonical_sha256(copied["question_plan"]),
            }
        output.append(copied)
    return output
