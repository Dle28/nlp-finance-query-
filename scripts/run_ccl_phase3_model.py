#!/usr/bin/env python3
"""Run one prepared CCL Phase 3 text route on a GPU-capable environment."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from finance_query.certified_canonical import run_model_route


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-manifest", required=True, type=Path)
    parser.add_argument("--requests", required=True, type=Path)
    parser.add_argument("--response-schema", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--route-id", required=True)
    parser.add_argument("--max-new-tokens", type=int, default=1024)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--no-4bit", action="store_true")
    args = parser.parse_args()
    result = run_model_route(
        job_manifest_path=args.job_manifest,
        requests_path=args.requests,
        response_schema_path=args.response_schema,
        output_dir=args.output_dir,
        route_id=args.route_id,
        max_new_tokens=args.max_new_tokens,
        limit=args.limit,
        load_in_4bit=not args.no_4bit,
    )
    print(
        json.dumps(
            {
                "output_dir": str(result.output_dir),
                "manifest": str(result.manifest_path),
                "response_count": result.response_count,
                "route_id": result.route_id,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
