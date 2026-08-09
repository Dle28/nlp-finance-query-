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
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from finance_query.semantic_rerank import (  # noqa: E402
    SEMANTIC_INPUT_RENDERER_VERSION,
    semantic_candidate_input,
    semantic_input_digest,
)


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


def evidence_context_path(bundle: Path, manifest: dict[str, Any]) -> Path:
    """Resolve the sidecar named by a rerank manifest without path traversal.

    Semantic V2 sidecars made before canonical-context V2 default to the V1
    filename. New sidecars carry their context filename explicitly, preserving
    auditability across the source-preserving preprocessing migration.
    """
    name = str(manifest.get("evidence_context_file") or "tables_evidence_context_v1.jsonl")
    if Path(name).name != name:
        raise ValueError("Semantic-rerank context filename must be local to the bundle")
    return bundle / name


def validate_sidecar(bundle: Path, sidecar: Path) -> dict[str, Any]:
    manifest_path = sidecar.with_suffix(".manifest.json")
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Semantic-rerank manifest missing: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if int(manifest.get("schema_version") or 0) not in {1, 2}:
        raise ValueError("Unsupported semantic-rerank schema")
    expected = {
        "bundle_review_items_sha256": bundle / "review_items.jsonl",
        "structured_tables_sha256": bundle / "tables_structured_v2.jsonl",
        "evidence_context_sha256": evidence_context_path(bundle, manifest),
    }
    for key, path in expected.items():
        if str(manifest.get(key) or "") != sha256_file(path):
            raise ValueError(f"Semantic-rerank manifest does not match {path.name}")
    if str(manifest.get("sidecar_sha256") or "") != sha256_file(sidecar):
        raise ValueError("Semantic-rerank sidecar hash does not match its manifest")
    return manifest


def validate_v2_source_inputs(
    bundle: Path,
    manifest: dict[str, Any],
    score_rows: dict[int, dict[str, Any]],
    items: dict[int, dict[str, Any]],
) -> None:
    """Prove every V2 score saw the stated, current exact-source input.

    V1 stored only a digest and remains usable for historical diagnostic
    comparison.  V2 stores its bounded source input and verifies that it can
    be regenerated from the immutable V2 row and canonical context.
    """
    if int(manifest.get("schema_version") or 0) < 2:
        return
    if int(manifest.get("input_renderer_version") or 0) != SEMANTIC_INPUT_RENDERER_VERSION:
        raise ValueError("Semantic-rerank input renderer version is not supported")
    tables = {
        str(row["internal_table_uid"]): row
        for row in load_jsonl(bundle / "tables_structured_v2.jsonl")
    }
    contexts = {
        str(row["internal_table_uid"]): row
        for row in load_jsonl(evidence_context_path(bundle, manifest))
    }
    for qid, score_row in score_rows.items():
        item = items.get(qid)
        if item is None:
            raise ValueError(f"Semantic score Q{qid} lacks a bundle item")
        candidates = {
            (str(candidate.get("internal_table_uid") or ""), int(candidate.get("rank") or 0)): candidate
            for candidate in item.get("candidates") or []
        }
        for score in score_row.get("candidate_scores") or []:
            uid = str(score.get("internal_table_uid") or "")
            rank = int(score.get("rank") or 0)
            candidate = candidates.get((uid, rank))
            source_input = str(score.get("source_input") or "")
            if candidate is None or not source_input:
                raise ValueError(f"Semantic V2 score Q{qid} has no attributable candidate/source input")
            expected = semantic_candidate_input(
                str(item.get("question") or ""),
                candidate,
                tables.get(uid) or {},
                contexts.get(uid) or {},
            )
            if not expected or expected != source_input:
                raise ValueError(f"Semantic V2 source input Q{qid} no longer matches raw V2 context")
            if str(score.get("input_sha256") or "") != semantic_input_digest(item["question"], source_input):
                raise ValueError(f"Semantic V2 source input hash mismatch for Q{qid}")


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
    validate_v2_source_inputs(bundle, manifest, score_rows, items)
    reviews = {int(row["id"]): row for row in load_jsonl(args.machine_reviews.resolve())}
    human = complete_human_uids(args.human_reviews.resolve() if args.human_reviews else None)

    rows: list[dict[str, Any]] = []
    margins: list[float] = []
    status_counts: Counter[str] = Counter()
    reviewed_matches = 0
    eligible_questions = 0
    human_checked = 0
    human_top_match = 0
    human_candidate_available = 0
    human_top_match_when_available = 0
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
        scored_uids = {str(value.get("internal_table_uid") or "") for value in ordered}
        human_candidate_present = bool(human_uids & scored_uids)
        if human_uids:
            human_checked += 1
            top_matches_human = str(top["internal_table_uid"]) in human_uids
            human_top_match += int(top_matches_human)
            human_candidate_available += int(human_candidate_present)
            human_top_match_when_available += int(top_matches_human and human_candidate_present)
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
                "complete_human_candidate_present_in_scored_top_k": (
                    None if not human_uids else human_candidate_present
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
            "complete_human_candidate_present_in_scored_top_k_count": human_candidate_available,
            "semantic_top_matches_complete_human_count": human_top_match,
            "semantic_top_matches_complete_human_rate_all": round(human_top_match / human_checked, 6)
            if human_checked
            else None,
            "semantic_top_matches_complete_human_when_candidate_present_count": human_top_match_when_available,
            "semantic_top_matches_complete_human_when_candidate_present_rate": round(
                human_top_match_when_available / human_candidate_available, 6
            )
            if human_candidate_available
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
