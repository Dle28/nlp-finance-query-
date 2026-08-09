#!/usr/bin/env python3
"""Audit a semantic-rerank sidecar without changing any review label.

Scores are meaningful only in conjunction with the source-bound candidate UID
and existing raw-V2 review gates.  This script measures agreement/margins and
optionally compares them with pre-existing complete human decisions; it never
promotes a candidate by itself.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle-dir", type=Path, required=True)
    parser.add_argument("--semantic-rerank", type=Path, required=True)
    parser.add_argument("--machine-reviews", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--human-reviews",
        type=Path,
        default=None,
        help="Optional existing complete human decisions used only as a held-out audit signal.",
    )
    return parser.parse_args()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8-sig") as file:
        return [json.loads(line) for line in file if line.strip()]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as file:
        json.dump(value, file, ensure_ascii=False, indent=2)
        file.flush()
        os.fsync(file.fileno())
    temporary.replace(path)


def validate_sidecar(bundle: Path, sidecar: Path) -> dict[str, Any]:
    manifest_path = sidecar.with_suffix(".manifest.json")
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Semantic-rerank manifest missing: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if int(manifest.get("schema_version") or 0) != 1:
        raise ValueError("Unsupported semantic-rerank schema")
    expected = {
        "bundle_review_items_sha256": bundle / "review_items.jsonl",
        "structured_tables_sha256": bundle / "tables_structured_v2.jsonl",
        "evidence_context_sha256": bundle / "tables_evidence_context_v1.jsonl",
    }
    for key, path in expected.items():
        if str(manifest.get(key) or "") != sha256_file(path):
            raise ValueError(f"Semantic-rerank manifest does not match {path.name}")
    if str(manifest.get("sidecar_sha256") or "") != sha256_file(sidecar):
        raise ValueError("Semantic-rerank sidecar hash does not match its manifest")
    return manifest


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = round((len(ordered) - 1) * q)
    return round(ordered[index], 6)


def complete_human_uids(path: Path | None) -> dict[int, set[str]]:
    if path is None:
        return {}
    output: dict[int, set[str]] = {}
    for row in load_jsonl(path):
        if str(row.get("annotation_status") or "") != "human_verified":
            continue
        if str(row.get("evidence_completeness") or "") != "complete":
            continue
        uids = {str(value) for value in row.get("positive_table_uids") or [] if str(value)}
        if uids:
            output[int(row["id"])] = uids
    return output


def main() -> None:
    args = parse_args()
    bundle = args.bundle_dir.resolve()
    sidecar = args.semantic_rerank.resolve()
    manifest = validate_sidecar(bundle, sidecar)
    score_rows = {int(row["id"]): row for row in load_jsonl(sidecar)}
    items = {int(row["id"]): row for row in load_jsonl(bundle / "review_items.jsonl")}
    reviews = {int(row["id"]): row for row in load_jsonl(args.machine_reviews.resolve())}
    human = complete_human_uids(args.human_reviews.resolve() if args.human_reviews else None)

    rows: list[dict[str, Any]] = []
    margins: list[float] = []
    status_counts: Counter[str] = Counter()
    reviewed_matches = 0
    eligible_questions = 0
    human_checked = 0
    human_top_match = 0
    for qid, score_row in score_rows.items():
        scores = score_row.get("candidate_scores") or []
        if not scores:
            continue
        if qid not in items or qid not in reviews:
            raise ValueError(f"Semantic score Q{qid} lacks bundle item or machine review")
        if any(value.get("score") is None for value in scores):
            raise ValueError(f"Semantic score Q{qid} is incomplete")
        ordered = sorted(scores, key=lambda value: float(value["score"]), reverse=True)
        top = ordered[0]
        next_score = float(ordered[1]["score"]) if len(ordered) > 1 else None
        margin = float(top["score"]) - next_score if next_score is not None else None
        if margin is not None:
            margins.append(margin)
        review = reviews[qid]
        review_uid = str(review.get("machine_candidate_uid") or "")
        review_score = next(
            (float(value["score"]) for value in ordered if str(value.get("internal_table_uid") or "") == review_uid),
            None,
        )
        match = bool(review_uid and review_uid == str(top["internal_table_uid"]))
        reviewed_matches += int(match)
        eligible_questions += 1
        status = str(review.get("consensus_status") or "")
        status_counts[status] += 1
        human_uids = human.get(qid, set())
        if human_uids:
            human_checked += 1
            human_top_match += int(str(top["internal_table_uid"]) in human_uids)
        item = items[qid]
        rows.append(
            {
                "id": qid,
                "family": score_row.get("family"),
                "question": item.get("question"),
                "machine_status": status,
                "machine_candidate_uid": review_uid or None,
                "machine_candidate_rank": review.get("machine_candidate_rank"),
                "machine_score": review_score,
                "semantic_top_uid": top["internal_table_uid"],
                "semantic_top_rank": top.get("rank"),
                "semantic_top_score": round(float(top["score"]), 6),
                "semantic_margin": None if margin is None else round(margin, 6),
                "matches_machine_choice": match,
                "matches_complete_human_choice": (
                    None if not human_uids else str(top["internal_table_uid"]) in human_uids
                ),
            }
        )

    disagreements = sorted(
        (row for row in rows if not row["matches_machine_choice"]),
        key=lambda row: float(row["semantic_margin"] or 0.0),
        reverse=True,
    )
    output = {
        "semantic_rerank_manifest": manifest,
        "audit_scope": {
            "scored_question_count": eligible_questions,
            "machine_review_match_count": reviewed_matches,
            "machine_review_match_rate": round(reviewed_matches / eligible_questions, 6)
            if eligible_questions
            else None,
            "machine_status_counts": dict(status_counts),
            "complete_human_audit_count": human_checked,
            "semantic_top_matches_complete_human_count": human_top_match,
            "semantic_top_matches_complete_human_rate": round(human_top_match / human_checked, 6)
            if human_checked
            else None,
        },
        "margin_quantiles": {
            "p10": percentile(margins, 0.10),
            "p25": percentile(margins, 0.25),
            "p50": percentile(margins, 0.50),
            "p75": percentile(margins, 0.75),
            "p90": percentile(margins, 0.90),
            "mean": round(statistics.fmean(margins), 6) if margins else None,
        },
        "high_margin_disagreements": disagreements[:100],
        "note": (
            "Semantic scores are diagnostic only. No status is promoted here; any later use must retain "
            "raw-V2 exact-row/cell, canonical-header, unit and provenance gates."
        ),
    }
    write_json(args.output.resolve(), output)
    print(json.dumps({"output": str(args.output.resolve()), **output["audit_scope"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
