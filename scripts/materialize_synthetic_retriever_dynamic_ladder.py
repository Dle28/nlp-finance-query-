#!/usr/bin/env python3
"""Audit issuer-held-out rankings and materialize dynamic retrieval ladders.

The source is a finished Kaggle ranking export.  This runner re-computes its
manifest metrics before using it, then evaluates document/page/table/exact
synthetic-operand reachability.  It is diagnostic-only and never promotes a
retriever or a synthetic row.
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from finance_query.retrieval_diagnostics import build_dynamic_candidate_set, evaluate_retrieval_ladder


PROTOCOL = "vifinqa_synthetic_retriever_dynamic_ladder_v1"
EVALUATION_PROTOCOL = "synthetic_issuer_heldout_retriever_evaluation_v1"
RESULTS_NAME = "synthetic_retriever_dynamic_ladder_v1.jsonl"
REPORT_NAME = "synthetic_retriever_dynamic_ladder_report.json"
MANIFEST_NAME = "synthetic_retriever_dynamic_ladder_manifest.json"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _jsonl(path: Path) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError(f"expected JSONL objects: {path}")
    return rows


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    with path.open("x", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _require_hash(path: Path, expected: object, label: str) -> None:
    if not isinstance(expected, str) or sha256_file(path) != expected:
        raise ValueError(f"SHA-256 mismatch for {label}")


def _ids(rows: Iterable[Mapping[str, Any]], key: str, label: str) -> dict[str, dict[str, Any]]:
    values = list(rows)
    output = {str(row.get(key) or ""): dict(row) for row in values}
    if not output or "" in output or len(output) != len(values):
        raise ValueError(f"{label} identities are malformed")
    return output


def _rank_metrics(rows: Iterable[Mapping[str, Any]], *, ks: list[int]) -> dict[str, Any]:
    values = list(rows)
    if not values:
        raise ValueError("ranking rows are empty")
    hit_counts = {k: 0 for k in ks}
    reciprocal = 0.0
    for row in values:
        ranked = row.get("ranked_table_uids")
        positives = row.get("positive_table_uids")
        if not isinstance(ranked, list) or not isinstance(positives, list):
            raise ValueError("ranking row lacks table ranking/positives")
        first = next((index for index, uid in enumerate(ranked, start=1) if uid in set(map(str, positives))), None)
        if row.get("first_positive_rank") != first:
            raise ValueError("ranking first_positive_rank does not match ranked table UIDs")
        reciprocal += 0.0 if first is None else 1.0 / first
        for k in ks:
            hit_counts[k] += int(first is not None and first <= k)
    count = len(values)
    return {"questions": count, "mrr": reciprocal / count, "recall_at_k": {str(k): hit_counts[k] / count for k in ks}}


def _same_metrics(actual: Mapping[str, Any], expected: Mapping[str, Any]) -> bool:
    if actual.get("questions") != expected.get("questions"):
        return False
    if abs(float(actual["mrr"]) - float(expected["mrr"])) > 1e-12:
        return False
    for key, value in actual["recall_at_k"].items():
        if key not in (expected.get("recall_at_k") or {}) or abs(float(value) - float(expected["recall_at_k"][key])) > 1e-12:
            return False
    return True


def _asset_index(rows: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    values = list(rows)
    output = {str(row.get("internal_table_uid") or ""): dict(row) for row in values}
    if not output or "" in output or len(output) != len(values):
        raise ValueError("source table identities are malformed")
    return output


def _anchor_ids(uid: str, asset: Mapping[str, Any]) -> list[str]:
    rows = asset.get("rows")
    if not isinstance(rows, list):
        raise ValueError(f"{uid}: source table has no raw grid")
    anchors: list[str] = []
    for row_index, row in enumerate(rows):
        if not isinstance(row, list):
            raise ValueError(f"{uid}: source table row is malformed")
        anchors.extend(f"{uid}:r{row_index}:c{column_index}" for column_index in range(len(row)))
    return anchors


def materialize_dynamic_ladder(
    *,
    curriculum: Path,
    source_tables: Path,
    evaluation_manifest: Path,
    rankings: Path,
    model_label: str,
    split: str,
    output_dir: Path,
) -> dict[str, Any]:
    inputs = {
        "curriculum": curriculum.resolve(),
        "source_tables": source_tables.resolve(),
        "evaluation_manifest": evaluation_manifest.resolve(),
        "rankings": rankings.resolve(),
    }
    if any(not path.is_file() for path in inputs.values()):
        raise FileNotFoundError("dynamic ladder input is missing")
    if split not in {"validation", "test"} or not model_label:
        raise ValueError("dynamic ladder only accepts issuer-held-out validation/test and a model label")
    if output_dir.exists() or output_dir.resolve() in {path.resolve() for path in inputs.values()}:
        raise FileExistsError("dynamic ladder output-dir must be new and distinct from inputs")
    before = {name: sha256_file(path) for name, path in inputs.items()}
    evaluation = _json(inputs["evaluation_manifest"])
    if (
        evaluation.get("protocol") != EVALUATION_PROTOCOL
        or evaluation.get("promotion_status") != "offline_evaluation_complete_not_promoted"
        or (evaluation.get("source_contract") or {}).get("issuer_held_out_splits_only") is not True
        or (evaluation.get("source_contract") or {}).get("benchmark_questions_read") is not False
    ):
        raise ValueError("evaluation manifest is not a non-promotable issuer-held-out run")
    _require_hash(inputs["curriculum"], (evaluation.get("inputs") or {}).get("curriculum_sha256"), "curriculum")
    _require_hash(inputs["source_tables"], (evaluation.get("inputs") or {}).get("source_tables_sha256"), "source tables")
    model = ((evaluation.get("models") or {}).get(model_label) or {})
    manifest_metrics = ((model.get("splits") or {}).get(split) or {})
    ks = sorted(int(value) for value in (evaluation.get("configuration") or {}).get("ks") or [] if int(value) > 0)
    if not ks or not manifest_metrics:
        raise ValueError("evaluation manifest lacks requested model/split metrics")

    curriculum_rows = [row for row in _jsonl(inputs["curriculum"]) if row.get("split") == split]
    curriculum_by_id = _ids(curriculum_rows, "curriculum_id", "curriculum")
    ranking_rows = _jsonl(inputs["rankings"])
    ranking_by_id = _ids(ranking_rows, "curriculum_id", "rankings")
    if set(curriculum_by_id) != set(ranking_by_id):
        raise ValueError("rankings do not cover exactly the issuer-held-out curriculum split")
    audited_metrics = _rank_metrics(ranking_rows, ks=ks)
    if not _same_metrics(audited_metrics, manifest_metrics):
        raise ValueError("downloaded rankings do not reproduce evaluation manifest metrics")

    assets = _asset_index(_jsonl(inputs["source_tables"]))
    diagnostics: list[dict[str, Any]] = []
    for curriculum_id in sorted(curriculum_by_id):
        example = curriculum_by_id[curriculum_id]
        ranking = ranking_by_id[curriculum_id]
        positives = [str(uid) for uid in example.get("positive_table_uids") or []]
        if positives != [str(uid) for uid in ranking.get("positive_table_uids") or []] or len(positives) != 1:
            raise ValueError("synthetic ranking positives do not match its curriculum source")
        positive_uid = positives[0]
        positive_asset = assets.get(positive_uid)
        if positive_asset is None:
            raise ValueError("synthetic positive table is absent from source tables")
        positive_anchor_ids = _anchor_ids(positive_uid, positive_asset)
        bindings = example.get("source_bindings")
        if not isinstance(bindings, list) or not bindings:
            raise ValueError("synthetic curriculum has no exact operand bindings")
        operand_sets: list[list[str]] = []
        for binding in bindings:
            if not isinstance(binding, Mapping) or binding.get("internal_table_uid") != positive_uid:
                raise ValueError("synthetic operand binding is not bound to the positive table")
            row_index, column_index = binding.get("row_index"), binding.get("column_index")
            if type(row_index) is not int or type(column_index) is not int:
                raise ValueError("synthetic operand binding lacks raw coordinates")
            anchor = f"{positive_uid}:r{row_index}:c{column_index}"
            if anchor not in positive_anchor_ids:
                raise ValueError("synthetic operand coordinate is outside the positive raw grid")
            operand_sets.append([anchor])
        dense_candidates = []
        for rank, uid in enumerate(ranking.get("ranked_table_uids") or [], start=1):
            uid = str(uid)
            asset = assets.get(uid)
            if asset is None:
                raise ValueError("ranked table is absent from source tables")
            dense_candidates.append(
                {
                    "internal_table_uid": uid,
                    "score": -float(rank),
                    "document_id": asset.get("document_id"),
                    "page_no": asset.get("page_no"),
                    "ticker": asset.get("ticker"),
                    "report_year": asset.get("report_year"),
                    "scope": asset.get("scope"),
                    # Only anchors from the positive table can satisfy this
                    # question's exact source-coordinate oracle.  Other table
                    # grids are irrelevant to this lookup and are intentionally
                    # not expanded into a multi-million-anchor diagnostic file.
                    "anchor_ids": positive_anchor_ids if uid == positive_uid else [],
                }
            )
        lineage = example.get("source_lineage") or {}
        candidate_set = build_dynamic_candidate_set(
            question_id=curriculum_id,
            lexical=[],
            dense=dense_candidates,
            metadata=[],
            budgets={"dense": len(dense_candidates)},
            coverage_requirements={
                "ticker": lineage.get("ticker"),
                "report_year": lineage.get("report_year"),
                "scope": lineage.get("scope"),
            },
        )
        diagnostic = evaluate_retrieval_ladder(
            candidate_set=candidate_set,
            oracle={
                "oracle_id": curriculum_id,
                "document_id": lineage.get("document_id"),
                "page_no": positive_asset.get("page_no"),
                "internal_table_uid": positive_uid,
                "operand_anchor_sets": operand_sets,
            },
        )
        diagnostics.append(
            {
                "schema_version": 1,
                "protocol": PROTOCOL,
                "curriculum_id": curriculum_id,
                "split": split,
                "model_label": model_label,
                "ranking_channels_available": ["dense"],
                "candidate_set_id": candidate_set["candidate_set_id"],
                "candidate_count": candidate_set["candidate_count"],
                "coverage": candidate_set["coverage"],
                "coverage_status": candidate_set["status"],
                "stages": diagnostic["stages"],
                "status": diagnostic["status"],
                "operand_oracle": "synthetic_exact_source_bindings_raw_grid_v1",
                "training_eligible": False,
                "promotion_allowed": False,
            }
        )
    after = {name: sha256_file(path) for name, path in inputs.items()}
    if after != before:
        raise ValueError("dynamic ladder changed a hash-bound input")
    output_dir.mkdir(parents=True)
    result_path = output_dir / RESULTS_NAME
    _write_jsonl(result_path, diagnostics)
    stage_counts = {
        stage: sum(bool((row["stages"].get(stage) or {}).get("hit")) for row in diagnostics)
        for stage in ("document", "page", "table", "complete_operand_set")
    }
    report = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "run_status": "issuer_heldout_dynamic_ladder_complete_not_promoted",
        "model_label": model_label,
        "split": split,
        "audited_ranking_metrics": audited_metrics,
        "candidate_channels_available": ["dense"],
        "question_count": len(diagnostics),
        "coverage_complete_count": sum(row["coverage_status"] == "COVERAGE_COMPLETE" for row in diagnostics),
        "stage_hit_counts": stage_counts,
        "stage_hit_rates": {stage: count / len(diagnostics) for stage, count in stage_counts.items()},
        "input_hashes_unchanged": True,
        "training_eligible_output_count": 0,
        "promotion_allowed": False,
        "next_gate": "compare_multiple_serving_candidate_channels_before_any_retriever_promotion",
    }
    report_path = output_dir / REPORT_NAME
    report_path.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "run_status": report["run_status"],
        "inputs": {name: {"path": str(path), "sha256": before[name]} for name, path in inputs.items()},
        "outputs": {RESULTS_NAME: {"sha256": sha256_file(result_path)}, REPORT_NAME: {"sha256": sha256_file(report_path)}},
        "training_eligible": False,
        "promotion_allowed": False,
    }
    (output_dir / MANIFEST_NAME).write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--curriculum", type=Path, required=True)
    parser.add_argument("--source-tables", type=Path, required=True)
    parser.add_argument("--evaluation-manifest", type=Path, required=True)
    parser.add_argument("--rankings", type=Path, required=True)
    parser.add_argument("--model-label", required=True)
    parser.add_argument("--split", choices=["validation", "test"], required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(materialize_dynamic_ladder(**vars(args)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
