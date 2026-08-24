#!/usr/bin/env python3
"""Build blind open-source proposer/critic requests for active-learning V1."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from finance_query.active_learning_models import build_model_job  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--active-cycle-manifest", type=Path, required=True)
    parser.add_argument("--model-policy", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = build_model_job(
        active_cycle_manifest_path=args.active_cycle_manifest.resolve(),
        model_policy_path=args.model_policy.resolve(),
        output_dir=args.output_dir.resolve(),
    )
    print(json.dumps({"status": "PREPARED_GPU_EXECUTION_NOT_RUN", "packet_count": result.packet_count, "manifest_path": str(result.manifest_path)}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
