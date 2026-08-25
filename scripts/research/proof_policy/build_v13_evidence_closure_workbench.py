#!/usr/bin/env python3
"""Build hash-bound, non-promotable receipt-intake queues for the V13 backlog."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from finance_query.research.proof_policy.evidence_closure import build_workbench  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    result = build_workbench(config_path=args.config.resolve(), output_dir=args.output_dir.resolve())
    print(json.dumps({"status": result["status"], "counts": result["counts"], "manifest_path": result["manifest_path"]}, indent=2))


if __name__ == "__main__":
    main()
