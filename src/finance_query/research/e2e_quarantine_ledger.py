"""Freeze an all-question, value-blind disposition ledger for one E2E replay."""

from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
from typing import Any, Iterable, Mapping


PROTOCOL = "vifinqa_e2e_quarantine_ledger_v1"
CONTRACT = {
    "research_only": True,
    "evidence_eligible": False,
    "may_materialize_answer": False,
    "training_eligible": False,
    "submission_eligible": False,
    "promotion_allowed": False,
}
FORBIDDEN = frozenset({"answer", "answer_decimal", "raw_value", "raw_values", "cell_value", "pandas_query", "human_verified"})


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain an object")
    return value


def _rows(path: Path) -> list[dict[str, Any]]:
    values = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not all(isinstance(value, dict) for value in values):
        raise ValueError(f"{path} must contain JSON objects")
    return values


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for row in rows), encoding="utf-8")


def _contains_forbidden(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(str(key) in FORBIDDEN or _contains_forbidden(child) for key, child in value.items())
    return any(_contains_forbidden(child) for child in value) if isinstance(value, list) else False


def _require_manifest_output(manifest_path: Path, output_path: Path, name: str) -> None:
    manifest = _json(manifest_path)
    descriptor = (manifest.get("outputs") or {}).get(name) or {}
    if sha256_file(output_path) != descriptor.get("sha256"):
        raise ValueError(f"{name} does not match its manifest")


def _by_question(path: Path, *, field: str = "question_id", expected_question_count: int) -> dict[int, dict[str, Any]]:
    index = {int(row[field]): row for row in _rows(path)}
    if len(index) != expected_question_count or set(index) != set(range(1, expected_question_count + 1)):
        raise ValueError(f"{path} does not cover the full question population")
    return index


def _binding_summary(binding: Mapping[str, Any]) -> tuple[dict[str, int], list[str], int]:
    statuses: Counter[str] = Counter()
    reasons: set[str] = set()
    operand_count = 0
    for stage in binding.get("stages") or []:
        if not isinstance(stage, Mapping):
            continue
        for operand in stage.get("required_operands") or []:
            if not isinstance(operand, Mapping):
                continue
            operand_count += 1
            statuses[str(operand.get("binding_status") or "MISSING")] += 1
            reasons.update(str(value) for value in operand.get("reason_codes") or [] if str(value))
    return dict(sorted(statuses.items())), sorted(reasons), operand_count


def build_e2e_quarantine_ledger(
    *,
    triage_path: Path,
    bindings_path: Path,
    bindings_manifest_path: Path,
    execution_path: Path,
    execution_manifest_path: Path,
    certificates_path: Path,
    certificates_manifest_path: Path,
    output_dir: Path,
    expected_question_count: int = 1012,
) -> dict[str, Any]:
    """Classify every question as an exact replay quarantine or an explicit blocker."""
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite output directory: {output_dir}")
    _require_manifest_output(bindings_manifest_path, bindings_path, "bindings")
    _require_manifest_output(execution_manifest_path, execution_path, "execution")
    _require_manifest_output(certificates_manifest_path, certificates_path, "answer_certificates")
    triage = _by_question(triage_path, expected_question_count=expected_question_count)
    bindings = _by_question(bindings_path, expected_question_count=expected_question_count)
    execution = _by_question(execution_path, expected_question_count=expected_question_count)
    certificates = _by_question(certificates_path, expected_question_count=expected_question_count)

    rows: list[dict[str, Any]] = []
    for question_id in range(1, expected_question_count + 1):
        binding, replay, triage_row, certificate_row = (
            bindings[question_id],
            execution[question_id],
            triage[question_id],
            certificates[question_id],
        )
        binding_status_counts, binding_reasons, operand_count = _binding_summary(binding)
        execution_status = str(replay.get("execution_status") or "")
        certificate = certificate_row.get("answer_certificate") or {}
        if execution_status == "execution_replay_ready":
            disposition = "EXACT_V2_E2E_REPLAY_AWAITS_SEMANTIC_EVIDENCE"
            source_status = "EXACT_V2_CELL_REPLAYED"
            reason_codes = ["SEMANTIC_EVIDENCE_GATE_REMAINS_FAIL_CLOSED"]
        else:
            disposition = "SOURCE_OR_PLAN_QUARANTINE"
            source_status = (
                "EXACT_V2_CELL_BINDING_BLOCKED"
                if binding_status_counts
                else "NO_EXACT_V2_CELL_PACKET"
            )
            reason_codes = [str(triage_row.get("primary_blocker") or "UNCLASSIFIED_E2E_BLOCKER"), *binding_reasons]
        row = {
            "schema_version": 1,
            "protocol": PROTOCOL,
            "question_id": question_id,
            "disposition": disposition,
            "source_status": source_status,
            "execution_status": execution_status,
            "certificate_status": certificate.get("status"),
            "route_status": binding.get("route_status"),
            "binding_packet_status": binding.get("binding_packet_status"),
            "exact_v2_operand_count": operand_count,
            "exact_v2_operand_status_counts": binding_status_counts,
            "quarantine_reason_codes": sorted(set(reason_codes)),
            "source_contract": dict(CONTRACT),
        }
        if _contains_forbidden(row):
            raise ValueError("quarantine ledger leaked a value or reviewer field")
        rows.append(row)
    summary = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "question_count": expected_question_count,
        "disposition_counts": dict(sorted(Counter(row["disposition"] for row in rows).items())),
        "source_status_counts": dict(sorted(Counter(row["source_status"] for row in rows).items())),
        "source_contract": dict(CONTRACT),
    }
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.tmp-", dir=output_dir.parent))
    try:
        ledger_path = temporary / "e2e_quarantine_ledger_v1.jsonl"
        summary_path = temporary / "e2e_quarantine_ledger_summary_v1.json"
        _write_jsonl(ledger_path, rows)
        _write_json(summary_path, summary)
        inputs = {
            "triage": triage_path,
            "bindings": bindings_path,
            "bindings_manifest": bindings_manifest_path,
            "execution": execution_path,
            "execution_manifest": execution_manifest_path,
            "certificates": certificates_path,
            "certificates_manifest": certificates_manifest_path,
        }
        _write_json(
            temporary / "manifest.json",
            {
                "schema_version": 1,
                "protocol": PROTOCOL,
                "inputs": {name: {"path": str(path), "sha256": sha256_file(path)} for name, path in inputs.items()},
                "outputs": {
                    "ledger": {"path": ledger_path.name, "sha256": sha256_file(ledger_path)},
                    "summary": {"path": summary_path.name, "sha256": sha256_file(summary_path)},
                },
                "source_contract": dict(CONTRACT),
            },
        )
        temporary.rename(output_dir)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return summary


def validate_e2e_quarantine_ledger(artifact_dir: Path, *, expected_question_count: int = 1012) -> dict[str, Any]:
    manifest = _json(artifact_dir / "manifest.json")
    if manifest.get("protocol") != PROTOCOL or manifest.get("source_contract") != CONTRACT:
        raise ValueError("unexpected quarantine ledger protocol")
    for group, relative in (("inputs", False), ("outputs", True)):
        for descriptor in (manifest.get(group) or {}).values():
            path = artifact_dir / str(descriptor.get("path") or "") if relative else Path(str(descriptor.get("path") or ""))
            if not path.is_file() or sha256_file(path) != descriptor.get("sha256"):
                raise ValueError("quarantine ledger hash mismatch")
    rows = _rows(artifact_dir / "e2e_quarantine_ledger_v1.jsonl")
    if {int(row["question_id"]) for row in rows} != set(range(1, expected_question_count + 1)):
        raise ValueError("quarantine ledger coverage mismatch")
    if any(_contains_forbidden(row) or row.get("source_contract") != CONTRACT or not row.get("quarantine_reason_codes") for row in rows):
        raise ValueError("quarantine ledger lost its fail-closed boundary")
    summary = _json(artifact_dir / "e2e_quarantine_ledger_summary_v1.json")
    counts = dict(sorted(Counter(row["disposition"] for row in rows).items()))
    if summary.get("question_count") != expected_question_count or summary.get("disposition_counts") != counts:
        raise ValueError("quarantine ledger summary mismatch")
    return {"status": "PASS", "question_count": expected_question_count, "disposition_counts": counts, "answer_eligible": False, "submission_eligible": False}
