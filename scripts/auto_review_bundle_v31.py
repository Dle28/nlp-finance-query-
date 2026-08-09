#!/usr/bin/env python3
"""V3.1 reviewer entrypoint.

Reuses the source-aware V3 reviewer, but makes verifier semantics family-aware:
strict for direct lookup, partial/abstaining for multi-step families.  This avoids
forcing most temporal/ratio/comparison/aggregation questions into human review
merely because one candidate cannot prove the whole computation.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

MODULE_PATH = Path(__file__).with_name("auto_review_bundle_v3.py")
spec = importlib.util.spec_from_file_location("auto_review_bundle_v3_impl", MODULE_PATH)
v3 = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(v3)


def verifier(item, candidate, token_gate, bigram_gate):
    if candidate is None:
        return {"verdict": "UNSUPPORTED", "reason": "No eligible candidate."}

    if not (candidate.get("structure_validation") or {}).get("validated"):
        return {
            "verdict": "UNSUPPORTED",
            "reason": "Candidate failed exact V2 row validation.",
        }

    g = v3.grounding(item, candidate, token_gate, bigram_gate)
    if not g["guard_pass"]:
        return {
            "verdict": "UNSUPPORTED",
            "reason": "Recovered adjacent table failed direct-evidence grounding guard.",
        }

    evidence = candidate.get("evidence_features") or {}
    metadata = float(candidate.get("metadata_score") or 0.0)
    row_score = float(evidence.get("row_score") or 0.0)
    numeric = bool(evidence.get("numeric"))
    coverage = float(g["token_coverage"])
    family = str(
        (item.get("question_plan") or {}).get("family")
        or item.get("weak_family")
        or ""
    )

    if metadata < 0.70:
        return {
            "verdict": "UNSUPPORTED",
            "reason": f"Metadata agreement too weak ({metadata:.2f}).",
        }

    # Direct lookup is the only family allowed to become HIGH before calibration,
    # therefore it keeps a strict exact-evidence gate.
    if family == "direct_lookup":
        if coverage >= 0.75 and row_score >= 0.40 and numeric:
            return {
                "verdict": "SUPPORTED",
                "reason": "Direct metric is grounded in source evidence with numeric value.",
            }
        if coverage >= 0.45 or row_score >= 0.35:
            return {
                "verdict": "UNCERTAIN",
                "reason": (
                    f"Direct candidate is plausible but incomplete "
                    f"(ground={coverage:.2f}, row={row_score:.2f}, numeric={numeric})."
                ),
            }
        return {
            "verdict": "UNSUPPORTED",
            "reason": f"Direct evidence too weak (ground={coverage:.2f}, row={row_score:.2f}).",
        }

    # Multi-step questions cannot be proved by a single row.  A candidate with
    # plausible grounded evidence is PARTIAL, not UNSUPPORTED.  The calibrator
    # and additional agents decide whether it can later be promoted.
    if family in {"temporal_change", "ratio_or_derived"}:
        if numeric and (coverage >= 0.30 or row_score >= 0.30):
            return {
                "verdict": "PARTIAL",
                "reason": "Candidate plausibly grounds one operand; second operand/value still required.",
            }
        return {
            "verdict": "UNCERTAIN",
            "reason": "Multi-value family has weak single-candidate evidence; keep provisional.",
        }

    if family in {
        "cross_entity_comparison",
        "multi_entity_or_period_aggregation",
        "conditional_analytical",
    }:
        if coverage >= 0.20 or row_score >= 0.25:
            return {
                "verdict": "PARTIAL",
                "reason": "Complex family candidate is plausible but cannot prove the full operation alone.",
            }
        return {
            "verdict": "UNCERTAIN",
            "reason": "Complex-family evidence is weak; abstain from promoting it to gold.",
        }

    return {
        "verdict": "UNCERTAIN",
        "reason": f"Unhandled family {family or 'unknown'}; keep provisional.",
    }


# review_item resolves ``verifier`` from the V3 module globals at runtime.
v3.verifier = verifier

if __name__ == "__main__":
    v3.main()
