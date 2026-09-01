"""Value-blind retrieval probes for checking whether navigation finds known tables.

The probe is deliberately separate from answer generation and exact-cell binding.
It can report that a direct target table was missed, but it cannot select a row,
read a numeric cell, or authorize evidence, answers, training, or submission.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
import json
from pathlib import Path
import shutil
import tempfile
from typing import Any

from .table_retrieval import sha256_file


PROTOCOL = "vifinqa_navigation_retrieval_probe_v1"
NAVIGATION_CONTRACT = {
    "navigation_metadata_only": True,
    "may_authorize_evidence": False,
    "may_authorize_answer": False,
    "training_eligible": False,
    "submission_eligible": False,
}
_ALLOWED_PROBE_KEYS = frozenset(
    {"probe_id", "query", "ticker", "report_year", "scope", "expected_table_uids"}
)
_ALLOWED_RESULT_KEYS = frozenset(
    {
        "schema_version",
        "protocol",
        "probe_id",
        "route",
        "expected_table_uids",
        "retrieved_table_uids",
        "matched_table_uids",
        "missing_expected_table_uids",
        "first_match_rank",
        "top_k",
        "retrieval_status",
        "source_contract",
    }
)

Search = Callable[[str, str, int, str | None, int], list[Mapping[str, Any]]]


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid probe JSONL at line {line_number}") from exc
        if not isinstance(row, dict):
            raise ValueError(f"probe line {line_number} is not an object")
        rows.append(row)
    return rows


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _integer(value: object, *, label: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be an integer")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be an integer") from exc


def load_retrieval_probes(path: Path) -> list[dict[str, Any]]:
    """Load a small, independently adjudicated target-table probe set.

    Probes purposely carry only query routing metadata and expected table UIDs.
    They cannot contain raw cells, source text, answer labels, or reviewer state.
    """
    probes: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for line_number, raw in enumerate(_read_jsonl(path), start=1):
        unexpected = set(raw) - _ALLOWED_PROBE_KEYS
        if unexpected:
            raise ValueError(f"probe line {line_number} has unsupported keys: {sorted(unexpected)}")
        probe_id = str(raw.get("probe_id") or "").strip()
        query = str(raw.get("query") or "").strip()
        ticker = str(raw.get("ticker") or "").strip().upper()
        report_year = _integer(raw.get("report_year"), label=f"probe {probe_id or line_number} report_year")
        scope_value = raw.get("scope")
        scope = None if scope_value is None else str(scope_value).strip()
        expected = raw.get("expected_table_uids")
        if not probe_id or probe_id in seen_ids:
            raise ValueError(f"probe line {line_number} has a missing or duplicate probe_id")
        if not query or not ticker:
            raise ValueError(f"probe {probe_id} requires a non-empty query and ticker")
        if scope is not None and scope not in {"consolidated", "separate", "aggregated", "unknown"}:
            raise ValueError(f"probe {probe_id} has unsupported scope: {scope!r}")
        if not isinstance(expected, list) or not expected:
            raise ValueError(f"probe {probe_id} requires one or more expected_table_uids")
        expected_uids = [str(value).strip() for value in expected]
        if not all(expected_uids) or len(set(expected_uids)) != len(expected_uids):
            raise ValueError(f"probe {probe_id} has invalid expected_table_uids")
        seen_ids.add(probe_id)
        probes.append(
            {
                "probe_id": probe_id,
                "query": query,
                "ticker": ticker,
                "report_year": report_year,
                "scope": scope,
                "expected_table_uids": expected_uids,
            }
        )
    if not probes:
        raise ValueError("probe file is empty")
    return probes


def evaluate_retrieval_probes(
    probes: Iterable[Mapping[str, Any]],
    *,
    search: Search,
    top_k: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Evaluate navigation recall without copying source text, scores, or values."""
    if top_k < 1 or top_k > 100:
        raise ValueError("top_k must be between 1 and 100")
    rows: list[dict[str, Any]] = []
    target_uid_count = 0
    target_uid_hit_count = 0
    for probe in probes:
        expected = [str(value) for value in probe["expected_table_uids"]]
        candidates = search(
            str(probe["query"]),
            str(probe["ticker"]),
            int(probe["report_year"]),
            probe.get("scope") if isinstance(probe.get("scope"), str) else None,
            top_k,
        )
        retrieved = [str(candidate.get("internal_table_uid") or "") for candidate in candidates]
        if not all(retrieved) or len(set(retrieved)) != len(retrieved):
            raise ValueError(f"probe {probe['probe_id']} search returned missing or duplicate table UIDs")
        matched = [uid for uid in retrieved if uid in set(expected)]
        missing = [uid for uid in expected if uid not in set(retrieved)]
        first_match_rank = next((rank for rank, uid in enumerate(retrieved, start=1) if uid in set(expected)), None)
        target_uid_count += len(expected)
        target_uid_hit_count += len(expected) - len(missing)
        rows.append(
            {
                "schema_version": 1,
                "protocol": PROTOCOL,
                "probe_id": str(probe["probe_id"]),
                "route": {
                    "ticker": str(probe["ticker"]),
                    "report_year": int(probe["report_year"]),
                    "scope": probe.get("scope"),
                },
                "expected_table_uids": expected,
                "retrieved_table_uids": retrieved,
                "matched_table_uids": matched,
                "missing_expected_table_uids": missing,
                "first_match_rank": first_match_rank,
                "top_k": top_k,
                "retrieval_status": "DIRECT_TARGET_RETRIEVED" if matched else "DIRECT_TARGET_MISSED",
                "source_contract": dict(NAVIGATION_CONTRACT),
            }
        )
    direct_hit_count = sum(row["retrieval_status"] == "DIRECT_TARGET_RETRIEVED" for row in rows)
    return rows, {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "probe_count": len(rows),
        "top_k": top_k,
        "direct_target_hit_count": direct_hit_count,
        "direct_target_miss_count": len(rows) - direct_hit_count,
        "direct_target_hit_rate": direct_hit_count / len(rows),
        "target_table_uid_count": target_uid_count,
        "target_table_uid_hit_count": target_uid_hit_count,
        "target_table_uid_recall": target_uid_hit_count / target_uid_count,
        "semantic_accuracy": "NOT_MEASURED",
        "source_contract": dict(NAVIGATION_CONTRACT),
    }


def build_retrieval_probe_artifact(
    *,
    probe_path: Path,
    output_dir: Path,
    retrieval_kind: str,
    top_k: int,
    search: Search,
) -> dict[str, Any]:
    """Write a hash-bound, navigation-only retrieval probe artifact."""
    if retrieval_kind not in {"lexical", "dense"}:
        raise ValueError("retrieval_kind must be lexical or dense")
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite retrieval probe output: {output_dir}")
    probes = load_retrieval_probes(probe_path)
    rows, summary = evaluate_retrieval_probes(probes, search=search, top_k=top_k)
    summary = {**summary, "retrieval_kind": retrieval_kind}
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.tmp-", dir=output_dir.parent))
    try:
        rows_path = temporary / "navigation_retrieval_probe_results_v1.jsonl"
        summary_path = temporary / "navigation_retrieval_probe_summary_v1.json"
        _write_jsonl(rows_path, rows)
        _write_json(summary_path, summary)
        manifest = {
            "schema_version": 1,
            "protocol": PROTOCOL,
            "inputs": {"probes": {"path": str(probe_path.resolve()), "sha256": sha256_file(probe_path)}},
            "outputs": {
                rows_path.name: {"sha256": sha256_file(rows_path)},
                summary_path.name: {"sha256": sha256_file(summary_path)},
            },
            "retrieval_kind": retrieval_kind,
            "source_contract": dict(NAVIGATION_CONTRACT),
        }
        _write_json(temporary / "manifest.json", manifest)
        temporary.replace(output_dir)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return summary


def validate_retrieval_probe_artifact(artifact_dir: Path) -> dict[str, Any]:
    """Validate the value-blind contract, hashes, and summary arithmetic."""
    manifest = _read_json(artifact_dir / "manifest.json")
    if manifest.get("protocol") != PROTOCOL or manifest.get("source_contract") != NAVIGATION_CONTRACT:
        raise ValueError("retrieval probe manifest has an invalid contract")
    retrieval_kind = manifest.get("retrieval_kind")
    if retrieval_kind not in {"lexical", "dense"}:
        raise ValueError("retrieval probe manifest has an invalid retrieval kind")
    outputs = manifest.get("outputs") or {}
    expected_outputs = {
        "navigation_retrieval_probe_results_v1.jsonl",
        "navigation_retrieval_probe_summary_v1.json",
    }
    if set(outputs) != expected_outputs:
        raise ValueError("retrieval probe manifest has unexpected outputs")
    for name, descriptor in outputs.items():
        path = artifact_dir / name
        if not path.is_file() or sha256_file(path) != str((descriptor or {}).get("sha256") or ""):
            raise ValueError(f"retrieval probe output hash mismatch: {name}")
    rows = _read_jsonl(artifact_dir / "navigation_retrieval_probe_results_v1.jsonl")
    summary = _read_json(artifact_dir / "navigation_retrieval_probe_summary_v1.json")
    if not rows or summary.get("protocol") != PROTOCOL or summary.get("source_contract") != NAVIGATION_CONTRACT:
        raise ValueError("retrieval probe output has an invalid contract")
    if summary.get("retrieval_kind") != retrieval_kind:
        raise ValueError("retrieval probe summary does not match its retrieval kind")
    if summary.get("semantic_accuracy") != "NOT_MEASURED":
        raise ValueError("retrieval probe must not claim semantic accuracy")
    top_k = _integer(summary.get("top_k"), label="retrieval probe top_k")
    if top_k < 1 or top_k > 100:
        raise ValueError("retrieval probe top_k is outside the supported range")
    target_count = target_hits = direct_hits = 0
    for row in rows:
        if set(row) != _ALLOWED_RESULT_KEYS or row.get("protocol") != PROTOCOL:
            raise ValueError("retrieval probe row has unsupported fields")
        if row.get("source_contract") != NAVIGATION_CONTRACT:
            raise ValueError("retrieval probe row is not navigation-only")
        route = row.get("route")
        if not isinstance(route, dict) or set(route) != {"ticker", "report_year", "scope"}:
            raise ValueError("retrieval probe row has an invalid route")
        if not str(route.get("ticker") or "").strip():
            raise ValueError("retrieval probe row has a missing ticker")
        _integer(route.get("report_year"), label="retrieval probe report_year")
        if route.get("scope") is not None and route.get("scope") not in {
            "consolidated",
            "separate",
            "aggregated",
            "unknown",
        }:
            raise ValueError("retrieval probe row has an unsupported scope")
        if not str(row.get("probe_id") or "").strip() or _integer(row.get("top_k"), label="row top_k") != top_k:
            raise ValueError("retrieval probe row has inconsistent identity or top_k")
        expected = [str(value) for value in row.get("expected_table_uids") or []]
        retrieved = [str(value) for value in row.get("retrieved_table_uids") or []]
        matched = [str(value) for value in row.get("matched_table_uids") or []]
        missing = [str(value) for value in row.get("missing_expected_table_uids") or []]
        if not expected or len(set(expected)) != len(expected) or len(set(retrieved)) != len(retrieved):
            raise ValueError("retrieval probe row has invalid UID lists")
        expected_matches = [uid for uid in retrieved if uid in set(expected)]
        expected_missing = [uid for uid in expected if uid not in set(retrieved)]
        first_rank = next((rank for rank, uid in enumerate(retrieved, start=1) if uid in set(expected)), None)
        status = "DIRECT_TARGET_RETRIEVED" if expected_matches else "DIRECT_TARGET_MISSED"
        if matched != expected_matches or missing != expected_missing or row.get("first_match_rank") != first_rank:
            raise ValueError("retrieval probe row has inconsistent target coverage")
        if row.get("retrieval_status") != status:
            raise ValueError("retrieval probe row has inconsistent status")
        target_count += len(expected)
        target_hits += len(expected_matches)
        direct_hits += int(bool(expected_matches))
    expected_summary = {
        "probe_count": len(rows),
        "direct_target_hit_count": direct_hits,
        "direct_target_miss_count": len(rows) - direct_hits,
        "target_table_uid_count": target_count,
        "target_table_uid_hit_count": target_hits,
    }
    for key, value in expected_summary.items():
        if summary.get(key) != value:
            raise ValueError(f"retrieval probe summary mismatch: {key}")
    if summary.get("direct_target_hit_rate") != direct_hits / len(rows):
        raise ValueError("retrieval probe direct-target hit rate is inconsistent")
    if summary.get("target_table_uid_recall") != target_hits / target_count:
        raise ValueError("retrieval probe target-table recall is inconsistent")
    return {"status": "VALID", **expected_summary}


__all__ = [
    "NAVIGATION_CONTRACT",
    "PROTOCOL",
    "build_retrieval_probe_artifact",
    "evaluate_retrieval_probes",
    "load_retrieval_probes",
    "validate_retrieval_probe_artifact",
]
