"""Union hash-bound candidate period packets without widening authorization.

The production period packet is the base.  A repair packet can replace a row
only when an independent, candidate-only recheck has materialized it.  This
module is deliberately a research producer: it never selects a value, emits
an answer, or changes an existing route overlay.
"""

from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
from typing import Any, Iterable, Mapping


PROTOCOL = "vifinqa_period_packet_union_v1"
CONTRACT = {
    "research_only": True,
    "machine_recheck_only": True,
    "candidate_only": True,
    "evidence_eligible": False,
    "may_materialize_answer": False,
    "may_select_value": False,
    "promotion_allowed": False,
    "submission_eligible": False,
    "training_eligible": False,
}


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _rows(path: Path) -> list[dict[str, Any]]:
    values = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not all(isinstance(value, dict) for value in values):
        raise ValueError(f"{path} must contain JSON objects")
    return values


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, values: Iterable[Mapping[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for value in values),
        encoding="utf-8",
    )


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _require_output(manifest_path: Path, artifact_path: Path, output_name: str) -> None:
    manifest = _json(manifest_path)
    expected = ((manifest.get("outputs") or {}).get(output_name) or {}).get("sha256")
    if not isinstance(expected, str) or sha256_file(artifact_path) != expected:
        raise ValueError(f"SHA-256 mismatch for {output_name}")


def _candidate_contract(row: Mapping[str, Any]) -> bool:
    contract = row.get("source_contract") or {}
    return bool(
        contract.get("candidate_only") is True
        and contract.get("evidence_eligible") is False
        and (
            contract.get("may_select_value") is False
            or contract.get("may_select_final_column") is False
        )
        and contract.get("submission_eligible") is False
    )


def build_period_packet_union(
    *,
    base_period_packets: Path,
    base_period_manifest: Path,
    repair_period_packets: Path,
    repair_period_manifest: Path,
    repair_audit: Path,
    route_overlay: Path,
    route_overlay_manifest: Path,
    output_dir: Path,
    expected_question_count: int = 1012,
) -> dict[str, Any]:
    """Replace only independently materialized repair rows in a base packet."""

    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite output directory: {output_dir}")
    base_manifest = _json(base_period_manifest)
    _require_output(base_period_manifest, base_period_packets, "period_packets")
    _require_output(repair_period_manifest, repair_period_packets, "period_packets")
    _require_output(route_overlay_manifest, route_overlay, "overlay")
    base_rows = _rows(base_period_packets)
    repair_rows = _rows(repair_period_packets)
    base = {int(row["question_id"]): row for row in base_rows}
    repair = {int(row["question_id"]): row for row in repair_rows}
    expected_ids = set(range(1, expected_question_count + 1))
    if set(base) != expected_ids or set(repair) != expected_ids:
        raise ValueError("period packet union requires complete question coverage")
    audit_rows = _rows(repair_audit)
    materialized = {
        int(row["question_id"])
        for row in audit_rows
        if row.get("period_recheck_status") == "MATERIALIZED_SOURCE_TITLE_PERIOD_CANDIDATE"
        or row.get("unit_recheck_status") == "MATERIALIZED_UNIT_TITLE_RECHECK"
    }
    if not materialized:
        raise ValueError("repair audit contains no materialized candidate")
    if set(materialized) - expected_ids:
        raise ValueError("repair audit contains an unknown question")
    route_rows = _rows(route_overlay)
    routes = {int(row["question_id"]): row for row in route_rows}
    if set(routes) != expected_ids:
        raise ValueError("route overlay coverage mismatch")
    for question_id in materialized:
        row = repair[question_id]
        if row.get("packet_status") != "unique_period_column_candidate":
            raise ValueError("materialized repair is not a unique period candidate")
        if not _candidate_contract(row):
            raise ValueError("repair packet is not candidate-only")
        if routes[question_id].get("route_status") != "route_complete":
            raise ValueError("period repair cannot silently complete an incomplete route")
    output_rows = [repair[q] if q in materialized else base[q] for q in sorted(expected_ids)]
    if any(
        q not in materialized and _canonical(output_rows[q - 1]) != _canonical(base[q])
        for q in expected_ids
    ):
        raise ValueError("non-materialized period packet changed")
    summary = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "question_count": expected_question_count,
        "replaced_question_count": len(materialized),
        "replaced_question_ids": sorted(materialized),
        "base_packet_status_counts": dict(sorted(Counter(str(row.get("packet_status") or "") for row in base_rows).items())),
        "output_packet_status_counts": dict(sorted(Counter(str(row.get("packet_status") or "") for row in output_rows).items())),
        "source_contract": dict(CONTRACT),
    }
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.tmp-", dir=output_dir.parent))
    try:
        packet_path = temporary / "period_column_candidate_packets_v1.jsonl"
        summary_path = temporary / "period_packet_union_summary_v1.json"
        _write_jsonl(packet_path, output_rows)
        _write_json(summary_path, summary)
        inputs = {
            "base_period_packets": base_period_packets,
            "base_period_manifest": base_period_manifest,
            "repair_period_packets": repair_period_packets,
            "repair_period_manifest": repair_period_manifest,
            "repair_audit": repair_audit,
            "route_overlay": route_overlay,
            "route_overlay_manifest": route_overlay_manifest,
        }
        # Keep the base producer's source descriptors (notably
        # ``structured_tables_v2`` and ``evidence_context_v3``) so downstream
        # exact-binding code can verify the same immutable lineage.  The union
        # descriptors are added under distinct names and never overwrite them.
        inherited_inputs = dict(base_manifest.get("inputs") or {})
        union_inputs = {
            name: {"path": str(path), "sha256": sha256_file(path)} for name, path in inputs.items()
        }
        manifest = {
            "schema_version": 1,
            "protocol": PROTOCOL,
            "inputs": {**inherited_inputs, **union_inputs},
            "outputs": {
                "period_packets": {"path": packet_path.name, "sha256": sha256_file(packet_path)},
                "summary": {"path": summary_path.name, "sha256": sha256_file(summary_path)},
            },
            "replaced_question_ids": sorted(materialized),
            "source_contract": dict(CONTRACT),
        }
        _write_json(temporary / "period_column_candidate_packets_v1.manifest.json", manifest)
        temporary.rename(output_dir)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return summary


def validate_period_packet_union(artifact_dir: Path, *, expected_question_count: int = 1012) -> dict[str, Any]:
    manifest = _json(artifact_dir / "period_column_candidate_packets_v1.manifest.json")
    if manifest.get("protocol") != PROTOCOL or manifest.get("source_contract") != CONTRACT:
        raise ValueError("unexpected period packet union protocol")
    for descriptor in (manifest.get("inputs") or {}).values():
        path = Path(str(descriptor.get("path") or ""))
        if not path.is_file() or sha256_file(path) != descriptor.get("sha256"):
            raise ValueError("period packet union input hash mismatch")
    for descriptor in (manifest.get("outputs") or {}).values():
        path = artifact_dir / str(descriptor.get("path") or "")
        if not path.is_file() or sha256_file(path) != descriptor.get("sha256"):
            raise ValueError("period packet union output hash mismatch")
    output = {int(row["question_id"]): row for row in _rows(artifact_dir / "period_column_candidate_packets_v1.jsonl")}
    if set(output) != set(range(1, expected_question_count + 1)):
        raise ValueError("period packet union coverage mismatch")
    replaced = {int(value) for value in manifest.get("replaced_question_ids") or []}
    repair = {int(row["question_id"]): row for row in _rows(Path(str((manifest.get("inputs") or {}).get("repair_period_packets", {}).get("path") or "")))}
    base = {int(row["question_id"]): row for row in _rows(Path(str((manifest.get("inputs") or {}).get("base_period_packets", {}).get("path") or "")))}
    for question_id in output:
        expected = repair[question_id] if question_id in replaced else base[question_id]
        if _canonical(output[question_id]) != _canonical(expected):
            raise ValueError("period packet union output diverges from declared inputs")
    summary = _json(artifact_dir / "period_packet_union_summary_v1.json")
    if summary.get("replaced_question_count") != len(replaced) or summary.get("question_count") != expected_question_count:
        raise ValueError("period packet union summary mismatch")
    return {
        "status": "PASS",
        "question_count": expected_question_count,
        "replaced_question_count": len(replaced),
        "replaced_question_ids": sorted(replaced),
        "answer_eligible": False,
        "submission_eligible": False,
    }
