#!/usr/bin/env python3
"""Validate raw CCL Phase 3 model responses without certifying them."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from finance_query.certified_canonical import validate_raw_responses


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-manifest", required=True, type=Path)
    parser.add_argument("--requests", required=True, type=Path)
    parser.add_argument("--raw-responses", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--route-id", default=None)
    args = parser.parse_args()
    result = validate_raw_responses(
        job_manifest_path=args.job_manifest,
        requests_path=args.requests,
        raw_responses_path=args.raw_responses,
        output_dir=args.output_dir,
        route_id=args.route_id,
    )
    print(
        json.dumps(
            {
                "output_dir": str(result.output_dir),
                "manifest": str(result.manifest_path),
                "valid_proposal_only_count": result.valid_response_count,
                "invalid_or_missing_unresolved_count": result.invalid_response_count,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
