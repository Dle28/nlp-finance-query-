"""Final submission compiler for the canonical flow.

This module is deliberately boring: it does not retrieve, propose, resolve,
or verify.  It joins the delivery rows from the compatibility builder with
the already-produced ``ResolvedPrediction`` and ``E2EReceipt`` ledgers,
records the selected release policy, and writes the competition JSON/CSV/ZIP.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
from typing import Any
import zipfile


SUBMISSION_LEDGER_PROTOCOL = "vifinqa_submission_ledger_v1"
SUBMISSION_COMPILE_PROTOCOL = "vifinqa_submission_compile_manifest_v1"


@dataclass(frozen=True, slots=True)
class SubmissionCompileResult:
    """Files emitted by the final delivery compiler."""

    output_dir: Path
    submission_path: Path
    archive_path: Path
    ledger_path: Path
    report_path: Path
    question_count: int


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _rows(path: Path) -> list[dict[str, Any]]:
    values = json.loads(path.read_text(encoding="utf-8")) if path.suffix == ".json" else [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not isinstance(values, list) or any(not isinstance(value, dict) for value in values):
        raise ValueError(f"{path} must contain a list of objects")
    return values


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _index(rows: Iterable[Mapping[str, Any]], *, key: str, label: str) -> dict[int, dict[str, Any]]:
    indexed: dict[int, dict[str, Any]] = {}
    for row in rows:
        try:
            identifier = int(row[key])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(f"{label} has an invalid question id") from error
        if identifier in indexed:
            raise ValueError(f"{label} has duplicate question id {identifier}")
        indexed[identifier] = dict(row)
    return indexed


def _copy_tree(source: Path, target: Path) -> None:
    if not source.is_dir():
        raise FileNotFoundError(source)
    shutil.copytree(source, target)


def _delivery_row(
    *,
    submission: Mapping[str, Any],
    resolved: Mapping[str, Any] | None,
    e2e: Mapping[str, Any] | None,
    policy: str,
) -> dict[str, Any]:
    e2e_status = str((e2e or {}).get("e2e_status") or "NOT_RUN")
    resolution_status = str((resolved or {}).get("resolution_status") or "NOT_RUN")
    strict_ready = resolution_status == "RESOLVED_CANDIDATE" and e2e_status == "VERIFIED"
    delivery_status = "STRICT_READY" if strict_ready else "BEST_EFFORT_CANDIDATE"
    if policy == "strict" and not strict_ready:
        delivery_status = "BLOCKED_STRICT_POLICY"
    payload = {
        "schema_version": 1,
        "protocol": SUBMISSION_LEDGER_PROTOCOL,
        "question_id": int(submission["id"]),
        "answer": submission.get("answer"),
        "prediction_tier": submission.get("prediction_tier"),
        "resolution_status": resolution_status,
        "e2e_status": e2e_status,
        "delivery_status": delivery_status,
        "resolved_prediction_id": (resolved or {}).get("resolved_prediction_id"),
        "e2e_receipt_id": (e2e or {}).get("e2e_receipt_id"),
        "answer_authorized": e2e_status == "VERIFIED",
        "submission_eligible": policy == "best_effort" or strict_ready,
        "release_authorized": False,
        "failure_reason_codes": sorted(
            set(str(value) for value in (e2e or {}).get("failure_reason_codes") or [])
        ),
    }
    return payload


def compile_submission_package(
    *,
    proposal_dir: Path,
    resolved_predictions_path: Path,
    e2e_receipts_path: Path | None,
    output_dir: Path,
    policy: str = "best_effort",
) -> SubmissionCompileResult:
    """Compile delivery artifacts only after the resolver/E2E hand-off."""

    if policy not in {"best_effort", "strict"}:
        raise ValueError("policy must be best_effort or strict")
    proposal_dir = proposal_dir.resolve()
    resolved_predictions_path = resolved_predictions_path.resolve()
    e2e_receipts_path = e2e_receipts_path.resolve() if e2e_receipts_path else None
    output_dir = output_dir.resolve()
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite compiled submission: {output_dir}")
    submission_path = proposal_dir / "submission.json"
    submission_rows = _rows(submission_path)
    resolved = _index(_jsonl(resolved_predictions_path), key="question_id", label="resolved predictions")
    e2e = (
        _index(_jsonl(e2e_receipts_path), key="question_id", label="E2E receipts")
        if e2e_receipts_path is not None
        else {}
    )
    if {int(row["id"]) for row in submission_rows} != set(resolved):
        raise ValueError("submission and resolved prediction ledgers cover different questions")
    if e2e and set(e2e) != set(resolved):
        raise ValueError("resolved prediction and E2E receipt ledgers cover different questions")
    ledger_rows = [
        _delivery_row(
            submission=row,
            resolved=resolved.get(int(row["id"])),
            e2e=e2e.get(int(row["id"])),
            policy=policy,
        )
        for row in submission_rows
    ]
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.tmp-", dir=output_dir.parent))
    try:
        (staging / "data").mkdir(parents=True, exist_ok=True)
        shutil.copy2(submission_path, staging / "submission.json")
        proposal_data = proposal_dir / "data"
        for csv_path in sorted(proposal_data.glob("*.csv")):
            shutil.copy2(csv_path, staging / "data" / csv_path.name)
        for name in (
            "diagnostics.jsonl",
            "prediction_audit_ledger_v1.jsonl",
            "best_surviving_candidates_v1.jsonl",
            "build_report.json",
        ):
            source = proposal_dir / name
            if source.is_file():
                shutil.copy2(source, staging / name)
        shutil.copy2(resolved_predictions_path, staging / resolved_predictions_path.name)
        if e2e_receipts_path is not None:
            shutil.copy2(e2e_receipts_path, staging / e2e_receipts_path.name)
        ledger_path = staging / "submission_ledger_v1.jsonl"
        ledger_path.write_text(
            "".join(
                json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
                for row in ledger_rows
            ),
            encoding="utf-8",
        )
        status_counts = Counter(str(row["delivery_status"]) for row in ledger_rows)
        report = {
            "schema_version": 1,
            "protocol": SUBMISSION_COMPILE_PROTOCOL,
            "delivery_policy": policy,
            "question_count": len(ledger_rows),
            "delivery_status_counts": dict(sorted(status_counts.items())),
            "strict_ready_count": sum(row["delivery_status"] == "STRICT_READY" for row in ledger_rows),
            "release_authorized": False,
            "inputs": {
                "proposal_dir": str(proposal_dir),
                "resolved_predictions": {
                    "path": str(resolved_predictions_path),
                    "sha256": _sha256_file(resolved_predictions_path),
                },
                "e2e_receipts": (
                    {"path": str(e2e_receipts_path), "sha256": _sha256_file(e2e_receipts_path)}
                    if e2e_receipts_path is not None
                    else None
                ),
            },
            "authority": {
                "compiler_generates_delivery_artifact": True,
                "compiler_generates_answer_proof": False,
                "release_gate_required": True,
                "model_output_authorizes_submission": False,
            },
        }
        report_path = staging / "submission_compile_report.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        archive_path = staging / "submission.zip"
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            archive.write(staging / "submission.json", "submission.json")
            for csv_path in sorted((staging / "data").glob("*.csv")):
                archive.write(csv_path, f"data/{csv_path.name}")
        manifest = {
            **report,
            "outputs": {
                "submission": {"path": str(output_dir / "submission.json"), "sha256": _sha256_file(staging / "submission.json")},
                "ledger": {"path": str(output_dir / ledger_path.name), "sha256": _sha256_file(ledger_path)},
                "archive": {"path": str(output_dir / archive_path.name), "sha256": _sha256_file(archive_path)},
                "report": {"path": str(output_dir / report_path.name), "sha256": _sha256_file(report_path)},
            },
        }
        manifest_path = staging / "submission_compile.manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        staging.rename(output_dir)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return SubmissionCompileResult(
        output_dir=output_dir,
        submission_path=output_dir / "submission.json",
        archive_path=output_dir / "submission.zip",
        ledger_path=output_dir / "submission_ledger_v1.jsonl",
        report_path=output_dir / "submission_compile_report.json",
        question_count=len(ledger_rows),
    )


__all__ = [
    "SUBMISSION_COMPILE_PROTOCOL",
    "SUBMISSION_LEDGER_PROTOCOL",
    "SubmissionCompileResult",
    "compile_submission_package",
]
