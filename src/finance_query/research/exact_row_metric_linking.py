"""Leakage-resistant exact-row metric-linking research utilities."""

from __future__ import annotations

from collections import Counter, defaultdict
import json
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any, Iterable, Mapping

from .scalar_multiply_experiment import (
    OP_RE,
    SOURCE_CONTRACT,
    _company_separated_development,
    _metric_family,
    _sha_file,
    _sha_text,
    _stable_take,
    _wording_template,
)


SPLIT_PROTOCOL = "exact_row_metric_linking_split_v1"
EVAL_PROTOCOL = "exact_row_metric_linking_evaluation_v1"
MANIFEST_PROTOCOL = "exact_row_metric_linking_split_manifest_v1"
MINIMUM_TOKEN_F1 = 0.2
MINIMUM_UNIQUE_MARGIN = 0.1
_STOPWORDS = frozenset(
    "what is the in of was were did how much many amount amounts for company companies total a an and "
    "to from on as at by during which year years change percentage average ratio".split()
)


def _tokens(value: str, *, normalized: bool) -> set[str]:
    words = re.findall(r"[a-z0-9]+", value.casefold())
    if not normalized:
        return set(words)
    result: set[str] = set()
    for word in words:
        if word in _STOPWORDS or word.isdigit():
            continue
        result.add(word[:-1] if len(word) > 4 and word.endswith("s") else word)
    return result


def _token_f1(question: str, row_label: str, *, normalized: bool) -> float:
    question_tokens = _tokens(question, normalized=normalized)
    label_tokens = _tokens(row_label, normalized=normalized)
    if not question_tokens or not label_tokens:
        return 0.0
    return 2 * len(question_tokens & label_tokens) / (len(question_tokens) + len(label_tokens))


def select_exact_row(
    question: str,
    table: Iterable[Iterable[Any]],
    *,
    normalized: bool = True,
    require_margin: bool = True,
) -> tuple[int | None, dict[str, float]]:
    scored = sorted(
        (
            (_token_f1(question, str(row[0]) if row else "", normalized=normalized), index)
            for index, row in enumerate(table)
            if index > 0
        ),
        key=lambda value: (-value[0], value[1]),
    )
    if not scored:
        return None, {"best_score": 0.0, "margin": 0.0}
    best_score, best_index = scored[0]
    second_score = scored[1][0] if len(scored) > 1 else 0.0
    margin = best_score - second_score
    best_ties = sum(score == best_score for score, _index in scored)
    eligible = (
        best_score >= MINIMUM_TOKEN_F1
        and best_ties == 1
        and (not require_margin or margin >= MINIMUM_UNIQUE_MARGIN)
    )
    return (best_index if eligible else None), {"best_score": best_score, "margin": margin}


def _gold_row_index(item: Mapping[str, Any]) -> int | None:
    gold = (item.get("qa") or {}).get("gold_inds") or {}
    table_keys = [str(key) for key in gold if str(key).startswith("table_")]
    text_keys = [str(key) for key in gold if str(key).startswith("text_")]
    if len(table_keys) != 1 or text_keys:
        return None
    match = re.fullmatch(r"table_(\d+)", table_keys[0])
    if match is None:
        return None
    index = int(match.group(1))
    table = list(item.get("table") or [])
    return index if 0 < index < len(table) else None


def _record(item: Mapping[str, Any], split: str, seed: str) -> dict[str, Any]:
    qa = item.get("qa") or {}
    question = str(qa.get("question") or "")
    program = str(qa.get("program") or "")
    table = list(item.get("table") or [])
    gold_row = _gold_row_index(item)
    source_id = str(item.get("id") or "")
    company = source_id.split("/", 1)[0]
    canonical_payload = {
        "question": question,
        "table": table,
        "program": program,
        "gold_row_label": str(table[gold_row][0]) if gold_row is not None else None,
    }
    canonical = json.dumps(
        canonical_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    operations = OP_RE.findall(program)
    return {
        "source_split": split,
        "source_record_id": source_id,
        "record_sha256": _sha_text(canonical),
        "selection_hash": _sha_text(seed + "\n" + canonical),
        "company_hash": _sha_text(company),
        "wording_template_hash": _sha_text(_wording_template(question)),
        "metric_family": _metric_family(question),
        "composition_signature": ">".join(operations) or "lookup",
        "gold_row_index": gold_row,
        "contaminated": any(token in program for token in ("exp(", "greater(")),
    }


def _eligible(records: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in records
        if isinstance(row.get("gold_row_index"), int) and row.get("contaminated") is False
    ]


def _seal(row: Mapping[str, Any], stage: str) -> dict[str, Any]:
    return {
        "protocol": SPLIT_PROTOCOL,
        "benchmark_stage": stage,
        "source_split": row["source_split"],
        "record_sha256": row["record_sha256"],
        "source_record_tracking_hash": _sha_text(str(row["source_record_id"])),
        "company_hash": row["company_hash"],
        "wording_template_hash": row["wording_template_hash"],
        "metric_family": row["metric_family"],
        "composition_signature": row["composition_signature"],
        "gold_row_tracking_hash": _sha_text(str(row["gold_row_index"])),
        "sealed_before_evaluation": stage == "untouched_evaluation",
        "question_or_row_materialized": False,
        "question_id_role": "tracking_only",
        "source_contract": SOURCE_CONTRACT,
    }


def freeze_exact_row_split(
    *, hypothesis_path: Path, finqa_root: Path, output_dir: Path
) -> dict[str, Any]:
    if output_dir.exists():
        raise FileExistsError(f"Refusing to overwrite artifact directory: {output_dir}")
    hypothesis = json.loads(hypothesis_path.read_text(encoding="utf-8"))
    if hypothesis.get("experiment_id") != "exact_row_metric_linking_v1":
        raise ValueError("unexpected exact-row hypothesis")
    design = hypothesis.get("split_design") or {}
    seed = str(design.get("seed") or "")
    raw: dict[str, list[dict[str, Any]]] = {}
    inputs: dict[str, dict[str, str]] = {}
    for split in ("train", "dev", "test"):
        path = finqa_root / f"{split}.json"
        items = json.loads(path.read_text(encoding="utf-8"))
        raw[split] = [_record(item, split, seed) for item in items]
        inputs[split] = {"path": str(path), "sha256": _sha_file(path)}
    eligible = {split: _eligible(rows) for split, rows in raw.items()}
    discovery = _stable_take(eligible["train"], int(design["discovery_size"]))

    metric_counts = Counter(row["metric_family"] for row in eligible["test"])
    metrics = sorted(
        (value for value, count in metric_counts.items() if count >= 6),
        key=lambda value: _sha_text(seed + "\nmetric\n" + value),
    )
    composition_counts = Counter(row["composition_signature"] for row in eligible["test"])
    compositions = sorted(
        (value for value, count in composition_counts.items() if count >= 6),
        key=lambda value: _sha_text(seed + "\ncomposition\n" + value),
    )
    development = None
    untouched_pool = None
    metric_holdout = None
    composition_holdout = None
    for metric in metrics:
        for composition in compositions:
            dev_pool = [
                row
                for row in eligible["dev"]
                if row["metric_family"] != metric and row["composition_signature"] != composition
            ]
            try:
                candidate_dev = _company_separated_development(
                    dev_pool,
                    eligible["test"],
                    int(design["development_size"]),
                    seed + "\ndevelopment\n" + metric + "\n" + composition,
                )
            except ValueError:
                continue
            dev_companies = {row["company_hash"] for row in candidate_dev}
            dev_wordings = {row["wording_template_hash"] for row in candidate_dev}
            pool = [
                row
                for row in eligible["test"]
                if row["company_hash"] not in dev_companies
                and row["wording_template_hash"] not in dev_wordings
            ]
            if (
                len(pool) >= int(design["untouched_evaluation_size"])
                and sum(row["metric_family"] == metric for row in pool) >= 6
                and sum(row["composition_signature"] == composition for row in pool) >= 6
            ):
                development, untouched_pool = candidate_dev, pool
                metric_holdout, composition_holdout = metric, composition
                break
        if development is not None:
            break
    if development is None or untouched_pool is None:
        raise ValueError("no exact-row split satisfies all holdouts")
    metric_rows = _stable_take(
        (row for row in untouched_pool if row["metric_family"] == metric_holdout), 6
    )
    composition_rows = _stable_take(
        (row for row in untouched_pool if row["composition_signature"] == composition_holdout), 6
    )
    chosen = {row["record_sha256"]: row for row in metric_rows + composition_rows}
    for row in sorted(untouched_pool, key=lambda value: value["selection_hash"]):
        chosen.setdefault(row["record_sha256"], row)
        if len(chosen) >= int(design["untouched_evaluation_size"]):
            break
    untouched = list(chosen.values())[: int(design["untouched_evaluation_size"])]
    if len(untouched) != int(design["untouched_evaluation_size"]):
        raise ValueError("exact-row untouched selection is incomplete")
    discovery_companies = {row["company_hash"] for row in discovery}
    dev_companies = {row["company_hash"] for row in development}
    untouched_companies = {row["company_hash"] for row in untouched}
    # Re-select discovery without issuers reserved by later original splits.
    discovery = _stable_take(
        (
            row
            for row in eligible["train"]
            if row["company_hash"] not in dev_companies | untouched_companies
        ),
        int(design["discovery_size"]),
    )
    discovery_companies = {row["company_hash"] for row in discovery}
    assignments = [
        *(_seal(row, "discovery") for row in discovery),
        *(_seal(row, "development") for row in development),
        *(_seal(row, "untouched_evaluation") for row in untouched),
    ]
    dev_wordings = {row["wording_template_hash"] for row in development}
    untouched_wordings = {row["wording_template_hash"] for row in untouched}
    summary = {
        "protocol": SPLIT_PROTOCOL,
        "experiment_id": hypothesis["experiment_id"],
        "stage_counts": dict(Counter(row["benchmark_stage"] for row in assignments)),
        "eligible_source_counts": {split: len(rows) for split, rows in eligible.items()},
        "contaminated_test_excluded_count": sum(row["contaminated"] for row in raw["test"]),
        "company_overlap_counts": {
            "discovery_development": len(discovery_companies & dev_companies),
            "discovery_untouched": len(discovery_companies & untouched_companies),
            "development_untouched": len(dev_companies & untouched_companies),
        },
        "wording_overlap_development_untouched": len(dev_wordings & untouched_wordings),
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
    validate_exact_row_assignments(assignments, summary, hypothesis)
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=output_dir.parent))
    try:
        assignments_path = temporary / "split_assignments_v1.jsonl"
        assignments_path.write_text(
            "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in assignments),
            encoding="utf-8",
        )
        summary_path = temporary / "split_summary_v1.json"
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        hypothesis_freeze = temporary / "hypothesis_freeze_v1.json"
        hypothesis_freeze.write_text(json.dumps(hypothesis, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        manifest = {
            "protocol": MANIFEST_PROTOCOL,
            "schema_version": 1,
            "inputs": {
                "hypothesis": {"path": str(hypothesis_path), "sha256": _sha_file(hypothesis_path)},
                **inputs,
            },
            "outputs": {
                path.name: {"sha256": _sha_file(path)}
                for path in (assignments_path, summary_path, hypothesis_freeze)
            },
            "source_contract": SOURCE_CONTRACT,
        }
        (temporary / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temporary.rename(output_dir)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return summary


def validate_exact_row_assignments(
    assignments: Iterable[Mapping[str, Any]], summary: Mapping[str, Any], hypothesis: Mapping[str, Any]
) -> None:
    rows = list(assignments)
    design = hypothesis.get("split_design") or {}
    expected = {
        "discovery": int(design["discovery_size"]),
        "development": int(design["development_size"]),
        "untouched_evaluation": int(design["untouched_evaluation_size"]),
    }
    if dict(Counter(row["benchmark_stage"] for row in rows)) != expected:
        raise ValueError("exact-row stage counts changed")
    hashes = [str(row.get("record_sha256") or "") for row in rows]
    if len(hashes) != len(set(hashes)) or not all(re.fullmatch(r"[0-9a-f]{64}", value) for value in hashes):
        raise ValueError("exact-row record hashes are invalid")
    if any(row.get("question_or_row_materialized") is not False for row in rows):
        raise ValueError("exact-row split materialized payload")
    if summary.get("selection_uses_source_record_id") is not False:
        raise ValueError("source record ID cannot influence selection")
    if any((summary.get("company_overlap_counts") or {}).values()):
        raise ValueError("company holdout overlap detected")
    if summary.get("wording_overlap_development_untouched") != 0:
        raise ValueError("wording holdout overlap detected")
    for key in ("metric_holdout", "composition_holdout"):
        holdout = summary.get(key) or {}
        if holdout.get("development_count") != 0 or int(holdout.get("untouched_count") or 0) < 6:
            raise ValueError(f"{key} invariant failed")


def validate_exact_row_split(artifact_dir: Path) -> dict[str, Any]:
    manifest = json.loads((artifact_dir / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("protocol") != MANIFEST_PROTOCOL:
        raise ValueError("unexpected exact-row manifest")
    for name, descriptor in (manifest.get("outputs") or {}).items():
        if _sha_file(artifact_dir / name) != descriptor.get("sha256"):
            raise ValueError(f"output hash mismatch: {name}")
    rows = [
        json.loads(line)
        for line in (artifact_dir / "split_assignments_v1.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]
    summary = json.loads((artifact_dir / "split_summary_v1.json").read_text(encoding="utf-8"))
    hypothesis = json.loads((artifact_dir / "hypothesis_freeze_v1.json").read_text(encoding="utf-8"))
    validate_exact_row_assignments(rows, summary, hypothesis)
    return {"status": "VALIDATION_PASSED", **summary}


def evaluate_exact_row_stage(
    *, assignments: Iterable[Mapping[str, Any]], finqa_items: Iterable[Mapping[str, Any]],
    stage: str, split: str, seed: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    selected = [dict(row) for row in assignments if row.get("benchmark_stage") == stage]
    source = {_record(item, split, seed)["record_sha256"]: item for item in finqa_items}
    arm_configs = {
        "A_current_no_automatic_variable_binding": None,
        "B_A_plus_normalized_unique_margin_linker": (True, True),
        "C_B_without_financial_stopword_and_morphology_normalization": (False, True),
        "D_B_without_unique_margin_gate": (True, False),
    }
    arm_counts: dict[str, Counter[str]] = defaultdict(Counter)
    outcomes: Counter[str] = Counter()
    metric_outcomes: dict[str, Counter[str]] = defaultdict(Counter)
    composition_outcomes: dict[str, Counter[str]] = defaultdict(Counter)
    result_rows: list[dict[str, Any]] = []
    for assignment in selected:
        item = source.get(str(assignment["record_sha256"]))
        if item is None:
            raise ValueError("sealed exact-row assignment cannot be resolved")
        record = _record(item, split, seed)
        gold = record["gold_row_index"]
        if _sha_text(str(gold)) != assignment["gold_row_tracking_hash"]:
            raise ValueError("sealed gold row changed")
        question = str((item.get("qa") or {}).get("question") or "")
        table = list(item.get("table") or [])
        arm_results: dict[str, dict[str, Any]] = {}
        for arm, config in arm_configs.items():
            if config is None:
                prediction, telemetry = None, {"best_score": None, "margin": None}
            else:
                prediction, telemetry = select_exact_row(
                    question, table, normalized=config[0], require_margin=config[1]
                )
            correct = prediction == gold if prediction is not None else None
            false_confident = prediction is not None and prediction != gold
            arm_results[arm] = {
                "predicted": prediction is not None,
                "correct_if_predicted": correct,
                "false_confident": false_confident,
                **telemetry,
            }
            arm_counts[arm]["predicted" if prediction is not None else "abstained"] += 1
            if prediction is not None:
                arm_counts[arm]["correct" if correct else "incorrect"] += 1
                if false_confident:
                    arm_counts[arm]["false_confident"] += 1
        baseline = arm_results["A_current_no_automatic_variable_binding"]
        candidate = arm_results["B_A_plus_normalized_unique_margin_linker"]
        if candidate["correct_if_predicted"] is True and baseline["predicted"] is False:
            outcome = "IMPROVED"
        elif baseline["correct_if_predicted"] is True and candidate["correct_if_predicted"] is not True:
            outcome = "REGRESSED"
        else:
            outcome = "UNCHANGED"
        outcomes[outcome] += 1
        metric_outcomes[str(assignment["metric_family"])][outcome] += 1
        composition_outcomes[str(assignment["composition_signature"])][outcome] += 1
        result_rows.append({
            "protocol": EVAL_PROTOCOL,
            "benchmark_stage": stage,
            "source_split": split,
            "record_sha256": assignment["record_sha256"],
            "source_record_tracking_hash": assignment["source_record_tracking_hash"],
            "metric_family": assignment["metric_family"],
            "composition_signature": assignment["composition_signature"],
            "arm_results": arm_results,
            "candidate_outcome": outcome,
            "question_or_row_materialized": False,
            "source_contract": assignment["source_contract"],
        })
    arms: dict[str, dict[str, Any]] = {}
    for arm in arm_configs:
        counts = arm_counts[arm]
        predicted = counts["predicted"]
        arms[arm] = {
            "predicted_count": predicted,
            "abstain_count": counts["abstained"],
            "correct_count": counts["correct"],
            "incorrect_count": counts["incorrect"],
            "false_confident_count": counts["false_confident"],
            "precision_on_predictions": counts["correct"] / predicted if predicted else None,
            "false_confidence_rate": counts["false_confident"] / predicted if predicted else None,
        }
    report = {
        "protocol": EVAL_PROTOCOL,
        "benchmark_stage": stage,
        "source_split": split,
        "record_count": len(result_rows),
        "arms": arms,
        "candidate_outcome_counts": dict(sorted(outcomes.items())),
        "metric_outcome_counts": {key: dict(sorted(value.items())) for key, value in sorted(metric_outcomes.items())},
        "composition_outcome_counts": {key: dict(sorted(value.items())) for key, value in sorted(composition_outcomes.items())},
        "question_or_row_materialized": False,
        "full_vifinqa_dataset_allowed": False,
    }
    return result_rows, report


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def evaluate_exact_row_full_reference(
    *, finqa_items_by_split: Mapping[str, Iterable[Mapping[str, Any]]], seed: str
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    all_rows: list[dict[str, Any]] = []
    source_counts: dict[str, int] = {}
    for split in ("train", "dev", "test"):
        items = [dict(item) for item in finqa_items_by_split.get(split, [])]
        records = _eligible(_record(item, split, seed) for item in items)
        assignments = [_seal(record, "full_dataset") for record in records]
        rows, _report = evaluate_exact_row_stage(
            assignments=assignments, finqa_items=items, stage="full_dataset", split=split, seed=seed
        )
        all_rows.extend(rows)
        source_counts[split] = len(rows)
    arms: dict[str, Counter[str]] = defaultdict(Counter)
    outcomes: Counter[str] = Counter()
    metric_outcomes: dict[str, Counter[str]] = defaultdict(Counter)
    composition_outcomes: dict[str, Counter[str]] = defaultdict(Counter)
    for row in all_rows:
        outcome = str(row["candidate_outcome"])
        outcomes[outcome] += 1
        metric_outcomes[str(row["metric_family"])][outcome] += 1
        composition_outcomes[str(row["composition_signature"])][outcome] += 1
        for arm, result in row["arm_results"].items():
            arms[arm]["predicted" if result["predicted"] else "abstained"] += 1
            if result["predicted"]:
                arms[arm]["correct" if result["correct_if_predicted"] else "incorrect"] += 1
                if result["false_confident"]:
                    arms[arm]["false_confident"] += 1
    arm_report: dict[str, dict[str, Any]] = {}
    for arm, counts in arms.items():
        predicted = counts["predicted"]
        arm_report[arm] = {
            "predicted_count": predicted,
            "abstain_count": counts["abstained"],
            "correct_count": counts["correct"],
            "incorrect_count": counts["incorrect"],
            "false_confident_count": counts["false_confident"],
            "precision_on_predictions": counts["correct"] / predicted if predicted else None,
            "false_confidence_rate": counts["false_confident"] / predicted if predicted else None,
        }
    return all_rows, {
        "protocol": "exact_row_metric_linking_full_external_reference_v1",
        "benchmark_stage": "full_dataset_after_unseen_gate",
        "source_dataset": "finqa",
        "source_split_counts": source_counts,
        "record_count": len(all_rows),
        "arms": arm_report,
        "candidate_outcome_counts": dict(sorted(outcomes.items())),
        "metric_outcome_counts": {key: dict(sorted(value.items())) for key, value in sorted(metric_outcomes.items())},
        "composition_outcome_counts": {key: dict(sorted(value.items())) for key, value in sorted(composition_outcomes.items())},
        "question_or_row_materialized": False,
        "full_vifinqa_dataset_allowed": False,
        "source_contract": SOURCE_CONTRACT,
    }
