#!/usr/bin/env python3
"""Validate a frozen, auditable research-review matrix for ViFinQA work."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from finance_query.research_review import (
    ResearchReviewValidationError,
    load_protocol,
    validate_matrix,
    write_report,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate a research-only evidence matrix; never promotes project data."
    )
    parser.add_argument("--protocol", required=True, type=Path)
    parser.add_argument("--matrix", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        protocol = load_protocol(args.protocol)
        report = validate_matrix(protocol, args.matrix)
        write_report(report, args.report)
    except (OSError, ValueError, ResearchReviewValidationError) as error:
        print(json.dumps({"validation_status": "VALIDATION FAILED", "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
