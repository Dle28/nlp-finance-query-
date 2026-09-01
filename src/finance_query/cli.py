"""The public operator entrypoint for ViFinQA verification and submission."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from .e2e import (
    load_deterministic_replay_inputs,
    run_deterministic_replay,
    summarize_deterministic_replay,
)

PUBLIC_COMMANDS = (
    "run-e2e",
    "build-submission",
    "run-submission-flow",
    "evaluate-blocked",
)


def _requested_command(argv: Sequence[str]) -> str | None:
    """Return the first positional command without parsing its options."""

    for token in argv:
        if token == "--":
            return None
        if not token.startswith("-"):
            return token
    return None


def build_parser(*, command: str | None = None) -> argparse.ArgumentParser:
    """Build the public CLI, loading proposal code only for proposal flows."""

    parser = argparse.ArgumentParser(prog="finance-query")
    subparsers = parser.add_subparsers(dest="command", required=True)
    replay = subparsers.add_parser(
        "run-e2e",
        help="Independently verify proposed evidence and emit a hash-bound receipt.",
    )
    replay.add_argument("--config", type=Path, required=True)
    replay.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="A new output directory; completed artefacts are immutable.",
    )
    submission = subparsers.add_parser(
        "build-submission",
        help="Propose, verification-rerank, and compile the best-effort submission.",
    )
    unified = subparsers.add_parser(
        "run-submission-flow",
        help="Run Proposal → Resolve → E2E → Compile → blocked-feedback as one flow.",
    )
    feedback = subparsers.add_parser(
        "evaluate-blocked",
        help="Evaluate or prepare feedback packets for strict-blocked submission rows.",
    )
    if command == "build-submission":
        from .e2e.submission_pipeline import (
            configure_parser as configure_submission_parser,
        )

        configure_submission_parser(submission)
    elif command == "run-submission-flow":
        from .pipeline.submission_flow import configure_submission_flow_parser

        configure_submission_flow_parser(unified)
    elif command == "evaluate-blocked":
        from .pipeline.submission_flow import configure_blocked_feedback_parser

        configure_blocked_feedback_parser(feedback)
    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments without importing the submission builder for E2E."""

    raw_args = list(sys.argv[1:] if argv is None else argv)
    return build_parser(command=_requested_command(raw_args)).parse_args(raw_args)


def main() -> None:
    args = parse_args()
    if args.command == "build-submission":
        from .e2e.submission_pipeline import build_primary_submission

        build_primary_submission(args)
        return
    if args.command == "run-submission-flow":
        from .pipeline.submission_flow import run_submission_flow

        run_submission_flow(args)
        return
    if args.command == "evaluate-blocked":
        from .pipeline.submission_flow import evaluate_blocked_submission

        print(
            json.dumps(
                evaluate_blocked_submission(args),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return
    if args.command != "run-e2e":  # Defensive: argparse owns the command set.
        raise RuntimeError(f"unsupported public command: {args.command}")
    output_dir = args.output_dir.resolve()
    receipt = run_deterministic_replay(
        load_deterministic_replay_inputs(args.config.resolve()),
        output_dir=output_dir,
    )
    print(
        json.dumps(
            summarize_deterministic_replay(receipt, output_dir=output_dir),
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
