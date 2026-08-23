#!/usr/bin/env python3
"""Run the hash-bound exact-cell -> authorization -> certificate replay."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from finance_query.grounded_e2e import load_inputs, run_grounded_e2e


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="New directory only; existing outputs are never overwritten.",
    )
    args = parser.parse_args()
    result = run_grounded_e2e(load_inputs(args.config), output_dir=args.output_dir)
    print(
        json.dumps(
            {
                "run_name": result["run_name"],
                "run_id": result["run_id"],
                "run_status": result["run_status"],
                "binding_status_counts": result["outputs"]["bindings"]["counts"][
                    "binding_packet_status_counts"
                ],
                "execution_status_counts": result["outputs"]["execution"]["counts"][
                    "execution_status_counts"
                ],
                "authorization_counts": result["outputs"]["authorization"]["counts"],
                "reproducibility": result["reproducibility"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
