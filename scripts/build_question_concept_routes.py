#!/usr/bin/env python3
"""Materialize deterministic, non-promotable question-to-concept routes."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from finance_query.financial_taxonomy import FinancialTaxonomy, normalize_label
from finance_query.metric_registry import FinancialMetricRegistry, QUESTION_ROUTE_PROTOCOL


TOP_UNMATCHED_PHRASE_LIMIT = 20


def sha256_file(path: Path) -> str:
    """Return the content hash used to bind an input or materialized output."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    """Load JSONL records without changing their question content."""
    with path.open(encoding="utf-8-sig") as handle:
        rows = [json.loads(line) for line in handle if line.strip()]
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError("Questions JSONL must contain object records")
    return rows


def question_id(item: Mapping[str, Any]) -> int:
    """Read a required ID fail-closed; never synthesize one from row order."""
    raw_id = item.get("id") if "id" in item else item.get("question_id")
    if raw_id is None or isinstance(raw_id, bool):
        raise ValueError("Question record is missing a usable id")
    try:
        return int(raw_id)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Question record has invalid id: {raw_id!r}") from exc


def _source_contract() -> dict[str, bool]:
    return {
        "navigation_metadata_only": True,
        "evidence_eligible": False,
        "training_eligible": False,
        "submission_eligible": False,
        "promotion_allowed": False,
    }


def _top_unmatched_phrases(rows: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    phrases = Counter(
        str(row.get("normalized_question") or normalize_label(row.get("question") or ""))
        for row in rows
        if "NO_LITERAL_METRIC_OR_CONCEPT_MATCH" in (row.get("reason_codes") or [])
    )
    return [
        {"normalized_phrase": phrase, "count": count}
        for phrase, count in sorted(phrases.items(), key=lambda pair: (-pair[1], pair[0]))[
            :TOP_UNMATCHED_PHRASE_LIMIT
        ]
    ]


def build_routes(
    *,
    questions: Path,
    taxonomy_path: Path,
    metric_registry_path: Path,
    output: Path,
) -> dict[str, Any]:
    """Build one hash-bound route/abstain record for every input question ID."""
    taxonomy = FinancialTaxonomy.load(taxonomy_path)
    registry = FinancialMetricRegistry.load(metric_registry_path, taxonomy=taxonomy)
    items = load_jsonl(questions)
    ids = [question_id(item) for item in items]
    duplicate_ids = sorted(question_id for question_id, count in Counter(ids).items() if count > 1)
    if duplicate_ids:
        raise ValueError(f"Questions contain duplicate ids: {duplicate_ids}")
    # Sort only by explicit IDs: a changed input record order cannot change the
    # JSONL artifact.  No question-derived value is used as a tie breaker.
    ordered_items = [item for _id, item in sorted(zip(ids, items), key=lambda pair: pair[0])]
    rows = [registry.build_question_route(item) for item in ordered_items]
    output_ids = [int(row["question_id"]) for row in rows]
    if output_ids != sorted(ids) or len(output_ids) != len(set(output_ids)):
        raise ValueError("Route materialization did not preserve exactly one record per question id")

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")

    status_counts = Counter(str(row["route_status"]) for row in rows)
    metric_counts: Counter[str] = Counter()
    concept_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    literal_candidate_question_count = 0
    for row in rows:
        reason_counts.update(str(value) for value in row.get("reason_codes") or [])
        stages = row.get("stages") or []
        if stages:
            literal_candidate_question_count += 1
        for stage in stages:
            if stage.get("metric_id"):
                metric_counts[str(stage["metric_id"])] += 1
            for operand in stage.get("required_operands") or []:
                if operand.get("concept_id"):
                    concept_counts[str(operand["concept_id"])] += 1

    question_count = len(rows)
    route_ready_question_count = sum(
        1 for row in rows if str(row.get("route_status")) != "abstain"
    )
    output_hash = sha256_file(output)
    manifest = {
        "schema_version": 1,
        "protocol": QUESTION_ROUTE_PROTOCOL,
        "question_count": question_count,
        "question_id_count": len(output_ids),
        "route_status_counts": dict(sorted(status_counts.items())),
        "metric_counts": dict(sorted(metric_counts.items())),
        "concept_counts": dict(sorted(concept_counts.items())),
        "reason_code_counts": dict(sorted(reason_counts.items())),
        # Candidate coverage is literal routing coverage, including records
        # correctly abstained for missing contract context.  Readiness is
        # separate so the two cannot be conflated.
        "literal_candidate_question_count": literal_candidate_question_count,
        "candidate_coverage": (
            literal_candidate_question_count / question_count if question_count else 0.0
        ),
        "route_ready_question_count": route_ready_question_count,
        "route_ready_coverage": (
            route_ready_question_count / question_count if question_count else 0.0
        ),
        "top_unmatched_normalized_phrases": _top_unmatched_phrases(rows),
        "inputs": {
            "questions": {"path": str(questions), "sha256": sha256_file(questions)},
            "taxonomy": {"path": str(taxonomy_path), "sha256": sha256_file(taxonomy_path)},
            "metric_registry": {
                "path": str(metric_registry_path),
                "sha256": sha256_file(metric_registry_path),
            },
        },
        "output": {"path": str(output), "sha256": output_hash},
        "source_contract": _source_contract(),
        "answer_eligible": False,
        "training_eligible": False,
        "submission_eligible": False,
        "promotion_allowed": False,
    }
    output.with_suffix(".manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--questions", type=Path, required=True)
    parser.add_argument("--taxonomy", type=Path, required=True)
    parser.add_argument("--metric-registry", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = build_routes(
        questions=args.questions.resolve(),
        taxonomy_path=args.taxonomy.resolve(),
        metric_registry_path=args.metric_registry.resolve(),
        output=args.output.resolve(),
    )
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
