#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from finance_query.navigation_binding_promotions import build_navigation_binding_promotion_review


def main() -> None:
    parser = argparse.ArgumentParser()
    for name in (
        "navigation-decisions", "navigation-decisions-manifest", "navigation-evidence",
        "navigation-evidence-manifest", "base-bindings", "base-bindings-manifest",
        "structured-tables", "evidence-context", "evidence-context-manifest", "config",
        "output-dir",
    ):
        parser.add_argument(f"--{name}", required=True, type=Path)
    args = parser.parse_args()
    result = build_navigation_binding_promotion_review(
        navigation_decisions=args.navigation_decisions,
        navigation_decisions_manifest=args.navigation_decisions_manifest,
        navigation_evidence=args.navigation_evidence,
        navigation_evidence_manifest=args.navigation_evidence_manifest,
        base_bindings=args.base_bindings,
        base_bindings_manifest=args.base_bindings_manifest,
        structured_tables=args.structured_tables,
        evidence_context=args.evidence_context,
        evidence_context_manifest=args.evidence_context_manifest,
        config_path=args.config,
        output_dir=args.output_dir,
    )
    print(result["manifest_path"])


if __name__ == "__main__":
    main()
