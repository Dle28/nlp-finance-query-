#!/usr/bin/env python3
"""Validate a completed value-blind retrieval probe artifact."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from finance_query.e2e.core.retrieval_probes import validate_retrieval_probe_artifact  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(validate_retrieval_probe_artifact(args.artifact_dir), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
