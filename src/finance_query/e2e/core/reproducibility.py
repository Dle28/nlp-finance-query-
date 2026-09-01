"""Reproducibility and model/RAG packaging contracts.

This module is intentionally small and dependency-free so it can be copied
into a Kaggle runtime together with the normal ``finance_query`` package.
It describes what a run actually had available, what it loaded, and what it
executed.  None of those facts grant promotion or answer authority.

The distinction is important for A/B runs: a checkpoint can load correctly
and still be rejected by an independent evaluation gate.  Likewise, a dense
index can be attached to a kernel without being queried when its input pool
is empty.  The manifest records those cases separately instead of inferring
promotion from a successful import.
"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
from pathlib import Path
from typing import Any


RUN_MANIFEST_SCHEMA = "vifinqa_model_rag_run_manifest_v1"
FULL_POPULATION_COUNT = 1012

_NOT_REQUESTED = "NOT_REQUESTED"

# These are deliberately limited to reproducibility-relevant controls.  Input
# paths and hashes are recorded separately so the route snapshot stays useful
# as a diffable policy object.
ROUTE_FLAG_NAMES = (
    "structured_table_filter",
    "selective_source_first_route_hydration",
    "require_source_line_map",
    "allow_local_ordinal_fallback",
    "candidate_top_k",
    "disable_candidate_validity",
    "reranker_policy",
    "model_device",
    "model_candidate_limit",
    "model_batch_size",
    "dense_candidate_limit",
    "dense_device",
    "dense_batch_size",
    "source_first_report_year_neighbor_offset",
    "disable_source_first_report_year_neighbor",
    "period_neighbor_offset",
    "period_neighbor_table_slots",
    "period_neighbor_navigation_only",
    "direct_replay_include_provisional",
    "disable_research_fusion",
    "disable_source_first_temporal",
    "disable_source_first_candidate_bound",
    "disable_source_first_period_extreme",
    "disable_source_first_reclassified_direct",
    "disable_source_first_financial_liability_total",
    "disable_source_first_financial_receivables_total",
    "disable_source_first_multi_entity_threshold",
    "disable_source_first_multi_entity_share_threshold",
    "disable_source_first_multi_entity_selector",
    "disable_source_first_multi_entity_ratio_selector",
    "disable_source_first_composed_total",
    "disable_source_first_conditional_temporal",
    "disable_source_first_cross_entity",
    "disable_source_first_multi_entity_direct_aggregation",
    "disable_source_first_multi_entity_conditional_count",
    "expected_question_count",
    "require_full_population",
)


def sha256_file(path: str | Path) -> str:
    """Return the SHA-256 digest of one file."""

    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tree_entries(path: Path) -> tuple[list[dict[str, Any]], int]:
    entries: list[dict[str, Any]] = []
    total_bytes = 0
    for child in sorted(path.rglob("*")):
        if not child.is_file():
            continue
        relative = child.relative_to(path).as_posix()
        size = child.stat().st_size
        entries.append({"path": relative, "sha256": sha256_file(child), "size_bytes": size})
        total_bytes += size
    return entries, total_bytes


def fingerprint_path(path: str | Path | None, *, requested: bool = False) -> dict[str, Any]:
    """Fingerprint a file/directory without pretending missing inputs exist.

    Directory digests are based on sorted relative file names, file digests,
    and sizes.  This is slower than hashing a directory name, but it makes a
    model/index bundle reproducible across mount points such as ``/kaggle``.
    """

    if path is None:
        return {
            "path": None,
            "exists": False,
            "kind": "none",
            "requested": bool(requested),
            "status": _NOT_REQUESTED if not requested else "MISSING",
            "sha256": None,
            "size_bytes": 0,
            "file_count": 0,
        }
    resolved = Path(path).expanduser().resolve()
    if not resolved.exists():
        return {
            "path": str(resolved),
            "exists": False,
            "kind": "missing",
            "requested": bool(requested),
            "status": "MISSING" if requested else "AVAILABLE_NOT_REQUESTED",
            "sha256": None,
            "size_bytes": 0,
            "file_count": 0,
        }
    if resolved.is_file():
        return {
            "path": str(resolved),
            "exists": True,
            "kind": "file",
            "requested": bool(requested),
            "status": "AVAILABLE",
            "sha256": sha256_file(resolved),
            "size_bytes": resolved.stat().st_size,
            "file_count": 1,
        }
    if resolved.is_dir():
        entries, total_bytes = _tree_entries(resolved)
        canonical = json.dumps(entries, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return {
            "path": str(resolved),
            "exists": True,
            "kind": "directory",
            "requested": bool(requested),
            "status": "AVAILABLE",
            "sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            "size_bytes": total_bytes,
            "file_count": len(entries),
            "files": entries,
        }
    return {
        "path": str(resolved),
        "exists": True,
        "kind": "other",
        "requested": bool(requested),
        "status": "UNSUPPORTED",
        "sha256": None,
        "size_bytes": 0,
        "file_count": 0,
    }


def _safe_json_value(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _safe_json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_json_value(item) for item in value]
    return value


def route_flag_snapshot(args: Mapping[str, Any] | Any) -> dict[str, Any]:
    """Return a stable snapshot of route/model switches from argparse args."""

    values = vars(args) if not isinstance(args, Mapping) else args
    snapshot: dict[str, Any] = {}
    for name in ROUTE_FLAG_NAMES:
        if name in values:
            snapshot[name] = _safe_json_value(values[name])
    # Preserve newly introduced disable switches without requiring a schema
    # edit for every route, while excluding input/output paths from this map.
    for name, value in values.items():
        if name.startswith("disable_") and name not in snapshot:
            snapshot[name] = _safe_json_value(value)
    return dict(sorted(snapshot.items()))


def stage_status(
    name: str,
    *,
    requested: bool,
    available: bool,
    loaded: bool,
    ran: bool,
    promotion_allowed: bool = False,
    candidate_only: bool = True,
    artifact: Mapping[str, Any] | None = None,
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a stage record with independent load/run/promotion facts."""

    if not requested:
        status = _NOT_REQUESTED
    elif not available:
        status = "REQUESTED_MISSING"
    elif ran and loaded:
        status = "LOADED_AND_RAN"
    elif loaded:
        status = "LOADED_NOT_RUN"
    elif ran:
        status = "RAN_WITHOUT_MODEL"
    else:
        status = "AVAILABLE_NOT_LOADED"
    record: dict[str, Any] = {
        "name": name,
        "requested": bool(requested),
        "available": bool(available),
        "loaded": bool(loaded),
        "ran": bool(ran),
        "status": status,
        "candidate_only": bool(candidate_only),
        "may_authorize_answer": False,
        # This flag is intentionally supplied by an independent gate.  A
        # successful load or model invocation never changes it to true.
        "promotion_allowed": bool(promotion_allowed),
        "promotion_basis": (
            "independent_promotion_gate"
            if promotion_allowed
            else "not_granted_by_load_or_run"
        ),
    }
    if artifact is not None:
        record["artifact"] = dict(artifact)
    if details:
        record["details"] = _safe_json_value(details)
    return record


def submission_gate(
    validation: Mapping[str, Any],
    *,
    expected_question_count: int | None = None,
    require_full_population: bool = False,
) -> dict[str, Any]:
    """Evaluate the local output gate without confusing it with promotion.

    ``expected_question_count`` is optional for small unit/diagnostic runs.
    Kaggle/preparation runs pass ``1012`` and ``require_full_population`` so a
    partial output cannot be labelled a successful full submission.
    """

    expected = expected_question_count
    if require_full_population and expected is None:
        expected = FULL_POPULATION_COUNT
    if expected is not None and expected < 1:
        raise ValueError("expected_question_count must be positive")
    records = validation.get("records")
    replayed = validation.get("queries_replayed")
    errors = list(validation.get("errors") or [])
    count_ok = (
        expected is None
        or (records == expected and replayed == expected)
    )
    errors_ok = not errors
    valid_flag = validation.get("valid") is True
    passed = bool(valid_flag and count_ok and errors_ok)
    return {
        "required": expected is not None,
        "expected_question_count": expected,
        "records": records,
        "queries_replayed": replayed,
        "errors": errors,
        "checks": {
            "validation_valid": valid_flag,
            "record_count": count_ok,
            "replay_count": count_ok,
            "errors_empty": errors_ok,
        },
        "passed": passed,
        "status": "PASS" if passed else "FAIL",
        "promotion_allowed": False,
        "promotion_note": "delivery gate only; does not promote a model",
    }


def validate_dense_index_artifact(path: str | Path) -> dict[str, Any]:
    """Validate the completed dense bundle before the builder consumes it.

    The check intentionally avoids loading NumPy arrays; the dense searcher
    performs shape validation when it opens the index.  This gate catches the
    common packaging failure where only ``dense_embeddings_v1.npy`` was copied
    and the metadata/receipt pair was omitted.
    """

    root = Path(path).expanduser().resolve()
    required = (
        "dense_embeddings_v1.npy",
        "dense_metadata_v1.jsonl",
        "dense_uids_v1.jsonl",
        "dense_build_receipt_v1.json",
        "manifest.json",
    )
    issues: list[str] = []
    if not root.is_dir():
        issues.append("index_directory_missing")
        return {
            "path": str(root),
            "valid": False,
            "required_files": list(required),
            "missing_files": list(required),
            "issues": issues,
        }
    missing = [name for name in required if not (root / name).is_file()]
    issues.extend(f"missing:{name}" for name in missing)
    receipt: dict[str, Any] = {}
    if not missing:
        try:
            raw_receipt = json.loads(
                (root / "dense_build_receipt_v1.json").read_text(encoding="utf-8")
            )
            if not isinstance(raw_receipt, dict):
                issues.append("receipt_not_object")
            else:
                receipt = raw_receipt
        except (OSError, json.JSONDecodeError) as exc:
            issues.append(f"receipt_invalid:{type(exc).__name__}")
    if receipt.get("navigation_metadata_only") is not True:
        issues.append("receipt_not_navigation_only")
    if receipt.get("may_authorize_answer") is not False:
        issues.append("receipt_answer_authority_not_false")
    if receipt.get("submission_eligible") is not False:
        issues.append("receipt_submission_eligible_not_false")
    manifest: dict[str, Any] = {}
    if not missing:
        try:
            raw_manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
            if isinstance(raw_manifest, dict):
                manifest = raw_manifest
            else:
                issues.append("manifest_not_object")
        except (OSError, json.JSONDecodeError) as exc:
            issues.append(f"manifest_invalid:{type(exc).__name__}")
    return {
        "path": str(root),
        "valid": not issues,
        "required_files": list(required),
        "missing_files": missing,
        "issues": issues,
        "receipt": receipt,
        "manifest_protocol": manifest.get("protocol"),
        "model": receipt.get("model"),
        "count": receipt.get("count"),
        "dimension": receipt.get("dimension"),
        "navigation_metadata_only": receipt.get("navigation_metadata_only"),
    }


def write_json(path: str | Path, value: Mapping[str, Any]) -> None:
    """Write deterministic, human-readable JSON manifest output."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
