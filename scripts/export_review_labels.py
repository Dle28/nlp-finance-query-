#!/usr/bin/env python3
"""Merge human review and calibrated machine review without losing provenance.

Human labels always win. Machine labels are emitted as pseudo-labels with an
explicit weight; they are never renamed to human_verified.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterable

from finance_query.evidence_context import AUTONOMOUS_REVIEW_PROTOCOL
from finance_query.direct_replay import DIRECT_REPLAY_PROTOCOL


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--machine-reviews", type=Path, required=True)
    parser.add_argument(
        "--direct-replay",
        type=Path,
        default=None,
        help="Hash-bound independent direct replay; required for production machine silver.",
    )
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


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_direct_replay(path: Path | None, machine_reviews: Path) -> tuple[dict[int, dict[str, Any]], str | None]:
    if path is None:
        return {}, None
    manifest_path = path.with_suffix(".manifest.json")
    if not path.is_file() or not manifest_path.is_file():
        raise FileNotFoundError("Direct replay sidecar or manifest is missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (
        int(manifest.get("schema_version") or 0) != 1
        or str(manifest.get("protocol") or "") != DIRECT_REPLAY_PROTOCOL
        or manifest.get("answer_eligible") is not False
        or manifest.get("training_eligible") is not False
        or manifest.get("provenance_promotion_allowed") is not False
    ):
        raise ValueError("Direct replay protocol is invalid")
    sidecar_sha = sha256_file(path)
    if str(manifest.get("sidecar_sha256") or "") != sidecar_sha:
        raise ValueError("Direct replay sidecar hash differs from manifest")
    if str(manifest.get("machine_reviews_sha256") or "") != sha256_file(machine_reviews):
        raise ValueError("Direct replay was built from different machine reviews")
    output: dict[int, dict[str, Any]] = {}
    for row in load_jsonl(path):
        qid = int(row["question_id"])
        if qid in output:
            raise ValueError(f"Duplicate Q{qid} in direct replay")
        output[qid] = row
    return output, sidecar_sha


def id_preview(ids: list[int], limit: int = 12) -> str:
    """Render a bounded operational log summary without hiding the count."""
    if not ids:
        return "0"
    preview = ", ".join(str(value) for value in ids[:limit])
    suffix = ", …" if len(ids) > limit else ""
    return f"{len(ids)} [{preview}{suffix}]"


def human_training_eligible(row: dict[str, Any]) -> bool:
    return bool(
        str(row.get("annotation_status") or "") == "human_verified"
        and row.get("positive_table_uids")
        and (row.get("structure_validation") or {}).get("complete")
    )


def machine_training_eligible(
    row: dict[str, Any],
    *,
    direct_replay: dict[str, Any] | None = None,
    include_provisional: bool = False,
) -> bool:
    status = str(row.get("consensus_status") or "")
    allowed = {"machine_calibrated", "machine_high_confidence"}
    if include_provisional:
        allowed.add("machine_provisional")
    self_review = row.get("machine_self_review") or {}
    selected_assessment = self_review.get("selected_assessment") or {}
    raw_metric_identity = selected_assessment.get("raw_metric_identity") or {}
    selected_uid = str(row.get("machine_candidate_uid") or "")
    replay_selected_uids = {
        str(candidate.get("internal_table_uid") or "")
        for candidate in (direct_replay or {}).get("valid_exact_candidates") or []
    }
    replay_gate = bool(
        direct_replay
        and str(direct_replay.get("protocol") or "") == DIRECT_REPLAY_PROTOCOL
        and str(direct_replay.get("status") or "") == "shadow_replay_ready"
        and int(direct_replay.get("question_id") or -1) == int(row.get("id") or -2)
        and str(direct_replay.get("machine_consensus_status") or "") == status
        and selected_uid in replay_selected_uids
    )
    return bool(
        status in allowed
        and row.get("machine_candidate_uid")
        and (row.get("structure_validation") or {}).get("validated")
        # Export is the first machine-silver boundary, rather than relying on
        # the later dense trainer to reject an ordinary/legacy machine label.
        # Provisional labels can be exported only through the explicit legacy
        # debug flag and still never pass the machine-silver trainer gate.
        and (
            include_provisional
            and status == "machine_provisional"
            or (
                str(self_review.get("protocol") or "")
                == AUTONOMOUS_REVIEW_PROTOCOL
                and bool(self_review.get("training_eligible"))
                and bool(raw_metric_identity.get("exact"))
                # Replay is an additional exclusion gate, not a new source of
                # labels or provenance promotion.
                and replay_gate
            )
        )
    )


def main() -> None:
    args = parse_args()
    machine = {int(row["id"]): row for row in load_jsonl(args.machine_reviews)}
    human = {int(row["id"]): row for row in load_jsonl(args.human_labels)}
    replay, replay_sha = load_direct_replay(args.direct_replay, args.machine_reviews)

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
            review,
            direct_replay=replay.get(qid),
            include_provisional=args.include_provisional,
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
                "direct_replay_gate": (
                    {
                        "protocol": DIRECT_REPLAY_PROTOCOL,
                        "status": replay[qid]["status"],
                        "question_id": qid,
                        "machine_selected_uid": selected,
                        "replay_artifact_sha256": replay_sha,
                        "training_gate_only": True,
                    }
                    if qid in replay
                    else None
                ),
            }
        )

    write_jsonl(args.output, output)

    print("Exported labels:", len(output))
    print("Human labels:", len(human))
    print("Excluded human partial/no-candidate IDs:", id_preview(excluded_human_nontraining))
    print("Excluded unresolved/provisional IDs:", id_preview(excluded))
    print("Output:", args.output)


if __name__ == "__main__":
    main()
