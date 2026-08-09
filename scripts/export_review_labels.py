#!/usr/bin/env python3
"""Merge human review and calibrated machine review without losing provenance.

Human labels always win. Machine labels are emitted as pseudo-labels with an
explicit weight; they are never renamed to human_verified.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Iterable


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--machine-reviews", type=Path, required=True)
    parser.add_argument("--human-labels", type=Path, default=None)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--include-provisional", action="store_true")
    parser.add_argument("--calibrated-weight", type=float, default=0.80)
    parser.add_argument("--high-weight", type=float, default=0.65)
    parser.add_argument("--provisional-weight", type=float, default=0.30)
    return parser.parse_args()


def load_jsonl(path: Path | None) -> list[dict[str, Any]]:
    if path is None or not path.is_file():
        return []
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


def human_training_eligible(row: dict[str, Any]) -> bool:
    return bool(
        str(row.get("annotation_status") or "") == "human_verified"
        and row.get("positive_table_uids")
        and (row.get("structure_validation") or {}).get("complete")
    )


def machine_training_eligible(
    row: dict[str, Any], *, include_provisional: bool = False
) -> bool:
    status = str(row.get("consensus_status") or "")
    allowed = {"machine_calibrated", "machine_high_confidence"}
    if include_provisional:
        allowed.add("machine_provisional")
    return bool(
        status in allowed
        and row.get("machine_candidate_uid")
        and (row.get("structure_validation") or {}).get("validated")
    )


def main() -> None:
    args = parse_args()
    machine = {int(row["id"]): row for row in load_jsonl(args.machine_reviews)}
    human = {int(row["id"]): row for row in load_jsonl(args.human_labels)}

    output: list[dict[str, Any]] = []
    excluded: list[int] = []
    excluded_human_nontraining: list[int] = []

    all_ids = sorted(set(machine) | set(human))
    for qid in all_ids:
        if qid in human:
            row = dict(human[qid])
            if not human_training_eligible(row):
                excluded_human_nontraining.append(qid)
                continue
            row["label_source"] = "human"
            row["training_weight"] = 1.0
            output.append(row)
            continue

        review = machine[qid]
        status = str(review.get("consensus_status") or "")
        selected = review.get("machine_candidate_uid")

        if not machine_training_eligible(
            review, include_provisional=args.include_provisional
        ):
            excluded.append(qid)
            continue

        if status == "machine_calibrated" and selected:
            weight = args.calibrated_weight
        elif status == "machine_high_confidence" and selected:
            weight = args.high_weight
        elif status == "machine_provisional" and selected and args.include_provisional:
            weight = args.provisional_weight
        else:  # guarded by machine_training_eligible; kept fail-closed
            excluded.append(qid)
            continue

        output.append(
            {
                "id": qid,
                "question": review.get("question"),
                "annotation_status": status,
                "human_verified": False,
                "positive_table_uids": [selected],
                "selected_ranks": [review.get("machine_candidate_rank")],
                "label_source": "machine",
                "training_weight": weight,
                "machine_confidence": review.get("machine_confidence"),
                "calibrated_probability": review.get("calibrated_probability"),
                "structure_validation": review.get("structure_validation"),
                "agent_votes": review.get("agent_votes"),
                "verifier": review.get("verifier"),
                # Autonomous V4 reviews attach their own source/semantic/critic
                # protocol here.  Keep it with machine silver so a downstream
                # trainer can reject ordinary machine guesses.
                "machine_self_review": review.get("machine_self_review"),
            }
        )

    write_jsonl(args.output, output)

    print("Exported labels:", len(output))
    print("Human labels:", len(human))
    print("Excluded human partial/no-candidate IDs:", excluded_human_nontraining)
    print("Excluded unresolved/provisional IDs:", excluded)
    print("Output:", args.output)


if __name__ == "__main__":
    main()
