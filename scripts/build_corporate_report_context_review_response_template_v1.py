#!/usr/bin/env python3
"""Create separate blank response templates for corporate report-context review."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from finance_query.corporate_report_context_review import (  # noqa: E402
    CONTEXT_REVIEW_SOURCE_CONTRACT,
    CORPORATE_REPORT_CONTEXT_RESPONSE_PROTOCOL,
    CORPORATE_REPORT_CONTEXT_REVIEW_VERSION,
    load_jsonl,
    response_template,
    sha256_file,
    validate_corporate_report_context_review_queue,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue-dir", type=Path, required=True)
    parser.add_argument("--reviewer-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest-output", type=Path, required=True)
    args = parser.parse_args()
    queue_dir, output, manifest_output = args.queue_dir.resolve(), args.output.resolve(), args.manifest_output.resolve()
    if output.exists() or manifest_output.exists():
        raise FileExistsError("Refusing to overwrite a response template")
    queue_manifest = validate_corporate_report_context_review_queue(queue_dir)
    queue_path = queue_dir / "corporate_report_context_review_queue_v1.jsonl"
    templates = [response_template(packet, reviewer_id=args.reviewer_id) for packet in load_jsonl(queue_path)]
    output.parent.mkdir(parents=True, exist_ok=True)
    manifest_output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in templates), encoding="utf-8"
    )
    result = {
        "schema_version": CORPORATE_REPORT_CONTEXT_REVIEW_VERSION,
        "protocol": "corporate_report_context_review_response_template_v1",
        "response_protocol": CORPORATE_REPORT_CONTEXT_RESPONSE_PROTOCOL,
        "reviewer_id": args.reviewer_id,
        "template_status": "blank_separate_response_template",
        "response_count": len(templates),
        "question_ids": [row["question_id"] for row in templates],
        "labels_prepopulated": False,
        "materialization_allowed": False,
        "inputs": {
            "queue": {"path": str(queue_path), "sha256": queue_manifest["queue_sha256"]},
            "queue_manifest": {
                "path": str(queue_dir / "corporate_report_context_review_queue_v1.manifest.json"),
                "sha256": sha256_file(queue_dir / "corporate_report_context_review_queue_v1.manifest.json"),
            },
        },
        "outputs": {"template": {"path": str(output), "sha256": sha256_file(output)}},
        "source_contract": dict(CONTEXT_REVIEW_SOURCE_CONTRACT),
    }
    manifest_output.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
