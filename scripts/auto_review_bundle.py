#!/usr/bin/env python3
"""Run multiple independent reviewer views over an offline review bundle.

The reviewers are intentionally heterogeneous and deterministic:

- lexical_agent: BM25 rank view;
- dense_agent: dense-rank view;
- metadata_agent: ticker/year/scope agreement;
- evidence_agent: exact-row metric/numeric support;
- challenger_agent: searches for an alternative without trusting rank #1;
- verifier: family-aware support/abstention gate.

Optionally a human-trained calibrator adds a learned reviewer. Machine labels are
never written as ``human_verified``.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


NUMERIC_RE = re.compile(r"^[\s()\-+\d.,%/]+$")

FEATURE_NAMES = [
    "rank_reciprocal",
    "lexical_reciprocal",
    "dense_reciprocal",
    "fused_score",
    "metadata_score",
    "row_score",
    "metric_overlap",
    "question_overlap",
    "numeric",
    "ticker_match",
    "scope_match",
    "year_match",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--calibrator", type=Path, default=None)
    parser.add_argument("--seed-queue", type=Path, default=None)
    parser.add_argument("--seed-size", type=int, default=12)
    parser.add_argument("--needs-human-queue", type=Path, default=None)
    parser.add_argument("--high-threshold", type=float, default=0.86)
    parser.add_argument("--calibrated-threshold", type=float, default=0.97)
    return parser.parse_args()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL: {path}:{line_number}") from exc
    return rows


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")
        file.flush()
        os.fsync(file.fileno())
    temporary.replace(path)


def reciprocal(value: Any) -> float:
    try:
        rank = int(value)
    except (TypeError, ValueError):
        return 0.0
    return 1.0 / rank if rank > 0 else 0.0


def candidate_features(candidate: dict[str, Any]) -> dict[str, float]:
    evidence = candidate.get("evidence_features") or {}
    return {
        "rank_reciprocal": reciprocal(candidate.get("rank")),
        "lexical_reciprocal": reciprocal(candidate.get("lexical_rank")),
        "dense_reciprocal": reciprocal(candidate.get("dense_rank")),
        "fused_score": float(candidate.get("fused_score") or 0.0),
        "metadata_score": float(candidate.get("metadata_score") or 0.0),
        "row_score": float(evidence.get("row_score") or 0.0),
        "metric_overlap": float(evidence.get("metric_overlap") or 0.0),
        "question_overlap": float(evidence.get("question_overlap") or 0.0),
        "numeric": float(bool(evidence.get("numeric"))),
        "ticker_match": float(bool(candidate.get("ticker_match"))),
        "scope_match": float(bool(candidate.get("scope_match"))),
        "year_match": float(bool(candidate.get("year_match"))),
    }


def feature_vector(candidate: dict[str, Any], feature_names: list[str] = FEATURE_NAMES) -> list[float]:
    features = candidate_features(candidate)
    return [features[name] for name in feature_names]


def uid(candidate: dict[str, Any] | None) -> str | None:
    return None if candidate is None else str(candidate["internal_table_uid"])


def argmin_rank(candidates: list[dict[str, Any]], key: str) -> dict[str, Any] | None:
    ranked = []
    for candidate in candidates:
        value = candidate.get(key)
        try:
            rank = int(value)
        except (TypeError, ValueError):
            continue
        ranked.append((rank, candidate))
    return min(ranked, key=lambda pair: pair[0])[1] if ranked else None


def metadata_choice(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda c: (
            float(c.get("metadata_score") or 0.0),
            float((c.get("evidence_features") or {}).get("row_score") or 0.0),
            -int(c.get("rank") or 10**9),
        ),
    )


def evidence_choice(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda c: (
            0.76 * float((c.get("evidence_features") or {}).get("row_score") or 0.0)
            + 0.24 * float(c.get("metadata_score") or 0.0),
            -int(c.get("rank") or 10**9),
        ),
    )


def challenger_choice(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Search for a strong alternative while down-weighting fused/rank anchoring."""
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda c: (
            0.58 * float((c.get("evidence_features") or {}).get("row_score") or 0.0)
            + 0.34 * float(c.get("metadata_score") or 0.0)
            + 0.08 * min(1.0, reciprocal(c.get("lexical_rank")) + reciprocal(c.get("dense_rank"))),
            float((c.get("evidence_features") or {}).get("metric_overlap") or 0.0),
        ),
    )


def numeric_cell_count(candidate: dict[str, Any]) -> int:
    best_index = candidate.get("best_row_index")
    for item in candidate.get("evidence_window") or []:
        if item.get("index") != best_index:
            continue
        count = 0
        for cell in item.get("row") or []:
            text = str(cell).strip()
            if text and NUMERIC_RE.fullmatch(text) and any(char.isdigit() for char in text):
                count += 1
        return count
    return 0


def verifier(item: dict[str, Any], candidate: dict[str, Any] | None) -> dict[str, Any]:
    if candidate is None:
        return {"verdict": "UNSUPPORTED", "reason": "No candidate selected."}

    plan = item.get("question_plan") or {}
    family = str(plan.get("family") or item.get("weak_family") or "")
    evidence = candidate.get("evidence_features") or {}
    metric_overlap = float(evidence.get("metric_overlap") or 0.0)
    row_score = float(evidence.get("row_score") or 0.0)
    numeric = bool(evidence.get("numeric"))
    metadata = float(candidate.get("metadata_score") or 0.0)
    numeric_cells = numeric_cell_count(candidate)

    if metadata < 0.70:
        return {
            "verdict": "UNSUPPORTED",
            "reason": f"Metadata agreement too weak ({metadata:.2f}).",
        }

    if metric_overlap < 0.35 or row_score < 0.38:
        return {
            "verdict": "UNSUPPORTED",
            "reason": (
                f"Exact-row support too weak (metric={metric_overlap:.2f}, row={row_score:.2f})."
            ),
        }

    if family == "direct_lookup":
        if numeric and metric_overlap >= 0.45:
            return {"verdict": "SUPPORTED", "reason": "Direct metric row + numeric evidence."}
        return {"verdict": "UNCERTAIN", "reason": "Direct lookup lacks strong numeric/metric evidence."}

    if family in {"temporal_change", "ratio_or_derived"}:
        if numeric and numeric_cells >= 2 and metric_overlap >= 0.40:
            return {
                "verdict": "SUPPORTED",
                "reason": f"Derived/temporal row exposes {numeric_cells} numeric cells.",
            }
        return {
            "verdict": "PARTIAL",
            "reason": "Candidate may support one operand but multi-value semantics are not fully proven.",
        }

    if family in {"cross_entity_comparison", "multi_entity_or_period_aggregation", "conditional_analytical"}:
        return {
            "verdict": "PARTIAL",
            "reason": "Complex family: single candidate evidence is provisional until calibrated/audited.",
        }

    return {
        "verdict": "UNCERTAIN",
        "reason": f"Unknown/unhandled family: {family or 'none'}.",
    }


def load_calibrator(path: Path | None):
    if path is None:
        return None
    if not path.is_file():
        raise FileNotFoundError(path)
    import joblib
    payload = joblib.load(path)
    if isinstance(payload, dict) and "model" in payload:
        return payload
    return {"model": payload, "feature_names": FEATURE_NAMES}


def calibrated_probabilities(
    candidates: list[dict[str, Any]],
    calibrator: dict[str, Any] | None,
) -> dict[str, float]:
    if calibrator is None or not candidates:
        return {}

    model = calibrator["model"]
    names = list(calibrator.get("feature_names") or FEATURE_NAMES)
    matrix = [feature_vector(candidate, names) for candidate in candidates]
    probabilities = model.predict_proba(matrix)[:, 1]
    return {
        str(candidate["internal_table_uid"]): float(probability)
        for candidate, probability in zip(candidates, probabilities)
    }


def review_item(
    item: dict[str, Any],
    calibrator: dict[str, Any] | None,
    high_threshold: float,
    calibrated_threshold: float,
) -> dict[str, Any]:
    candidates = list(item.get("candidates") or [])
    if not candidates:
        return {
            "id": int(item["id"]),
            "question": item["question"],
            "family": (item.get("question_plan") or {}).get("family") or item.get("weak_family"),
            "machine_candidate_uid": None,
            "machine_candidate_rank": None,
            "agent_votes": {},
            "agreement": 0.0,
            "verifier": {"verdict": "UNSUPPORTED", "reason": "Retriever returned no candidates."},
            "machine_confidence": 0.0,
            "calibrated_probability": None,
            "consensus_status": "retrieval_failure",
            "review_reason": "No candidate in Top-K. This does NOT mean no gold exists in corpus.",
        }

    lexical = argmin_rank(candidates, "lexical_rank")
    dense = argmin_rank(candidates, "dense_rank")
    metadata = metadata_choice(candidates)
    evidence = evidence_choice(candidates)
    challenger = challenger_choice(candidates)

    votes: dict[str, str | None] = {
        "lexical_agent": uid(lexical),
        "dense_agent": uid(dense),
        "metadata_agent": uid(metadata),
        "evidence_agent": uid(evidence),
        "challenger_agent": uid(challenger),
    }

    probabilities = calibrated_probabilities(candidates, calibrator)
    if probabilities:
        learned_uid = max(probabilities, key=probabilities.get)
        votes["calibrator_agent"] = learned_uid

    usable_votes = [value for value in votes.values() if value]
    counts = Counter(usable_votes)
    selected_uid, selected_votes = counts.most_common(1)[0]
    selected = next(c for c in candidates if c["internal_table_uid"] == selected_uid)
    agreement = selected_votes / max(1, len(usable_votes))

    check = verifier(item, selected)
    evidence_features = selected.get("evidence_features") or {}
    row_score = float(evidence_features.get("row_score") or 0.0)
    metadata_score = float(selected.get("metadata_score") or 0.0)
    rank_support = min(
        1.0,
        reciprocal(selected.get("lexical_rank")) + reciprocal(selected.get("dense_rank")),
    )
    heuristic_confidence = min(
        1.0,
        0.36 * agreement
        + 0.29 * row_score
        + 0.21 * metadata_score
        + 0.14 * rank_support,
    )

    calibrated = probabilities.get(selected_uid) if probabilities else None
    confidence = (
        0.65 * calibrated + 0.35 * heuristic_confidence
        if calibrated is not None
        else heuristic_confidence
    )

    plan = item.get("question_plan") or {}
    family = str(plan.get("family") or item.get("weak_family") or "")
    challenger_agrees = votes.get("challenger_agent") == selected_uid
    verifier_supported = check["verdict"] == "SUPPORTED"

    if (
        calibrated is not None
        and calibrated >= calibrated_threshold
        and agreement >= 0.60
        and challenger_agrees
        and verifier_supported
    ):
        status = "machine_calibrated"
        reason = "Human-trained calibrator + multi-agent consensus + verifier all agree."
    elif (
        family == "direct_lookup"
        and confidence >= high_threshold
        and agreement >= 0.60
        and challenger_agrees
        and verifier_supported
    ):
        status = "machine_high_confidence"
        reason = "Conservative direct-lookup consensus with exact-row support."
    elif check["verdict"] == "UNSUPPORTED" or agreement < 0.40:
        status = "needs_human"
        reason = "Agent disagreement or verifier rejected the selected candidate."
    else:
        status = "machine_provisional"
        reason = "Machine selected a candidate, but evidence is not strong enough to call gold."

    return {
        "id": int(item["id"]),
        "question": item["question"],
        "family": family,
        "machine_candidate_uid": selected_uid,
        "machine_candidate_rank": int(selected.get("rank") or 0),
        "machine_candidate_summary": selected.get("one_line_summary"),
        "machine_candidate_direct_evidence": selected.get("direct_evidence"),
        "agent_votes": votes,
        "vote_counts": dict(counts),
        "agreement": agreement,
        "verifier": check,
        "heuristic_confidence": heuristic_confidence,
        "calibrated_probability": calibrated,
        "machine_confidence": confidence,
        "challenger_agrees": challenger_agrees,
        "consensus_status": status,
        "review_reason": reason,
    }


def make_seed_queue(
    reviews: list[dict[str, Any]],
    items_by_id: dict[int, dict[str, Any]],
    seed_size: int,
) -> list[dict[str, Any]]:
    if seed_size <= 0:
        return []

    by_family: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for review in reviews:
        by_family[str(review.get("family") or "unknown")].append(review)

    selected_ids: set[int] = set()
    queue: list[dict[str, Any]] = []

    # First pass: one confident + one uncertain per family when possible.
    for family in sorted(by_family):
        group = by_family[family]
        confident = sorted(group, key=lambda r: float(r.get("machine_confidence") or 0.0), reverse=True)
        uncertain = sorted(group, key=lambda r: abs(float(r.get("machine_confidence") or 0.0) - 0.55))

        for review, reason in [
            (confident[0] if confident else None, "family_high_confidence_audit"),
            (uncertain[0] if uncertain else None, "family_uncertainty_seed"),
        ]:
            if review is None or int(review["id"]) in selected_ids:
                continue
            qid = int(review["id"])
            selected_ids.add(qid)
            queue.append({
                "id": qid,
                "family": family,
                "seed_reason": reason,
                "machine_confidence": review.get("machine_confidence"),
                "consensus_status": review.get("consensus_status"),
                "question": items_by_id[qid]["question"],
            })
            if len(queue) >= seed_size:
                return queue

    # Fill remaining slots by maximum uncertainty.
    remaining = [
        review for review in reviews
        if int(review["id"]) not in selected_ids
    ]
    remaining.sort(
        key=lambda r: (
            r.get("consensus_status") not in {"needs_human", "retrieval_failure"},
            abs(float(r.get("machine_confidence") or 0.0) - 0.55),
        )
    )
    for review in remaining:
        qid = int(review["id"])
        queue.append({
            "id": qid,
            "family": review.get("family"),
            "seed_reason": "global_uncertainty_fill",
            "machine_confidence": review.get("machine_confidence"),
            "consensus_status": review.get("consensus_status"),
            "question": items_by_id[qid]["question"],
        })
        if len(queue) >= seed_size:
            break

    return queue


def main() -> None:
    args = parse_args()
    bundle_dir = args.bundle_dir.resolve()
    manifest_path = bundle_dir / "manifest.json"
    items_path = bundle_dir / "review_items.jsonl"

    if not manifest_path.is_file() or not items_path.is_file():
        raise FileNotFoundError("Bundle must contain manifest.json and review_items.jsonl")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if int(manifest.get("schema_version") or 0) < 2:
        raise RuntimeError("Unsupported review bundle schema. Re-export with build_review_bundle.py.")
    if int(manifest.get("error_count") or 0) != 0:
        raise RuntimeError("Bundle contains retrieval errors; auto-review refused.")

    items = load_jsonl(items_path)
    calibrator = load_calibrator(args.calibrator)

    reviews = [
        review_item(
            item,
            calibrator=calibrator,
            high_threshold=args.high_threshold,
            calibrated_threshold=args.calibrated_threshold,
        )
        for item in items
    ]

    write_jsonl(args.output, reviews)
    items_by_id = {int(item["id"]): item for item in items}

    if args.seed_queue is not None:
        queue = make_seed_queue(reviews, items_by_id, args.seed_size)
        write_jsonl(args.seed_queue, queue)
        print("Human seed queue:", args.seed_queue, "| size=", len(queue))

    if args.needs_human_queue is not None:
        unresolved = [
            {
                "id": int(review["id"]),
                "family": review.get("family"),
                "consensus_status": review.get("consensus_status"),
                "machine_confidence": review.get("machine_confidence"),
                "reason": review.get("review_reason"),
                "question": review.get("question"),
            }
            for review in reviews
            if review.get("consensus_status") in {"needs_human", "retrieval_failure"}
        ]
        write_jsonl(args.needs_human_queue, unresolved)
        print("Needs-human queue:", args.needs_human_queue, "| size=", len(unresolved))

    statuses = Counter(review["consensus_status"] for review in reviews)
    print("Machine reviews:", args.output)
    print("Status counts:", dict(statuses))


if __name__ == "__main__":
    main()
