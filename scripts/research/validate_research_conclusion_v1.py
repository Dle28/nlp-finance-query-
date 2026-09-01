#!/usr/bin/env python3
"""Validate hashes and fail-closed semantics of a research conclusion artifact."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from finance_query.research.full_dataset_compare import sha256_file
from finance_query.research.research_conclusion import validate_research_conclusion


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-dir", type=Path, required=True)
    args = parser.parse_args()
    manifest_path = args.artifact_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("protocol") != "vifinqa_antileakage_research_conclusion_manifest_v1":
        raise SystemExit("unexpected conclusion manifest protocol")
    for group in ("inputs", "outputs"):
        for name, descriptor in (manifest.get(group) or {}).items():
            path = Path(descriptor["path"]) if group == "inputs" else args.artifact_dir / name
            if not path.is_absolute():
                path = REPO_ROOT / path
            if not path.is_file() or sha256_file(path) != descriptor.get("sha256"):
                raise SystemExit(f"{group} hash mismatch: {name}")
    report = json.loads((args.artifact_dir / "research_conclusion_v1.json").read_text(encoding="utf-8"))
    validate_research_conclusion(report)
    print(json.dumps({
        "status": "VALIDATION_PASSED",
        "experiment_sections": 19,
        "final_research_questions": 17,
        "retained_rules": len(report["retained_rules"]),
        "submission_ready": False,
        "manifest_sha256": sha256_file(manifest_path),
    }, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
