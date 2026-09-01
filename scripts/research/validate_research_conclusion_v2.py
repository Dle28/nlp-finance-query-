#!/usr/bin/env python3
"""Validate hashes and semantic gates of a research conclusion v2 artifact."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from finance_query.research.research_conclusion_v2 import validate_research_conclusion_v2  # noqa: E402


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-dir", type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads((args.artifact_dir / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("protocol") != "vifinqa_antileakage_research_conclusion_manifest_v2":
        raise SystemExit("manifest protocol mismatch")
    for descriptor in manifest["inputs"].values():
        path = ROOT / descriptor["path"]
        if sha256(path) != descriptor["sha256"]:
            raise SystemExit(f"input hash mismatch: {path}")
    for name, descriptor in manifest["outputs"].items():
        path = args.artifact_dir / name
        if sha256(path) != descriptor["sha256"]:
            raise SystemExit(f"output hash mismatch: {path}")
    report = json.loads((args.artifact_dir / "research_conclusion_v2.json").read_text(encoding="utf-8"))
    validate_research_conclusion_v2(report)
    print(json.dumps({
        "status": "VALIDATION_PASSED",
        "experiment_count": len(report["experiment_reports"]),
        "sections_per_experiment": 19,
        "final_questions": len(report["final_research_questions"]),
        "retained_rules": len(report["retained_rules"]),
        "submission_zip_created": report["submission_readiness"]["submission_zip_created"],
    }, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
