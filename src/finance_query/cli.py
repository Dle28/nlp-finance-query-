"""The single public operator entrypoint for the canonical ViFinQA pipeline.

Research scripts intentionally live outside this CLI. They may build
non-authorizing candidate artefacts but cannot be confused with the
deterministic E2E replay that produces the only operational receipt.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .e2e import (
    load_deterministic_replay_inputs,
    run_deterministic_replay,
    summarize_deterministic_replay,
)


def build_parser() -> argparse.ArgumentParser:
    """Build the one-command public CLI for contract checks and operators."""

    parser = argparse.ArgumentParser(prog="finance-query")
    subparsers = parser.add_subparsers(dest="command", required=True)
    replay = subparsers.add_parser(
        "run-e2e",
        help="Run the canonical deterministic pipeline and emit a hash-bound receipt.",
    )
    replay.add_argument("--config", type=Path, required=True)
    replay.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="A new output directory; completed artefacts are immutable.",
    )
    return parser


def parse_args() -> argparse.Namespace:
    return build_parser().parse_args()


def main() -> None:
    args = parse_args()
    if args.command != "run-e2e":  # Defensive: argparse currently has one command.
        raise RuntimeError(f"unsupported public command: {args.command}")
    output_dir = args.output_dir.resolve()
    receipt = run_deterministic_replay(
        load_deterministic_replay_inputs(args.config.resolve()),
        output_dir=output_dir,
    )
    print(json.dumps(
        summarize_deterministic_replay(receipt, output_dir=output_dir),
        ensure_ascii=False,
        sort_keys=True,
    ))


if __name__ == "__main__":
    main()
