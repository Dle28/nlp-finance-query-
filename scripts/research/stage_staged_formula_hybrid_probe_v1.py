#!/usr/bin/env python3
"""Stage only newly materialized staged formulas for a dense/lexical probe.

The output is navigation input, not an E2E plan overlay.  It preserves all
1,012 question records for coverage accounting while leaving exactly the
requested staged-formula questions eligible for a research-only hybrid run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
from typing import Any


CONTRACT = {
    "research_only": True,
    "navigation_metadata_only": True,
    "evidence_eligible": False,
    "may_materialize_answer": False,
    "training_eligible": False,
    "submission_eligible": False,
    "promotion_allowed": False,
}
FORBIDDEN = {"answer", "answer_decimal", "raw_value", "raw_values", "cell_value", "pandas_query", "human_verified"}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must be a JSON object")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError(f"{path} must contain JSON objects")
    return rows


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )


def _contains_forbidden(value: object) -> bool:
    if isinstance(value, dict):
        return any(str(key) in FORBIDDEN or _contains_forbidden(child) for key, child in value.items())
    return any(_contains_forbidden(child) for child in value) if isinstance(value, list) else False


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plans", type=Path, required=True)
    parser.add_argument("--targets", type=Path, required=True)
    parser.add_argument("--candidate-artifact-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--expected-question-count", type=int, default=1012)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise FileExistsError(f"refusing to overwrite output directory: {args.output_dir}")

    candidate_manifest = _read_json(args.candidate_artifact_dir / "manifest.json")
    table_candidates = args.candidate_artifact_dir / "table_candidates_v1.jsonl"
    expected_candidates = ((candidate_manifest.get("outputs") or {}).get(table_candidates.name) or {}).get("sha256")
    if _sha(table_candidates) != expected_candidates:
        raise ValueError("candidate artifact table list does not match manifest")

    plans = _read_jsonl(args.plans)
    targets = _read_jsonl(args.targets)
    target_ids = {int(row["question_id"]) for row in targets}
    expected_ids = set(range(1, args.expected_question_count + 1))
    if {int(row["question_id"]) for row in plans} != expected_ids or len(plans) != args.expected_question_count:
        raise ValueError("plan input does not cover the expected question population")
    if not target_ids or not target_ids <= expected_ids:
        raise ValueError("target set is empty or outside the question population")

    staged_plans: list[dict[str, Any]] = []
    expected_routes: set[tuple[int, str, int]] = set()
    for original in plans:
        plan = dict(original)
        question_id = int(plan["question_id"])
        if question_id in target_ids:
            if plan.get("decomposition_status") != "typed_non_executable":
                raise ValueError("target plan must be typed_non_executable")
            for operand in plan.get("operands") or []:
                for year in operand.get("years") or []:
                    expected_routes.add((question_id, str(operand.get("operand_id") or ""), int(year)))
        elif plan.get("decomposition_status") == "typed_non_executable":
            # Prevent pre-existing non-executable plans from entering this
            # probe.  They remain unchanged in their authoritative sidecar.
            plan["decomposition_status"] = "abstain"
            plan["operands"] = []
            plan["operation_ast"] = {"op": "abstain"}
            plan["formula_id"] = None
        staged_plans.append(plan)
    if not expected_routes:
        raise ValueError("targets contain no operand-year routes")

    staged_candidates = [
        row
        for row in _read_jsonl(table_candidates)
        if int(row.get("question_id") or 0) in target_ids
    ]
    observed_routes = {
        (int(row["question_id"]), str(row["operand_id"]), int(row["report_year"]))
        for row in staged_candidates
    }
    if observed_routes != expected_routes:
        missing = sorted(expected_routes - observed_routes)
        extra = sorted(observed_routes - expected_routes)
        raise ValueError(f"hybrid probe lexical routes differ from staged plans; missing={missing[:3]}, extra={extra[:3]}")
    if _contains_forbidden(staged_candidates):
        raise ValueError("hybrid probe lexical candidates include a forbidden field")

    args.output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{args.output_dir.name}.tmp-", dir=args.output_dir.parent))
    try:
        plans_path = temporary / "hybrid_probe_plans.jsonl"
        candidates_path = temporary / "hybrid_probe_lexical_candidates.jsonl"
        _write_jsonl(plans_path, staged_plans)
        _write_jsonl(candidates_path, staged_candidates)
        summary = {
            "question_count": args.expected_question_count,
            "target_question_count": len(target_ids),
            "route_count": len(expected_routes),
            "lexical_candidate_count": len(staged_candidates),
            "source_contract": CONTRACT,
        }
        _write_json(temporary / "summary.json", summary)
        _write_json(
            temporary / "manifest.json",
            {
                "protocol": "vifinqa_staged_formula_hybrid_probe_input_v1",
                "schema_version": 1,
                "inputs": {
                    "plans": {"path": str(args.plans), "sha256": _sha(args.plans)},
                    "targets": {"path": str(args.targets), "sha256": _sha(args.targets)},
                    "candidate_manifest": {"path": str(args.candidate_artifact_dir / "manifest.json"), "sha256": _sha(args.candidate_artifact_dir / "manifest.json")},
                    "table_candidates": {"path": str(table_candidates), "sha256": _sha(table_candidates)},
                },
                "outputs": {
                    "plans": {"path": plans_path.name, "sha256": _sha(plans_path)},
                    "lexical_candidates": {"path": candidates_path.name, "sha256": _sha(candidates_path)},
                    "summary": {"path": "summary.json", "sha256": _sha(temporary / "summary.json")},
                },
                "source_contract": CONTRACT,
            },
        )
        temporary.rename(args.output_dir)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
