#!/usr/bin/env python3
"""Build closed-world CCL Phase 5 source-component selection packets; never run an LLM."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from finance_query.certified_canonical.phase5_component_selection import build_phase5_component_selection_packets


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--table-structure-contexts", type=Path, required=True)
    parser.add_argument("--table-structure-context-manifest", type=Path, required=True)
    parser.add_argument("--navigation-overlay-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--route-id", required=True)
    args = parser.parse_args()
    result = build_phase5_component_selection_packets(**vars(args))
    print(json.dumps({"output_dir": str(result.output_dir), "packet_count": result.packet_count, "deterministic_bypass_count": result.deterministic_bypass_count, "navigation_blocked_count": result.navigation_blocked_count, "llm_dispatch_allowed": False, "training_eligible": False, "certification_allowed": False}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
