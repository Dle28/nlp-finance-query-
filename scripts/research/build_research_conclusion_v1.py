#!/usr/bin/env python3
"""Build the immutable 19-section experiment and 17-question conclusion."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from finance_query.research.full_dataset_compare import sha256_file
from finance_query.research.research_conclusion import (
    build_research_conclusion,
    render_markdown,
    validate_research_conclusion,
)


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset-map", type=Path, required=True)
    parser.add_argument("--split-summary", type=Path, required=True)
    parser.add_argument("--discovery", type=Path, required=True)
    parser.add_argument("--development", type=Path, required=True)
    parser.add_argument("--untouched", type=Path, required=True)
    parser.add_argument("--full-external", type=Path, required=True)
    parser.add_argument("--full-vifinqa", type=Path, required=True)
    parser.add_argument("--candidate-replay", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise SystemExit(f"refusing to overwrite existing output directory: {args.output_dir}")
    input_paths = {
        "dataset_map": args.dataset_map,
        "split_summary": args.split_summary,
        "discovery": args.discovery,
        "development": args.development,
        "untouched": args.untouched,
        "full_external": args.full_external,
        "full_vifinqa": args.full_vifinqa,
        "candidate_replay": args.candidate_replay,
    }
    report = build_research_conclusion(
        dataset_map=_load(args.dataset_map),
        split_summary=_load(args.split_summary),
        discovery=_load(args.discovery),
        development=_load(args.development),
        untouched=_load(args.untouched),
        full_external=_load(args.full_external),
        full_vifinqa=_load(args.full_vifinqa),
        candidate_replay=_load(args.candidate_replay),
    )
    validate_research_conclusion(report)
    args.output_dir.mkdir(parents=True)
    json_path = args.output_dir / "research_conclusion_v1.json"
    md_path = args.output_dir / "research_conclusion_v1.md"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    md_path.write_text(render_markdown(report), encoding="utf-8")
    manifest = {
        "protocol": "vifinqa_antileakage_research_conclusion_manifest_v1",
        "schema_version": 1,
        "inputs": {
            name: {"path": str(path), "sha256": sha256_file(path)}
            for name, path in input_paths.items()
        },
        "outputs": {
            json_path.name: {"sha256": sha256_file(json_path)},
            md_path.name: {"sha256": sha256_file(md_path)},
        },
        "source_contract": report["source_contract"],
    }
    (args.output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "status": "BUILT",
        "experiment_sections": len(report["experiment_report_sections"]),
        "final_research_questions": len(report["final_research_questions"]),
        "retained_rules": len(report["retained_rules"]),
        "submission_readiness": report["submission_readiness"],
    }, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
