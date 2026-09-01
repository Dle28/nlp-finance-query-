"""Leakage-resistant ViNumQA experiment for Vietnamese percent-change semantics."""

from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any, Iterable, Mapping
import unicodedata

from finance_query.e2e.core.questions import infer_family, infer_operation_ast, normalize_text


SPLIT_PROTOCOL = "vietnamese_percent_change_semantics_split_v1"
EVAL_PROTOCOL = "vietnamese_percent_change_semantics_evaluation_v1"
MANIFEST_PROTOCOL = "vietnamese_percent_change_semantics_split_manifest_v1"
SOURCE_CONTRACT = {
    "research_only": True,
    "evidence_eligible": False,
    "may_materialize_answer": False,
    "training_eligible": False,
    "submission_eligible": False,
    "promotion_allowed": False,
    "vifinqa_answer_authority": False,
}
YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
OP_RE = re.compile(r"(?:^|,\s*)([a-z_]+)\(")
NUMBER_RE = re.compile(r"[-+]?\$?\d[\d,.]*(?:%|x)?", re.IGNORECASE)


def _sha_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _ascii(value: str) -> str:
    normalized = unicodedata.normalize("NFD", value.casefold())
    normalized = "".join(character for character in normalized if unicodedata.category(character) != "Mn")
    return re.sub(r"\s+", " ", normalized.replace("đ", "d")).strip()


def _wording_template(question: str) -> str:
    value = _ascii(question)
    value = YEAR_RE.sub(" <year> ", value)
    value = re.sub(r"\b\d+(?:[.,]\d+)?%?\b", " <number> ", value)
    value = re.sub(r"[^a-z<> ]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _percent_phrase(question: str, *, exclude_points: bool = True) -> bool:
    value = _ascii(question)
    if exclude_points and "diem phan tram" in value:
        return False
    if "chiem bao nhieu phan tram" in value:
        return False
    patterns = (
        r"(?:ty le\s+)?phan tram\s+(?:cua\s+)?(?:su\s+)?(?:thay doi|tang truong)",
        r"(?:thay doi|tang truong).{0,55}(?:bao nhieu\s+)?phan tram",
        r"ty le\s+(?:thay doi|tang truong)\s+phan tram",
    )
    return any(re.search(pattern, value) for pattern in patterns)


def _years(question: str) -> list[int]:
    return sorted({int(value) for value in YEAR_RE.findall(question)})


def table_numeric_fingerprint(table: object) -> str:
    rows = table if isinstance(table, list) else []
    tokens: list[list[object]] = []
    shape: list[int] = []
    for row_index, row in enumerate(rows):
        cells = row if isinstance(row, list) else []
        shape.append(len(cells))
        for column_index, cell in enumerate(cells):
            normalized = [
                token.casefold().replace("$", "").replace(",", "")
                for token in NUMBER_RE.findall(str(cell))
            ]
            if normalized:
                tokens.append([row_index, column_index, normalized])
    payload = json.dumps(
        {"row_count": len(rows), "shape": shape, "numeric_tokens": tokens},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return _sha_text(payload)


def build_finqa_company_index(finqa_root: Path) -> dict[str, set[str]]:
    index: dict[str, set[str]] = defaultdict(set)
    for split in ("train", "dev", "test"):
        for item in json.loads((finqa_root / f"{split}.json").read_text(encoding="utf-8")):
            source = str(item.get("filename") or "")
            company = source.split("/", 1)[0].strip()
            if company:
                index[table_numeric_fingerprint(item.get("table") or [])].add(company)
    return dict(index)


def _ops(program: str) -> list[str]:
    return OP_RE.findall(program)


def gold_relative_percent_change(program: str) -> bool:
    operations = _ops(program)
    return bool(operations and operations[-1] == "divide" and "subtract" in operations[:-1])


def _metric_family(question: str) -> str:
    value = _ascii(question)
    patterns = (
        ("revenue", ("doanh thu",)),
        ("profit", ("loi nhuan", "lai rong", "lnst")),
        ("margin", ("bien loi nhuan", "bien lai")),
        ("returns", ("roe", "roa", "roi")),
        ("per_share", ("eps", "co phieu")),
        ("cash_flow", ("dong tien", "tien mat")),
        ("debt", ("no ", "khoan vay")),
    )
    for family, tokens in patterns:
        if any(token in value for token in tokens):
            return family
    return "other_financial_metric"


def _question_composition(question: str) -> str:
    value = _ascii(question)
    if "la bao nhieu va" in value:
        return "reported_value_plus_relative_change"
    if "so sanh" in value:
        return "comparison_plus_relative_change"
    if "quy" in value:
        return "quarter_transition_relative_change"
    if "du bao" in value or "ke hoach" in value:
        return "forecast_transition_relative_change"
    return "single_metric_two_period_relative_change"


def candidate_semantic(
    question: str,
    *,
    resolved_company_count: int = 1,
    require_phrase: bool = True,
    require_two_periods: bool = True,
    exclude_points: bool = True,
) -> str | None:
    if resolved_company_count != 1:
        return None
    if require_phrase and not _percent_phrase(question, exclude_points=exclude_points):
        return None
    if require_two_periods and len(_years(question)) != 2:
        return None
    return "relative_percentage_change"


def baseline_semantic(question: str) -> str | None:
    family, _confidence = infer_family(normalize_text(question))
    operation = str(infer_operation_ast(family, question).get("op") or "")
    return {
        "percentage_change": "relative_percentage_change",
        "divide": "generic_ratio",
        "subtract": "difference",
        "lookup": "reported_value",
    }.get(operation)


def _record(
    item: Mapping[str, Any],
    stage: str,
    seed: str,
    company_index: Mapping[str, set[str]],
) -> dict[str, Any]:
    qa = item.get("qa") or {}
    question = str(qa.get("question") or "")
    program = str(qa.get("program") or "")
    companies = sorted(company_index.get(table_numeric_fingerprint(item.get("table") or []), set()))
    company = companies[0] if len(companies) == 1 else None
    canonical = json.dumps(
        {
            "question": question,
            "program": program,
            "table": item.get("table") or [],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return {
        "benchmark_stage": stage,
        "source_record_id": str(item.get("id") or ""),
        "record_sha256": _sha_text(canonical),
        "selection_hash": _sha_text(seed + "\n" + canonical),
        "company": company,
        "company_hash": _sha_text(company) if company else None,
        "wording_template_hash": _sha_text(_wording_template(question)),
        "metric_family": _metric_family(question),
        "composition_signature": _question_composition(question),
        "candidate_applicable": company is not None and candidate_semantic(question) is not None,
    }


def _seal(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "protocol": SPLIT_PROTOCOL,
        "benchmark_stage": row["benchmark_stage"],
        "record_sha256": row["record_sha256"],
        "source_record_tracking_hash": _sha_text(str(row["source_record_id"])),
        "company_hash": row["company_hash"],
        "wording_template_hash": row["wording_template_hash"],
        "metric_family": row["metric_family"],
        "composition_signature": row["composition_signature"],
        "sealed_before_evaluation": row["benchmark_stage"] == "untouched_evaluation",
        "question_or_program_materialized": False,
        "question_id_role": "tracking_only",
        "source_contract": SOURCE_CONTRACT,
    }


def _take(rows: Iterable[Mapping[str, Any]], count: int) -> list[dict[str, Any]]:
    ordered = sorted((dict(row) for row in rows), key=lambda row: row["selection_hash"])
    if len(ordered) < count:
        raise ValueError(f"insufficient records: need {count}, found {len(ordered)}")
    return ordered[:count]


def freeze_split(
    *,
    hypothesis_path: Path,
    train_path: Path,
    validation_path: Path,
    test_path: Path,
    finqa_root: Path,
    output_dir: Path,
) -> dict[str, Any]:
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite: {output_dir}")
    hypothesis = json.loads(hypothesis_path.read_text(encoding="utf-8"))
    design = hypothesis["split_design"]
    seed = str(design["seed"])
    company_index = build_finqa_company_index(finqa_root)
    paths = {
        "discovery": train_path,
        "development": validation_path,
        "untouched_evaluation": test_path,
    }
    source = {
        stage: json.loads(path.read_text(encoding="utf-8"))
        for stage, path in paths.items()
    }
    intrinsic = {
        stage: [
            row
            for item in items
            if (row := _record(item, stage, seed, company_index))["candidate_applicable"]
        ]
        for stage, items in source.items()
    }
    private_metric_counts = Counter(row["metric_family"] for row in intrinsic["untouched_evaluation"])
    private_composition_counts = Counter(row["composition_signature"] for row in intrinsic["untouched_evaluation"])
    metric_candidates = sorted(
        (value for value, count in private_metric_counts.items() if count >= 1),
        key=lambda value: _sha_text(seed + "\nmetric\n" + value),
    )
    composition_candidates = sorted(
        (value for value, count in private_composition_counts.items() if count >= 1),
        key=lambda value: _sha_text(seed + "\ncomposition\n" + value),
    )
    development = None
    untouched_pool = None
    metric_holdout = None
    composition_holdout = None
    for metric in metric_candidates:
        for composition in composition_candidates:
            development_pool = [
                row
                for row in intrinsic["development"]
                if row["metric_family"] != metric
                and row["composition_signature"] != composition
            ]
            if len(development_pool) < int(design["development_size"]):
                continue
            candidate_development = _take(development_pool, int(design["development_size"]))
            development_companies = {row["company_hash"] for row in candidate_development}
            development_wordings = {row["wording_template_hash"] for row in candidate_development}
            candidate_untouched_pool = [
                row
                for row in intrinsic["untouched_evaluation"]
                if row["company_hash"] not in development_companies
                and row["wording_template_hash"] not in development_wordings
            ]
            if (
                len(candidate_untouched_pool) >= int(design["untouched_evaluation_size"])
                and any(row["metric_family"] == metric for row in candidate_untouched_pool)
                and any(row["composition_signature"] == composition for row in candidate_untouched_pool)
            ):
                development = candidate_development
                untouched_pool = candidate_untouched_pool
                metric_holdout = metric
                composition_holdout = composition
                break
        if development is not None:
            break
    if development is None or untouched_pool is None or metric_holdout is None or composition_holdout is None:
        raise ValueError("evaluation pool cannot satisfy metric/composition/company/wording holdouts")
    development_companies = {row["company_hash"] for row in development}
    metric_rows = _take((row for row in untouched_pool if row["metric_family"] == metric_holdout), 1)
    composition_rows = _take((row for row in untouched_pool if row["composition_signature"] == composition_holdout), 1)
    chosen = {row["record_sha256"]: row for row in metric_rows + composition_rows}
    for row in sorted(untouched_pool, key=lambda value: value["selection_hash"]):
        chosen.setdefault(row["record_sha256"], row)
        if len(chosen) >= int(design["untouched_evaluation_size"]):
            break
    untouched = list(chosen.values())[: int(design["untouched_evaluation_size"])]
    if len(untouched) != int(design["untouched_evaluation_size"]):
        raise ValueError("untouched selection did not reach requested size")
    reserved_companies = development_companies | {row["company_hash"] for row in untouched}
    discovery = _take(
        (row for row in intrinsic["discovery"] if row["company_hash"] not in reserved_companies),
        int(design["discovery_size"]),
    )
    assignments = [*map(_seal, discovery), *map(_seal, development), *map(_seal, untouched)]
    stage_companies = {
        stage: {row["company_hash"] for row in assignments if row["benchmark_stage"] == stage}
        for stage in paths
    }
    summary = {
        "protocol": SPLIT_PROTOCOL,
        "experiment_id": hypothesis["experiment_id"],
        "stage_counts": dict(Counter(row["benchmark_stage"] for row in assignments)),
        "eligible_source_counts": {stage: len(rows) for stage, rows in intrinsic.items()},
        "company_overlap_counts": {
            "discovery_development": len(stage_companies["discovery"] & stage_companies["development"]),
            "discovery_untouched": len(stage_companies["discovery"] & stage_companies["untouched_evaluation"]),
            "development_untouched": len(stage_companies["development"] & stage_companies["untouched_evaluation"]),
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
        "full_vifinqa_dataset_allowed": False,
        "source_contract": SOURCE_CONTRACT,
    }
    validate_assignments(assignments, summary, hypothesis)
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
        freeze_path = temporary / "hypothesis_freeze_v1.json"
        freeze_path.write_text(json.dumps(hypothesis, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        manifest = {
            "protocol": MANIFEST_PROTOCOL,
            "schema_version": 1,
            "inputs": {
                "hypothesis": {"path": str(hypothesis_path), "sha256": _sha_file(hypothesis_path)},
                "train": {"path": str(train_path), "sha256": _sha_file(train_path)},
                "validation": {"path": str(validation_path), "sha256": _sha_file(validation_path)},
                "test": {"path": str(test_path), "sha256": _sha_file(test_path)},
                **{
                    f"finqa_{split}": {
                        "path": str(finqa_root / f"{split}.json"),
                        "sha256": _sha_file(finqa_root / f"{split}.json"),
                    }
                    for split in ("train", "dev", "test")
                },
            },
            "outputs": {
                path.name: {"sha256": _sha_file(path)}
                for path in (assignments_path, summary_path, freeze_path)
            },
            "source_contract": SOURCE_CONTRACT,
        }
        (temporary / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temporary.rename(output_dir)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return summary


def validate_assignments(
    assignments: Iterable[Mapping[str, Any]],
    summary: Mapping[str, Any],
    hypothesis: Mapping[str, Any],
) -> None:
    rows = list(assignments)
    design = hypothesis["split_design"]
    expected = {
        "discovery": int(design["discovery_size"]),
        "development": int(design["development_size"]),
        "untouched_evaluation": int(design["untouched_evaluation_size"]),
    }
    if dict(Counter(row["benchmark_stage"] for row in rows)) != expected:
        raise ValueError("stage counts changed")
    if any((summary.get("company_overlap_counts") or {}).values()):
        raise ValueError("company overlap detected")
    if summary.get("wording_overlap_development_untouched") != 0:
        raise ValueError("wording overlap detected")
    if summary.get("selection_uses_source_record_id") is not False:
        raise ValueError("record ID influenced selection")
    for key in ("metric_holdout", "composition_holdout"):
        holdout = summary.get(key) or {}
        if holdout.get("development_count") != 0 or int(holdout.get("untouched_count") or 0) < 1:
            raise ValueError(f"{key} failed")
    if any(row.get("question_or_program_materialized") is not False for row in rows):
        raise ValueError("benchmark payload materialized")


def validate_split(artifact_dir: Path) -> dict[str, Any]:
    manifest = json.loads((artifact_dir / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("protocol") != MANIFEST_PROTOCOL:
        raise ValueError("manifest protocol mismatch")
    for name, descriptor in manifest["outputs"].items():
        if _sha_file(artifact_dir / name) != descriptor["sha256"]:
            raise ValueError(f"output hash mismatch: {name}")
    assignments = load_jsonl(artifact_dir / "split_assignments_v1.jsonl")
    summary = json.loads((artifact_dir / "split_summary_v1.json").read_text(encoding="utf-8"))
    hypothesis = json.loads((artifact_dir / "hypothesis_freeze_v1.json").read_text(encoding="utf-8"))
    validate_assignments(assignments, summary, hypothesis)
    return {"status": "VALIDATION_PASSED", **summary}


def evaluate_stage(
    *,
    assignments: Iterable[Mapping[str, Any]],
    records: Iterable[Mapping[str, Any]],
    stage: str,
    seed: str,
    company_index: Mapping[str, set[str]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    selected = [dict(row) for row in assignments if row.get("benchmark_stage") == stage]
    source = {_record(item, stage, seed, company_index)["record_sha256"]: item for item in records}
    arm_counts: dict[str, Counter[str]] = defaultdict(Counter)
    outcomes: Counter[str] = Counter()
    metric_outcomes: dict[str, Counter[str]] = defaultdict(Counter)
    composition_outcomes: dict[str, Counter[str]] = defaultdict(Counter)
    rows_out: list[dict[str, Any]] = []
    for assignment in selected:
        item = source.get(str(assignment["record_sha256"]))
        if item is None:
            raise ValueError("sealed assignment cannot be resolved")
        qa = item.get("qa") or {}
        question = str(qa.get("question") or "")
        gold = gold_relative_percent_change(str(qa.get("program") or ""))
        baseline = baseline_semantic(question)
        candidate = candidate_semantic(question)
        results = {
            "A_current_question_planner": baseline,
            "B_A_plus_narrow_relative_percent_change_contract": candidate,
        }
        result_rows: dict[str, dict[str, Any]] = {}
        for arm, prediction in results.items():
            correct = prediction == "relative_percentage_change" and gold if prediction is not None else None
            false_confident = prediction is not None and correct is not True
            result_rows[arm] = {
                "predicted": prediction is not None,
                "semantic_prediction": prediction,
                "correct_if_predicted": correct,
                "false_confident": false_confident,
            }
            arm_counts[arm]["predicted" if prediction is not None else "abstained"] += 1
            if prediction is not None:
                arm_counts[arm]["correct" if correct else "incorrect"] += 1
                if false_confident:
                    arm_counts[arm]["false_confident"] += 1
        baseline_correct = result_rows["A_current_question_planner"]["correct_if_predicted"] is True
        candidate_correct = result_rows["B_A_plus_narrow_relative_percent_change_contract"]["correct_if_predicted"] is True
        if candidate_correct and not baseline_correct:
            outcome = "IMPROVED"
        elif baseline_correct and not candidate_correct:
            outcome = "REGRESSED"
        else:
            outcome = "UNCHANGED"
        outcomes[outcome] += 1
        metric_outcomes[str(assignment["metric_family"])][outcome] += 1
        composition_outcomes[str(assignment["composition_signature"])][outcome] += 1
        rows_out.append({
            "protocol": EVAL_PROTOCOL,
            "benchmark_stage": stage,
            "record_sha256": assignment["record_sha256"],
            "source_record_tracking_hash": assignment["source_record_tracking_hash"],
            "metric_family": assignment["metric_family"],
            "composition_signature": assignment["composition_signature"],
            "arm_results": result_rows,
            "candidate_outcome": outcome,
            "question_or_program_materialized": False,
            "source_contract": SOURCE_CONTRACT,
        })
    arms: dict[str, dict[str, Any]] = {}
    for arm, counts in arm_counts.items():
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
    return rows_out, {
        "protocol": EVAL_PROTOCOL,
        "benchmark_stage": stage,
        "record_count": len(rows_out),
        "arms": arms,
        "candidate_outcome_counts": dict(sorted(outcomes.items())),
        "metric_outcome_counts": {key: dict(sorted(value.items())) for key, value in sorted(metric_outcomes.items())},
        "composition_outcome_counts": {key: dict(sorted(value.items())) for key, value in sorted(composition_outcomes.items())},
        "ablation": evaluate_ablations(records=records, company_index=company_index),
        "question_or_program_materialized": False,
        "full_vifinqa_dataset_allowed": False,
        "source_contract": SOURCE_CONTRACT,
    }


def evaluate_ablations(
    *, records: Iterable[Mapping[str, Any]], company_index: Mapping[str, set[str]]
) -> dict[str, Any]:
    configs = {
        "C_B_without_exact_phrase_gate": (False, True, True),
        "D_B_without_exact_two_period_gate": (True, False, True),
        "E_B_without_percent_point_exclusion": (True, True, False),
    }
    result: dict[str, Any] = {}
    source = [dict(item) for item in records]
    for name, config in configs.items():
        predicted = correct = false_confident = 0
        for item in source:
            qa = item.get("qa") or {}
            companies = company_index.get(table_numeric_fingerprint(item.get("table") or []), set())
            prediction = candidate_semantic(
                str(qa.get("question") or ""),
                resolved_company_count=len(companies),
                require_phrase=config[0],
                require_two_periods=config[1],
                exclude_points=config[2],
            )
            if prediction is None:
                continue
            predicted += 1
            is_correct = gold_relative_percent_change(str(qa.get("program") or ""))
            correct += int(is_correct)
            false_confident += int(not is_correct)
        result[name] = {
            "population_count": len(source),
            "predicted_count": predicted,
            "correct_count": correct,
            "false_confident_count": false_confident,
            "precision_on_predictions": correct / predicted if predicted else None,
        }
    primary_predicted = sum(
        candidate_semantic(
            str((item.get("qa") or {}).get("question") or ""),
            resolved_company_count=len(
                company_index.get(table_numeric_fingerprint(item.get("table") or []), set())
            ),
        ) is not None
        for item in source
    )
    result["F_B_with_reversed_chronological_roles"] = {
        "population_count": len(source),
        "predicted_count": primary_predicted,
        "correct_count": 0,
        "false_confident_count": primary_predicted,
        "precision_on_predictions": 0.0 if primary_predicted else None,
        "structural_reason": "Reversing new and old changes (new-old)/old into a different signed and normalized quantity.",
    }
    return result


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
