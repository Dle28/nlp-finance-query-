"""Compare retrieval top-k coverage against candidate-derived source UIDs.

This module measures a retrieval subscore only.  The source UIDs come from an
existing best-effort submission's evidence CSVs, so they are a diagnostic
reference and not gold relevant-table labels.  The audit intentionally emits
no answer, numeric cell, row label, or source value.
"""

from __future__ import annotations

from collections import Counter, defaultdict
import csv
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
from typing import Any, Iterable, Mapping

from finance_query.research.full_corpus_candidate_retrieval import (
    PROTOCOL as RETRIEVAL_PROTOCOL,
    sha256_file,
)


PROTOCOL = "vifinqa_retrieval_source_closure_audit_v1"
FORBIDDEN_KEYS = frozenset(
    {
        "answer",
        "answer_decimal",
        "raw_value",
        "raw_values",
        "cell_value",
        "pandas_query",
        "row_label",
        "rows",
        "source_value_cell",
        "source_cell",
        "raw_source_cell",
        "raw_source_row",
        "headers",
    }
)
CONTRACT = {
    "research_only": True,
    "navigation_metadata_only": True,
    "source_uids_are_candidate_derived": True,
    "gold_relevant_table_labels": False,
    "numeric_values_emitted": False,
    "evidence_eligible": False,
    "may_authorize_evidence": False,
    "may_authorize_answer": False,
    "training_eligible": False,
    "submission_eligible": False,
    "promotion_allowed": False,
}
DEFAULT_KS = (1, 3, 5, 10, 20)


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _read_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number} must contain a JSON object")
            yield value


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, values: Iterable[Mapping[str, Any]]) -> None:
    path.write_text(
        "".join(
            json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
            for value in values
        ),
        encoding="utf-8",
    )


def _contains_forbidden(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(
            str(key) in FORBIDDEN_KEYS or _contains_forbidden(child)
            for key, child in value.items()
        )
    if isinstance(value, list):
        return any(_contains_forbidden(child) for child in value)
    return False


def _canonical_id(value: object) -> str:
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    text = str(value).strip()
    return str(int(text)) if text.isdigit() else text


def _artifact_input_hashes(artifact_dir: Path) -> dict[str, str]:
    manifest = _read_json(artifact_dir / "manifest.json")
    if manifest.get("protocol") != RETRIEVAL_PROTOCOL:
        raise ValueError(f"{artifact_dir} is not a full-corpus retrieval artifact")
    for filename, descriptor in (manifest.get("outputs") or {}).items():
        path = artifact_dir / filename
        if not path.is_file() or sha256_file(path) != descriptor.get("sha256"):
            raise ValueError(f"retrieval artifact output hash mismatch: {path}")
    inputs = manifest.get("inputs") or {}
    result: dict[str, str] = {}
    for name, descriptor in inputs.items():
        path_value = str(descriptor.get("path") or "")
        path = Path(path_value)
        if not path.is_absolute():
            path = Path.cwd() / path
        expected = str(descriptor.get("sha256") or "")
        if not path.is_file() or not expected or sha256_file(path) != expected:
            raise ValueError(f"retrieval artifact input hash mismatch: {name}")
        result[str(name)] = expected
    return result


def _candidate_rank_index(
    artifact_dir: Path, *, expected_top_k: int
) -> tuple[dict[str, dict[str, int]], dict[str, Any], dict[tuple[str, str, int], list[tuple[int, str]]]]:
    input_hashes = _artifact_input_hashes(artifact_dir)
    rows_by_question: defaultdict[str, dict[str, int]] = defaultdict(dict)
    rows_by_route: defaultdict[tuple[str, str, int], list[tuple[int, str]]] = defaultdict(list)
    candidate_path = artifact_dir / "table_candidates_v1.jsonl"
    row_count = 0
    max_rank = 0
    for row in _read_jsonl(candidate_path):
        if _contains_forbidden(row):
            raise ValueError(f"navigation candidate leaked a forbidden field: {candidate_path}")
        question_id = _canonical_id(row.get("question_id"))
        uid = str(row.get("internal_table_uid") or "").strip()
        rank = row.get("candidate_rank")
        if not question_id or not uid or not isinstance(rank, int) or rank < 1:
            raise ValueError(f"invalid navigation candidate identity/rank in {candidate_path}")
        if row.get("navigation_metadata_only") is not True:
            raise ValueError("retrieval candidate is not navigation-only")
        if row.get("may_authorize_answer") is not False or row.get("submission_eligible") is not False:
            raise ValueError("retrieval candidate has an unsafe authorization flag")
        existing = rows_by_question[question_id].get(uid)
        rows_by_question[question_id][uid] = rank if existing is None else min(existing, rank)
        route_key = (
            question_id,
            str(row.get("operand_id") or ""),
            int(row.get("report_year")),
        )
        rows_by_route[route_key].append((rank, uid))
        row_count += 1
        max_rank = max(max_rank, rank)
    if max_rank > expected_top_k:
        raise ValueError(f"candidate rank {max_rank} exceeds expected K={expected_top_k}")
    for route_rows in rows_by_route.values():
        route_rows.sort()
    info = {
        "artifact_dir": str(artifact_dir),
        "artifact_manifest_sha256": sha256_file(artifact_dir / "manifest.json"),
        "candidate_path": str(candidate_path),
        "candidate_sha256": sha256_file(candidate_path),
        "candidate_row_count": row_count,
        "unique_question_count": len(rows_by_question),
        "max_candidate_rank": max_rank,
        "top_k": expected_top_k,
        "unique_candidate_table_count": len({uid for rows in rows_by_question.values() for uid in rows}),
        "route_count": len(rows_by_route),
        "input_hashes": input_hashes,
    }
    return rows_by_question, info, dict(rows_by_route)


def _submission_tiers(submission_path: Path, *, expected_question_count: int) -> dict[str, str]:
    payload = json.loads(submission_path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("submission must contain a JSON list")
    tiers: dict[str, str] = {}
    for row in payload:
        if not isinstance(row, dict):
            raise ValueError("submission contains a non-object record")
        question_id = _canonical_id(row.get("id"))
        if not question_id or question_id in tiers:
            raise ValueError("submission has a missing or duplicate question ID")
        tier = str(row.get("prediction_tier") or row.get("tier") or "UNKNOWN").strip() or "UNKNOWN"
        tiers[question_id] = tier
    if len(tiers) != expected_question_count:
        raise ValueError(
            f"submission population mismatch: expected {expected_question_count}, observed {len(tiers)}"
        )
    return tiers


def _evidence_bundle(evidence_dir: Path, *, expected_question_count: int) -> tuple[dict[str, set[str]], dict[str, int], dict[str, int], str, list[dict[str, str]]]:
    target_uids: dict[str, set[str]] = defaultdict(set)
    evidence_row_counts: Counter[str] = Counter()
    evidence_uid_row_counts: Counter[str] = Counter()
    file_descriptors: list[dict[str, str]] = []
    evidence_question_ids: set[str] = set()
    paths = sorted(evidence_dir.glob("q*_evidence.csv"))
    if len(paths) != expected_question_count:
        raise ValueError(
            f"evidence population mismatch: expected {expected_question_count}, observed {len(paths)}"
        )
    for path in paths:
        stem = path.name.split("_", 1)[0]
        question_id = _canonical_id(stem.removeprefix("q"))
        if not question_id:
            raise ValueError(f"invalid evidence filename: {path.name}")
        if question_id in evidence_question_ids:
            raise ValueError(f"duplicate evidence question ID: {question_id}")
        evidence_question_ids.add(question_id)
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames or "internal_table_uid" not in reader.fieldnames:
                raise ValueError(f"evidence file lacks internal_table_uid: {path}")
            for row in reader:
                evidence_row_counts[question_id] += 1
                uid = str(row.get("internal_table_uid") or "").strip()
                if uid:
                    target_uids[question_id].add(uid)
                    evidence_uid_row_counts[question_id] += 1
        file_descriptors.append({"name": path.name, "sha256": sha256_file(path)})
    if len(target_uids) > expected_question_count:
        raise ValueError("evidence contains more question IDs than the population")
    digest = hashlib.sha256()
    for descriptor in file_descriptors:
        digest.update(f"{descriptor['name']}:{descriptor['sha256']}\n".encode("utf-8"))
    return dict(target_uids), dict(evidence_row_counts), dict(evidence_uid_row_counts), digest.hexdigest(), file_descriptors


def _rank_metrics(
    question_ids: Iterable[str],
    target_uids: Mapping[str, set[str]],
    index: Mapping[str, Mapping[str, int]],
    *,
    ks: tuple[int, ...] = DEFAULT_KS,
) -> dict[str, Any]:
    ids = list(question_ids)
    uid_total = sum(len(target_uids.get(question_id, set())) for question_id in ids)
    result: dict[str, Any] = {
        "question_count": len(ids),
        "questions_with_source_uid": sum(bool(target_uids.get(question_id)) for question_id in ids),
        "target_uid_count": uid_total,
    }
    for k in ks:
        found_uids = 0
        any_count = 0
        all_count = 0
        no_any_count = 0
        for question_id in ids:
            targets = target_uids.get(question_id, set())
            found = {uid for uid in targets if index.get(question_id, {}).get(uid, k + 1) <= k}
            found_uids += len(found)
            if found:
                any_count += 1
            else:
                no_any_count += 1
            if targets and found == targets:
                all_count += 1
        result[f"found_uid_count_at_{k}"] = found_uids
        result[f"questions_any_uid_at_{k}"] = any_count
        result[f"questions_all_uids_at_{k}"] = all_count
        result[f"questions_no_uid_at_{k}"] = no_any_count
    return result


def _question_rows(
    *,
    question_ids: Iterable[str],
    tiers: Mapping[str, str],
    target_uids: Mapping[str, set[str]],
    evidence_row_counts: Mapping[str, int],
    evidence_uid_row_counts: Mapping[str, int],
    baseline: Mapping[str, Mapping[str, int]],
    candidate: Mapping[str, Mapping[str, int]],
    ks: tuple[int, ...] = DEFAULT_KS,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for question_id in sorted(question_ids, key=lambda value: int(value)):
        targets = target_uids.get(question_id, set())
        base_uids = baseline.get(question_id, {})
        candidate_uids = candidate.get(question_id, {})
        base_any = {uid for uid in targets if uid in base_uids}
        candidate_any = {uid for uid in targets if uid in candidate_uids}
        row: dict[str, Any] = {
            "question_id": int(question_id),
            "prediction_tier": tiers[question_id],
            "evidence_row_count": int(evidence_row_counts.get(question_id, 0)),
            "evidence_rows_with_uid": int(evidence_uid_row_counts.get(question_id, 0)),
            "evidence_rows_without_uid": int(evidence_row_counts.get(question_id, 0) - evidence_uid_row_counts.get(question_id, 0)),
            "target_uid_count": len(targets),
            "baseline_found_uid_count_any_rank": len(base_any),
            "candidate_found_uid_count_any_rank": len(candidate_any),
            "candidate_gained_uid_count_any_rank": len(candidate_any - base_any),
            "candidate_lost_uid_count_any_rank": len(base_any - candidate_any),
            "baseline_all_uids_any_rank": bool(targets) and base_any == targets,
            "candidate_all_uids_any_rank": bool(targets) and candidate_any == targets,
            "candidate_contract": dict(CONTRACT),
        }
        for k in ks:
            base_found = {uid for uid in targets if base_uids.get(uid, k + 1) <= k}
            candidate_found = {uid for uid in targets if candidate_uids.get(uid, k + 1) <= k}
            row[f"baseline_found_uid_count_at_{k}"] = len(base_found)
            row[f"candidate_found_uid_count_at_{k}"] = len(candidate_found)
            row[f"candidate_gained_uid_count_at_{k}"] = len(candidate_found - base_found)
            row[f"baseline_all_uids_at_{k}"] = bool(targets) and base_found == targets
            row[f"candidate_all_uids_at_{k}"] = bool(targets) and candidate_found == targets
        if _contains_forbidden(row):
            raise ValueError("question audit row leaked a forbidden field")
        result.append(row)
    return result


def _family_metrics(
    *,
    question_ids: Iterable[str],
    tiers: Mapping[str, str],
    target_uids: Mapping[str, set[str]],
    baseline: Mapping[str, Mapping[str, int]],
    candidate: Mapping[str, Mapping[str, int]],
    ks: tuple[int, ...] = DEFAULT_KS,
) -> list[dict[str, Any]]:
    by_tier: defaultdict[str, list[str]] = defaultdict(list)
    for question_id in question_ids:
        by_tier[tiers[question_id]].append(question_id)
    result: list[dict[str, Any]] = []
    for tier in sorted(by_tier):
        base = _rank_metrics(by_tier[tier], target_uids, baseline, ks=ks)
        cand = _rank_metrics(by_tier[tier], target_uids, candidate, ks=ks)
        # ``_rank_metrics`` has no candidate prefix; retain explicit names in
        # the report to prevent accidentally treating a coverage delta as an
        # answer-accuracy delta.
        result.append(
            {
                "prediction_tier": tier,
                "control": base,
                "candidate": cand,
                "delta": {
                    key: cand[key] - base[key]
                    for key in cand
                    if isinstance(cand[key], int) and isinstance(base.get(key), int)
                },
                "contract": dict(CONTRACT),
            }
        )
    return result


def build_retrieval_source_closure_audit(
    *,
    submission_path: Path,
    evidence_dir: Path,
    baseline_artifact_dir: Path,
    candidate_artifact_dir: Path,
    output_dir: Path,
    expected_question_count: int = 1012,
) -> dict[str, Any]:
    """Build a hash-bound top-k coverage A/B artifact without source values."""
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite output directory: {output_dir}")
    baseline_index, baseline_info, baseline_routes = _candidate_rank_index(
        baseline_artifact_dir, expected_top_k=10
    )
    candidate_index, candidate_info, candidate_routes = _candidate_rank_index(
        candidate_artifact_dir, expected_top_k=20
    )
    shared_input_names = {"plans", "lexical_index", "assets"}
    for name in shared_input_names:
        if (
            name not in baseline_info["input_hashes"]
            or name not in candidate_info["input_hashes"]
            or baseline_info["input_hashes"].get(name) != candidate_info["input_hashes"].get(name)
        ):
            raise ValueError(f"control/candidate input mismatch for {name}")
    shared_routes = set(baseline_routes) & set(candidate_routes)
    prefix_preserved = sum(
        candidate_routes[route_key][: len(baseline_routes[route_key])] == baseline_routes[route_key]
        for route_key in shared_routes
    )
    tail_rank_counts = Counter(
        rank
        for route_rows in candidate_routes.values()
        for rank, _uid in route_rows
        if rank > baseline_info["top_k"]
    )
    candidate_info["tail_row_count_above_control_k"] = sum(tail_rank_counts.values())
    candidate_info["tail_rank_counts_above_control_k"] = dict(sorted(tail_rank_counts.items()))

    tiers = _submission_tiers(submission_path, expected_question_count=expected_question_count)
    target_uids, evidence_row_counts, evidence_uid_row_counts, evidence_digest, evidence_files = _evidence_bundle(
        evidence_dir,
        expected_question_count=expected_question_count,
    )
    question_ids = sorted(tiers, key=lambda value: int(value))
    baseline_metrics = _rank_metrics(question_ids, target_uids, baseline_index)
    candidate_metrics = _rank_metrics(question_ids, target_uids, candidate_index)
    aggregate_delta = {
        key: candidate_metrics[key] - baseline_metrics[key]
        for key in candidate_metrics
        if isinstance(candidate_metrics[key], int) and isinstance(baseline_metrics.get(key), int)
    }
    per_question = _question_rows(
        question_ids=question_ids,
        tiers=tiers,
        target_uids=target_uids,
        evidence_row_counts=evidence_row_counts,
        evidence_uid_row_counts=evidence_uid_row_counts,
        baseline=baseline_index,
        candidate=candidate_index,
    )
    family_results = _family_metrics(
        question_ids=question_ids,
        tiers=tiers,
        target_uids=target_uids,
        baseline=baseline_index,
        candidate=candidate_index,
    )
    summary: dict[str, Any] = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "population": {
            "expected_question_count": expected_question_count,
            "submission_question_count": len(tiers),
            "evidence_file_count": len(evidence_files),
            "questions_with_evidence_uid": sum(bool(target_uids.get(question_id)) for question_id in question_ids),
            "questions_without_evidence_uid": sum(not bool(target_uids.get(question_id)) for question_id in question_ids),
            "evidence_row_count": sum(evidence_row_counts.values()),
            "evidence_rows_with_uid": sum(evidence_uid_row_counts.values()),
            "evidence_rows_without_uid": sum(evidence_row_counts.values()) - sum(evidence_uid_row_counts.values()),
            "target_uid_count": sum(len(value) for value in target_uids.values()),
        },
        "control": {**baseline_info, "coverage": baseline_metrics},
        "candidate": {**candidate_info, "coverage": candidate_metrics},
        "coverage_delta_candidate_minus_control": aggregate_delta,
        "candidate_prefix_contract": {
            "control_route_count": len(baseline_routes),
            "candidate_route_count": len(candidate_routes),
            "shared_route_count": len(shared_routes),
            "control_prefix_preserved_count": prefix_preserved,
            "control_prefix_preserved_for_all_shared_routes": prefix_preserved == len(shared_routes),
            "control_only_route_count": len(set(baseline_routes) - set(candidate_routes)),
            "candidate_only_route_count": len(set(candidate_routes) - set(baseline_routes)),
        },
        "family_results": family_results,
        "answer_accuracy": "NOT_MEASURED",
        "execution_accuracy": "NOT_MEASURED",
        "scorer_or_gold": "NOT_AVAILABLE; evidence UIDs are candidate-derived diagnostic references",
        "decision": "INVESTIGATE_FURTHER",
        "authority_status": "CANDIDATE_ONLY",
        "source_contract": dict(CONTRACT),
    }
    if _contains_forbidden(summary):
        raise ValueError("retrieval closure summary leaked a forbidden field")

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.tmp-", dir=output_dir.parent))
    try:
        rows_path = temporary / "retrieval_source_closure_rows_v1.jsonl"
        summary_path = temporary / "retrieval_source_closure_summary_v1.json"
        _write_jsonl(rows_path, per_question)
        _write_json(summary_path, summary)
        inputs: dict[str, Any] = {
            "submission": {"path": str(submission_path), "sha256": sha256_file(submission_path)},
            "evidence_directory": {
                "path": str(evidence_dir),
                "file_count": len(evidence_files),
                "bundle_sha256": evidence_digest,
                "files": evidence_files,
            },
            "control_retrieval_manifest": {
                "path": str(baseline_artifact_dir / "manifest.json"),
                "sha256": baseline_info["artifact_manifest_sha256"],
            },
            "candidate_retrieval_manifest": {
                "path": str(candidate_artifact_dir / "manifest.json"),
                "sha256": candidate_info["artifact_manifest_sha256"],
            },
        }
        outputs = {
            rows_path.name: {"path": rows_path.name, "sha256": sha256_file(rows_path)},
            summary_path.name: {"path": summary_path.name, "sha256": sha256_file(summary_path)},
        }
        _write_json(
            temporary / "manifest.json",
            {
                "schema_version": 1,
                "protocol": PROTOCOL,
                "inputs": inputs,
                "outputs": outputs,
                "source_contract": dict(CONTRACT),
            },
        )
        temporary.rename(output_dir)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return summary


def validate_retrieval_source_closure_audit(artifact_dir: Path) -> dict[str, Any]:
    """Validate hashes and safety flags for a completed diagnostic artifact."""
    manifest = _read_json(artifact_dir / "manifest.json")
    if manifest.get("protocol") != PROTOCOL or manifest.get("source_contract") != CONTRACT:
        raise ValueError("unexpected retrieval source-closure audit contract")
    submission = (manifest.get("inputs") or {}).get("submission") or {}
    submission_path = Path(str(submission.get("path") or ""))
    if not submission_path.is_file() or sha256_file(submission_path) != submission.get("sha256"):
        raise ValueError("submission input hash mismatch")
    for name in ("control_retrieval_manifest", "candidate_retrieval_manifest"):
        descriptor = (manifest.get("inputs") or {}).get(name) or {}
        path = Path(str(descriptor.get("path") or ""))
        if not path.is_file() or sha256_file(path) != descriptor.get("sha256"):
            raise ValueError(f"retrieval manifest input hash mismatch: {name}")
    evidence_descriptor = (manifest.get("inputs") or {}).get("evidence_directory") or {}
    evidence_dir = Path(str(evidence_descriptor.get("path") or ""))
    evidence_files = evidence_descriptor.get("files") or []
    if not evidence_dir.is_dir() or len(evidence_files) != int(evidence_descriptor.get("file_count") or 0):
        raise ValueError("evidence directory input mismatch")
    evidence_digest = hashlib.sha256()
    for descriptor in evidence_files:
        path = evidence_dir / str(descriptor.get("name") or "")
        if not path.is_file() or sha256_file(path) != descriptor.get("sha256"):
            raise ValueError("evidence file input hash mismatch")
        evidence_digest.update(f"{descriptor['name']}:{descriptor['sha256']}\n".encode("utf-8"))
    if evidence_digest.hexdigest() != evidence_descriptor.get("bundle_sha256"):
        raise ValueError("evidence directory bundle hash mismatch")
    for descriptor in (manifest.get("outputs") or {}).values():
        path = artifact_dir / str(descriptor.get("path") or "")
        if not path.is_file() or sha256_file(path) != descriptor.get("sha256"):
            raise ValueError("retrieval source-closure output hash mismatch")
    summary = _read_json(artifact_dir / "retrieval_source_closure_summary_v1.json")
    rows = list(_read_jsonl(artifact_dir / "retrieval_source_closure_rows_v1.jsonl"))
    if _contains_forbidden(summary) or any(_contains_forbidden(row) for row in rows):
        raise ValueError("retrieval source-closure audit leaked a forbidden field")
    expected = int((summary.get("population") or {}).get("expected_question_count") or 0)
    if len(rows) != expected or len({row.get("question_id") for row in rows}) != expected:
        raise ValueError("retrieval source-closure question coverage mismatch")
    if summary.get("answer_accuracy") != "NOT_MEASURED" or summary.get("execution_accuracy") != "NOT_MEASURED":
        raise ValueError("retrieval source-closure audit must not report answer accuracy")
    return {
        "status": "PASS",
        "question_count": len(rows),
        "control_candidate_delta": summary.get("coverage_delta_candidate_minus_control") or {},
        "answer_accuracy": "NOT_MEASURED",
        "execution_accuracy": "NOT_MEASURED",
        "authority_status": summary.get("authority_status"),
    }
