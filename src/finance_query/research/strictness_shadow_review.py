"""Build a value-blind local review package for E2E strictness checks.

This module deliberately produces neither a contest submission nor an answer
candidate.  It packages only the question, immutable source coordinates, and
the semantic fields that still block authorization.  The reviewer can inspect
the cited OCR/V2 table locally without the package becoming an answer path.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any, Iterable, Mapping
import zipfile


PROTOCOL = "vifinqa_strictness_shadow_review_v1"
CONTRACT = {
    "review_only": True,
    "contains_numeric_values": False,
    "evidence_eligible": False,
    "may_materialize_answer": False,
    "submission_eligible": False,
    "promotion_allowed": False,
}
FORBIDDEN_KEYS = frozenset(
    {
        "answer",
        "answer_decimal",
        "raw_value",
        "raw_values",
        "cell_value",
        "pandas_query",
        "human_verified",
        "raw_source_cell",
        "raw_source_row",
        "raw_decimal_candidate",
    }
)
PACKAGE_FILES = (
    "README.md",
    "strictness_review_items_v1.jsonl",
    "strictness_review_template_v1.jsonl",
    "manifest.json",
)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain one JSON object")
    return value


def _rows(path: Path) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError(f"{path} must contain JSON objects")
    return rows


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )


def _contains_forbidden(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(str(key) in FORBIDDEN_KEYS or _contains_forbidden(child) for key, child in value.items())
    return any(_contains_forbidden(child) for child in value) if isinstance(value, list) else False


def _require_descriptor(descriptor: Mapping[str, Any], *, name: str) -> Path:
    path = Path(str(descriptor.get("path") or ""))
    expected_sha256 = str(descriptor.get("sha256") or "")
    if not path.is_file() or not expected_sha256 or sha256_file(path) != expected_sha256:
        raise ValueError(f"{name} is missing or does not match the pinned E2E manifest")
    return path


def _question_index(rows: Iterable[Mapping[str, Any]], *, name: str) -> dict[int, dict[str, Any]]:
    index: dict[int, dict[str, Any]] = {}
    for row in rows:
        question_id = int(row["question_id"])
        if question_id in index:
            raise ValueError(f"{name} contains duplicate question ID {question_id}")
        index[question_id] = dict(row)
    return index


def _is_numeric_cell_text(value: object) -> bool:
    text = str(value).strip()
    return bool(text) and bool(re.fullmatch(r"[+\-−()\s\d.,%]+", text))


def _safe_row_labels(queue_item: Mapping[str, Any]) -> list[dict[str, Any]]:
    labels: list[dict[str, Any]] = []
    for candidate in queue_item.get("row_label_candidates") or []:
        if not isinstance(candidate, Mapping):
            continue
        raw_text = str(candidate.get("raw_text") or "").strip()
        if not raw_text or _is_numeric_cell_text(raw_text):
            continue
        labels.append(
            {
                "row_index": candidate.get("row_index"),
                "column_index": candidate.get("column_index"),
                "label": raw_text,
                "label_sha256": candidate.get("raw_text_sha256"),
            }
        )
    return labels


def _pending_checks(field_statuses: Mapping[str, Any]) -> list[str]:
    checks: list[str] = []
    readable = {
        "entity_status": "Công ty/ticker trong câu hỏi phải được nguồn chứng minh trực tiếp.",
        "period_status": "Header/cột phải chứng minh đúng năm hoặc kỳ được hỏi.",
        "scope_status": "Nguồn phải đúng phạm vi: công ty mẹ, riêng lẻ hoặc hợp nhất.",
        "unit_status": "Đơn vị và quy đổi phải được neo vào header nguồn.",
        "variable_status": "Tên dòng nguồn phải chứng minh đúng chỉ tiêu tài chính được hỏi.",
        "source_integrity_status": "Ô nguồn phải còn liên kết được tới bảng V2 và OCR gốc.",
    }
    for field, text in readable.items():
        if str(field_statuses.get(field) or "UNRESOLVED") not in {"PASS", "NOT_APPLICABLE"}:
            checks.append(text)
    return checks


def _binding_operands(rows: Iterable[Mapping[str, Any]]) -> dict[tuple[int, str, str], dict[str, Any]]:
    index: dict[tuple[int, str, str], dict[str, Any]] = {}
    for row in rows:
        question_id = int(row["question_id"])
        for stage in row.get("stages") or []:
            if not isinstance(stage, Mapping):
                continue
            stage_id = str(stage.get("stage_id") or "")
            for operand in stage.get("required_operands") or []:
                if not isinstance(operand, Mapping) or operand.get("binding_status") != "binding_ready":
                    continue
                key = (question_id, stage_id, str(operand.get("role") or ""))
                if key in index:
                    raise ValueError(f"bindings contain duplicate ready operand {key}")
                index[key] = dict(operand)
    return index


def _evidence_by_operand(rows: Iterable[Mapping[str, Any]]) -> dict[tuple[int, str, str], dict[str, Any]]:
    index: dict[tuple[int, str, str], dict[str, Any]] = {}
    for row in rows:
        evidence = row.get("evidence_binding")
        if not isinstance(evidence, Mapping):
            continue
        key = (int(row["question_id"]), str(row.get("stage_id") or ""), str(row.get("role") or ""))
        if key in index:
            raise ValueError(f"evidence bindings contain duplicate operand {key}")
        index[key] = dict(evidence)
    return index


def _queue_by_operand(rows: Iterable[Mapping[str, Any]]) -> dict[tuple[int, str, str], dict[str, Any]]:
    index: dict[tuple[int, str, str], dict[str, Any]] = {}
    for row in rows:
        key = (int(row["question_id"]), str(row.get("stage_id") or ""), str(row.get("role") or ""))
        if key in index:
            raise ValueError(f"semantic queue contains duplicate operand {key}")
        index[key] = dict(row)
    return index


def _readme(question_count: int, operand_count: int) -> str:
    return f"""# Gói kiểm tra độ chặt nguồn — chỉ dùng để review cục bộ

Gói này có {question_count} câu hỏi và {operand_count} ô nguồn V2 đã chạy lại được trong E2E.

Nó **không phải bài nộp cuộc thi**: không có `submission.json`, `answer`, số liệu ô nguồn,
`pandas_query` hoặc thư mục `data/`. Dashboard sẽ không nhận gói này.

Mục đích là kiểm tra liệu từng chặn có thực sự cần thiết. Với từng operand, mở `source_path`
trong kho dữ liệu gốc, đi tới `char_start`, bảng có `table_sha256`, rồi đối chiếu `row_index`
và `column_index`. Không suy ra hay ghi số liệu vào gói này.

Các trường còn chặn thường là: tên chỉ tiêu ở dòng, cột/kỳ báo cáo, công ty/ticker, phạm vi
riêng lẻ/hợp nhất và đơn vị. `strictness_review_template_v1.jsonl` chỉ là mẫu ghi nhận nhận
xét; nó không được E2E đọc và không thể mở khóa đáp án.
"""


def build_strictness_shadow_review(
    *,
    e2e_run_manifest_path: Path,
    output_dir: Path,
    expected_question_count: int = 1012,
) -> dict[str, Any]:
    """Build a non-submittable, value-blind package for executed E2E replays."""
    if output_dir.exists() or output_dir.with_suffix(".zip").exists():
        raise FileExistsError(f"refusing to overwrite output package: {output_dir}")
    run = _json(e2e_run_manifest_path)
    if run.get("protocol") != "vifinqa_grounded_e2e_v1" or run.get("source_contract", {}).get("submission_eligible") is not False:
        raise ValueError("shadow review requires a pinned research-only E2E run")
    inputs = run.get("inputs") or {}
    outputs = run.get("outputs") or {}
    bindings_path = _require_descriptor(outputs.get("bindings") or {}, name="exact-cell bindings")
    execution_path = _require_descriptor(outputs.get("execution") or {}, name="execution replay")
    authorization = outputs.get("authorization") or {}
    certificates_path = Path(str(authorization.get("answer_certificates_path") or ""))
    if not certificates_path.is_file() or sha256_file(certificates_path) != authorization.get("answer_certificates_sha256"):
        raise ValueError("answer certificates are missing or do not match the pinned E2E manifest")
    evidence_path = Path(str(authorization.get("evidence_bindings_path") or ""))
    if not evidence_path.is_file() or sha256_file(evidence_path) != authorization.get("evidence_bindings_sha256"):
        raise ValueError("evidence bindings are missing or do not match the pinned E2E manifest")
    route_overlay_path = _require_descriptor(inputs.get("route_overlay") or {}, name="route overlay")
    semantic_queue_path = _require_descriptor(inputs.get("semantic_review_queue") or {}, name="semantic review queue")

    bindings = _question_index(_rows(bindings_path), name="bindings")
    execution = _question_index(_rows(execution_path), name="execution")
    certificates = _question_index(_rows(certificates_path), name="certificates")
    overlays = _question_index(_rows(route_overlay_path), name="route overlay")
    if set(bindings) != set(execution) or set(bindings) != set(certificates) or set(bindings) != set(overlays):
        raise ValueError("E2E artifacts do not cover the same questions")
    if len(bindings) != expected_question_count:
        raise ValueError("E2E artifacts do not cover the expected question population")

    ready_question_ids = sorted(
        question_id
        for question_id, row in execution.items()
        if row.get("execution_status") == "execution_replay_ready"
    )
    if not ready_question_ids:
        raise ValueError("there are no execution-ready questions to review")
    binding_operands = _binding_operands(bindings.values())
    evidence_operands = _evidence_by_operand(_rows(evidence_path))
    queue_operands = _queue_by_operand(_rows(semantic_queue_path))

    review_rows: list[dict[str, Any]] = []
    template_rows: list[dict[str, Any]] = []
    total_operands = 0
    for question_id in ready_question_ids:
        overlay = overlays[question_id]
        certificate = certificates[question_id].get("answer_certificate") or {}
        operand_rows: list[dict[str, Any]] = []
        for key, operand in sorted(binding_operands.items()):
            if key[0] != question_id:
                continue
            evidence = evidence_operands.get(key)
            queue_item = queue_operands.get(key)
            if evidence is None or queue_item is None:
                raise ValueError(f"execution-ready operand {key} lacks its evidence or review packet")
            source = queue_item.get("source_provenance") or {}
            value_cell = queue_item.get("value_cell") or {}
            field_statuses = evidence.get("field_statuses") or {}
            operand_rows.append(
                {
                    "stage_id": key[1],
                    "role": key[2],
                    "concept_id": operand.get("concept_id"),
                    "source_document": {
                        "document_uid": queue_item.get("document_uid"),
                        "source_path": source.get("source_path"),
                        "char_start": source.get("char_start"),
                        "source_sha256": source.get("source_sha256"),
                        "table_sha256": source.get("table_sha256"),
                        "internal_table_uid": queue_item.get("internal_table_uid"),
                        "source_title": queue_item.get("source_title"),
                    },
                    "source_cell": {
                        "row_index": value_cell.get("row_index"),
                        "column_index": value_cell.get("column_index"),
                        "value_cell_sha256": value_cell.get("raw_text_sha256"),
                        "header_cells": [
                            {"row_index": item.get("row_index"), "column_index": item.get("column_index")}
                            for item in operand.get("header_source_cells") or []
                            if isinstance(item, Mapping)
                        ],
                    },
                    "row_label_candidates": _safe_row_labels(queue_item),
                    "claimed_period_labels": list(operand.get("period_labels") or []),
                    "source_unit": operand.get("source_unit"),
                    "field_statuses": dict(field_statuses),
                    "pending_checks": _pending_checks(field_statuses),
                }
            )
        if not operand_rows:
            raise ValueError(f"execution-ready question {question_id} has no ready operand")
        total_operands += len(operand_rows)
        row = {
            "schema_version": 1,
            "protocol": PROTOCOL,
            "question_id": question_id,
            "question": overlay.get("question"),
            "question_context": overlay.get("question_context"),
            "requested_output_unit": {
                key: value
                for key, value in (overlay.get("requested_output_unit") or {}).items()
                if key in {"kind", "unit", "source"}
            },
            "e2e_status": {
                "execution_status": execution[question_id].get("execution_status"),
                "certificate_status": certificate.get("status"),
                "authorization_status": certificates[question_id].get("authorization_status"),
                "certificate_blocker_codes": sorted(
                    set(str(code) for code in certificate.get("abstain_reason_codes") or [] if str(code))
                ),
            },
            "why_strict_review_is_required": [
                "Phép tính chạy lại không chứng minh riêng rằng ô được chọn đúng chỉ tiêu, đúng kỳ và đúng phạm vi.",
                "Không có kết quả số trong gói review; số phải được đọc lại từ OCR/V2 theo tọa độ nguồn.",
            ],
            "operands": operand_rows,
            "source_contract": dict(CONTRACT),
        }
        if _contains_forbidden(row):
            raise ValueError("shadow review attempted to leak a numeric value or answer field")
        review_rows.append(row)
        template_rows.append(
            {
                "schema_version": 1,
                "protocol": PROTOCOL,
                "question_id": question_id,
                "strictness_assessment": "",
                "allowed_assessments": [
                    "strictness_confirmed",
                    "source_sufficient_but_gate_too_strict",
                    "source_mismatch",
                    "needs_source_clarification",
                ],
                "notes": "",
                "source_contract": dict(CONTRACT),
            }
        )

    summary = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "review_question_count": len(review_rows),
        "review_operand_count": total_operands,
        "source_contract": dict(CONTRACT),
    }
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.tmp-", dir=output_dir.parent))
    zip_path = output_dir.with_suffix(".zip")
    try:
        _write_jsonl(temporary / "strictness_review_items_v1.jsonl", review_rows)
        _write_jsonl(temporary / "strictness_review_template_v1.jsonl", template_rows)
        (temporary / "README.md").write_text(_readme(len(review_rows), total_operands), encoding="utf-8")
        _write_json(
            temporary / "manifest.json",
            {
                **summary,
                "inputs": {
                    "e2e_run_manifest": {"path": str(e2e_run_manifest_path), "sha256": sha256_file(e2e_run_manifest_path)},
                    "bindings": {"path": str(bindings_path), "sha256": sha256_file(bindings_path)},
                    "execution": {"path": str(execution_path), "sha256": sha256_file(execution_path)},
                    "certificates": {"path": str(certificates_path), "sha256": sha256_file(certificates_path)},
                    "evidence_bindings": {"path": str(evidence_path), "sha256": sha256_file(evidence_path)},
                    "route_overlay": {"path": str(route_overlay_path), "sha256": sha256_file(route_overlay_path)},
                    "semantic_queue": {"path": str(semantic_queue_path), "sha256": sha256_file(semantic_queue_path)},
                },
                "outputs": {
                    name: {"path": name, "sha256": sha256_file(temporary / name)}
                    for name in PACKAGE_FILES
                    if name != "manifest.json"
                },
                "package_zip": {"path": str(zip_path), "contains": list(PACKAGE_FILES)},
            },
        )
        temporary.rename(output_dir)
        with zipfile.ZipFile(zip_path, "x", compression=zipfile.ZIP_DEFLATED) as archive:
            for name in PACKAGE_FILES:
                archive.write(output_dir / name, arcname=name)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        if zip_path.exists():
            zip_path.unlink()
        raise
    return {**summary, "package_dir": str(output_dir), "package_zip": str(zip_path)}


def validate_strictness_shadow_review(artifact_dir: Path, *, expected_question_count: int = 1012) -> dict[str, Any]:
    manifest = _json(artifact_dir / "manifest.json")
    if manifest.get("protocol") != PROTOCOL or manifest.get("source_contract") != CONTRACT:
        raise ValueError("unexpected shadow review protocol or contract")
    for descriptor in (manifest.get("inputs") or {}).values():
        _require_descriptor(descriptor, name="pinned input")
    for descriptor in (manifest.get("outputs") or {}).values():
        path = artifact_dir / str(descriptor.get("path") or "")
        if not path.is_file() or sha256_file(path) != descriptor.get("sha256"):
            raise ValueError("shadow review output hash mismatch")
    rows = _rows(artifact_dir / "strictness_review_items_v1.jsonl")
    template_rows = _rows(artifact_dir / "strictness_review_template_v1.jsonl")
    if not rows or len(rows) > expected_question_count or len(rows) != len(template_rows):
        raise ValueError("shadow review coverage is invalid")
    question_ids = [int(row["question_id"]) for row in rows]
    if len(question_ids) != len(set(question_ids)) or set(question_ids) != {int(row["question_id"]) for row in template_rows}:
        raise ValueError("shadow review question IDs are inconsistent")
    if any(_contains_forbidden(row) or row.get("source_contract") != CONTRACT for row in [*rows, *template_rows]):
        raise ValueError("shadow review leaked a forbidden field or lost its contract")
    zip_path = Path(str((manifest.get("package_zip") or {}).get("path") or ""))
    if not zip_path.is_file():
        raise ValueError("shadow review ZIP is missing")
    with zipfile.ZipFile(zip_path) as archive:
        if set(archive.namelist()) != set(PACKAGE_FILES):
            raise ValueError("shadow review ZIP has unexpected contents")
        for name in PACKAGE_FILES:
            if archive.read(name) != (artifact_dir / name).read_bytes():
                raise ValueError(f"shadow review ZIP content mismatch for {name}")
    return {
        "status": "PASS",
        "review_question_count": len(rows),
        "answer_eligible": False,
        "submission_eligible": False,
    }
