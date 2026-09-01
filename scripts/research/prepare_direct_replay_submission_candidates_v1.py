#!/usr/bin/env python3
"""Prepare a compact direct-source replay artifact for submission builds.

The source file is a larger machine-review artifact.  This adapter copies only
the records that have one exact source candidate and a replayed output value;
the competition builder still replays every copied coordinate against the
current structured table before using it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number} is not a JSON object")
            rows.append(value)
    return rows


def compact_record(record: dict[str, Any]) -> dict[str, Any]:
    selected_uid = str(record.get("machine_selected_uid") or "")
    candidates = record.get("valid_exact_candidates") or []
    candidate = next(
        (
            value
            for value in candidates
            if str(value.get("internal_table_uid") or "") == selected_uid
        ),
        None,
    )
    if not isinstance(candidate, dict):
        raise ValueError(
            f"machine_selected_uid={selected_uid!r} is absent from valid_exact_candidates"
        )
    return {
        "schema_version": 1,
        "protocol": record.get("protocol"),
        "question_id": record.get("question_id"),
        "status": record.get("status"),
        "machine_consensus_status": record.get("machine_consensus_status"),
        "machine_selected_uid": record.get("machine_selected_uid"),
        "distinct_exact_value_count": record.get("distinct_exact_value_count"),
        "replay_unit": record.get("replay_unit"),
        "replay_value": record.get("replay_value"),
        "valid_exact_candidate_count": len(candidates),
        "valid_exact_candidates": [
            {
                "internal_table_uid": candidate.get("internal_table_uid"),
                "row_index": candidate.get("row_index"),
                "column_index": candidate.get("column_index"),
                "raw_value": candidate.get("raw_value"),
                "parsed_value": candidate.get("parsed_value"),
                "comparison_value": candidate.get("comparison_value"),
                "source_unit": candidate.get("source_unit"),
                "comparison_unit": candidate.get("comparison_unit"),
            }
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--include-provisional",
        action="store_true",
        help="include unique replay-ready records marked machine_provisional",
    )
    args = parser.parse_args()
    source = args.input.expanduser().resolve()
    output = args.output.expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    records = read_jsonl(source)
    allowed = {"machine_calibrated"}
    if args.include_provisional:
        allowed.add("machine_provisional")

    selected: list[dict[str, Any]] = []
    statuses: Counter[str] = Counter()
    seen_questions: set[int] = set()
    for record in records:
        if record.get("status") != "shadow_replay_ready":
            continue
        if record.get("distinct_exact_value_count") != 1:
            continue
        if record.get("machine_consensus_status") not in allowed:
            continue
        candidates = record.get("valid_exact_candidates")
        if not isinstance(candidates, list) or not candidates:
            continue
        selected_uid = str(record.get("machine_selected_uid") or "")
        if not any(
            isinstance(candidate, dict)
            and str(candidate.get("internal_table_uid") or "") == selected_uid
            for candidate in candidates
        ):
            continue
        question_id = int(record["question_id"])
        if question_id in seen_questions:
            raise ValueError(f"duplicate selected question_id={question_id}")
        seen_questions.add(question_id)
        selected.append(compact_record(record))
        statuses[str(record.get("machine_consensus_status"))] += 1

    selected.sort(key=lambda row: int(row["question_id"]))
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for record in selected:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")

    manifest = {
        "schema_version": 1,
        "protocol": "vifinqa_direct_replay_submission_candidate_manifest_v1",
        "source": {
            "path": str(source),
            "sha256": sha256_file(source),
            "protocol": "independent_direct_exact_source_replay_v1",
        },
        "output": {
            "path": str(output),
            "sha256": sha256_file(output),
            "record_count": len(selected),
            "status_counts": dict(sorted(statuses.items())),
        },
        "selection_contract": {
            "accepted_status": "shadow_replay_ready",
            "distinct_exact_value_count": 1,
            "selected_exact_candidate_is_present": True,
            "include_provisional": bool(args.include_provisional),
            "human_verified": False,
            "promotion_allowed": False,
            "numeric_authority": "current_structured_table_decimal_replay",
            "lane": "authorized_best_effort_submission_candidate",
        },
    }
    manifest_path = output.with_suffix(output.suffix + ".manifest.json")
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
