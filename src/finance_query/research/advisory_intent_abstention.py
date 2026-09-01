"""Frozen VNFinsQA experiment for explicit advisory-intent abstention."""

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

from finance_query.e2e.core.questions import infer_family, normalize_text


SPLIT_PROTOCOL = "explicit_advisory_intent_abstention_split_v1"
EVAL_PROTOCOL = "explicit_advisory_intent_abstention_evaluation_v1"
MANIFEST_PROTOCOL = "explicit_advisory_intent_abstention_split_manifest_v1"
SOURCE_CONTRACT = {
    "research_only": True,
    "evidence_eligible": False,
    "may_materialize_answer": False,
    "training_eligible": False,
    "submission_eligible": False,
    "promotion_allowed": False,
    "vifinqa_answer_authority": False,
}
SAFE_ABSTENTION_CATEGORIES = frozenset({"recommendation", "valuation", "technical", "screening"})
ACTIONS = "mua|ban|giu|dau tu"


def _sha_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _ascii(value: str) -> str:
    normalized = unicodedata.normalize("NFD", value.casefold().replace("đ", "d"))
    return re.sub(
        r"\s+",
        " ",
        "".join(character for character in normalized if unicodedata.category(character) != "Mn"),
    ).strip()


def _tickers(record: Mapping[str, Any]) -> list[str]:
    return sorted({value.strip().upper() for value in str(record.get("tickers") or "").split(",") if value.strip()})


def _wording_template(question: str) -> str:
    value = re.sub(r"\b(?:19|20)\d{2}\b", " <year> ", _ascii(question))
    value = re.sub(r"\b\d+(?:[.,]\d+)?%?\b", " <number> ", value)
    value = re.sub(r"[^a-z<> ]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def explicit_advisory_intent(
    question: str,
    *,
    ticker_count: int,
    require_co_prefix: bool = True,
    require_action: bool = True,
    require_single_ticker: bool = True,
) -> bool:
    value = _ascii(question)
    if require_single_ticker and ticker_count != 1:
        return False
    prefix = r"co nen" if require_co_prefix else r"(?:co )?nen"
    action = rf"(?:{ACTIONS})" if require_action else r"[a-z]+"
    return re.search(rf"\b{prefix}\s+{action}\b", value) is not None


def baseline_route(question: str) -> str:
    family, _confidence = infer_family(normalize_text(question))
    return str(family)


def _record(record: Mapping[str, Any], stage: str, seed: str) -> dict[str, Any]:
    question = str(record.get("question") or "")
    tickers = _tickers(record)
    intrinsic = {
        "question": question,
        "tickers": tickers,
        "question_category": str(record.get("question_category") or ""),
        "difficulty": str(record.get("difficulty") or ""),
        "requires_multi_doc": bool(record.get("requires_multi_doc")),
    }
    canonical = json.dumps(intrinsic, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        "benchmark_stage": stage,
        "source_record_id": str(record.get("vnfinsqa_id") or ""),
        "record_sha256": _sha_text(canonical),
        "selection_hash": _sha_text(seed + "\n" + canonical),
        "company": tickers[0] if len(tickers) == 1 else None,
        "company_hash": _sha_text(tickers[0]) if len(tickers) == 1 else None,
        "wording_template_hash": _sha_text(_wording_template(question)),
        "metric_family": intrinsic["question_category"],
        "composition_signature": f"{intrinsic['difficulty']}|multi_doc={str(intrinsic['requires_multi_doc']).lower()}",
        "candidate_applicable": explicit_advisory_intent(question, ticker_count=len(tickers)),
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
        "question_materialized": False,
        "question_id_role": "tracking_only",
        "source_contract": SOURCE_CONTRACT,
    }


def _take(rows: Iterable[Mapping[str, Any]], count: int) -> list[dict[str, Any]]:
    ordered = sorted((dict(row) for row in rows), key=lambda row: row["selection_hash"])
    if len(ordered) < count:
        raise ValueError(f"insufficient records: need {count}, found {len(ordered)}")
    return ordered[:count]


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def freeze_split(*, hypothesis_path: Path, dataset_path: Path, output_dir: Path) -> dict[str, Any]:
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite: {output_dir}")
    hypothesis = json.loads(hypothesis_path.read_text(encoding="utf-8"))
    design = hypothesis["split_design"]
    seed = str(design["seed"])
    source = load_jsonl(dataset_path)
    intrinsic = [row for record in source if (row := _record(record, "pool", seed))["candidate_applicable"]]
    metric_counts = Counter(row["metric_family"] for row in intrinsic)
    composition_counts = Counter(row["composition_signature"] for row in intrinsic)
    metrics = sorted((x for x, n in metric_counts.items() if n >= 1), key=lambda x: _sha_text(seed + "\nmetric\n" + x))
    compositions = sorted((x for x, n in composition_counts.items() if n >= 1), key=lambda x: _sha_text(seed + "\ncomposition\n" + x))
    discovery = development = untouched = None
    metric_holdout = composition_holdout = None
    for metric in metrics:
        for composition in compositions:
            dev_pool = [row for row in intrinsic if row["metric_family"] != metric and row["composition_signature"] != composition]
            if len(dev_pool) < int(design["development_size"]):
                continue
            candidate_dev = _take(dev_pool, int(design["development_size"]))
            dev_companies = {row["company_hash"] for row in candidate_dev}
            dev_wordings = {row["wording_template_hash"] for row in candidate_dev}
            unseen_pool = [row for row in intrinsic if row["company_hash"] not in dev_companies and row["wording_template_hash"] not in dev_wordings]
            if len(unseen_pool) < int(design["untouched_evaluation_size"]):
                continue
            if not any(row["metric_family"] == metric for row in unseen_pool) or not any(row["composition_signature"] == composition for row in unseen_pool):
                continue
            required = [
                _take((row for row in unseen_pool if row["metric_family"] == metric), 1)[0],
                _take((row for row in unseen_pool if row["composition_signature"] == composition), 1)[0],
            ]
            selected = {row["record_sha256"]: row for row in required}
            for row in sorted(unseen_pool, key=lambda item: item["selection_hash"]):
                selected.setdefault(row["record_sha256"], row)
                if len(selected) >= int(design["untouched_evaluation_size"]):
                    break
            candidate_unseen = list(selected.values())[: int(design["untouched_evaluation_size"])]
            reserved = dev_companies | {row["company_hash"] for row in candidate_unseen}
            discovery_pool = [row for row in intrinsic if row["company_hash"] not in reserved]
            if len(discovery_pool) < int(design["discovery_size"]):
                continue
            discovery = _take(discovery_pool, int(design["discovery_size"]))
            development = candidate_dev
            untouched = candidate_unseen
            metric_holdout = metric
            composition_holdout = composition
            break
        if development is not None:
            break
    if any(value is None for value in (discovery, development, untouched, metric_holdout, composition_holdout)):
        raise ValueError("no split satisfies all holdouts")
    staged = [
        *({**row, "benchmark_stage": "discovery"} for row in discovery),
        *({**row, "benchmark_stage": "development"} for row in development),
        *({**row, "benchmark_stage": "untouched_evaluation"} for row in untouched),
    ]
    assignments = list(map(_seal, staged))
    stage_companies = {stage: {row["company_hash"] for row in assignments if row["benchmark_stage"] == stage} for stage in ("discovery", "development", "untouched_evaluation")}
    summary = {
        "protocol": SPLIT_PROTOCOL,
        "experiment_id": hypothesis["experiment_id"],
        "eligible_source_count": len(intrinsic),
        "stage_counts": dict(Counter(row["benchmark_stage"] for row in assignments)),
        "company_overlap_counts": {
            "discovery_development": len(stage_companies["discovery"] & stage_companies["development"]),
            "discovery_untouched": len(stage_companies["discovery"] & stage_companies["untouched_evaluation"]),
            "development_untouched": len(stage_companies["development"] & stage_companies["untouched_evaluation"]),
        },
        "wording_overlap_development_untouched": len({row["wording_template_hash"] for row in development} & {row["wording_template_hash"] for row in untouched}),
        "metric_holdout": {"family": metric_holdout, "development_count": sum(row["metric_family"] == metric_holdout for row in development), "untouched_count": sum(row["metric_family"] == metric_holdout for row in untouched)},
        "composition_holdout": {"signature": composition_holdout, "development_count": sum(row["composition_signature"] == composition_holdout for row in development), "untouched_count": sum(row["composition_signature"] == composition_holdout for row in untouched)},
        "selection_uses_source_record_id": False,
        "untouched_payload_sealed": True,
        "full_vifinqa_dataset_allowed": False,
        "source_contract": SOURCE_CONTRACT,
    }
    validate_assignments(assignments, summary, hypothesis)
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=output_dir.parent))
    try:
        assignment_path = temporary / "split_assignments_v1.jsonl"
        assignment_path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in assignments), encoding="utf-8")
        summary_path = temporary / "split_summary_v1.json"
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        freeze_path = temporary / "hypothesis_freeze_v1.json"
        freeze_path.write_text(json.dumps(hypothesis, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        manifest = {"protocol": MANIFEST_PROTOCOL, "schema_version": 1, "inputs": {"hypothesis": {"path": str(hypothesis_path), "sha256": _sha_file(hypothesis_path)}, "dataset": {"path": str(dataset_path), "sha256": _sha_file(dataset_path)}}, "outputs": {path.name: {"sha256": _sha_file(path)} for path in (assignment_path, summary_path, freeze_path)}, "source_contract": SOURCE_CONTRACT}
        (temporary / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temporary.rename(output_dir)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return summary


def validate_assignments(assignments: Iterable[Mapping[str, Any]], summary: Mapping[str, Any], hypothesis: Mapping[str, Any]) -> None:
    rows = list(assignments); design = hypothesis["split_design"]
    expected = {"discovery": int(design["discovery_size"]), "development": int(design["development_size"]), "untouched_evaluation": int(design["untouched_evaluation_size"])}
    if dict(Counter(row["benchmark_stage"] for row in rows)) != expected: raise ValueError("stage counts changed")
    if any((summary.get("company_overlap_counts") or {}).values()): raise ValueError("company overlap detected")
    if summary.get("wording_overlap_development_untouched") != 0: raise ValueError("wording overlap detected")
    if summary.get("selection_uses_source_record_id") is not False: raise ValueError("record ID influenced selection")
    for key in ("metric_holdout", "composition_holdout"):
        holdout = summary.get(key) or {}
        if holdout.get("development_count") != 0 or int(holdout.get("untouched_count") or 0) < 1: raise ValueError(f"{key} failed")
    if any(row.get("question_materialized") is not False for row in rows): raise ValueError("question materialized")


def validate_split(artifact_dir: Path) -> dict[str, Any]:
    manifest=json.loads((artifact_dir/"manifest.json").read_text());
    if manifest.get("protocol")!=MANIFEST_PROTOCOL: raise ValueError("manifest protocol mismatch")
    for name,descriptor in manifest["outputs"].items():
        if _sha_file(artifact_dir/name)!=descriptor["sha256"]: raise ValueError(f"hash mismatch: {name}")
    rows=load_jsonl(artifact_dir/"split_assignments_v1.jsonl");summary=json.loads((artifact_dir/"split_summary_v1.json").read_text());hypothesis=json.loads((artifact_dir/"hypothesis_freeze_v1.json").read_text());validate_assignments(rows,summary,hypothesis);return {"status":"VALIDATION_PASSED",**summary}


def evaluate_stage(*, assignments: Iterable[Mapping[str, Any]], records: Iterable[Mapping[str, Any]], stage: str, seed: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    selected=[dict(row) for row in assignments if row.get("benchmark_stage")==stage];source={_record(record,"pool",seed)["record_sha256"]:record for record in records};counts:dict[str,Counter[str]]=defaultdict(Counter);outcomes=Counter();metric_outcomes:dict[str,Counter[str]]=defaultdict(Counter);composition_outcomes:dict[str,Counter[str]]=defaultdict(Counter);rows=[]
    for assignment in selected:
        record=source.get(str(assignment["record_sha256"]));
        if record is None: raise ValueError("sealed assignment cannot be resolved")
        question=str(record.get("question") or "");tickers=_tickers(record);gold=str(record.get("question_category") or "") in SAFE_ABSTENTION_CATEGORIES
        predictions={"A_current_numeric_question_planner":baseline_route(question),"B_A_plus_explicit_advisory_abstention":"OUT_OF_SCOPE_ABSTAIN" if explicit_advisory_intent(question,ticker_count=len(tickers)) else None}
        arm_results={}
        for arm,prediction in predictions.items():
            correct=(prediction=="OUT_OF_SCOPE_ABSTAIN" and gold) if prediction is not None else None;false=prediction is not None and correct is not True;arm_results[arm]={"predicted":prediction is not None,"prediction":prediction,"correct_if_predicted":correct,"false_confident":false};counts[arm]["predicted" if prediction is not None else "abstained"]+=1
            if prediction is not None:counts[arm]["correct" if correct else "incorrect"]+=1;counts[arm]["false_confident"]+=int(false)
        base=arm_results["A_current_numeric_question_planner"]["correct_if_predicted"] is True;cand=arm_results["B_A_plus_explicit_advisory_abstention"]["correct_if_predicted"] is True;outcome="IMPROVED" if cand and not base else "REGRESSED" if base and not cand else "UNCHANGED";outcomes[outcome]+=1;metric_outcomes[str(assignment["metric_family"])][outcome]+=1;composition_outcomes[str(assignment["composition_signature"])][outcome]+=1;rows.append({"protocol":EVAL_PROTOCOL,"benchmark_stage":stage,"record_sha256":assignment["record_sha256"],"source_record_tracking_hash":assignment["source_record_tracking_hash"],"metric_family":assignment["metric_family"],"composition_signature":assignment["composition_signature"],"arm_results":arm_results,"candidate_outcome":outcome,"question_materialized":False,"source_contract":SOURCE_CONTRACT})
    arms={}
    for arm,c in counts.items():
        p=c["predicted"];arms[arm]={"predicted_count":p,"abstain_count":c["abstained"],"correct_count":c["correct"],"incorrect_count":c["incorrect"],"false_confident_count":c["false_confident"],"precision_on_predictions":c["correct"]/p if p else None,"false_confidence_rate":c["false_confident"]/p if p else None}
    report={"protocol":EVAL_PROTOCOL,"benchmark_stage":stage,"record_count":len(rows),"arms":arms,"candidate_outcome_counts":dict(sorted(outcomes.items())),"metric_outcome_counts":{k:dict(sorted(v.items())) for k,v in sorted(metric_outcomes.items())},"composition_outcome_counts":{k:dict(sorted(v.items())) for k,v in sorted(composition_outcomes.items())},"ablation":evaluate_ablations(records),"question_materialized":False,"full_vifinqa_dataset_allowed":False,"source_contract":SOURCE_CONTRACT};return rows,report


def evaluate_full_reference_population(
    *, records: Iterable[Mapping[str, Any]], seed: str
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Evaluate every intrinsically applicable record after the unseen gate passes."""
    source = list(records)
    assignments = []
    for record in source:
        intrinsic = _record(record, "pool", seed)
        if intrinsic["candidate_applicable"]:
            assignments.append(_seal({**intrinsic, "benchmark_stage": "full_reference_population"}))
    rows, report = evaluate_stage(
        assignments=assignments,
        records=source,
        stage="full_reference_population",
        seed=seed,
    )
    report["full_reference_record_count"] = len(source)
    report["full_vifinqa_dataset_allowed"] = False
    return rows, report


def evaluate_ablations(records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    source=list(records);configs={"C_B_without_exact_co_prefix":(False,True,True),"D_B_without_action_vocabulary":(True,False,True),"E_B_without_single_ticker_compatibility":(True,True,False)};result={}
    for name,config in configs.items():
        predicted=correct=false=0
        for record in source:
            applies=explicit_advisory_intent(str(record.get("question") or ""),ticker_count=len(_tickers(record)),require_co_prefix=config[0],require_action=config[1],require_single_ticker=config[2])
            if not applies:continue
            predicted+=1;ok=str(record.get("question_category") or "") in SAFE_ABSTENTION_CATEGORIES;correct+=int(ok);false+=int(not ok)
        result[name]={"population_count":len(source),"predicted_count":predicted,"correct_count":correct,"false_confident_count":false,"precision_on_predictions":correct/predicted if predicted else None}
    result["F_B_without_advisory_abstention"]={"population_count":len(source),"predicted_count":0,"correct_count":0,"false_confident_count":0,"precision_on_predictions":None};return result
