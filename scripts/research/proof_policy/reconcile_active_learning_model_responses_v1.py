#!/usr/bin/env python3
"""Route one Qwen proposer output into provisional or human-review rows."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from finance_query.research.proof_policy.active_learning_models import reconcile_validated_responses  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packets", type=Path, required=True)
    parser.add_argument("--proposer-validated", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = reconcile_validated_responses(packets_path=args.packets.resolve(), proposer_validated_path=args.proposer_validated.resolve(), output_dir=args.output_dir.resolve())
    print(json.dumps({"proposal_count": result.proposal_count, "escalation_count": result.escalation_count, "manifest_path": str(result.manifest_path)}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
