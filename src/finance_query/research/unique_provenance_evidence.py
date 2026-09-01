"""Exact provenance compatibility and abstention experiment over FinRank."""

from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any, Iterable, Mapping


SPLIT_PROTOCOL = "unique_provenance_evidence_split_v1"
EVAL_PROTOCOL = "unique_provenance_evidence_evaluation_v1"
MANIFEST_PROTOCOL = "unique_provenance_evidence_split_manifest_v1"
SOURCE_CONTRACT = {
    "research_only": True,
    "evidence_eligible": False,
    "may_materialize_answer": False,
    "training_eligible": False,
    "submission_eligible": False,
    "promotion_allowed": False,
    "vifinqa_answer_authority": False,
}


def _sha_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _wording_template(question: str) -> str:
    value = " ".join(question.casefold().split())
    value = re.sub(r"\b(?:19|20)\d{2}\b", " <year> ", value)
    value = re.sub(r"\b\d+(?:[.,]\d+)?%?\b", " <number> ", value)
    value = re.sub(r"[^a-z<> ]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _passage_metadata(record: Mapping[str, Any], passage: Mapping[str, Any], *, gold: bool) -> tuple[str, str, str]:
    return (
        str(record.get("ticker") if gold else passage.get("ticker") or ""),
        str(record.get("year") if gold else passage.get("year") or ""),
        str(record.get("doc_type") if gold else passage.get("doc_type") or ""),
    )


def _candidates(record: Mapping[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for passage in record.get("passages") or []:
        result.append({
            "gold": True,
            "passage_tracking_hash": _sha_text(str(passage.get("text_sha1") or passage.get("text") or "")),
            "metadata": _passage_metadata(record, passage, gold=True),
        })
    for passage in record.get("hard_negatives") or []:
        result.append({
            "gold": False,
            "passage_tracking_hash": _sha_text(str(passage.get("text_sha1") or passage.get("text") or "")),
            "metadata": _passage_metadata(record, passage, gold=False),
        })
    return result


def select_unique_compatible(
    record: Mapping[str, Any], *, use_ticker: bool = True, use_year: bool = True,
    use_form: bool = True, require_unique: bool = True,
) -> dict[str, Any] | None:
    expected = (str(record.get("ticker") or ""), str(record.get("year") or ""), str(record.get("doc_type") or ""))
    fields = (use_ticker, use_year, use_form)
    compatible = [
        candidate
        for candidate in _candidates(record)
        if all(not enabled or candidate["metadata"][index] == expected[index] for index, enabled in enumerate(fields))
    ]
    if not compatible or (require_unique and len(compatible) != 1):
        return None
    return sorted(compatible, key=lambda row: row["passage_tracking_hash"])[0]


def _record(record: Mapping[str, Any], stage: str, seed: str) -> dict[str, Any]:
    question = str(record.get("question") or "")
    canonical_candidates = sorted(
        (
            {
                "gold": candidate["gold"],
                "metadata": list(candidate["metadata"]),
                "passage_tracking_hash": candidate["passage_tracking_hash"],
            }
            for candidate in _candidates(record)
        ),
        key=lambda candidate: json.dumps(
            candidate, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ),
    )
    canonical = json.dumps(
        {"question": question, "candidate_hashes": canonical_candidates},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return {
        "benchmark_stage": stage,
        "source_record_id": str(record.get("id") or ""),
        "record_sha256": _sha_text(canonical),
        "selection_hash": _sha_text(seed + "\n" + canonical),
        "ticker": str(record.get("ticker") or ""),
        "ticker_hash": _sha_text(str(record.get("ticker") or "")),
        "wording_template_hash": _sha_text(_wording_template(question)),
        "metric_family": str(record.get("topic") or "Unknown"),
        "composition_signature": f"{record.get('reasoning_type')}|{record.get('difficulty')}",
        "single_passage": record.get("passage_type") == "Single-Passage" and len(record.get("passages") or []) == 1,
    }


def _seal(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "protocol": SPLIT_PROTOCOL,
        "benchmark_stage": row["benchmark_stage"],
        "record_sha256": row["record_sha256"],
        "source_record_tracking_hash": _sha_text(str(row["source_record_id"])),
        "ticker_hash": row["ticker_hash"],
        "wording_template_hash": row["wording_template_hash"],
        "metric_family": row["metric_family"],
        "composition_signature": row["composition_signature"],
        "sealed_before_evaluation": row["benchmark_stage"] == "untouched_evaluation",
        "question_or_passage_materialized": False,
        "question_id_role": "tracking_only",
        "source_contract": SOURCE_CONTRACT,
    }


def _take(rows: Iterable[Mapping[str, Any]], count: int) -> list[dict[str, Any]]:
    ordered = sorted((dict(row) for row in rows), key=lambda row: row["selection_hash"])
    if len(ordered) < count:
        raise ValueError(f"insufficient records: need {count}, found {len(ordered)}")
    return ordered[:count]


def freeze_unique_provenance_split(
    *, hypothesis_path: Path, dataset_path: Path, official_splits_path: Path, output_dir: Path
) -> dict[str, Any]:
    if output_dir.exists():
        raise FileExistsError(f"Refusing to overwrite artifact directory: {output_dir}")
    hypothesis = json.loads(hypothesis_path.read_text(encoding="utf-8"))
    if hypothesis.get("experiment_id") != "unique_provenance_evidence_v1":
        raise ValueError("unexpected provenance hypothesis")
    design = hypothesis["split_design"]
    seed = str(design["seed"])
    records = [json.loads(line) for line in dataset_path.read_text(encoding="utf-8").splitlines() if line]
    official = json.loads(official_splits_path.read_text(encoding="utf-8"))["ticker"]
    dev_tickers = set(official["held_out_dev_tickers"])
    test_tickers = set(official["held_out_test_tickers"])
    partitions = {
        "discovery": [record for record in records if record["ticker"] not in dev_tickers | test_tickers],
        "development": [record for record in records if record["ticker"] in dev_tickers],
        "untouched_evaluation": [record for record in records if record["ticker"] in test_tickers],
    }
    intrinsic = {
        stage: [row for record in values if (row := _record(record, stage, seed))["single_passage"]]
        for stage, values in partitions.items()
    }
    test_metric_counts = Counter(row["metric_family"] for row in intrinsic["untouched_evaluation"])
    metrics = sorted(
        (value for value, count in test_metric_counts.items() if count >= 6),
        key=lambda value: _sha_text(seed + "\nmetric\n" + value),
    )
    test_comp_counts = Counter(row["composition_signature"] for row in intrinsic["untouched_evaluation"])
    compositions = sorted(
        (value for value, count in test_comp_counts.items() if count >= 6),
        key=lambda value: _sha_text(seed + "\ncomposition\n" + value),
    )
    development = None
    untouched_pool = None
    metric_holdout = None
    composition_holdout = None
    for metric in metrics:
        for composition in compositions:
            dev_pool = [row for row in intrinsic["development"] if row["metric_family"] != metric and row["composition_signature"] != composition]
            if len(dev_pool) < int(design["development_size"]):
                continue
            pool = intrinsic["untouched_evaluation"]
            if (
                len(pool) >= int(design["untouched_evaluation_size"])
                and sum(row["metric_family"] == metric for row in pool) >= 6
                and sum(row["composition_signature"] == composition for row in pool) >= 6
            ):
                development = _take(dev_pool, int(design["development_size"]))
                untouched_pool = pool
                metric_holdout, composition_holdout = metric, composition
                break
        if development is not None:
            break
    if development is None or untouched_pool is None:
        raise ValueError("no provenance split satisfies metric/composition holdouts")
    metric_rows = _take((row for row in untouched_pool if row["metric_family"] == metric_holdout), 6)
    composition_rows = _take((row for row in untouched_pool if row["composition_signature"] == composition_holdout), 6)
    chosen = {row["record_sha256"]: row for row in metric_rows + composition_rows}
    for row in sorted(untouched_pool, key=lambda value: value["selection_hash"]):
        chosen.setdefault(row["record_sha256"], row)
        if len(chosen) >= int(design["untouched_evaluation_size"]):
            break
    untouched = list(chosen.values())[: int(design["untouched_evaluation_size"])]
    discovery = _take(intrinsic["discovery"], int(design["discovery_size"]))
    assignments = [*map(_seal, discovery), *map(_seal, development), *map(_seal, untouched)]
    stage_tickers = {
        stage: {row["ticker_hash"] for row in assignments if row["benchmark_stage"] == stage}
        for stage in partitions
    }
    summary = {
        "protocol": SPLIT_PROTOCOL,
        "experiment_id": hypothesis["experiment_id"],
        "stage_counts": dict(Counter(row["benchmark_stage"] for row in assignments)),
        "eligible_source_counts": {stage: len(rows) for stage, rows in intrinsic.items()},
        "company_overlap_counts": {
            "discovery_development": len(stage_tickers["discovery"] & stage_tickers["development"]),
            "discovery_untouched": len(stage_tickers["discovery"] & stage_tickers["untouched_evaluation"]),
            "development_untouched": len(stage_tickers["development"] & stage_tickers["untouched_evaluation"]),
        },
        "wording_overlap_development_untouched": len(
            {row["wording_template_hash"] for row in development}
            & {row["wording_template_hash"] for row in untouched}
        ),
        "metric_holdout": {
            "family": metric_holdout,
            "development_count": sum(row["metric_family"] == metric_holdout for row in development),
            "untouched_count": sum(row["metric_family"] == metric_holdout for row in untouched),
        },
        "composition_holdout": {
            "signature": composition_holdout,
            "development_count": sum(row["composition_signature"] == composition_holdout for row in development),
            "untouched_count": sum(row["composition_signature"] == composition_holdout for row in untouched),
        },
        "selection_uses_source_record_id": False,
        "untouched_payload_sealed": True,
        "model_change_allowed": True,
        "full_vifinqa_dataset_allowed": False,
        "source_contract": SOURCE_CONTRACT,
    }
    validate_unique_provenance_assignments(assignments, summary, hypothesis)
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=output_dir.parent))
    try:
        assignments_path = temporary / "split_assignments_v1.jsonl"
        assignments_path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in assignments), encoding="utf-8")
        summary_path = temporary / "split_summary_v1.json"
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        freeze_path = temporary / "hypothesis_freeze_v1.json"
        freeze_path.write_text(json.dumps(hypothesis, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        manifest = {
            "protocol": MANIFEST_PROTOCOL, "schema_version": 1,
            "inputs": {
                "hypothesis": {"path": str(hypothesis_path), "sha256": _sha_file(hypothesis_path)},
                "dataset": {"path": str(dataset_path), "sha256": _sha_file(dataset_path)},
                "official_splits": {"path": str(official_splits_path), "sha256": _sha_file(official_splits_path)},
            },
            "outputs": {path.name: {"sha256": _sha_file(path)} for path in (assignments_path, summary_path, freeze_path)},
            "source_contract": SOURCE_CONTRACT,
        }
        (temporary / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temporary.rename(output_dir)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return summary


def validate_unique_provenance_assignments(assignments: Iterable[Mapping[str, Any]], summary: Mapping[str, Any], hypothesis: Mapping[str, Any]) -> None:
    rows = list(assignments)
    design = hypothesis["split_design"]
    expected = {"discovery": int(design["discovery_size"]), "development": int(design["development_size"]), "untouched_evaluation": int(design["untouched_evaluation_size"])}
    if dict(Counter(row["benchmark_stage"] for row in rows)) != expected:
        raise ValueError("provenance split counts changed")
    if any((summary.get("company_overlap_counts") or {}).values()):
        raise ValueError("company overlap detected")
    if summary.get("wording_overlap_development_untouched") != 0:
        raise ValueError("wording overlap detected")
    if summary.get("selection_uses_source_record_id") is not False:
        raise ValueError("record ID influenced selection")
    for key in ("metric_holdout", "composition_holdout"):
        holdout = summary.get(key) or {}
        if holdout.get("development_count") != 0 or int(holdout.get("untouched_count") or 0) < 6:
            raise ValueError(f"{key} failed")
    hashes = [str(row.get("record_sha256") or "") for row in rows]
    if len(hashes) != len(set(hashes)) or not all(re.fullmatch(r"[0-9a-f]{64}", value) for value in hashes):
        raise ValueError("record hashes invalid")
    if any(row.get("question_or_passage_materialized") is not False for row in rows):
        raise ValueError("payload materialized")


def validate_unique_provenance_split(artifact_dir: Path) -> dict[str, Any]:
    manifest = json.loads((artifact_dir / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("protocol") != MANIFEST_PROTOCOL:
        raise ValueError("manifest protocol mismatch")
    for name, descriptor in manifest["outputs"].items():
        if _sha_file(artifact_dir / name) != descriptor["sha256"]:
            raise ValueError(f"output hash mismatch: {name}")
    rows = [json.loads(line) for line in (artifact_dir / "split_assignments_v1.jsonl").read_text(encoding="utf-8").splitlines() if line]
    summary = json.loads((artifact_dir / "split_summary_v1.json").read_text(encoding="utf-8"))
    hypothesis = json.loads((artifact_dir / "hypothesis_freeze_v1.json").read_text(encoding="utf-8"))
    validate_unique_provenance_assignments(rows, summary, hypothesis)
    return {"status": "VALIDATION_PASSED", **summary}


def evaluate_unique_provenance_stage(
    *, assignments: Iterable[Mapping[str, Any]], records: Iterable[Mapping[str, Any]], stage: str, seed: str
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    selected = [dict(row) for row in assignments if row.get("benchmark_stage") == stage]
    source = {_record(record, stage, seed)["record_sha256"]: record for record in records}
    arms = {
        "A_current_no_automatic_evidence_authorization": None,
        "B_A_plus_ticker_year_form_unique_gate": (True, True, True, True),
        "C_B_without_ticker_compatibility": (False, True, True, True),
        "D_B_without_year_compatibility": (True, False, True, True),
        "E_B_without_form_compatibility": (True, True, False, True),
        "F_B_without_unique_candidate_abstention": (True, True, True, False),
    }
    arm_counts: dict[str, Counter[str]] = defaultdict(Counter)
    outcomes: Counter[str] = Counter()
    metric_outcomes: dict[str, Counter[str]] = defaultdict(Counter)
    composition_outcomes: dict[str, Counter[str]] = defaultdict(Counter)
    rows_out: list[dict[str, Any]] = []
    for assignment in selected:
        record = source.get(str(assignment["record_sha256"]))
        if record is None:
            raise ValueError("sealed provenance assignment cannot be resolved")
        results: dict[str, dict[str, Any]] = {}
        for arm, config in arms.items():
            prediction = None if config is None else select_unique_compatible(
                record, use_ticker=config[0], use_year=config[1], use_form=config[2], require_unique=config[3]
            )
            correct = prediction.get("gold") is True if prediction is not None else None
            false_confident = prediction is not None and not correct
            results[arm] = {"predicted": prediction is not None, "correct_if_predicted": correct, "false_confident": false_confident}
            arm_counts[arm]["predicted" if prediction is not None else "abstained"] += 1
            if prediction is not None:
                arm_counts[arm]["correct" if correct else "incorrect"] += 1
                if false_confident:
                    arm_counts[arm]["false_confident"] += 1
        baseline, candidate = results["A_current_no_automatic_evidence_authorization"], results["B_A_plus_ticker_year_form_unique_gate"]
        if candidate["correct_if_predicted"] is True and baseline["predicted"] is False:
            outcome = "IMPROVED"
        elif baseline["correct_if_predicted"] is True and candidate["correct_if_predicted"] is not True:
            outcome = "REGRESSED"
        else:
            outcome = "UNCHANGED"
        outcomes[outcome] += 1
        metric_outcomes[str(assignment["metric_family"])][outcome] += 1
        composition_outcomes[str(assignment["composition_signature"])][outcome] += 1
        rows_out.append({
            "protocol": EVAL_PROTOCOL, "benchmark_stage": stage,
            "record_sha256": assignment["record_sha256"],
            "source_record_tracking_hash": assignment["source_record_tracking_hash"],
            "metric_family": assignment["metric_family"], "composition_signature": assignment["composition_signature"],
            "arm_results": results, "candidate_outcome": outcome,
            "question_or_passage_materialized": False, "source_contract": assignment["source_contract"],
        })
    arm_report: dict[str, dict[str, Any]] = {}
    for arm in arms:
        counts = arm_counts[arm]
        predicted = counts["predicted"]
        arm_report[arm] = {
            "predicted_count": predicted, "abstain_count": counts["abstained"],
            "correct_count": counts["correct"], "incorrect_count": counts["incorrect"],
            "false_confident_count": counts["false_confident"],
            "precision_on_predictions": counts["correct"] / predicted if predicted else None,
            "false_confidence_rate": counts["false_confident"] / predicted if predicted else None,
        }
    return rows_out, {
        "protocol": EVAL_PROTOCOL, "benchmark_stage": stage, "record_count": len(rows_out),
        "arms": arm_report, "candidate_outcome_counts": dict(sorted(outcomes.items())),
        "metric_outcome_counts": {key: dict(sorted(value.items())) for key, value in sorted(metric_outcomes.items())},
        "composition_outcome_counts": {key: dict(sorted(value.items())) for key, value in sorted(composition_outcomes.items())},
        "question_or_passage_materialized": False, "full_vifinqa_dataset_allowed": False,
    }


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def evaluate_unique_provenance_full_reference(
    *, records: Iterable[Mapping[str, Any]], seed: str
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    source = [dict(record) for record in records]
    intrinsic = [row for record in source if (row := _record(record, "full_dataset", seed))["single_passage"]]
    assignments = [_seal(row) for row in intrinsic]
    rows, report = evaluate_unique_provenance_stage(
        assignments=assignments, records=source, stage="full_dataset", seed=seed
    )
    report = {
        **report,
        "protocol": "unique_provenance_evidence_full_external_reference_v1",
        "benchmark_stage": "full_dataset_after_unseen_gate",
        "source_dataset": "FinRank",
        "source_contract": SOURCE_CONTRACT,
    }
    return rows, report
