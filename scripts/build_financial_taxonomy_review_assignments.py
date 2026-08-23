#!/usr/bin/env python3
"""Create two blind, hash-bound review assignments from a calibration set."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


PROTOCOL = "financial_taxonomy_blind_review_assignment_v1"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8-sig") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def slot_key(slot: str, item_id: str) -> str:
    return hashlib.sha256(f"{slot}|{item_id}".encode("utf-8")).hexdigest()


def immutable_review_payload(item: dict[str, Any]) -> dict[str, Any]:
    return {
        key: item.get(key)
        for key in (
            "calibration_item_id",
            "risk_band",
            "stratum",
            "review_type",
            "risk_score",
            "risk_reason_codes",
            "candidate",
            "source_excerpt",
            "review_instructions",
            "review_contract",
            "source_contract",
        )
    }


def immutable_review_sha256(item: dict[str, Any]) -> str:
    encoded = json.dumps(
        immutable_review_payload(item),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def build_assignments(
    *,
    calibration_set: Path,
    calibration_manifest: Path,
    output_dir: Path,
) -> dict[str, Any]:
    items = load_jsonl(calibration_set)
    ids = [str(item.get("calibration_item_id") or "") for item in items]
    if "" in ids or len(ids) != len(set(ids)):
        raise ValueError("Calibration set contains missing or duplicate item IDs")
    source_manifest = json.loads(calibration_manifest.read_text(encoding="utf-8"))
    expected = ((source_manifest.get("output") or {}).get("sha256"))
    actual = sha256_file(calibration_set)
    if expected != actual:
        raise ValueError("Calibration set hash does not match its manifest")
    if any(
        value is not None or key == "reason_codes" and value != []
        for item in items
        for key, value in (item.get("calibration_labels") or {}).items()
        if key != "reason_codes" or value != []
    ):
        raise ValueError("Calibration set already contains review labels")

    output_dir.mkdir(parents=True, exist_ok=True)
    outputs: dict[str, dict[str, str]] = {}
    for slot in ("reviewer_a", "reviewer_b"):
        output = output_dir / f"financial_taxonomy_{slot}_assignment_v1.jsonl"
        ordered = sorted(items, key=lambda item: slot_key(slot, str(item["calibration_item_id"])))
        with output.open("w", encoding="utf-8") as handle:
            for position, item in enumerate(ordered, start=1):
                record = {
                    **item,
                    "assignment_protocol": PROTOCOL,
                    "immutable_review_payload_sha256": immutable_review_sha256(item),
                    "reviewer_slot": slot,
                    "assignment_position": position,
                    "review_provenance": {
                        "reviewer_id": None,
                        "reviewer_type": None,
                        "completed_at_utc": None,
                        "blind_to_other_review": True,
                    },
                }
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        outputs[slot] = {"path": str(output), "sha256": sha256_file(output)}

    manifest = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "assignment_count_per_reviewer": len(items),
        "reviewer_slots": ["reviewer_a", "reviewer_b"],
        "ordering_policy": "slot-specific deterministic sha256 order",
        "blind_to_other_review": True,
        "labels_prepopulated": False,
        "input": {
            "calibration_set": {"path": str(calibration_set), "sha256": actual},
            "calibration_manifest": {
                "path": str(calibration_manifest),
                "sha256": sha256_file(calibration_manifest),
            },
        },
        "outputs": outputs,
        "answer_eligible": False,
        "training_eligible": False,
        "submission_eligible": False,
        "promotion_allowed": False,
    }
    manifest_path = output_dir / "financial_taxonomy_review_assignments_v1.manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--calibration-set", type=Path, required=True)
    parser.add_argument("--calibration-manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    manifest = build_assignments(
        calibration_set=args.calibration_set.resolve(),
        calibration_manifest=args.calibration_manifest.resolve(),
        output_dir=args.output_dir.resolve(),
    )
    print(json.dumps(manifest, ensure_ascii=False))


if __name__ == "__main__":
    main()
