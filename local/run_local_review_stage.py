#!/usr/bin/env python3
"""Convenience runner for the local ViFinQA review stages.

Modes:
- diagnose: verify/analyze a review bundle, write no labels;
- baseline: deterministic multi-agent review + small human seed queue;
- calibrate: train from human seed, rerun agents with learned calibrator;
- final: merge human labels + calibrated machine pseudo-labels with provenance.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["diagnose", "baseline", "calibrate", "final"])
    parser.add_argument("--bundle-dir", type=Path, default=None)
    parser.add_argument("--bundle-archive", type=Path, default=None)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--seed-size", type=int, default=12)
    parser.add_argument("--audit-size", type=int, default=12)
    parser.add_argument(
        "--diagnostic-output",
        type=Path,
        default=Path("data/diagnostics/run_001"),
    )
    parser.add_argument(
        "--human-labels",
        type=Path,
        default=Path("data/labels/retriever_verified_60.jsonl"),
    )
    return parser.parse_args()


def run(command: list[str], cwd: Path) -> None:
    print("$", " ".join(command))
    subprocess.run(command, cwd=cwd, check=True)


def main() -> None:
    args = parse_args()
    root = args.repo_root.resolve()

    if args.mode == "diagnose":
        if bool(args.bundle_dir) == bool(args.bundle_archive):
            raise ValueError("diagnose requires exactly one of --bundle-dir or --bundle-archive")
        output = args.diagnostic_output
        output = output if output.is_absolute() else root / output
        command = [
            sys.executable,
            str(root / "local/diagnose_review_bundle.py"),
            "--output-dir", str(output),
            "--audit-size", str(args.audit_size),
            "--force",
        ]
        if args.bundle_dir:
            command += ["--bundle-dir", str(args.bundle_dir.resolve())]
        else:
            command += ["--bundle-archive", str(args.bundle_archive.resolve())]
        run(command, root)
        print("\nNEXT — inspect these files before baseline auto-review:")
        print(output / "diagnostic_summary.json")
        print(output / "review_bundle_summary.csv")
        print(output / "manual_audit_queue.jsonl")
        print("\nIf the audit shows systematic retrieval/evidence errors, fix those first. Do not run baseline blindly.")
        return

    if args.bundle_dir is None:
        raise ValueError(f"{args.mode} requires --bundle-dir with an extracted review bundle")

    bundle = args.bundle_dir.resolve()
    labels = root / "data/labels"
    artifacts = root / "artifacts"
    labels.mkdir(parents=True, exist_ok=True)
    artifacts.mkdir(parents=True, exist_ok=True)

    human = args.human_labels if args.human_labels.is_absolute() else root / args.human_labels
    baseline_reviews = labels / "machine_reviews_60.jsonl"
    seed_queue = labels / f"human_seed_queue_{args.seed_size}.jsonl"
    calibrator = artifacts / "review_calibrator.joblib"
    calibrated_reviews = labels / "machine_reviews_60_calibrated.jsonl"
    needs_human = labels / "needs_human_after_calibration.jsonl"
    final_labels = labels / "retriever_labels_v2.jsonl"

    if args.mode == "baseline":
        run(
            [
                sys.executable,
                str(root / "scripts/auto_review_bundle.py"),
                "--bundle-dir", str(bundle),
                "--output", str(baseline_reviews),
                "--seed-queue", str(seed_queue),
                "--seed-size", str(args.seed_size),
            ],
            root,
        )
        print("\nNEXT — human reviews only the seed queue:")
        print(
            "%run local/review_bundle_widget.py "
            f"--bundle-dir {bundle} "
            f"--machine-reviews {baseline_reviews} "
            f"--queue {seed_queue} "
            f"--output {human}"
        )
        return

    if args.mode == "calibrate":
        if not human.is_file():
            raise FileNotFoundError(
                f"Human seed labels missing: {human}. Run baseline + review the seed queue first."
            )

        run(
            [
                sys.executable,
                str(root / "scripts/train_review_calibrator.py"),
                "--bundle-dir", str(bundle),
                "--human-labels", str(human),
                "--output", str(calibrator),
            ],
            root,
        )
        run(
            [
                sys.executable,
                str(root / "scripts/auto_review_bundle.py"),
                "--bundle-dir", str(bundle),
                "--calibrator", str(calibrator),
                "--output", str(calibrated_reviews),
                "--needs-human-queue", str(needs_human),
            ],
            root,
        )
        print("\nNEXT — inspect only unresolved/abstained cases if desired:")
        print(
            "%run local/review_bundle_widget.py "
            f"--bundle-dir {bundle} "
            f"--machine-reviews {calibrated_reviews} "
            f"--queue {needs_human} "
            f"--output {human}"
        )
        return

    if args.mode == "final":
        if not calibrated_reviews.is_file():
            raise FileNotFoundError(
                f"Calibrated machine review missing: {calibrated_reviews}. Run calibrate first."
            )

        command = [
            sys.executable,
            str(root / "scripts/export_review_labels.py"),
            "--machine-reviews", str(calibrated_reviews),
            "--output", str(final_labels),
        ]
        if human.is_file():
            command += ["--human-labels", str(human)]
        run(command, root)

        print("\nFinal provenance-preserving label file:", final_labels)
        print("Human labels have weight 1.0; calibrated machine labels remain explicit pseudo-labels.")


if __name__ == "__main__":
    main()
