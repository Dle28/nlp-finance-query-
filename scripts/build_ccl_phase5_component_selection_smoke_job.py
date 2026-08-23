#!/usr/bin/env python3
"""Build the exact five-packet proposal-only component-selection GPU smoke job."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from finance_query.certified_canonical.phase5_component_selection_smoke import build_phase5_component_selection_smoke_job


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = build_phase5_component_selection_smoke_job(config_path=args.config, output_dir=args.output_dir)
    print(json.dumps({"output_dir": str(result.output_dir), "request_count": result.request_count, "manifest_path": str(result.manifest_path), "training_eligible": False, "certification_allowed": False}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
