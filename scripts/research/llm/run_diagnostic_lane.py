#!/usr/bin/env python3
"""Build, verify, or evaluate the bounded proposer-only LLM lane."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from finance_query.research.llm.diagnostic_lane import (  # noqa: E402
    build_diagnostic_batch,
    evaluate_diagnostic_batch,
    verify_diagnostic_batch,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build")
    build.add_argument("--source-model-job-manifest", type=Path, required=True)
    build.add_argument("--control-policy", type=Path, required=True)
    build.add_argument("--output-dir", type=Path, required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("manifest", type=Path)
    evaluate = commands.add_parser("evaluate")
    evaluate.add_argument("--diagnostic-manifest", type=Path, required=True)
    evaluate.add_argument("--validated-responses", type=Path, required=True)
    evaluate.add_argument("--human-reviews", type=Path, required=True)
    evaluate.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "build":
        result = build_diagnostic_batch(
            source_model_job_manifest_path=args.source_model_job_manifest.resolve(),
            control_policy_path=args.control_policy.resolve(),
            output_dir=args.output_dir.resolve(),
        )
        payload = {
            "status": "PREPARED_PROPOSER_DIAGNOSTIC_NOT_RUN",
            "packet_count": result.packet_count,
            "manifest": str(result.manifest_path),
        }
    elif args.command == "verify":
        payload = verify_diagnostic_batch(args.manifest.resolve())
    else:
        result = evaluate_diagnostic_batch(
            diagnostic_manifest_path=args.diagnostic_manifest.resolve(),
            validated_responses_path=args.validated_responses.resolve(),
            human_reviews_path=args.human_reviews.resolve(),
            output_path=args.output.resolve(),
        )
        payload = {
            "decision": result.decision,
            "completed_human_review_count": result.completed_human_review_count,
            "output": str(result.output_path),
        }
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
