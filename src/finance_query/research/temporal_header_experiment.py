"""Freeze source-grounded temporal-semantics splits without exposing test text."""

from __future__ import annotations

from collections import Counter
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


PROTOCOL = "temporal_header_semantics_split_v1"
MANIFEST_PROTOCOL = "temporal_header_semantics_split_manifest_v1"
_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
_DURATION_HEADER_RE = re.compile(r"\byears? ended\b|\bfiscal years?\b|\byear to date\b")
_INSTANT_HEADER_RE = re.compile(
    r"\bas of\b|\bat (?:december|january|february|march|april|may|june|july|august|september|october|november)\b|\bbalance sheets?\b"
)
_DURATION_QUESTION_RE = re.compile(r"\byears? ended\b|\bfiscal years?\b")
_INSTANT_QUESTION_RE = re.compile(r"\bas of\b|\bat (?:the )?end\b|\bat (?:the )?date\b")


def _header_text(table: Iterable[Iterable[Any]]) -> str:
    rows = list(table)[:5]
    return " ".join(" ".join(str(cell) for cell in row) for row in rows).casefold()


def header_temporal_gold(table: Iterable[Iterable[Any]]) -> str | None:
    """Return a label only for a header with one unambiguous period anchor."""
    text = _header_text(table)
    duration = bool(_DURATION_HEADER_RE.search(text))
    instant = bool(_INSTANT_HEADER_RE.search(text))
    if duration == instant:
        return None
    return "duration" if duration else "instant"


def question_only_temporal(question: str) -> str | None:
    text = " ".join(question.casefold().split())
    duration = bool(_DURATION_QUESTION_RE.search(text))
    instant = bool(_INSTANT_QUESTION_RE.search(text))
    if duration == instant:
        return None
    return "duration" if duration else "instant"


def temporal_header_prediction(
    question: str,
    table: Iterable[Iterable[Any]],
    *,
    allow_duration: bool = True,
    allow_instant: bool = True,
) -> str | None:
    question_prediction = question_only_temporal(question)
    if question_prediction is not None:
        return question_prediction
    header_prediction = header_temporal_gold(table)
    if header_prediction == "duration" and allow_duration:
        return header_prediction
    if header_prediction == "instant" and allow_instant:
        return header_prediction
    return None


def _record(item: Mapping[str, Any], split: str, seed: str) -> dict[str, Any]:
    qa = item.get("qa") or {}
    question = str(qa.get("question") or "")
    program = str(qa.get("program") or "")
    source_id = str(item.get("id") or "")
    table = list(item.get("table") or [])
    gold = header_temporal_gold(table)
    baseline = question_only_temporal(question)
    operations = OP_RE.findall(program)
    company = source_id.split("/", 1)[0]
    canonical = json.dumps(
        {
            "source_id": source_id,
            "question": question,
            "header": _header_text(table),
            "program": program,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return {
        "source_split": split,
        "source_record_id": source_id,
        "record_sha256": _sha_text(canonical),
        "selection_hash": _sha_text(seed + "\n" + canonical),
        "company_hash": _sha_text(company),
        "wording_template_hash": _sha_text(_wording_template(question)),
        "metric_family": _metric_family(question),
        "composition_signature": f"{gold}|{'>'.join(operations) or 'lookup'}",
        "gold_temporal_type": gold,
        "baseline_temporal_type": baseline,
        "has_year": bool(_YEAR_RE.search(question)),
        "contaminated": any(token in program for token in ("exp(", "greater(")),
    }


def _eligible(records: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        dict(record)
        for record in records
        if record.get("gold_temporal_type") in {"duration", "instant"}
        and record.get("baseline_temporal_type") is None
        and record.get("has_year") is True
        and record.get("contaminated") is False
    ]


def _seal(record: Mapping[str, Any], stage: str) -> dict[str, Any]:
    return {
        "protocol": PROTOCOL,
        "benchmark_stage": stage,
        "source_split": record["source_split"],
        "record_sha256": record["record_sha256"],
        "source_record_tracking_hash": _sha_text(str(record["source_record_id"])),
        "company_hash": record["company_hash"],
        "wording_template_hash": record["wording_template_hash"],
        "metric_family": record["metric_family"],
        "composition_signature": record["composition_signature"],
        "gold_temporal_type": record["gold_temporal_type"],
        "sealed_before_evaluation": stage == "untouched_evaluation",
        "question_or_header_materialized": False,
        "question_id_role": "tracking_only",
        "source_contract": SOURCE_CONTRACT,
    }


def freeze_temporal_header_split(
    *, hypothesis_path: Path, finqa_root: Path, output_dir: Path
) -> dict[str, Any]:
    if output_dir.exists():
        raise FileExistsError(f"Refusing to overwrite artifact directory: {output_dir}")
    hypothesis = json.loads(hypothesis_path.read_text(encoding="utf-8"))
    if hypothesis.get("experiment_id") != "temporal_header_semantics_v1":
        raise ValueError("unexpected temporal header hypothesis")
    design = hypothesis.get("split_design") or {}
    seed = str(design.get("seed") or "")
    if not seed:
        raise ValueError("split seed is required")
    raw: dict[str, list[dict[str, Any]]] = {}
    inputs: dict[str, dict[str, str]] = {}
    for split in ("train", "dev", "test"):
        path = finqa_root / f"{split}.json"
        items = json.loads(path.read_text(encoding="utf-8"))
        raw[split] = [_record(item, split, seed) for item in items]
        inputs[split] = {"path": str(path), "sha256": _sha_file(path)}
    eligible = {split: _eligible(rows) for split, rows in raw.items()}

    test_metric_counts = Counter(row["metric_family"] for row in eligible["test"])
    metric_candidates = sorted(
        (value for value, count in test_metric_counts.items() if count >= 6),
        key=lambda value: _sha_text(seed + "\nmetric\n" + value),
    )
    test_composition_counts = Counter(row["composition_signature"] for row in eligible["test"])
    composition_candidates = sorted(
        (value for value, count in test_composition_counts.items() if count >= 6),
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
                for row in eligible["dev"]
                if row["metric_family"] != metric
                and row["composition_signature"] != composition
            ]
            try:
                candidate_development = _company_separated_development(
                    development_pool,
                    eligible["test"],
                    int(design["development_size"]),
                    seed + "\ndevelopment\n" + metric + "\n" + composition,
                )
            except ValueError:
                continue
            dev_companies = {row["company_hash"] for row in candidate_development}
            dev_wordings = {row["wording_template_hash"] for row in candidate_development}
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
                development = candidate_development
                untouched_pool = pool
                metric_holdout = metric
                composition_holdout = composition
                break
        if development is not None:
            break
    if (
        development is None
        or untouched_pool is None
        or metric_holdout is None
        or composition_holdout is None
    ):
        raise ValueError("no deterministic temporal split satisfies all holdouts")

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
        raise ValueError("untouched selection did not reach requested size")
    reserved_companies = {
        *(row["company_hash"] for row in development),
        *(row["company_hash"] for row in untouched),
    }
    discovery = _stable_take(
        (row for row in eligible["train"] if row["company_hash"] not in reserved_companies),
        int(design["discovery_size"]),
    )
    assignments = [
        *(_seal(row, "discovery") for row in discovery),
        *(_seal(row, "development") for row in development),
        *(_seal(row, "untouched_evaluation") for row in untouched),
    ]
    stage_companies = {
        stage: {row["company_hash"] for row in assignments if row["benchmark_stage"] == stage}
        for stage in ("discovery", "development", "untouched_evaluation")
    }
    dev_wordings = {
        row["wording_template_hash"]
        for row in assignments
        if row["benchmark_stage"] == "development"
    }
    test_wordings = {
        row["wording_template_hash"]
        for row in assignments
        if row["benchmark_stage"] == "untouched_evaluation"
    }
    summary = {
        "protocol": PROTOCOL,
        "experiment_id": hypothesis["experiment_id"],
        "stage_counts": dict(Counter(row["benchmark_stage"] for row in assignments)),
        "eligible_source_counts": {split: len(rows) for split, rows in eligible.items()},
        "contaminated_test_excluded_count": sum(row["contaminated"] for row in raw["test"]),
        "company_overlap_counts": {
            "discovery_development": len(stage_companies["discovery"] & stage_companies["development"]),
            "discovery_untouched": len(stage_companies["discovery"] & stage_companies["untouched_evaluation"]),
            "development_untouched": len(stage_companies["development"] & stage_companies["untouched_evaluation"]),
        },
        "wording_overlap_development_untouched": len(dev_wordings & test_wordings),
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
        "untouched_payload_sealed": True,
        "model_change_allowed": True,
        "full_vifinqa_dataset_allowed": False,
        "source_contract": SOURCE_CONTRACT,
    }
    validate_temporal_header_assignments(assignments, summary, hypothesis)
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
        (temporary / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.rename(output_dir)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return summary


def validate_temporal_header_assignments(
    assignments: Iterable[Mapping[str, Any]],
    summary: Mapping[str, Any],
    hypothesis: Mapping[str, Any],
) -> None:
    rows = list(assignments)
    design = hypothesis.get("split_design") or {}
    expected = {
        "discovery": int(design["discovery_size"]),
        "development": int(design["development_size"]),
        "untouched_evaluation": int(design["untouched_evaluation_size"]),
    }
    if dict(Counter(row["benchmark_stage"] for row in rows)) != expected:
        raise ValueError("temporal split stage counts changed")
    hashes = [str(row.get("record_sha256") or "") for row in rows]
    if len(hashes) != len(set(hashes)) or not all(re.fullmatch(r"[0-9a-f]{64}", value) for value in hashes):
        raise ValueError("temporal split record hashes must be unique")
    if any(row.get("question_or_header_materialized") is not False for row in rows):
        raise ValueError("temporal split cannot materialize question or header")
    if any((summary.get("company_overlap_counts") or {}).values()):
        raise ValueError("company holdout overlap detected")
    if summary.get("wording_overlap_development_untouched") != 0:
        raise ValueError("wording holdout overlap detected")
    for key in ("metric_holdout", "composition_holdout"):
        holdout = summary.get(key) or {}
        if holdout.get("development_count") != 0 or int(holdout.get("untouched_count") or 0) < 6:
            raise ValueError(f"{key} invariant failed")
    untouched = [row for row in rows if row["benchmark_stage"] == "untouched_evaluation"]
    if not untouched or any(row.get("sealed_before_evaluation") is not True for row in untouched):
        raise ValueError("untouched temporal assignments are not sealed")


def validate_temporal_header_split(artifact_dir: Path) -> dict[str, Any]:
    manifest = json.loads((artifact_dir / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("protocol") != MANIFEST_PROTOCOL:
        raise ValueError("unexpected temporal split manifest")
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
    validate_temporal_header_assignments(rows, summary, hypothesis)
    return {"status": "VALIDATION_PASSED", **summary}
