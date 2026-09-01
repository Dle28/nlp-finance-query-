"""Normalize independent financial-QA benchmarks behind a leakage firewall."""

from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping


PROTOCOL = "external_financial_reference_benchmark_v1"
SOURCE_PROTOCOL = "external_financial_reference_sources_v1"
ALLOWED_STAGES = {"discovery", "development", "untouched_evaluation"}
FINQA_OPERATORS = {
    "add",
    "divide",
    "exp",
    "greater",
    "multiply",
    "subtract",
    "table_average",
    "table_max",
    "table_min",
    "table_sum",
}
FORBIDDEN_AUTHORITY = {
    "training_eligible": False,
    "evidence_eligible": False,
    "may_materialize_vifinqa_answer": False,
    "submission_eligible": False,
    "promotion_allowed": False,
}
_OP_RE = re.compile(r"([a-z_]+)\(")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_json(value: Mapping[str, Any]) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _read_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _source_contract() -> dict[str, bool]:
    return {"research_only": True, **FORBIDDEN_AUTHORITY}


def _finqa_rows(path: Path, split: str, stage: str, source: Mapping[str, Any]) -> Iterable[dict[str, Any]]:
    file_sha = _sha256_file(path)
    for item in _read_json(path):
        qa = item.get("qa") or {}
        program = str(qa.get("program") or "").strip()
        operators = _OP_RE.findall(program)
        unknown = sorted(set(operators) - FINQA_OPERATORS)
        if unknown:
            raise ValueError(f"FinQA program contains unsupported operators: {unknown}")
        source_id = str(item.get("id") or "").strip()
        if not source_id:
            raise ValueError("FinQA record is missing id")
        core = {
            "protocol": PROTOCOL,
            "source_dataset": "finqa",
            "source_split": split,
            "benchmark_stage": stage,
            "source_record_id": source_id,
            "question": str(qa.get("question") or "").strip(),
            "task_family": "program_reasoning",
            "operators": operators,
            "reasoning_target": {
                "program": program,
                "expected_answer": qa.get("exe_ans"),
                "gold_evidence_keys": sorted((qa.get("gold_inds") or {}).keys()),
            },
            "source_locator": f"dataset/{split}.json#id={source_id}",
            "source_commit": source["commit"],
            "source_file_sha256": file_sha,
            "source_contract": _source_contract(),
        }
        core["reference_id"] = _sha256_json(core)
        yield core


def _tatqa_rows(path: Path, split: str, stage: str, source: Mapping[str, Any]) -> Iterable[dict[str, Any]]:
    file_sha = _sha256_file(path)
    for context_index, item in enumerate(_read_json(path)):
        for question in item.get("questions") or []:
            source_id = str(question.get("uid") or "").strip()
            if not source_id:
                raise ValueError("TAT-QA question is missing uid")
            answer_type = str(question.get("answer_type") or "").strip()
            core = {
                "protocol": PROTOCOL,
                "source_dataset": "tatqa",
                "source_split": split,
                "benchmark_stage": stage,
                "source_record_id": source_id,
                "question": str(question.get("question") or "").strip(),
                "task_family": answer_type,
                "operators": ["derivation"] if answer_type == "arithmetic" else [],
                "reasoning_target": {
                    "derivation": str(question.get("derivation") or "").strip(),
                    "expected_answer": question.get("answer"),
                    "answer_from": question.get("answer_from"),
                    "scale": str(question.get("scale") or "").strip(),
                },
                "source_locator": f"dataset_raw/tatqa_dataset_{split}.json#context={context_index};uid={source_id}",
                "source_commit": source["commit"],
                "source_file_sha256": file_sha,
                "source_contract": _source_contract(),
            }
            core["reference_id"] = _sha256_json(core)
            yield core


def validate_source_config(config: Mapping[str, Any]) -> None:
    if config.get("protocol") != SOURCE_PROTOCOL or config.get("schema_version") != 1:
        raise ValueError("invalid external reference source protocol")
    firewall = config.get("firewall") or {}
    required_false = {
        "vifinqa_question_content_allowed",
        "vifinqa_question_id_allowed",
        "hidden_competition_labels_allowed",
        "leaderboard_feedback_allowed_for_development",
        "external_gold_may_authorize_vifinqa_answer",
    }
    if any(firewall.get(field) is not False for field in required_false):
        raise ValueError("external reference firewall has been weakened")
    if firewall.get("original_split_mapping_must_be_preserved") is not True:
        raise ValueError("external source splits must remain frozen")
    for name, source in (config.get("sources") or {}).items():
        if name not in {"finqa", "tatqa"}:
            raise ValueError(f"unsupported source: {name}")
        if not str(source.get("repository") or "").startswith("https://github.com/"):
            raise ValueError(f"source {name} must use an official HTTPS repository")
        if not re.fullmatch(r"[0-9a-f]{40}", str(source.get("commit") or "")):
            raise ValueError(f"source {name} must pin a Git commit")
        if set((source.get("splits") or {}).values()) - ALLOWED_STAGES:
            raise ValueError(f"source {name} has an invalid benchmark stage")
    contract = config.get("source_contract") or {}
    if contract.get("research_only") is not True:
        raise ValueError("external reference lane must remain research-only")
    for field, expected in FORBIDDEN_AUTHORITY.items():
        if contract.get(field) is not expected:
            raise ValueError(f"external reference lane cannot authorize {field}")


def validate_reference_rows(rows: Iterable[Mapping[str, Any]], config: Mapping[str, Any]) -> dict[str, Any]:
    validate_source_config(config)
    expected_splits = {
        (dataset, split): stage
        for dataset, source in config["sources"].items()
        for split, stage in source["splits"].items()
    }
    seen: set[str] = set()
    counts: Counter[str] = Counter()
    stage_counts: Counter[str] = Counter()
    operator_counts: Counter[str] = Counter()
    row_count = 0
    for row in rows:
        row_count += 1
        reference_id = str(row.get("reference_id") or "")
        if not reference_id or reference_id in seen:
            raise ValueError("reference_id must be non-empty and unique")
        seen.add(reference_id)
        dataset = str(row.get("source_dataset") or "")
        split = str(row.get("source_split") or "")
        stage = str(row.get("benchmark_stage") or "")
        if expected_splits.get((dataset, split)) != stage:
            raise ValueError("external benchmark split mapping drifted")
        if "vifinqa" in str(row.get("source_locator") or "").casefold():
            raise ValueError("ViFinQA content cannot enter the external reference benchmark")
        if not str(row.get("question") or "").strip():
            raise ValueError("reference question is empty")
        target = row.get("reasoning_target") or {}
        if target.get("expected_answer") in (None, "", []):
            raise ValueError("external reference requires an expected answer")
        contract = row.get("source_contract") or {}
        if contract.get("research_only") is not True or any(
            contract.get(field) is not expected for field, expected in FORBIDDEN_AUTHORITY.items()
        ):
            raise ValueError("reference row authority exceeds research-only scope")
        counts[dataset] += 1
        stage_counts[stage] += 1
        operator_counts.update(row.get("operators") or [])
    if not row_count:
        raise ValueError("external reference benchmark is empty")
    return {
        "status": "VALIDATION_PASSED",
        "protocol": PROTOCOL,
        "record_count": row_count,
        "dataset_counts": dict(sorted(counts.items())),
        "stage_counts": dict(sorted(stage_counts.items())),
        "operator_counts": dict(sorted(operator_counts.items())),
    }


def build_external_reference(
    *,
    finqa_root: Path,
    tatqa_root: Path,
    source_config: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    validate_source_config(source_config)
    rows: list[dict[str, Any]] = []
    finqa_source = source_config["sources"]["finqa"]
    for split, stage in finqa_source["splits"].items():
        rows.extend(_finqa_rows(finqa_root / f"{split}.json", split, stage, finqa_source))
    tatqa_source = source_config["sources"]["tatqa"]
    for split, stage in tatqa_source["splits"].items():
        rows.extend(
            _tatqa_rows(
                tatqa_root / f"tatqa_dataset_{split}.json",
                split,
                stage,
                tatqa_source,
            )
        )
    rows.sort(key=lambda row: (row["source_dataset"], row["source_split"], row["source_record_id"]))
    return rows, validate_reference_rows(rows, source_config)
