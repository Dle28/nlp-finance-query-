"""Freeze leakage-resistant FinQA splits for scalar-multiply experiments."""

from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any, Iterable, Mapping


PROTOCOL = "dimensionless_scalar_multiply_split_v1"
MANIFEST_PROTOCOL = "dimensionless_scalar_multiply_split_manifest_v1"
SOURCE_CONTRACT = {
    "research_only": True,
    "evidence_eligible": False,
    "may_materialize_answer": False,
    "training_eligible": False,
    "submission_eligible": False,
    "promotion_allowed": False,
    "vifinqa_answer_authority": False,
}
OP_RE = re.compile(r"([a-z_]+)\(")
STEP_RE = re.compile(r"([a-z_]+)\(([^()]*)\)(?:, |$)")

METRIC_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("return_and_future_value", (r"\breturn\b", r"\byield\b", r"\bmatur", r"\bfuture value\b")),
    ("revenue_and_income", (r"\brevenue\b", r"\bsales\b", r"\bincome\b")),
    ("profit_and_margin", (r"\bprofit\b", r"\bmargin\b", r"\bearnings\b")),
    ("expense_and_tax", (r"\bexpense\b", r"\bcost\b", r"\btax\b")),
    ("assets_and_equity", (r"\basset", r"\bequity\b", r"\bcapital\b")),
    ("debt_and_liabilities", (r"\bdebt\b", r"\bliabilit", r"\bborrow", r"\bloan\b")),
    ("cash_flow", (r"\bcash flow\b", r"\bcash provided\b", r"\bcash used\b")),
    ("shares_and_ownership", (r"\bshares?\b", r"\bstock\b", r"\bownership\b")),
)


def _sha_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_question(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def _wording_template(question: str) -> str:
    value = _canonical_question(question)
    value = re.sub(r"\b(?:19|20)\d{2}\b", " <year> ", value)
    value = re.sub(r"\b\d+(?:[.,]\d+)?%?\b", " <number> ", value)
    value = re.sub(r"[^a-z<> ]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _metric_family(question: str) -> str:
    value = _canonical_question(question)
    for family, patterns in METRIC_PATTERNS:
        if any(re.search(pattern, value) for pattern in patterns):
            return family
    return "other_financial_metric"


def _scalar_kinds(program: str) -> list[str]:
    kinds: set[str] = set()
    for match in STEP_RE.finditer(program):
        if match.group(1) != "multiply":
            continue
        arguments = match.group(2).split(", ", 1)
        for token in arguments:
            token = token.strip()
            if token.startswith("const_"):
                kinds.add("named_constant")
            elif token.endswith("%"):
                kinds.add("percent_literal")
    return sorted(kinds)


def _record(item: Mapping[str, Any], split: str, seed: str) -> dict[str, Any]:
    qa = item.get("qa") or {}
    source_id = str(item.get("id") or "")
    source_filename = str(item.get("filename") or "")
    question = str(qa.get("question") or "")
    program = str(qa.get("program") or "")
    company = source_filename.split("/", 1)[0]
    operations = OP_RE.findall(program)
    scalar_kinds = _scalar_kinds(program)
    canonical = json.dumps(
        {
            "source_filename": source_filename,
            "question": question,
            "program": program,
            "table": item.get("table") or [],
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
        "company": company,
        "company_hash": _sha_text(company),
        "wording_template": _wording_template(question),
        "wording_template_hash": _sha_text(_wording_template(question)),
        "metric_family": _metric_family(question),
        "composition_signature": ">".join(operations),
        "operators": operations,
        "scalar_kinds": scalar_kinds,
        "contaminated": any(token in program for token in ("exp(", "greater(")),
    }


def _eligible(records: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        dict(record)
        for record in records
        if "multiply" in record.get("operators", [])
        and record.get("scalar_kinds")
        and not record.get("contaminated")
    ]


def _stable_target(values: Iterable[str], *, counts: Mapping[str, int], minimum: int, seed: str) -> str:
    eligible = sorted({value for value in values if counts.get(value, 0) >= minimum})
    if not eligible:
        raise ValueError(f"no holdout category has at least {minimum} records")
    return min(eligible, key=lambda value: _sha_text(seed + "\n" + value))


def _stable_take(records: Iterable[Mapping[str, Any]], count: int) -> list[dict[str, Any]]:
    rows = sorted((dict(record) for record in records), key=lambda row: row["selection_hash"])
    if len(rows) < count:
        raise ValueError(f"insufficient eligible records: need {count}, found {len(rows)}")
    return rows[:count]


def _company_separated_development(
    records: Iterable[Mapping[str, Any]],
    test_records: Iterable[Mapping[str, Any]],
    count: int,
    seed: str,
) -> list[dict[str, Any]]:
    """Select enough dev rows while minimizing excluded untouched issuers."""
    candidates = [dict(record) for record in records]
    groups: dict[str, list[dict[str, Any]]] = {}
    for record in candidates:
        groups.setdefault(str(record["company_hash"]), []).append(record)
    test_counts = Counter(str(record["company_hash"]) for record in test_records)
    # capacity -> (test exclusion penalty, deterministic tie key, companies)
    states: dict[int, tuple[int, str, tuple[str, ...]]] = {0: (0, "", ())}
    ordered_companies = sorted(groups, key=lambda company: _sha_text(seed + "\n" + company))
    for company in ordered_companies:
        next_states = dict(states)
        capacity = len(groups[company])
        penalty = test_counts.get(company, 0)
        for current_capacity, (current_penalty, tie_key, companies) in states.items():
            new_capacity = min(count, current_capacity + capacity)
            new_companies = companies + (company,)
            new_tie = _sha_text(tie_key + "\n" + company)
            candidate = (current_penalty + penalty, new_tie, new_companies)
            previous = next_states.get(new_capacity)
            if previous is None or candidate[:2] < previous[:2]:
                next_states[new_capacity] = candidate
        states = next_states
    if count not in states:
        raise ValueError(f"insufficient company-separated development records: need {count}")
    selected_companies = set(states[count][2])
    return _stable_take(
        (record for record in candidates if record["company_hash"] in selected_companies),
        count,
    )


def _seal(record: Mapping[str, Any], stage: str) -> dict[str, Any]:
    sealed = stage == "untouched_evaluation"
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
        "scalar_kinds": record["scalar_kinds"],
        "sealed_before_evaluation": sealed,
        "question_or_program_materialized": False,
        "question_id_role": "tracking_only",
        "source_contract": SOURCE_CONTRACT,
    }


def freeze_scalar_multiply_split(
    *,
    hypothesis_path: Path,
    finqa_root: Path,
    output_dir: Path,
) -> dict[str, Any]:
    if output_dir.exists():
        raise FileExistsError(f"Refusing to overwrite artifact directory: {output_dir}")
    hypothesis = json.loads(hypothesis_path.read_text(encoding="utf-8"))
    if hypothesis.get("experiment_id") != "dimensionless_scalar_multiply_v1":
        raise ValueError("unexpected scalar-multiply hypothesis")
    design = hypothesis.get("split_design") or {}
    seed = str(design.get("seed") or "")
    if not seed:
        raise ValueError("split seed is required")
    raw: dict[str, list[dict[str, Any]]] = {}
    input_hashes: dict[str, dict[str, str]] = {}
    for split in ("train", "dev", "test"):
        path = finqa_root / f"{split}.json"
        items = json.loads(path.read_text(encoding="utf-8"))
        raw[split] = [_record(item, split, seed) for item in items]
        input_hashes[split] = {"path": str(path), "sha256": _sha_file(path)}
    eligible = {split: _eligible(records) for split, records in raw.items()}
    test_metric_counts = Counter(row["metric_family"] for row in eligible["test"])
    metric_holdout = _stable_target(
        test_metric_counts,
        counts=test_metric_counts,
        minimum=6,
        seed=seed + "\nmetric",
    )
    test_composition_counts = Counter(row["composition_signature"] for row in eligible["test"])
    composition_candidates = sorted(
        (signature for signature, count in test_composition_counts.items() if count >= 6),
        key=lambda value: _sha_text(seed + "\ncomposition\n" + value),
    )
    development: list[dict[str, Any]] | None = None
    untouched_pool: list[dict[str, Any]] | None = None
    composition_holdout: str | None = None
    for candidate_signature in composition_candidates:
        development_pool = [
            row
            for row in eligible["dev"]
            if row["metric_family"] != metric_holdout
            and row["composition_signature"] != candidate_signature
        ]
        try:
            candidate_development = _company_separated_development(
                development_pool,
                eligible["test"],
                int(design["development_size"]),
                seed + "\ndevelopment\n" + candidate_signature,
            )
        except ValueError:
            continue
        development_companies = {row["company_hash"] for row in candidate_development}
        development_wordings = {row["wording_template_hash"] for row in candidate_development}
        candidate_untouched_pool = [
            row
            for row in eligible["test"]
            if row["company_hash"] not in development_companies
            and row["wording_template_hash"] not in development_wordings
        ]
        if (
            len(candidate_untouched_pool) >= int(design["untouched_evaluation_size"])
            and
            sum(row["metric_family"] == metric_holdout for row in candidate_untouched_pool) >= 6
            and sum(row["composition_signature"] == candidate_signature for row in candidate_untouched_pool) >= 6
        ):
            development = candidate_development
            untouched_pool = candidate_untouched_pool
            composition_holdout = candidate_signature
            break
    if development is None or untouched_pool is None or composition_holdout is None:
        raise ValueError("no deterministic development/untouched split satisfies all holdouts")
    metric_rows = _stable_take(
        (row for row in untouched_pool if row["metric_family"] == metric_holdout),
        6,
    )
    composition_rows = _stable_take(
        (row for row in untouched_pool if row["composition_signature"] == composition_holdout),
        6,
    )
    chosen: dict[str, dict[str, Any]] = {
        row["record_sha256"]: row for row in metric_rows + composition_rows
    }
    for row in sorted(untouched_pool, key=lambda item: item["selection_hash"]):
        chosen.setdefault(row["record_sha256"], row)
        if len(chosen) >= int(design["untouched_evaluation_size"]):
            break
    untouched = list(chosen.values())[: int(design["untouched_evaluation_size"])]
    if len(untouched) != int(design["untouched_evaluation_size"]):
        raise ValueError("untouched selection did not reach requested size")
    reserved_companies = development_companies | {row["company_hash"] for row in untouched}
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
    stage_wordings = {
        stage: {row["wording_template_hash"] for row in assignments if row["benchmark_stage"] == stage}
        for stage in ("development", "untouched_evaluation")
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
        "wording_overlap_development_untouched": len(
            stage_wordings["development"] & stage_wordings["untouched_evaluation"]
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
    validate_scalar_multiply_assignments(assignments, summary, hypothesis)
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=output_dir.parent))
    try:
        assignments_path = temp_dir / "split_assignments_v1.jsonl"
        assignments_path.write_text(
            "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in assignments),
            encoding="utf-8",
        )
        summary_path = temp_dir / "split_summary_v1.json"
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        freeze_path = temp_dir / "hypothesis_freeze_v1.json"
        freeze_path.write_text(json.dumps(hypothesis, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        manifest = {
            "protocol": MANIFEST_PROTOCOL,
            "schema_version": 1,
            "inputs": {
                "hypothesis": {"path": str(hypothesis_path), "sha256": _sha_file(hypothesis_path)},
                **input_hashes,
            },
            "outputs": {
                path.name: {"sha256": _sha_file(path)}
                for path in (assignments_path, summary_path, freeze_path)
            },
            "source_contract": SOURCE_CONTRACT,
        }
        (temp_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temp_dir.rename(output_dir)
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise
    return summary


def validate_scalar_multiply_assignments(
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
        raise ValueError("split stage counts changed")
    record_hashes = [str(row.get("record_sha256") or "") for row in rows]
    if len(record_hashes) != len(set(record_hashes)) or not all(re.fullmatch(r"[0-9a-f]{64}", value) for value in record_hashes):
        raise ValueError("split record hashes must be unique SHA-256 values")
    if any(row.get("question_or_program_materialized") is not False for row in rows):
        raise ValueError("split assignments cannot materialize benchmark payload")
    if any((summary.get("company_overlap_counts") or {}).values()):
        raise ValueError("company holdout overlap detected")
    if summary.get("wording_overlap_development_untouched") != 0:
        raise ValueError("wording holdout overlap detected")
    if summary.get("selection_uses_source_record_id") is not False:
        raise ValueError("record ID influenced selection")
    for key in ("metric_holdout", "composition_holdout"):
        holdout = summary.get(key) or {}
        if holdout.get("development_count") != 0 or int(holdout.get("untouched_count") or 0) < 6:
            raise ValueError(f"{key} invariant failed")
    untouched = [row for row in rows if row["benchmark_stage"] == "untouched_evaluation"]
    if not untouched or any(row.get("sealed_before_evaluation") is not True for row in untouched):
        raise ValueError("untouched assignments are not sealed")


def validate_scalar_multiply_split(artifact_dir: Path) -> dict[str, Any]:
    manifest = json.loads((artifact_dir / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("protocol") != MANIFEST_PROTOCOL:
        raise ValueError("unexpected scalar multiply split manifest")
    for name, descriptor in (manifest.get("outputs") or {}).items():
        if _sha_file(artifact_dir / name) != descriptor.get("sha256"):
            raise ValueError(f"output hash mismatch: {name}")
    rows = [
        json.loads(line)
        for line in (artifact_dir / "split_assignments_v1.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    summary = json.loads((artifact_dir / "split_summary_v1.json").read_text(encoding="utf-8"))
    hypothesis = json.loads((artifact_dir / "hypothesis_freeze_v1.json").read_text(encoding="utf-8"))
    validate_scalar_multiply_assignments(rows, summary, hypothesis)
    return {"status": "VALIDATION_PASSED", **summary}
