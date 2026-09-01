#!/usr/bin/env python3
"""Build the hash-bound six-experiment research synthesis."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from finance_query.research.research_conclusion_v2 import (  # noqa: E402
    build_research_conclusion_v2,
    render_markdown,
)


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise SystemExit(f"refusing to overwrite: {args.output_dir}")
    input_path = args.inputs.resolve()
    inputs = load(input_path)
    paths: dict[str, Path] = {"inputs_config": input_path}
    dataset_path = ROOT / inputs["dataset_map"]
    vifinqa_path = ROOT / inputs["full_vifinqa"]
    replay_path = ROOT / inputs["candidate_replay"]
    paths.update(dataset_map=dataset_path, full_vifinqa=vifinqa_path, candidate_replay=replay_path)
    loaded = []
    for specification in inputs["experiments"]:
        payloads = {}
        for key in ("config", "split", "discovery", "development", "untouched", "full_reference"):
            if key not in specification:
                continue
            path = ROOT / specification[key]
            paths[f"{specification['experiment_id']}:{key}"] = path
            payloads[key] = load(path)
        loaded.append((specification, payloads))
    report = build_research_conclusion_v2(
        input_config=inputs,
        loaded_experiments=loaded,
        dataset_map=load(dataset_path),
        full_vifinqa=load(vifinqa_path),
        candidate_replay=load(replay_path),
    )
    args.output_dir.mkdir(parents=True)
    json_path = args.output_dir / "research_conclusion_v2.json"
    markdown_path = args.output_dir / "research_conclusion_v2.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    manifest = {
        "protocol": "vifinqa_antileakage_research_conclusion_manifest_v2",
        "schema_version": 2,
        "inputs": {
            name: {"path": str(path.relative_to(ROOT)), "sha256": sha256(path)}
            for name, path in sorted(paths.items())
        },
        "outputs": {
            json_path.name: {"sha256": sha256(json_path)},
            markdown_path.name: {"sha256": sha256(markdown_path)},
        },
        "source_contract": report["source_contract"],
    }
    (args.output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "status": "BUILT",
        "experiment_count": report["experiment_count"],
        "sections_per_experiment": 19,
        "final_questions": len(report["final_research_questions"]),
        "retained_rules": len(report["retained_rules"]),
        "submission_readiness": report["submission_readiness"],
    }, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
