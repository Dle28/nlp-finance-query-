#!/usr/bin/env python3
"""Build hash-bound, numeric-literal-free ChatGPT semantic corrections."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from finance_query.semantic_binding_corrections import build_semantic_binding_correction_review


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-decision", type=Path, required=True)
    parser.add_argument("--audit-manifest", type=Path, required=True)
    parser.add_argument("--base-bindings", type=Path, required=True)
    parser.add_argument("--base-bindings-manifest", type=Path, required=True)
    parser.add_argument("--structured-tables", type=Path, required=True)
    parser.add_argument("--semantic-review-queue", type=Path, required=True)
    parser.add_argument("--semantic-human-decisions", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = build_semantic_binding_correction_review(
        audit_decision=args.audit_decision.resolve(),
        audit_manifest=args.audit_manifest.resolve(),
        base_bindings=args.base_bindings.resolve(),
        base_bindings_manifest=args.base_bindings_manifest.resolve(),
        structured_tables=args.structured_tables.resolve(),
        semantic_review_queue=args.semantic_review_queue.resolve(),
        semantic_human_decisions=args.semantic_human_decisions.resolve(),
        config_path=args.config.resolve(),
        output_dir=args.output_dir.resolve(),
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
