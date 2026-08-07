#!/usr/bin/env python3
"""Source-aware multi-agent review for schema-v3 review bundles.

Key safety rule: a candidate recovered from ``context_before`` is not trusted
merely because it ranks first after repair.  It must be grounded back to its own
exported direct evidence.  This prevents a false adjacent-table recovery from
outvoting a directly retrieved table that contains the exact financial concept.

The script keeps the same output contract as ``auto_review_bundle.py`` so the
existing local widget / calibrator workflow can consume it.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

TOKEN_RE = re.compile(r"[A-Za-zÀ-ỹ0-9%]+", re.UNICODE)
STOPWORDS = {
    "cua", "của", "la", "là", "bao", "nhieu", "nhiêu", "vao", "vào",
    "tai", "tại", "trong", "cho", "theo", "den", "đến", "ngay", "ngày",
    "thang", "tháng", "nam", "năm", "cuoi", "cuối", "dau", "đầu", "va", "và",
    "mot", "một", "cac", "các", "duoc", "được", "co", "có", "dong", "đồng",
    "trieu", "triệu", "ty", "tỷ", "bao_nhieu",
}
FEATURE_NAMES = [
    "rank_reciprocal", "lexical_reciprocal", "dense_reciprocal", "fused_score",
    "metadata_score", "row_score", "metric_overlap", "question_overlap", "numeric",
    "ticker_match", "scope_match", "year_match",
]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--bundle-dir", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--calibrator", type=Path, default=None)
    p.add_argument("--seed-queue", type=Path, default=None)
    p.add_argument("--seed-size", type=int, default=12)
    p.add_argument("--needs-human-queue", type=Path, default=None)
    p.add_argument("--high-threshold", type=float, default=0.84)
    p.add_argument("--calibrated-threshold", type=float, default=0.97)
    p.add_argument("--adjacent-min-token-coverage", type=float, default=0.85)
    p.add_argument("--adjacent-min-bigram-ratio", type=float, default=0.45)
    return p.parse_args()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig") as f:
        for line_no, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL {path}:{line_no}") from exc
    return out


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        f.flush(); os.fsync(f.fileno())
    tmp.replace(path)


def token_sequence(text: Any) -> list[str]:
    return [
        token.casefold()
        for token in TOKEN_RE.findall(str(text))
        if len(token) >= 2 and token.casefold() not in STOPWORDS
    ]


def reciprocal(value: Any) -> float:
    try:
        rank = int(value)
    except (TypeError, ValueError):
        return 0.0
    return 1.0 / rank if rank > 0 else 0.0


def candidate_features(candidate: dict[str, Any]) -> dict[str, float]:
    e = candidate.get("evidence_features") or {}
    return {
        "rank_reciprocal": reciprocal(candidate.get("rank")),
        "lexical_reciprocal": reciprocal(candidate.get("lexical_rank")),
        "dense_reciprocal": reciprocal(candidate.get("dense_rank")),
        "fused_score": float(candidate.get("fused_score") or 0.0),
        "metadata_score": float(candidate.get("metadata_score") or 0.0),
        "row_score": float(e.get("row_score") or 0.0),
        "metric_overlap": float(e.get("metric_overlap") or 0.0),
        "question_overlap": float(e.get("question_overlap") or 0.0),
        "numeric": float(bool(e.get("numeric"))),
        "ticker_match": float(bool(candidate.get("ticker_match"))),
        "scope_match": float(bool(candidate.get("scope_match"))),
        "year_match": float(bool(candidate.get("year_match"))),
    }


def feature_vector(candidate: dict[str, Any], names: list[str] = FEATURE_NAMES) -> list[float]:
    f = candidate_features(candidate)
    return [f[name] for name in names]


def metric_text(item: dict[str, Any], candidate: dict[str, Any]) -> str:
    value = str(candidate.get("effective_metric") or item.get("effective_metric") or "").strip()
    if value:
        return value
    ops = (item.get("question_plan") or {}).get("operands") or []
    if ops:
        value = str(ops[0].get("metric") or "").strip()
    return value or str(item.get("question") or "")


def grounding(item: dict[str, Any], candidate: dict[str, Any], token_gate: float, bigram_gate: float) -> dict[str, Any]:
    metric_tokens = token_sequence(metric_text(item, candidate))
    evidence_tokens = token_sequence(candidate.get("direct_evidence") or "")
    mset, eset = set(metric_tokens), set(evidence_tokens)
    coverage = len(mset & eset) / max(1, len(mset))
    evidence_norm = " ".join(evidence_tokens)
    bigrams = [" ".join(metric_tokens[i:i+2]) for i in range(len(metric_tokens)-1)]
    trigrams = [" ".join(metric_tokens[i:i+3]) for i in range(len(metric_tokens)-2)]
    bigram_ratio = sum(x in evidence_norm for x in bigrams) / max(1, len(bigrams)) if bigrams else coverage
    trigram_ratio = sum(x in evidence_norm for x in trigrams) / max(1, len(trigrams)) if trigrams else bigram_ratio
    quality = 0.60 * coverage + 0.25 * bigram_ratio + 0.15 * trigram_ratio
    adjacent = candidate.get("candidate_source") == "adjacent_previous_due_context"
    guard_pass = (not adjacent) or (coverage >= token_gate and bigram_ratio >= bigram_gate)
    return {
        "metric": metric_text(item, candidate),
        "token_coverage": coverage,
        "bigram_ratio": bigram_ratio,
        "trigram_ratio": trigram_ratio,
        "quality": quality,
        "adjacent_candidate": adjacent,
        "guard_pass": guard_pass,
    }


def candidate_score(item: dict[str, Any], candidate: dict[str, Any], token_gate: float, bigram_gate: float) -> float:
    g = grounding(item, candidate, token_gate, bigram_gate)
    if not g["guard_pass"]:
        return -1.0
    e = candidate.get("evidence_features") or {}
    retrieval = max(reciprocal(candidate.get("lexical_rank")), reciprocal(candidate.get("dense_rank")))
    return (
        0.34 * float(e.get("row_score") or 0.0)
        + 0.20 * float(candidate.get("metadata_score") or 0.0)
        + 0.29 * float(g["quality"])
        + 0.10 * float(e.get("period_match") or candidate.get("period_match") or 0.0)
        + 0.07 * retrieval
    )


def min_rank(candidates: list[dict[str, Any]], key: str) -> dict[str, Any] | None:
    ranked = []
    for c in candidates:
        if c.get("candidate_source") != "retrieved":
            continue
        try:
            ranked.append((int(c.get(key)), c))
        except (TypeError, ValueError):
            pass
    return min(ranked, key=lambda x: x[0])[1] if ranked else None


def max_choice(candidates: list[dict[str, Any]], key) -> dict[str, Any] | None:
    return max(candidates, key=key) if candidates else None


def load_calibrator(path: Path | None):
    if path is None:
        return None
    if not path.is_file():
        raise FileNotFoundError(path)
    import joblib
    payload = joblib.load(path)
    return payload if isinstance(payload, dict) and "model" in payload else {"model": payload, "feature_names": FEATURE_NAMES}


def calibrated_probabilities(candidates: list[dict[str, Any]], calibrator) -> dict[str, float]:
    if calibrator is None or not candidates:
        return {}
    names = list(calibrator.get("feature_names") or FEATURE_NAMES)
    matrix = [feature_vector(c, names) for c in candidates]
    probs = calibrator["model"].predict_proba(matrix)[:, 1]
    return {str(c["internal_table_uid"]): float(p) for c, p in zip(candidates, probs)}


def verifier(item: dict[str, Any], candidate: dict[str, Any] | None, token_gate: float, bigram_gate: float) -> dict[str, Any]:
    if candidate is None:
        return {"verdict": "UNSUPPORTED", "reason": "No eligible candidate."}
    g = grounding(item, candidate, token_gate, bigram_gate)
    if not g["guard_pass"]:
        return {"verdict": "UNSUPPORTED", "reason": "Recovered adjacent table failed direct-evidence grounding guard."}
    e = candidate.get("evidence_features") or {}
    metadata = float(candidate.get("metadata_score") or 0.0)
    row_score = float(e.get("row_score") or 0.0)
    family = str((item.get("question_plan") or {}).get("family") or item.get("weak_family") or "")
    if metadata < 0.70:
        return {"verdict": "UNSUPPORTED", "reason": f"Metadata agreement too weak ({metadata:.2f})."}
    if g["token_coverage"] < 0.70 or row_score < 0.40:
        return {"verdict": "UNSUPPORTED", "reason": f"Grounding/row evidence too weak (ground={g['token_coverage']:.2f}, row={row_score:.2f})."}
    if family == "direct_lookup":
        if bool(e.get("numeric")) and g["token_coverage"] >= 0.75:
            return {"verdict": "SUPPORTED", "reason": "Metric grounded in direct evidence with numeric value."}
        return {"verdict": "UNCERTAIN", "reason": "Direct lookup lacks grounded numeric evidence."}
    if family in {"temporal_change", "ratio_or_derived"}:
        return {"verdict": "PARTIAL", "reason": "Multi-value family requires operand-level validation."}
    return {"verdict": "PARTIAL", "reason": "Complex family remains provisional until calibrated/audited."}


def review_item(item: dict[str, Any], calibrator, high_threshold: float, calibrated_threshold: float, token_gate: float, bigram_gate: float) -> dict[str, Any]:
    candidates = list(item.get("candidates") or [])
    if not candidates:
        return {
            "id": int(item["id"]), "question": item["question"],
            "family": (item.get("question_plan") or {}).get("family") or item.get("weak_family"),
            "machine_candidate_uid": None, "machine_candidate_rank": None,
            "agent_votes": {}, "agreement": 0.0,
            "verifier": {"verdict": "UNSUPPORTED", "reason": "No candidate in bundle."},
            "machine_confidence": 0.0, "calibrated_probability": None,
            "consensus_status": "retrieval_failure",
            "review_reason": "No candidate in Top-K; this is not proof that gold is absent from corpus.",
        }

    guarded = [c for c in candidates if grounding(item, c, token_gate, bigram_gate)["guard_pass"]]
    if not guarded:
        return {
            "id": int(item["id"]), "question": item["question"],
            "family": (item.get("question_plan") or {}).get("family") or item.get("weak_family"),
            "machine_candidate_uid": None, "machine_candidate_rank": None,
            "agent_votes": {}, "agreement": 0.0,
            "verifier": {"verdict": "UNSUPPORTED", "reason": "Every candidate failed grounding guard."},
            "machine_confidence": 0.0, "calibrated_probability": None,
            "consensus_status": "needs_human", "review_reason": "No grounded candidate survived source-aware guard.",
        }

    lex = min_rank(guarded, "lexical_rank")
    dense = min_rank(guarded, "dense_rank")
    metadata = max_choice(guarded, lambda c: 0.50*float(c.get("metadata_score") or 0.0) + 0.30*grounding(item,c,token_gate,bigram_gate)["quality"] + 0.20*float((c.get("evidence_features") or {}).get("row_score") or 0.0))
    evidence = max_choice(guarded, lambda c: candidate_score(item,c,token_gate,bigram_gate))
    challenger = max_choice(guarded, lambda c: 0.58*grounding(item,c,token_gate,bigram_gate)["quality"] + 0.32*float((c.get("evidence_features") or {}).get("row_score") or 0.0) + 0.10*float(c.get("metadata_score") or 0.0))
    grounder = max_choice(guarded, lambda c: 0.75*grounding(item,c,token_gate,bigram_gate)["quality"] + 0.25*float(c.get("metadata_score") or 0.0))

    def uid(c): return None if c is None else str(c["internal_table_uid"])
    votes = {
        "lexical_agent": uid(lex), "dense_agent": uid(dense), "metadata_agent": uid(metadata),
        "evidence_agent": uid(evidence), "challenger_agent": uid(challenger), "grounding_agent": uid(grounder),
    }
    probs = calibrated_probabilities(guarded, calibrator)
    if probs:
        learned_uid = max(probs, key=probs.get)
        votes["calibrator_agent"] = learned_uid

    usable = [x for x in votes.values() if x]
    counts = Counter(usable)
    max_votes = max(counts.values())
    tied = {u for u, n in counts.items() if n == max_votes}
    selected = max(
        [c for c in guarded if str(c["internal_table_uid"]) in tied],
        key=lambda c: candidate_score(item,c,token_gate,bigram_gate),
    )
    selected_uid = str(selected["internal_table_uid"])
    agreement = counts[selected_uid] / max(1, len(usable))
    check = verifier(item, selected, token_gate, bigram_gate)
    g = grounding(item, selected, token_gate, bigram_gate)
    e = selected.get("evidence_features") or {}
    metadata_score = float(selected.get("metadata_score") or 0.0)
    retrieval = max(reciprocal(selected.get("lexical_rank")), reciprocal(selected.get("dense_rank")))
    heuristic = min(1.0, 0.30*agreement + 0.24*float(e.get("row_score") or 0.0) + 0.18*metadata_score + 0.23*float(g["quality"]) + 0.05*retrieval)
    calibrated = probs.get(selected_uid) if probs else None
    confidence = 0.65*calibrated + 0.35*heuristic if calibrated is not None else heuristic
    family = str((item.get("question_plan") or {}).get("family") or item.get("weak_family") or "")
    challenger_agrees = votes.get("challenger_agent") == selected_uid
    supported = check["verdict"] == "SUPPORTED"

    if calibrated is not None and calibrated >= calibrated_threshold and agreement >= 0.60 and challenger_agrees and supported:
        status, reason = "machine_calibrated", "Calibrator + source-aware consensus + verifier agree."
    elif family == "direct_lookup" and confidence >= high_threshold and agreement >= 0.60 and challenger_agrees and supported:
        status, reason = "machine_high_confidence", "Grounded direct-lookup consensus."
    elif check["verdict"] == "UNSUPPORTED" or agreement < 0.40:
        status, reason = "needs_human", "Verifier rejected the candidate or agents disagree."
    else:
        status, reason = "machine_provisional", "Candidate selected, but not strong enough to promote to gold."

    rejected_adjacent = [
        {
            "rank": c.get("rank"), "uid": c.get("internal_table_uid"),
            **grounding(item,c,token_gate,bigram_gate),
        }
        for c in candidates
        if c.get("candidate_source") == "adjacent_previous_due_context"
        and not grounding(item,c,token_gate,bigram_gate)["guard_pass"]
    ]
    return {
        "id": int(item["id"]), "question": item["question"], "family": family,
        "machine_candidate_uid": selected_uid, "machine_candidate_rank": int(selected.get("rank") or 0),
        "machine_candidate_summary": selected.get("one_line_summary"),
        "machine_candidate_direct_evidence": selected.get("direct_evidence"),
        "machine_candidate_source": selected.get("candidate_source"),
        "agent_votes": votes, "vote_counts": dict(counts), "agreement": agreement,
        "verifier": check, "grounding": g, "rejected_adjacent_candidates": rejected_adjacent,
        "heuristic_confidence": heuristic, "calibrated_probability": calibrated,
        "machine_confidence": confidence, "challenger_agrees": challenger_agrees,
        "consensus_status": status, "review_reason": reason,
    }


def make_queue(reviews: list[dict[str, Any]], size: int) -> list[dict[str, Any]]:
    if size <= 0: return []
    by_family: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in reviews: by_family[str(r.get("family") or "unknown")].append(r)
    chosen: list[dict[str, Any]] = []; seen: set[int] = set()
    for family in sorted(by_family):
        group = by_family[family]
        confident = sorted(group, key=lambda r: float(r.get("machine_confidence") or 0.0), reverse=True)
        uncertain = sorted(group, key=lambda r: (r.get("consensus_status") not in {"needs_human","retrieval_failure"}, float(r.get("machine_confidence") or 0.0)))
        for r in [confident[0], uncertain[0]]:
            qid = int(r["id"])
            if qid not in seen:
                chosen.append({"id": qid, "family": family, "machine_status": r.get("consensus_status"), "machine_confidence": r.get("machine_confidence")})
                seen.add(qid)
    for r in sorted(reviews, key=lambda r: (r.get("consensus_status") not in {"needs_human","retrieval_failure"}, float(r.get("machine_confidence") or 0.0))):
        if len(chosen) >= size: break
        qid = int(r["id"])
        if qid not in seen:
            chosen.append({"id": qid, "family": r.get("family"), "machine_status": r.get("consensus_status"), "machine_confidence": r.get("machine_confidence")})
            seen.add(qid)
    return chosen[:size]


def main() -> None:
    a = parse_args(); bundle = a.bundle_dir.resolve()
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    if int(manifest.get("error_count") or 0) != 0:
        raise RuntimeError("Refuse auto-review: bundle contains retrieval errors.")
    if int(manifest.get("schema_version") or 0) < 3:
        raise RuntimeError("auto_review_bundle_v3.py requires schema_version >= 3.")
    items = load_jsonl(bundle / "review_items.jsonl")
    calibrator = load_calibrator(a.calibrator)
    reviews = [review_item(x, calibrator, a.high_threshold, a.calibrated_threshold, a.adjacent_min_token_coverage, a.adjacent_min_bigram_ratio) for x in items]
    write_jsonl(a.output, reviews)
    print("Machine reviews:", a.output)
    counts = Counter(str(r.get("consensus_status")) for r in reviews)
    print("Status counts:", dict(counts))
    print("Adjacent candidates rejected by grounding guard:", sum(len(r.get("rejected_adjacent_candidates") or []) for r in reviews))
    if a.seed_queue:
        queue = make_queue(reviews, a.seed_size); write_jsonl(a.seed_queue, queue); print("Human seed queue:", a.seed_queue, "count=", len(queue))
    if a.needs_human_queue:
        queue = [
            {"id": int(r["id"]), "family": r.get("family"), "machine_status": r.get("consensus_status"), "machine_confidence": r.get("machine_confidence")}
            for r in reviews if r.get("consensus_status") in {"needs_human", "retrieval_failure"}
        ]
        write_jsonl(a.needs_human_queue, queue); print("Needs-human queue:", a.needs_human_queue, "count=", len(queue))


if __name__ == "__main__": main()
