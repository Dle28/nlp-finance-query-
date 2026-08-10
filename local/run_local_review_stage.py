#!/usr/bin/env python3
"""Convenience runner for the local ViFinQA review stages.

Modes:
- diagnose: verify/analyze a review bundle, write no labels;
- repair-tables: build local raw-HTML grid sidecar, write no labels;
- baseline: source-aware deterministic multi-agent review + small human seed queue;
- collaborate: merge Codex-assisted review and human checks into a full ledger;
- calibrate: train from human seed, rerun V3.1 agents with learned calibrator;
- final: export a full audit ledger plus a provenance-filtered training subset.
- pilot: train/evaluate a shadow Top-K candidate reranker; no corpus/index rebuild.
- preprocess: derive canonical header/period context from immutable V2 grids.
- formula-evidence: derive exact-cell coverage for controlled formula operands.
- source-completion: audit and revalidate raw tables omitted from the bundle;
  then run a scope-gap pass into a combined formula-coverage-only shadow
  sidecar with no label promotion.
- autonomous: V4 machine self-review and source-gated machine-silver export.
- autotrain: train only when enough V4 machine-silver pairs have accumulated.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument(
        "mode", choices=["diagnose", "repair-tables", "baseline", "collaborate", "calibrate", "final", "pilot", "preprocess", "formula-evidence", "source-completion", "autonomous", "autotrain", "submission"]
    )
    p.add_argument("--bundle-dir", type=Path, default=None)
    p.add_argument("--bundle-archive", type=Path, default=None)
    p.add_argument("--repo-root", type=Path, default=Path.cwd())
    p.add_argument("--seed-size", type=int, default=12)
    p.add_argument("--audit-size", type=int, default=12)
    p.add_argument("--diagnostic-output", type=Path, default=Path("data/diagnostics/run_001"))
    p.add_argument("--human-labels", type=Path, default=Path("data/labels/retriever_verified_60.jsonl"))
    p.add_argument(
        "--assistant-labels",
        type=Path,
        default=Path("data/labels/codex_assisted_reviews.jsonl"),
    )
    p.add_argument("--human-check-size", type=int, default=6)
    p.add_argument(
        "--reports-root",
        type=Path,
        default=Path("data/ViFinQA/financial_statements"),
        help="Raw reports used only by repair-tables.",
    )
    p.add_argument("--repair-force", action="store_true")
    p.add_argument(
        "--pilot-output",
        type=Path,
        default=Path("artifacts/pilot_candidate_reranker.joblib"),
        help="Shadow-only Top-K candidate reranker artifact written by pilot mode.",
    )
    p.add_argument(
        "--autonomous-min-pairs",
        type=int,
        default=200,
        help="Minimum V4 source-gated machine-silver pairs before autonomous dense training.",
    )
    p.add_argument(
        "--question-plan-overrides",
        type=Path,
        default=None,
        help="Hash-bound effective-plan sidecar used by autonomous/retrain/submission stages.",
    )
    p.add_argument(
        "--submission-output-dir",
        type=Path,
        default=None,
        help="New directory for a validated submission package; submission mode never overwrites it.",
    )
    return p.parse_args()


def run(command: list[str], cwd: Path) -> None:
    print("$", " ".join(command)); subprocess.run(command, cwd=cwd, check=True)


def reviewer_script(root: Path, bundle: Path) -> Path:
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    version = int(manifest.get("schema_version") or 0)
    if version >= 3:
        script = root / "scripts/auto_review_bundle_v31.py"
        if not script.is_file():
            raise FileNotFoundError(script)
        sidecar = bundle / "tables_structured_v2.jsonl"
        if not sidecar.is_file():
            raise FileNotFoundError(
                f"V3.1 review requires exact-row sidecar: {sidecar}. "
                "Run repair-tables first."
            )
        print(f"Using source-aware V3.1 reviewer for bundle schema {version}")
        return script
    print(f"Using legacy reviewer for bundle schema {version}")
    return root / "scripts/auto_review_bundle.py"


def main() -> None:
    args = parse_args(); root = args.repo_root.resolve()

    if args.mode == "diagnose":
        if bool(args.bundle_dir) == bool(args.bundle_archive):
            raise ValueError("diagnose requires exactly one of --bundle-dir or --bundle-archive")
        output = args.diagnostic_output if args.diagnostic_output.is_absolute() else root / args.diagnostic_output
        command = [sys.executable, str(root / "local/diagnose_review_bundle.py"), "--output-dir", str(output), "--audit-size", str(args.audit_size), "--force"]
        command += ["--bundle-dir", str(args.bundle_dir.resolve())] if args.bundle_dir else ["--bundle-archive", str(args.bundle_archive.resolve())]
        run(command, root)
        print("\nNEXT — inspect before baseline:")
        print(output / "diagnostic_summary.json")
        print(output / "review_bundle_summary.csv")
        print(output / "manual_audit_queue.jsonl")
        return

    if args.bundle_dir is None:
        raise ValueError(f"{args.mode} requires --bundle-dir with an extracted review bundle")
    bundle = args.bundle_dir.resolve()
    if not (bundle / "manifest.json").is_file():
        raise FileNotFoundError(bundle / "manifest.json")
    bundle_manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    review_item_count = int(bundle_manifest.get("review_item_count") or 0)
    if review_item_count < 1:
        raise ValueError("Bundle manifest has no review items")
    run_tag = str(review_item_count)

    if args.mode == "repair-tables":
        reports = (
            args.reports_root
            if args.reports_root.is_absolute()
            else root / args.reports_root
        )
        command = [
            sys.executable,
            str(root / "scripts/repair_review_bundle_tables.py"),
            "--bundle-dir",
            str(bundle),
            "--reports-root",
            str(reports),
        ]
        if args.repair_force:
            command.append("--force")
        run(command, root)
        print("\nNEXT — open the repaired local UI in Jupyter:")
        print(
            "%run local/review_bundle_widget.py "
            f"--bundle-dir {bundle} "
            "--machine-reviews data/labels/machine_reviews_60.jsonl "
            "--queue data/labels/human_seed_queue_12.jsonl "
            "--output data/labels/retriever_verified_60.jsonl"
        )
        return

    reviewer = (
        reviewer_script(root, bundle)
        if args.mode not in {"preprocess", "formula-evidence", "source-completion", "autonomous", "autotrain", "pilot", "submission"}
        else None
    )

    labels = root / "data/labels"; artifacts = root / "artifacts"
    labels.mkdir(parents=True, exist_ok=True); artifacts.mkdir(parents=True, exist_ok=True)
    human = args.human_labels if args.human_labels.is_absolute() else root / args.human_labels
    assistant = (
        args.assistant_labels
        if args.assistant_labels.is_absolute()
        else root / args.assistant_labels
    )
    baseline_reviews = labels / f"machine_reviews_{run_tag}.jsonl"
    seed_queue = labels / f"human_seed_queue_{args.seed_size}_{run_tag}.jsonl"
    calibrator = artifacts / "review_calibrator.joblib"
    calibrated_reviews = labels / f"machine_reviews_{run_tag}_calibrated.jsonl"
    needs_human = labels / f"needs_human_after_calibration_{run_tag}.jsonl"
    final_labels = labels / f"retriever_labels_v2_{run_tag}.jsonl"
    review_ledger = labels / f"review_ledger_{run_tag}.jsonl"
    human_check_queue = labels / f"human_check_queue_{run_tag}.jsonl"
    evidence_context = bundle / "tables_evidence_context_v3.jsonl"
    overrides = (
        args.question_plan_overrides.resolve()
        if args.question_plan_overrides is not None
        else None
    )
    if overrides is not None and not overrides.is_file():
        raise FileNotFoundError(overrides)
    plan_variant = "_replanned" if overrides is not None else ""
    # Context V1/V2 files remain auditable history. New default reviews must be
    # physically distinguishable, otherwise autotrain/submission could consume
    # a result made before the numeric-safety contract existed.
    artifact_variant = f"{plan_variant}_context_v3"
    autonomous_reviews = labels / f"machine_reviews_{run_tag}_autonomous{artifact_variant}.jsonl"
    autonomous_quarantine = labels / f"autonomous_quarantine_{run_tag}{artifact_variant}.jsonl"
    autonomous_silver = labels / f"machine_silver_labels_{run_tag}{artifact_variant}.jsonl"
    execution_ledger = labels / f"machine_execution_ledger_{run_tag}{artifact_variant}.jsonl"
    formula_evidence = bundle / "formula_evidence_sets_context_v3_discovered.jsonl"
    direct_evidence = bundle / "direct_evidence_sets_context_v3_discovered.jsonl"
    source_completion_audit = bundle / "formula_source_completion_audit_v1.json"
    source_completion_tables = bundle / "source_completion_tables_v1.jsonl"
    source_completion_context = bundle / "source_completion_context_v1.jsonl"
    source_completion_formula = bundle / "formula_evidence_sets_context_v3_source_completion_shadow.jsonl"
    source_completion_formula_audit = source_completion_formula.with_suffix(".audit.json")
    source_completion_scope_audit = bundle / "formula_source_completion_scope_audit_v1.json"
    source_completion_combined_tables = bundle / "source_completion_combined_v1.jsonl"
    source_completion_combined_context = bundle / "source_completion_combined_context_v1.jsonl"
    source_completion_combined_formula = (
        bundle / "formula_evidence_sets_context_v3_source_completion_combined_shadow.jsonl"
    )
    source_completion_combined_formula_audit = source_completion_combined_formula.with_suffix(".audit.json")

    if args.mode == "preprocess":
        command = [
            sys.executable,
            str(root / "scripts/build_table_evidence_context.py"),
            "--bundle-dir",
            str(bundle),
        ]
        if evidence_context.is_file():
            command.append("--force")
        run(command, root)
        print("\nCanonical context sidecar:", evidence_context)
        print("It preserves raw V2 rows and only derives header/period/provenance context.")
        return

    if args.mode == "formula-evidence":
        if not evidence_context.is_file():
            raise FileNotFoundError(
                f"Canonical context missing: {evidence_context}. Run preprocess first."
            )
        run(
            [
                sys.executable,
                str(root / "scripts/build_formula_evidence_sets.py"),
                "--bundle-dir",
                str(bundle),
                "--output",
                str(formula_evidence),
                "--evidence-context",
                str(evidence_context),
                "--discover-source-operands",
            ],
            root,
        )
        print("\nFormula EvidenceSet sidecar:", formula_evidence)
        print("It records only exact raw-row/cell operand coverage; it does not generate answers or labels.")
        return

    if args.mode == "source-completion":
        if not evidence_context.is_file():
            raise FileNotFoundError(
                f"Canonical context missing: {evidence_context}. Run preprocess first."
            )
        completion_reports = (
            args.reports_root if args.reports_root.is_absolute() else root / args.reports_root
        ).resolve()
        # Rebuild the ordinary formula coverage first, so the audit sees the
        # exact current missing operands. It remains separate from the later
        # supplemental shadow EvidenceSet.
        run(
            [
                sys.executable,
                str(root / "scripts/build_formula_evidence_sets.py"),
                "--bundle-dir", str(bundle),
                "--output", str(formula_evidence),
                "--evidence-context", str(evidence_context),
                "--discover-source-operands",
            ],
            root,
        )
        run(
            [
                sys.executable,
                str(root / "scripts/audit_formula_source_coverage.py"),
                "--bundle-dir", str(bundle),
                "--formula-evidence", str(formula_evidence),
                "--reports-root", str(completion_reports),
                "--output", str(source_completion_audit),
            ],
            root,
        )
        run(
            [
                sys.executable,
                str(root / "scripts/build_source_completion_sidecar.py"),
                "--bundle-dir", str(bundle),
                "--source-audit", str(source_completion_audit),
                "--reports-root", str(completion_reports),
            ],
            root,
        )
        run(
            [
                sys.executable,
                str(root / "scripts/build_formula_evidence_sets.py"),
                "--bundle-dir", str(bundle),
                "--output", str(source_completion_formula),
                "--evidence-context", str(evidence_context),
                "--discover-source-operands",
                "--source-completion-tables", str(source_completion_tables),
                "--source-completion-context", str(source_completion_context),
            ],
            root,
        )
        run(
            [
                sys.executable,
                str(root / "scripts/analyze_formula_evidence.py"),
                "--bundle-dir", str(bundle),
                "--formula-evidence", str(source_completion_formula),
                "--output", str(source_completion_formula_audit),
            ],
            root,
        )
        # The first pass covers genuinely missing operands. Re-audit that
        # revalidated shadow with the narrow scope-gap policy so a staged
        # multi-entity program can prove whether a common scope exists. The
        # second completion snapshot *inherits* the first one; it does not
        # replace previously revalidated raw source tables.
        run(
            [
                sys.executable,
                str(root / "scripts/audit_formula_source_coverage.py"),
                "--bundle-dir", str(bundle),
                "--formula-evidence", str(source_completion_formula),
                "--reports-root", str(completion_reports),
                "--include-scope-gap-operands",
                "--output", str(source_completion_scope_audit),
            ],
            root,
        )
        run(
            [
                sys.executable,
                str(root / "scripts/build_source_completion_sidecar.py"),
                "--bundle-dir", str(bundle),
                "--source-audit", str(source_completion_scope_audit),
                "--reports-root", str(completion_reports),
                "--base-tables", str(source_completion_tables),
                "--base-contexts", str(source_completion_context),
                "--tables-output", str(source_completion_combined_tables),
                "--contexts-output", str(source_completion_combined_context),
            ],
            root,
        )
        run(
            [
                sys.executable,
                str(root / "scripts/build_formula_evidence_sets.py"),
                "--bundle-dir", str(bundle),
                "--output", str(source_completion_combined_formula),
                "--evidence-context", str(evidence_context),
                "--discover-source-operands",
                "--source-completion-tables", str(source_completion_combined_tables),
                "--source-completion-context", str(source_completion_combined_context),
            ],
            root,
        )
        run(
            [
                sys.executable,
                str(root / "scripts/analyze_formula_evidence.py"),
                "--bundle-dir", str(bundle),
                "--formula-evidence", str(source_completion_combined_formula),
                "--output", str(source_completion_combined_formula_audit),
            ],
            root,
        )
        print("\nRaw-source completion audit:", source_completion_audit)
        print("Revalidated supplemental tables:", source_completion_tables)
        print("Scope-gap audit:", source_completion_scope_audit)
        print("Combined supplemental tables:", source_completion_combined_tables)
        print("Coverage-only formula shadow:", source_completion_combined_formula)
        print("No corpus/index, review status, answer, execution record, or training label was changed.")
        return

    if args.mode == "autonomous":
        if not evidence_context.is_file():
            run(
                [
                    sys.executable,
                    str(root / "scripts/build_table_evidence_context.py"),
                    "--bundle-dir",
                    str(bundle),
                ],
                root,
            )
        run(
            [
                sys.executable,
                str(root / "scripts/build_formula_evidence_sets.py"),
                "--bundle-dir",
                str(bundle),
                "--output",
                str(formula_evidence),
                "--evidence-context",
                str(evidence_context),
                "--discover-source-operands",
            ],
            root,
        )
        direct_command = [
            sys.executable,
            str(root / "scripts/build_direct_evidence_sets.py"),
            "--bundle-dir",
            str(bundle),
            "--evidence-context",
            str(evidence_context),
            "--output",
            str(direct_evidence),
        ]
        if overrides is not None:
            direct_command.extend(["--question-plan-overrides", str(overrides)])
        run(direct_command, root)
        review_command = [
            sys.executable,
            str(root / "scripts/auto_review_bundle_v4.py"),
            "--bundle-dir",
            str(bundle),
            "--evidence-context",
            str(evidence_context),
            "--output",
            str(autonomous_reviews),
            "--quarantine-output",
            str(autonomous_quarantine),
            "--direct-evidence",
            str(direct_evidence),
        ]
        if overrides is not None:
            review_command.extend(["--question-plan-overrides", str(overrides)])
        run(review_command, root)
        run(
            [
                sys.executable,
                str(root / "scripts/export_review_labels.py"),
                "--machine-reviews",
                str(autonomous_reviews),
                "--output",
                str(autonomous_silver),
            ],
            root,
        )
        run(
            [
                sys.executable,
                str(root / "scripts/build_execution_ledger.py"),
                "--bundle-dir",
                str(bundle),
                "--machine-reviews",
                str(autonomous_reviews),
                "--evidence-context",
                str(evidence_context),
                "--formula-evidence",
                str(formula_evidence),
                "--output",
                str(execution_ledger),
            ],
            root,
        )
        print("\nAutonomous reviews:", autonomous_reviews)
        print("Quarantined dirty/ambiguous candidates:", autonomous_quarantine)
        print("V4 machine-silver labels only:", autonomous_silver)
        print("Exact-cell execution ledger (direct lookups + audited formula allow-list):", execution_ledger)
        print("No label is human_verified; formula execution does not promote review status or enter silver training.")
        return

    if args.mode == "autotrain":
        if not autonomous_silver.is_file():
            raise FileNotFoundError(
                f"Autonomous silver labels missing: {autonomous_silver}. Run autonomous first."
            )
        run(
            [
                sys.executable,
                str(root / "scripts/train_dense_retriever.py"),
                "--train-jsonl",
                str(autonomous_silver),
                "--bundle-tables",
                str(bundle / "tables.jsonl"),
                "--label-provenance",
                "machine_silver",
                "--min-pairs",
                str(args.autonomous_min_pairs),
                "--defer-below-min",
                "--output-dir",
                str(artifacts / "retriever_autonomous_silver"),
            ],
            root,
        )
        print("\nAutonomous training never rebuilds the dense/FAISS index.")
        print("A model is written only after enough V4 source-gated silver pairs exist.")
        return

    if args.mode == "submission":
        if not execution_ledger.is_file():
            raise FileNotFoundError(
                f"Execution ledger missing: {execution_ledger}. Run autonomous first."
            )
        submission_output = (
            args.submission_output_dir
            if args.submission_output_dir is not None
            else root / "submissions" / f"vifinqa_submission_{run_tag}"
        )
        if not submission_output.is_absolute():
            submission_output = root / submission_output
        run(
            [
                sys.executable,
                str(root / "scripts/compile_vifinqa_submission.py"),
                "--bundle-dir",
                str(bundle),
                "--execution-ledger",
                str(execution_ledger),
                "--output-dir",
                str(submission_output),
            ],
            root,
        )
        print("\nValidated submission package:", submission_output)
        print("ZIP:", submission_output.with_suffix(".zip"))
        return

    if args.mode == "pilot":
        pilot_output = (
            args.pilot_output
            if args.pilot_output.is_absolute()
            else root / args.pilot_output
        )
        run(
            [
                sys.executable,
                str(root / "scripts/train_pilot_candidate_reranker.py"),
                "--bundle-dir",
                str(bundle),
                "--labels",
                str(final_labels),
                "--ledger",
                str(review_ledger),
                "--output",
                str(pilot_output),
            ],
            root,
        )
        print("\nPilot only scores existing immutable Top-K candidates.")
        print("No corpus, lexical index, dense index, review bundle, or labels were modified.")
        print("Artifact:", pilot_output)
        print("Next: inspect the adjacent .json metadata and .shadow.jsonl audit ranking before any further training.")
        return

    if args.mode == "baseline":
        if reviewer is None:  # pragma: no cover - guarded by mode selection
            raise RuntimeError("Baseline reviewer was not initialized")
        run([sys.executable, str(reviewer), "--bundle-dir", str(bundle), "--output", str(baseline_reviews), "--seed-queue", str(seed_queue), "--seed-size", str(args.seed_size)], root)
        print("\nNEXT — review only the seed queue:")
        print("%run local/review_bundle_widget.py " f"--bundle-dir {bundle} " f"--machine-reviews {baseline_reviews} " f"--queue {seed_queue} " f"--output {human}")
        return

    if args.mode == "collaborate":
        machine_source = calibrated_reviews if calibrated_reviews.is_file() else baseline_reviews
        if not machine_source.is_file():
            raise FileNotFoundError("Machine reviews missing. Run baseline first.")
        command = [
            sys.executable,
            str(root / "scripts/build_review_ledger.py"),
            "--bundle-dir", str(bundle),
            "--machine-reviews", str(machine_source),
            "--output", str(review_ledger),
            "--human-check-queue", str(human_check_queue),
            "--human-check-size", str(args.human_check_size),
        ]
        if assistant.is_file():
            command += ["--assistant-reviews", str(assistant)]
        if human.is_file():
            command += ["--human-labels", str(human)]
        run(command, root)
        print("\nNEXT — human verifies only the collaborative check queue:")
        widget = (
            "%run local/review_bundle_widget.py "
            f"--bundle-dir {bundle} --machine-reviews {machine_source} "
            f"--queue {human_check_queue} --output {human}"
        )
        if assistant.is_file():
            widget += f" --assistant-reviews {assistant}"
        print(widget)
        return

    if args.mode == "calibrate":
        if reviewer is None:  # pragma: no cover - guarded by mode selection
            raise RuntimeError("Calibrate reviewer was not initialized")
        if not human.is_file():
            raise FileNotFoundError(f"Human seed labels missing: {human}. Run baseline + review seed first.")
        run([sys.executable, str(root / "scripts/train_review_calibrator.py"), "--bundle-dir", str(bundle), "--human-labels", str(human), "--output", str(calibrator)], root)
        run([sys.executable, str(reviewer), "--bundle-dir", str(bundle), "--calibrator", str(calibrator), "--output", str(calibrated_reviews), "--needs-human-queue", str(needs_human)], root)
        print("\nNEXT — review only unresolved cases:")
        print("%run local/review_bundle_widget.py " f"--bundle-dir {bundle} " f"--machine-reviews {calibrated_reviews} " f"--queue {needs_human} " f"--output {human}")
        return

    if args.mode == "final":
        if not calibrated_reviews.is_file():
            raise FileNotFoundError(f"Calibrated machine review missing: {calibrated_reviews}. Run calibrate first.")
        ledger_command = [
            sys.executable,
            str(root / "scripts/build_review_ledger.py"),
            "--bundle-dir", str(bundle),
            "--machine-reviews", str(calibrated_reviews),
            "--output", str(review_ledger),
        ]
        if assistant.is_file():
            ledger_command += ["--assistant-reviews", str(assistant)]
        if human.is_file():
            ledger_command += ["--human-labels", str(human)]
        run(ledger_command, root)

        training_command = [
            sys.executable,
            str(root / "scripts/export_review_labels.py"),
            "--machine-reviews", str(calibrated_reviews),
            "--output", str(final_labels),
        ]
        if human.is_file():
            training_command += ["--human-labels", str(human)]
        run(training_command, root)
        print("\nFull provenance ledger:", review_ledger)
        print("Training-eligible label subset:", final_labels)


if __name__ == "__main__": main()
