"""Create human-readable, non-approving review forms for exact-cell research."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from finance_query.e2e.core.table_retrieval import load_jsonl, sha256_file


PROTOCOL = "vifinqa_exact_cell_human_review_forms_v1"
FORBIDDEN_KEYS = frozenset(
    {"answer", "raw_value", "raw_values", "cell_value", "pandas_query", "rows"}
)


def _canonical_sha(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _contains_forbidden_key(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(
            key in FORBIDDEN_KEYS or _contains_forbidden_key(child)
            for key, child in value.items()
        )
    if isinstance(value, list):
        return any(_contains_forbidden_key(child) for child in value)
    return False


def _write_jsonl(path: Path, rows: list[Mapping[str, Any]]) -> None:
    path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows
        ),
        encoding="utf-8",
    )


def _checkbox(label: str) -> str:
    return f"- [ ] {label}"


def _candidate_markdown(candidate: Mapping[str, Any]) -> list[str]:
    locator = candidate["exact_table_locator"]
    lines = [
        f"### Ứng viên {candidate['review_priority_rank']}",
        "",
        f"- Bảng: `{candidate['document_id']}`",
        f"- UID bảng: `{candidate['internal_table_uid']}`",
        f"- Scope quan sát: `{candidate['observed_scope']}` — {candidate['scope_status']}",
        f"- Loại bảng: {candidate.get('table_function', {}).get('label', 'Chưa xác định')}",
        f"- Vị trí: trang {locator.get('page_no')}, table #{locator.get('local_ordinal')}, ký tự {locator.get('char_start')}",
        f"- File nguồn: `{locator.get('source_path')}`",
        f"- Kỳ: {candidate['period_status']}; fallback: {candidate['fallback_period_status']}",
        f"- Cột năm khớp trực tiếp: {candidate['matching_year_column_indices'] or 'không có'}",
        f"- Unit: {candidate['unit_status']}; đề xuất: {candidate.get('source_unit_candidate') or 'chưa rõ'}",
        "",
        "Header cột (số tiền đã ẩn):",
        "",
    ]
    for header in candidate.get("column_headers") or []:
        labels = " | ".join(header.get("header_labels") or []) or "(trống)"
        years = ", ".join(str(year) for year in header.get("header_years") or []) or "không thấy"
        lines.append(f"- Cột {header['column_index']}: {labels}; năm nhận diện: {years}")
    lines.extend(["", "Dòng gợi ý (số tiền đã ẩn):", ""])
    for row in candidate.get("row_candidates") or []:
        lines.append(
            f"- Dòng {row['row_index']} — {row['row_label']} "
            f"(độ khớp {row['row_label_token_jaccard']:.2f}; cột số {row['numeric_column_indices']})"
        )
    lines.extend(
        [
            "",
            "Quyết định riêng cho ứng viên này:",
            "",
            _checkbox("Bảng đúng chủ đề tài chính cần tìm."),
            _checkbox("Scope đúng hoặc câu hỏi không yêu cầu scope."),
            _checkbox("Chọn được một dòng đúng về nghĩa."),
            _checkbox("Chọn được một cột đúng kỳ báo cáo."),
            _checkbox("Đơn vị được xác nhận từ chính bảng hoặc nguồn."),
            _checkbox("Từ chối ứng viên này."),
            _checkbox("Chưa chắc; cần xem thêm nguồn OCR."),
            "",
        ]
    )
    return lines


def _packet_markdown(packet: Mapping[str, Any], question: str) -> str:
    lines = [
        f"# Review Q{packet['question_id']} — {packet['operand_id']}",
        "",
        f"- Mức ưu tiên: `{packet['priority_bucket']}`",
        f"- Công ty: `{packet['ticker']}`; năm hỏi: `{packet['report_year']}`; scope yêu cầu: `{packet['requested_scope'] or 'không nêu'}`",
        f"- Câu hỏi đầy đủ: {question}",
        f"- Chỉ tiêu cần kiểm tra: {packet['metric_core_query']}",
        "",
        "Mục tiêu review: xác nhận bảng, dòng, kỳ và unit. Không chọn đáp án hay ghi số tiền ở form này.",
        "",
    ]
    for candidate in packet.get("candidate_packets") or []:
        lines.extend(_candidate_markdown(candidate))
    lines.extend(
        [
            "## Kết luận cho tuyến này",
            "",
            _checkbox("APPROVE — chỉ khi cùng một ứng viên đã xác nhận đủ bảng, dòng, kỳ và unit."),
            _checkbox("REJECT — không ứng viên nào phù hợp."),
            _checkbox("UNCERTAIN — cần thêm nguồn/không thể phân biệt."),
            "",
            "Nếu APPROVE, ghi vào file quyết định: UID bảng, row index, column index, reviewer ID và ghi chú. Không điền giá trị tiền.",
            "",
            f"Mã mẫu: `{packet['sample_id']}`",
        ]
    )
    return "\n".join(lines) + "\n"


def _guide() -> str:
    return """# Hướng dẫn review exact-cell v1

Mỗi form kiểm tra một tuyến: chỉ tiêu + công ty + năm. Form này không cho phép
chọn đáp án, không hiển thị số tiền và không tự cấp quyền cho submission.

Quy trình cho một form:

1. Mở file OCR ở đường dẫn nguồn và đến đúng trang/vị trí đã nêu.
2. Chọn một trong tối đa ba bảng ứng viên, hoặc từ chối tất cả.
3. Kiểm tra scope: consolidated/separate phải khớp nếu câu hỏi có nêu.
4. Kiểm tra dòng: tên dòng phải đúng chỉ tiêu, không chỉ giống từ.
5. Kiểm tra cột: header phải thực sự chỉ đúng năm/kỳ cần hỏi.
6. Kiểm tra unit từ chính bảng hoặc tiêu đề nguồn.
7. Chọn một quyết định tổng: APPROVE, REJECT hoặc UNCERTAIN.

APPROVE chỉ hợp lệ khi bạn tự xác nhận đủ cả bảng, dòng, cột kỳ và unit trên
nguồn gốc. Nếu header "Năm nay/Số cuối năm" chưa được tiêu đề nguồn xác thực,
chọn UNCERTAIN. REJECT là đúng khi ứng viên là mục lục, bảng khác chủ đề, sai
scope hoặc không có dòng/cột phù hợp.

Sau khi review, điền `review_decisions_template_v1.jsonl` bằng một bản sao mới;
không sửa artifact gốc. Quyết định vẫn cần validator riêng trước khi trở thành
`human_verified`.
"""


def build_exact_cell_human_review_forms(
    *,
    config_path: Path,
    sample_path: Path,
    plans_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Render one Markdown form per sample plus an empty decision sidecar."""
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite output directory: {output_dir}")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if config.get("protocol") != PROTOCOL:
        raise ValueError("unexpected human review form protocol")
    packets = list(load_jsonl(sample_path))
    expected_count = int(config["expected_review_sample_count"])
    if len(packets) != expected_count:
        raise ValueError("review sample count does not match form config")
    if any(_contains_forbidden_key(packet) for packet in packets):
        raise ValueError("review sample contains forbidden numeric-value fields")
    sample_ids = [str(packet["sample_id"]) for packet in packets]
    if len(sample_ids) != len(set(sample_ids)):
        raise ValueError("review sample contains duplicate sample IDs")
    plans = {int(plan["question_id"]): plan for plan in load_jsonl(plans_path)}

    output_dir.mkdir(parents=True)
    packet_dir = output_dir / "packets"
    packet_dir.mkdir()
    decisions: list[dict[str, Any]] = []
    index_rows: list[str] = ["# Danh sách form review", "", "| Nhóm | Câu | Chỉ tiêu | Form |", "|---|---:|---|---|"]
    for packet in packets:
        question_id = int(packet["question_id"])
        plan = plans.get(question_id)
        if plan is None or not str(plan.get("question") or "").strip():
            raise ValueError(f"sample question {question_id} is missing from plans")
        filename = f"Q{question_id}_{packet['operand_id']}_{packet['sample_id'][:10]}.md"
        (packet_dir / filename).write_text(
            _packet_markdown(packet, str(plan["question"])), encoding="utf-8"
        )
        index_rows.append(
            f"| {packet['priority_bucket']} | {question_id} | {packet['metric_core_query']} | [mở form](packets/{filename}) |"
        )
        decisions.append(
            {
                "protocol": PROTOCOL,
                "sample_id": packet["sample_id"],
                "review_packet_id": packet["review_packet_id"],
                "route_id": packet["route_id"],
                "question_id": question_id,
                "operand_id": packet["operand_id"],
                "review_state": "UNREVIEWED",
                "overall_decision": None,
                "selected_internal_table_uid": None,
                "selected_row_index": None,
                "selected_column_index": None,
                "reviewer_id": None,
                "reviewed_at": None,
                "reviewer_notes": None,
                "human_verified": False,
                "may_authorize_evidence": False,
                "may_authorize_answer": False,
                "training_eligible": False,
                "submission_eligible": False,
            }
        )
    guide_path = output_dir / "README_REVIEW_VI.md"
    index_path = output_dir / "review_index.md"
    decisions_path = output_dir / "review_decisions_template_v1.jsonl"
    guide_path.write_text(_guide(), encoding="utf-8")
    index_path.write_text("\n".join(index_rows) + "\n", encoding="utf-8")
    _write_jsonl(decisions_path, decisions)
    form_files = sorted(packet_dir.glob("*.md"))
    manifest = {
        "protocol": PROTOCOL,
        "schema_version": 1,
        "inputs": {
            "config": {"path": str(config_path), "sha256": sha256_file(config_path)},
            "sample": {"path": str(sample_path), "sha256": sha256_file(sample_path)},
            "plans": {"path": str(plans_path), "sha256": sha256_file(plans_path)},
        },
        "outputs": {
            path.relative_to(output_dir).as_posix(): {
                "sha256": sha256_file(path),
                "size_bytes": path.stat().st_size,
            }
            for path in [guide_path, index_path, decisions_path, *form_files]
        },
        "authorization": config["authorization"],
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {
        "protocol": PROTOCOL,
        "review_sample_count": len(packets),
        "form_count": len(form_files),
        "decision_template_count": len(decisions),
        "human_verified_count": 0,
        "answer_eligible": False,
        "submission_eligible": False,
    }


def validate_exact_cell_human_review_forms(
    artifact_dir: Path, *, expected_review_sample_count: int = 150
) -> dict[str, Any]:
    """Verify form coverage and ensure the decision sidecar is non-approving."""
    manifest = json.loads((artifact_dir / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("protocol") != PROTOCOL:
        raise ValueError("unexpected human review form protocol")
    for relative_path, contract in manifest["outputs"].items():
        if sha256_file(artifact_dir / relative_path) != contract["sha256"]:
            raise ValueError(f"output hash mismatch: {relative_path}")
    decisions = list(load_jsonl(artifact_dir / "review_decisions_template_v1.jsonl"))
    forms = sorted((artifact_dir / "packets").glob("*.md"))
    if len(decisions) != expected_review_sample_count or len(forms) != expected_review_sample_count:
        raise ValueError("review form coverage mismatch")
    sample_ids = [str(row["sample_id"]) for row in decisions]
    if len(sample_ids) != len(set(sample_ids)):
        raise ValueError("duplicate decision template sample ID")
    for decision in decisions:
        if _contains_forbidden_key(decision):
            raise ValueError("decision template contains forbidden value field")
        if decision.get("review_state") != "UNREVIEWED":
            raise ValueError("decision template is not blank")
        if any(
            decision.get(field) is not None
            for field in (
                "overall_decision",
                "selected_internal_table_uid",
                "selected_row_index",
                "selected_column_index",
                "reviewer_id",
                "reviewed_at",
                "reviewer_notes",
            )
        ):
            raise ValueError("decision template has a prefilled review decision")
        if decision.get("human_verified") is not False:
            raise ValueError("decision template incorrectly claims human verification")
        if decision.get("may_authorize_answer") is not False:
            raise ValueError("decision template incorrectly authorizes an answer")
    return {
        "status": "PASS",
        "review_sample_count": len(decisions),
        "form_count": len(forms),
        "human_verified_count": 0,
        "answer_eligible": False,
        "submission_eligible": False,
    }
