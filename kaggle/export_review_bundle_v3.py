#!/usr/bin/env python3
"""Kaggle entrypoint for the fixed v3 review bundle.

Normal use after artifacts already exist:
    python kaggle/export_review_bundle_v3.py --top-k 20 --force

This does NOT rebuild dense/lexical/table assets. It only validates and re-exports
with boundary recovery + improved grounded evidence selection.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_ROOT = Path("/kaggle/working/AI_guru")
WORKING = Path("/kaggle/working")


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--repo-root", type=Path, default=DEFAULT_ROOT)
    p.add_argument("--questions", type=Path, default=Path("data/labels/annotation_questions_60.jsonl"))
    p.add_argument("--config", type=Path, default=Path("configs/annotation_baseline.yaml"))
    p.add_argument("--top-k", type=int, default=20)
    p.add_argument("--max-review-candidates", type=int, default=40)
    p.add_argument(
        "--max-formula-support-tables",
        type=int,
        default=128,
        help="Metadata-selected formula statement tables per question; not shown as review candidates.",
    )
    p.add_argument("--neighbor-radius", type=int, default=1)
    p.add_argument("--force", action="store_true")
    p.add_argument("--no-dense", action="store_true")
    return p.parse_args()


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    args = parse_args()
    root = args.repo_root.resolve()
    output_parent = WORKING / "vifinqa_review_export_v3"
    bundle_dir = output_parent / "vifinqa_review_bundle_v3"
    if output_parent.exists() and args.force:
        shutil.rmtree(output_parent)
    output_parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable,
        str(root / "scripts/build_review_bundle_v3.py"),
        "--repo-root", str(root),
        "--questions", str(args.questions),
        "--config", str(args.config),
        "--output-dir", str(bundle_dir),
        "--top-k", str(args.top_k),
        "--max-review-candidates", str(args.max_review_candidates),
        "--max-formula-support-tables", str(args.max_formula_support_tables),
        "--neighbor-radius", str(args.neighbor_radius),
    ]
    if args.force:
        cmd.append("--force")
    if args.no_dense:
        cmd.append("--no-dense")
    print("$", " ".join(cmd))
    subprocess.run(cmd, cwd=root, check=True)

    generated_archive = output_parent / "vifinqa_review_bundle_v3.tar.gz"
    generated_sha = output_parent / "vifinqa_review_bundle_v3.tar.gz.sha256"
    if not generated_archive.is_file():
        raise FileNotFoundError(generated_archive)

    final_archive = WORKING / "vifinqa_review_bundle_v3.tar.gz"
    final_sha = WORKING / "vifinqa_review_bundle_v3.tar.gz.sha256"
    final_handoff = WORKING / "vifinqa_review_handoff_v3.json"
    shutil.copy2(generated_archive, final_archive)
    shutil.copy2(generated_sha, final_sha)

    handoff = {
        "schema_version": 3,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "repo_root": str(root),
        "bundle_dir": str(bundle_dir),
        "download_archive": str(final_archive),
        "download_sha256": str(final_sha),
        "archive_sha256": sha256(final_archive),
        "top_k": args.top_k,
        "max_formula_support_tables": args.max_formula_support_tables,
        "next_local_steps": [
            "Download vifinqa_review_bundle_v3.tar.gz and its .sha256 file.",
            "Verify SHA256 locally.",
            "Extract bundle.",
            "Run: python local/run_local_review_stage.py diagnose --bundle-archive <ARCHIVE> --diagnostic-output data/diagnostics/run_002 --audit-size 12",
            "If diagnostics improve, run baseline multi-agent review.",
        ],
        "fixes": [
            "previous-table boundary recovery from context leakage",
            "direct_lookup effective metric cleanup",
            "header + numeric value-row grounded evidence",
            "period-aware end/start-of-year row selection",
        ],
    }
    final_handoff.write_text(json.dumps(handoff, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\nDOWNLOAD")
    print(final_archive)
    print(final_sha)
    print(final_handoff)
    try:
        from IPython.display import FileLink, display
        for p in [final_archive, final_sha, final_handoff]:
            display(FileLink(str(p)))
    except Exception:
        pass


if __name__ == "__main__":
    main()
