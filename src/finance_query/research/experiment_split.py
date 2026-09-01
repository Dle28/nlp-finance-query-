"""Freeze phenomenon-specific, hash-selected generalization experiment splits."""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .dataset_map import SOURCE_CONTRACT, load_jsonl, sha256_file


HYPOTHESIS_PROTOCOL = "vifinqa_generalization_hypothesis_v1"
SPLIT_PROTOCOL = "vifinqa_generalization_split_freeze_v1"
MANIFEST_PROTOCOL = "vifinqa_generalization_split_manifest_v1"


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain one JSON object")
    return value


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )


def _rank(row: Mapping[str, Any], seed: str) -> str:
    # Deliberately excludes Question ID: only immutable question text hash and
    # the public experiment seed influence selection order.
    return hashlib.sha256(f"{seed}|{row['question_sha256']}".encode("utf-8")).hexdigest()


def _select_diverse(candidates: Sequence[Mapping[str, Any]], size: int, seed: str) -> list[Mapping[str, Any]]:
    remaining = list(candidates)
    selected: list[Mapping[str, Any]] = []
    seen_entities: set[str] = set()
    seen_years: set[int] = set()
    seen_metrics: set[str] = set()
    seen_wordings: set[str] = set()
    seen_topologies: set[str] = set()
    while remaining and len(selected) < size:
        def score(row: Mapping[str, Any]) -> tuple[int, int, int, int, int, str]:
            taxonomy = row["taxonomy"]
            entities = set(taxonomy["entities_resolved"])
            years = set(taxonomy["years_mentioned"])
            metrics = set(taxonomy["metric_families"])
            wording = str(taxonomy["wording_template"])
            topology = str(taxonomy["expected_source_topology"])
            return (
                len(entities - seen_entities),
                len(metrics - seen_metrics),
                len(years - seen_years),
                int(wording not in seen_wordings),
                int(topology not in seen_topologies),
                _rank(row, seed),
            )
        chosen = max(remaining, key=score)
        remaining.remove(chosen)
        selected.append(chosen)
        taxonomy = chosen["taxonomy"]
        seen_entities.update(taxonomy["entities_resolved"])
        seen_years.update(taxonomy["years_mentioned"])
        seen_metrics.update(taxonomy["metric_families"])
        seen_wordings.add(str(taxonomy["wording_template"]))
        seen_topologies.add(str(taxonomy["expected_source_topology"]))
    if len(selected) != size:
        raise ValueError(f"cannot select {size} diverse rows from {len(candidates)} candidates")
    return selected


def _validate_hypothesis(config: Mapping[str, Any]) -> None:
    if config.get("protocol") != HYPOTHESIS_PROTOCOL or config.get("schema_version") != 1:
        raise ValueError("invalid hypothesis protocol")
    hypothesis = config.get("hypothesis") or {}
    required = {
        "observed_phenomenon", "possible_cause", "expected_improvement",
        "possible_side_effect", "questions_that_should_improve",
        "questions_that_should_remain_unchanged",
    }
    if set(hypothesis) != required or any(not str(hypothesis[key]).strip() for key in required):
        raise ValueError("hypothesis must freeze all six predictive fields")
    change = config.get("change_tested") or {}
    if change.get("primary_change_count") != 1:
        raise ValueError("experiment must test exactly one primary change")
    evaluator = config.get("evaluator") or {}
    if evaluator.get("status") != "MISSING_INDEPENDENT_EVALUATOR" or evaluator.get("model_change_allowed") is not False:
        raise ValueError("pre-evaluator hypothesis must fail closed")
    if config.get("expected_result_frozen_before_run") is not True:
        raise ValueError("expected result must be frozen before split/run")
    if len(config.get("ablation_arms") or []) < 2:
        raise ValueError("baseline and changed ablation arms are required")


def freeze_experiment_split(*, hypothesis: Path, taxonomy: Path, output_dir: Path) -> dict[str, Any]:
    if output_dir.exists():
        raise FileExistsError(f"Refusing to overwrite split freeze: {output_dir}")
    config = _json(hypothesis)
    _validate_hypothesis(config)
    observation = config["observation"]
    baseline_receipt = Path(str(observation["baseline_receipt"]))
    if not baseline_receipt.is_file():
        raise FileNotFoundError(f"baseline receipt not found: {baseline_receipt}")
    if sha256_file(baseline_receipt) != observation.get("baseline_receipt_sha256"):
        raise ValueError("baseline receipt SHA-256 does not match frozen hypothesis")
    baseline_data = _json(baseline_receipt)
    observed_period_counts = (
        (((((baseline_data.get("outputs") or {}).get("authorization") or {}).get("counts") or {}).get("field_status_counts") or {}).get("period") or {})
    )
    if observed_period_counts != observation.get("period_status_counts"):
        raise ValueError("hypothesis observation does not match baseline period status counts")
    rows = load_jsonl(taxonomy)
    if len(rows) != 1012:
        raise ValueError("taxonomy must cover exactly 1,012 questions")
    split = config["split_design"]
    target_temporal = set(split["target_temporal_families"])
    targets = [row for row in rows if target_temporal.intersection(row["taxonomy"]["temporal_families"])]
    seed = str(split["seed"])
    unseen_wordings = set(split["untouched_wording_families"])
    unseen_candidates = [
        row for row in targets
        if unseen_wordings.intersection(row["taxonomy"]["temporal_families"])
        and row["taxonomy"]["entities_resolved"]
    ]
    metric = str(split["metric_holdout_family"])
    metric_quota = int(split["minimum_metric_holdout_items"])
    composition_quota = int(split["minimum_composition_holdout_items"])
    development_topology = str(split["development_topology"])
    unseen_selected: list[Mapping[str, Any]] = []
    unseen_ids: set[str] = set()

    def add(selection: Sequence[Mapping[str, Any]]) -> None:
        for row in selection:
            key = str(row["question_sha256"])
            if key not in unseen_ids:
                unseen_selected.append(row)
                unseen_ids.add(key)

    metric_candidates = [row for row in unseen_candidates if metric in row["taxonomy"]["metric_families"]]
    add(_select_diverse(metric_candidates, metric_quota, seed + "|metric"))
    composition_candidates = [
        row for row in unseen_candidates
        if row["taxonomy"]["expected_source_topology"] != development_topology
        and str(row["question_sha256"]) not in unseen_ids
    ]
    add(_select_diverse(composition_candidates, composition_quota, seed + "|composition"))
    fill_candidates = [row for row in unseen_candidates if str(row["question_sha256"]) not in unseen_ids]
    add(_select_diverse(fill_candidates, int(split["untouched_evaluation_size"]) - len(unseen_selected), seed + "|fill"))
    unseen_entities = {entity for row in unseen_selected for entity in row["taxonomy"]["entities_resolved"]}
    development_candidates = [
        row for row in targets
        if split["development_wording_family"] in row["taxonomy"]["temporal_families"]
        and not unseen_wordings.intersection(row["taxonomy"]["temporal_families"])
        and row["taxonomy"]["expected_source_topology"] == development_topology
        and metric not in row["taxonomy"]["metric_families"]
        and row["taxonomy"]["entities_resolved"]
        and unseen_entities.isdisjoint(row["taxonomy"]["entities_resolved"])
    ]
    development_selected = _select_diverse(development_candidates, int(split["development_size"]), seed + "|development")
    used_hashes = {str(row["question_sha256"]) for row in unseen_selected + development_selected}
    discovery_candidates = [row for row in targets if str(row["question_sha256"]) not in used_hashes]
    discovery_selected = _select_diverse(discovery_candidates, int(split["discovery_size"]), seed + "|discovery")
    assignments: list[dict[str, Any]] = []
    for stage, selected in (
        ("discovery", discovery_selected),
        ("development", development_selected),
        ("untouched_evaluation", unseen_selected),
    ):
        for row in selected:
            taxonomy_data = row["taxonomy"]
            holdouts: list[str] = []
            if stage == "untouched_evaluation":
                holdouts.extend(["company", "wording"])
                if metric in taxonomy_data["metric_families"]:
                    holdouts.append("metric")
                if taxonomy_data["expected_source_topology"] != development_topology:
                    holdouts.append("composition")
            assignments.append({
                "protocol": SPLIT_PROTOCOL,
                "schema_version": 1,
                "experiment_id": config["experiment_id"],
                "stage": stage,
                "question_id": row["question_id"],
                "question_id_role": "tracking_only",
                "question_sha256": row["question_sha256"],
                "question": row["question"],
                "taxonomy": taxonomy_data,
                "holdout_roles": holdouts,
                "selection_basis": "question_intrinsic_taxonomy_plus_seeded_question_hash",
                "source_contract": SOURCE_CONTRACT,
            })
    assignments.sort(key=lambda row: (str(row["stage"]), str(row["question_sha256"])))
    counts = Counter(str(row["stage"]) for row in assignments)
    summary = {
        "protocol": SPLIT_PROTOCOL,
        "schema_version": 1,
        "experiment_id": config["experiment_id"],
        "stage_counts": dict(sorted(counts.items())),
        "target_population_count": len(targets),
        "selection_uses_question_id": False,
        "company_holdout": {
            "development_entities": sorted({entity for row in development_selected for entity in row["taxonomy"]["entities_resolved"]}),
            "untouched_entities": sorted(unseen_entities),
            "overlap": [],
        },
        "wording_holdout": {
            "development": [split["development_wording_family"]],
            "untouched": sorted(unseen_wordings),
        },
        "metric_holdout": {
            "family": metric,
            "development_count": sum(metric in row["taxonomy"]["metric_families"] for row in development_selected),
            "untouched_count": sum(metric in row["taxonomy"]["metric_families"] for row in unseen_selected),
        },
        "composition_holdout": {
            "development_topology": development_topology,
            "untouched_non_development_topology_count": sum(row["taxonomy"]["expected_source_topology"] != development_topology for row in unseen_selected),
        },
        "evaluator_status": config["evaluator"]["status"],
        "model_change_allowed": False,
        "full_dataset_allowed": False,
        "source_contract": SOURCE_CONTRACT,
    }
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=output_dir.parent))
    try:
        hypothesis_freeze = temp_dir / "hypothesis_freeze_v1.json"
        assignments_path = temp_dir / "split_assignments_v1.jsonl"
        summary_path = temp_dir / "split_summary_v1.json"
        _write_json(hypothesis_freeze, config)
        _write_jsonl(assignments_path, assignments)
        _write_json(summary_path, summary)
        manifest = {
            "protocol": MANIFEST_PROTOCOL,
            "schema_version": 1,
            "experiment_id": config["experiment_id"],
            "inputs": {
                "hypothesis": {"path": str(hypothesis), "sha256": sha256_file(hypothesis)},
                "taxonomy": {"path": str(taxonomy), "sha256": sha256_file(taxonomy)},
                "baseline_receipt": {"path": str(baseline_receipt), "sha256": sha256_file(baseline_receipt)},
            },
            "outputs": {
                hypothesis_freeze.name: {"sha256": sha256_file(hypothesis_freeze)},
                assignments_path.name: {"sha256": sha256_file(assignments_path)},
                summary_path.name: {"sha256": sha256_file(summary_path)},
            },
            "source_contract": SOURCE_CONTRACT,
        }
        _write_json(temp_dir / "manifest.json", manifest)
        temp_dir.rename(output_dir)
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise
    return summary


def validate_experiment_split(output_dir: Path) -> dict[str, Any]:
    manifest = _json(output_dir / "manifest.json")
    if manifest.get("protocol") != MANIFEST_PROTOCOL:
        raise ValueError("invalid split manifest protocol")
    for name, descriptor in (manifest.get("inputs") or {}).items():
        path = Path(str(descriptor.get("path") or ""))
        if not path.is_file() or sha256_file(path) != descriptor.get("sha256"):
            raise ValueError(f"split input hash mismatch: {name}")
    for name, descriptor in (manifest.get("outputs") or {}).items():
        if sha256_file(output_dir / name) != descriptor.get("sha256"):
            raise ValueError(f"split output hash mismatch: {name}")
    config = _json(output_dir / "hypothesis_freeze_v1.json")
    _validate_hypothesis(config)
    rows = load_jsonl(output_dir / "split_assignments_v1.jsonl")
    summary = _json(output_dir / "split_summary_v1.json")
    split = config["split_design"]
    expected_counts = {
        "discovery": int(split["discovery_size"]),
        "development": int(split["development_size"]),
        "untouched_evaluation": int(split["untouched_evaluation_size"]),
    }
    counts = Counter(str(row["stage"]) for row in rows)
    if dict(counts) != expected_counts:
        raise ValueError(f"unexpected split sizes: {dict(counts)}")
    hashes_by_stage = {stage: {str(row["question_sha256"]) for row in rows if row["stage"] == stage} for stage in expected_counts}
    for first, second in (("discovery", "development"), ("discovery", "untouched_evaluation"), ("development", "untouched_evaluation")):
        if hashes_by_stage[first].intersection(hashes_by_stage[second]):
            raise ValueError(f"split leakage between {first} and {second}")
    if any(row.get("question_id_role") != "tracking_only" or row.get("selection_basis") != "question_intrinsic_taxonomy_plus_seeded_question_hash" for row in rows):
        raise ValueError("Question ID influenced split selection")
    dev = [row for row in rows if row["stage"] == "development"]
    unseen = [row for row in rows if row["stage"] == "untouched_evaluation"]
    dev_entities = {entity for row in dev for entity in row["taxonomy"]["entities_resolved"]}
    unseen_entities = {entity for row in unseen for entity in row["taxonomy"]["entities_resolved"]}
    if dev_entities.intersection(unseen_entities):
        raise ValueError("company holdout leakage")
    metric = str(split["metric_holdout_family"])
    if any(metric in row["taxonomy"]["metric_families"] for row in dev):
        raise ValueError("metric holdout leaked into development")
    if sum(metric in row["taxonomy"]["metric_families"] for row in unseen) < int(split["minimum_metric_holdout_items"]):
        raise ValueError("metric holdout quota not met")
    unseen_wordings = set(split["untouched_wording_families"])
    if any(unseen_wordings.intersection(row["taxonomy"]["temporal_families"]) for row in dev):
        raise ValueError("wording holdout leaked into development")
    if any(not unseen_wordings.intersection(row["taxonomy"]["temporal_families"]) for row in unseen):
        raise ValueError("untouched evaluation contains non-holdout wording")
    development_topology = str(split["development_topology"])
    if any(row["taxonomy"]["expected_source_topology"] != development_topology for row in dev):
        raise ValueError("development composition is not frozen")
    if sum(row["taxonomy"]["expected_source_topology"] != development_topology for row in unseen) < int(split["minimum_composition_holdout_items"]):
        raise ValueError("composition holdout quota not met")
    if summary.get("model_change_allowed") is not False or summary.get("full_dataset_allowed") is not False:
        raise ValueError("missing evaluator cannot authorize model/full-dataset work")
    return {
        "status": "VALIDATION_PASSED",
        "experiment_id": config["experiment_id"],
        "stage_counts": expected_counts,
        "company_overlap_count": 0,
        "metric_holdout_count": summary["metric_holdout"]["untouched_count"],
        "composition_holdout_count": summary["composition_holdout"]["untouched_non_development_topology_count"],
        "model_change_allowed": False,
    }
