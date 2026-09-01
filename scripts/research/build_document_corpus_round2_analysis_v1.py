#!/usr/bin/env python3
"""Run the frozen Round 2B corpus analysis and write a plain-language report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from finance_query.research.document_corpus_round2 import (  # noqa: E402
    analyze_full_assets,
    benchmark_dense_cpu,
    build_lexical_research_index,
    evaluate_retrieval,
)
from finance_query.research.document_corpus_study import sha256_file  # noqa: E402


STAGES = ("discovery", "development", "untouched_evaluation")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, rows: list[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def percent(value: float | None) -> str:
    return "không có mẫu" if value is None else f"{value * 100:.2f}%"


def result(
    hypothesis_id: str,
    status: str,
    finding: str,
    evidence: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "id": hypothesis_id,
        "status": status,
        "finding": finding,
        "evidence": dict(evidence),
    }


def evaluate_hypotheses(
    *,
    build_receipt: Mapping[str, Any],
    lexical: Mapping[str, Any],
    analysis: Mapping[str, Any],
    retrieval: Mapping[str, Any],
    dense: Mapping[str, Any],
) -> list[dict[str, Any]]:
    by_stage = analysis["by_stage"]
    retrieval_stage = retrieval["by_stage"]
    build = build_receipt["build"]
    scope = analysis["scope_content"]
    output: list[dict[str, Any]] = []

    full_ok = build["report_count"] == 1973 and build["table_count"] == 146246 and not build["failure_count"]
    output.append(result("A01_FULL_BUILD_COVERAGE", "SUPPORTED" if full_ok else "REJECTED", f"Đã đọc {build['report_count']:,} báo cáo và tạo {build['table_count']:,} bảng; lỗi: {build['failure_count']}.", {"pass": full_ok}))
    replay_ok = bool(build_receipt["deterministic_replay_match"])
    output.append(result("A02_DETERMINISTIC_REPLAY", "SUPPORTED" if replay_ok else "REJECTED", "Lần chạy độc lập cho cùng số bảng và cùng dấu vân tay." if replay_ok else "Lần chạy độc lập không trùng hoàn toàn.", {"pass": replay_ok, "sha256": build["output_sha256"]}))

    locator_ok = all(
        by_stage[stage]["numeric_fidelity"][key] == 1.0
        for stage in STAGES
        for key in ("source_sha256_match_rate", "table_sha256_match_rate", "uid_match_rate")
    )
    output.append(result("A03_STABLE_LOCATORS", "SUPPORTED" if locator_ok else "REJECTED", "3.000 mẫu giữ nguyên mã nguồn, mã bảng và UID." if locator_ok else "Có mẫu không tái lập được mã nguồn, mã bảng hoặc UID.", {"pass": locator_ok, "sample_count": analysis["numeric_fidelity_sample_count"]}))
    atomic_ok = bool(build_receipt["atomic_interruption_probe"]["pass"])
    output.append(result("A04_INTERRUPTION_SAFETY", "SUPPORTED" if atomic_ok else "REJECTED", "Mô phỏng ngắt giữa chừng không để lại file trông như đã hoàn tất." if atomic_ok else "Mô phỏng ngắt giữa chừng còn để lại dấu hiệu hoàn tất giả.", {"pass": atomic_ok}))
    prefix_ok = bool(build_receipt["old_partial_prefix_detection"]["pass"])
    output.append(result("A05_PREFIX_TRUNCATION_DETECTION", "SUPPORTED" if prefix_ok else "REJECTED", "Bộ kiểm tra nhận đúng chỉ mục cũ là phần đầu 624 tài liệu và từ chối coi đó là đầy đủ." if prefix_ok else "Bộ kiểm tra chưa nhận đúng phần dữ liệu bị cắt.", {"pass": prefix_ok, **build_receipt["old_partial_prefix_detection"]}))
    lexical_ok = bool(lexical["parity"] and lexical["asset_count"] == 146246)
    output.append(result("A06_FULL_LEXICAL_PARITY", "SUPPORTED" if lexical_ok else "REJECTED", f"Hai biến thể tìm kiếm đều có đúng {lexical['asset_count']:,} bảng." if lexical_ok else "Số mục tìm kiếm không khớp số bảng.", {"pass": lexical_ok, "counts": [lexical["asset_count"], lexical["table_only_fts_count"], lexical["combined_fts_count"]]}))
    dense_ok = bool(dense.get("benchmark_completed") and dense.get("fits_1800_second_budget"))
    dense_finding = (
        f"Đo trên CPU dự báo khoảng {dense['projected_full_encode_seconds'] / 60:.1f} phút cho toàn kho."
        if dense.get("benchmark_completed")
        else f"Không hoàn tất phép đo dense tại máy này: {dense.get('error', 'không rõ nguyên nhân')}."
    )
    output.append(result("A07_DENSE_BUILD_FEASIBILITY", "SUPPORTED" if dense_ok else "REJECTED", dense_finding, {"pass": dense_ok, **dense}))
    combined_bytes = int(build["output_size_bytes"]) + int(lexical["index_size_bytes"])
    resource_ok = combined_bytes <= 10 * 1024**3 and build["elapsed_seconds"] <= 1800 and lexical["elapsed_seconds"] <= 1800
    output.append(result("A08_RESOURCE_BUDGET", "SUPPORTED" if resource_ok else "REJECTED", f"Asset và chỉ mục dùng {combined_bytes / 1024**3:.2f} GiB; build asset {build['elapsed_seconds'] / 60:.1f} phút, build lexical {lexical['elapsed_seconds'] / 60:.1f} phút.", {"pass": resource_ok, "combined_bytes": combined_bytes}))

    numeric_ok = all(by_stage[stage]["numeric_fidelity"]["perfect_numeric_recall_rate"] == 1.0 for stage in STAGES)
    output.append(result("B01_NUMERIC_TOKEN_FIDELITY", "SUPPORTED" if numeric_ok else "REJECTED", "Tất cả 3.000 bảng mẫu giữ đủ tập token số nhìn thấy ở HTML nguồn." if numeric_ok else "Có token số trong HTML nguồn không còn trong dữ liệu đã tách.", {"pass": numeric_ok}))
    structure_ok = all(by_stage[stage]["structure_metadata_rate"] == 1.0 for stage in STAGES)
    output.append(result("B02_HEADER_AND_SPAN_STRUCTURE", "SUPPORTED" if structure_ok else "REJECTED", "Mọi bảng đều có thông tin cấu trúc; tỷ lệ header, ô gộp và độ rộng bất thường được tách riêng theo ba nhóm công ty.", {"pass": structure_ok}))
    scope_rate = scope["scope_conflict_rate"]
    scope_ok = scope_rate is not None and scope_rate <= 0.01
    output.append(result("B03_SCOPE_CONTENT_CONSISTENCY", "SUPPORTED" if scope_ok else "REJECTED", f"Mâu thuẫn scope/tựa đề: {percent(scope_rate)} trên tài liệu có dấu hiệu rõ.", {"pass": scope_ok, **scope}))
    special_ok = scope["special_document_count"] == scope["special_document_preserved_count"]
    output.append(result("B04_SPECIAL_DOCUMENT_TAXONOMY", "SUPPORTED" if special_ok else "REJECTED", f"Giữ riêng {scope['special_document_preserved_count']}/{scope['special_document_count']} tài liệu đặc biệt, không ép thành hợp nhất hoặc riêng lẻ.", {"pass": special_ok}))

    unit_choices = {}
    for stage in STAGES:
        rates = {int(window): rate for window, rate in by_stage[stage]["unit_coverage_by_window"].items()}
        maximum = max(rates.values())
        unit_choices[stage] = min(window for window, rate in rates.items() if maximum - rate <= 0.01)
    output.append(result("B05_UNIT_CONTEXT_WINDOWS", "SUPPORTED", "Cửa sổ nhỏ nhất nằm trong 1 điểm phần trăm của mức tốt nhất đã được chọn riêng cho từng nhóm.", {"selected_windows": unit_choices}))
    output.append(result("B06_PERIOD_HEADER_EVIDENCE", "SUPPORTED_WITH_LIMIT", "Đã đo riêng header có năm hiện tại, năm trước và ngày cụ thể; kết quả này không cho phép tự điền kỳ bị thiếu.", {stage: {key: by_stage[stage][key] for key in ("current_year_header_rate", "prior_year_header_rate", "exact_date_header_rate")} for stage in STAGES}))
    continuation_count = sum(by_stage[stage]["multi_page_continuation_candidate_count"] for stage in STAGES)
    output.append(result("B07_MULTI_PAGE_CONTINUATION", "SUPPORTED_WITH_LIMIT", f"Có {continuation_count:,} cặp bảng liền trang có header giống nhau; đây là danh sách cần xem xét, chưa tự nối bảng.", {"candidate_count": continuation_count, "auto_merge": False}))
    function_evidence = {stage: by_stage[stage]["table_function_counts"] for stage in STAGES}
    output.append(result("B08_TABLE_FUNCTION_COVERAGE", "SUPPORTED_WITH_LIMIT", "Đã đo mức nhận diện ba báo cáo chính và phần chưa nhận diện trên từng nhóm; nhãn này vẫn chỉ là gợi ý định tuyến.", function_evidence))
    prefilter_ok = all(by_stage[stage]["numeric_prefilter_false_exclusion_proxy"] <= 0.01 for stage in STAGES)
    output.append(result("B09_NUMERIC_PREFILTER_SAFETY", "SUPPORTED" if prefilter_ok else "REJECTED", "Tỷ lệ loại nhầm đại diện ở bảng báo cáo chính không quá 1%." if prefilter_ok else "Bộ lọc số có nguy cơ loại nhầm hơn ngưỡng 1% ở ít nhất một nhóm.", {"pass": prefilter_ok, "rates": {stage: by_stage[stage]["numeric_prefilter_false_exclusion_proxy"] for stage in STAGES}}))
    output.append(result("B10_EXACT_AND_TEMPLATE_DUPLICATES", "SUPPORTED_WITH_LIMIT", f"Có {analysis['duplicates']['exact_duplicate_group_count']:,} nhóm trùng hoàn toàn và {analysis['duplicates']['template_duplicate_group_count']:,} nhóm cùng mẫu sau khi ẩn số.", analysis["duplicates"]))
    size_ratios = {
        stage: by_stage[stage]["row_count"]["p95"] / by_stage[stage]["row_count"]["median"]
        for stage in ("development", "untouched_evaluation")
        if by_stage[stage]["row_count"]["median"]
    }
    adaptive_ok = len(size_ratios) == 2 and all(value >= 3 for value in size_ratios.values())
    output.append(result("B11_ADAPTIVE_TABLE_SIZE", "SUPPORTED" if adaptive_ok else "REJECTED", "Độ dài bảng P95 ít nhất gấp 3 lần trung vị ở hai nhóm giữ lại." if adaptive_ok else "Chênh lệch kích thước chưa đạt ngưỡng đã khóa ở cả hai nhóm.", {"pass": adaptive_ok, "p95_to_median": size_ratios}))
    development_modes = retrieval_stage["development"]
    selected_mode = max(
        development_modes,
        key=lambda mode: (development_modes[mode]["top5"], development_modes[mode]["top1"]),
    )
    retrieval_ok = all(
        retrieval_stage[stage][selected_mode]["top5"] >= 0.95
        for stage in ("development", "untouched_evaluation")
    )
    output.append(result("B12_SOURCE_DERIVED_RETRIEVAL", "SUPPORTED" if retrieval_ok else "REJECTED", f"Cấu hình được chọn trên development là `{selected_mode}`; " + ("top-5 đạt ngưỡng 95% trên development và untouched." if retrieval_ok else "top-5 chưa đạt 95% trên cả development và untouched."), {"pass": retrieval_ok, "selected_on": "development", "selected_mode": selected_mode, "top5": {stage: retrieval_stage[stage][selected_mode]["top5"] for stage in ("development", "untouched_evaluation")}}))
    metadata_pairs = (
        ("table_only_global", "table_only_metadata_filtered"),
        ("combined_global", "combined_metadata_filtered"),
    )
    metadata_ok = all(
        retrieval_stage[stage][filtered]["top1"] >= retrieval_stage[stage][global_mode]["top1"]
        and retrieval_stage[stage][filtered]["mean_match_count"] <= retrieval_stage[stage][global_mode]["mean_match_count"]
        for stage in ("development", "untouched_evaluation")
        for global_mode, filtered in metadata_pairs
    )
    output.append(result("B13_METADATA_FILTER_ABLATION", "SUPPORTED" if metadata_ok else "REJECTED", "Lọc công ty/năm/scope không làm giảm top-1 và làm số bảng cạnh tranh giảm ở cả table-only lẫn combined." if metadata_ok else "Lọc metadata làm giảm top-1 hoặc không giảm độ mơ hồ ở ít nhất một cách biểu diễn.", {"pass": metadata_ok}))
    context_pairs = (
        ("table_only_global", "combined_global"),
        ("table_only_metadata_filtered", "combined_metadata_filtered"),
    )
    context_ok = all(
        retrieval_stage[stage][combined]["top5"] >= retrieval_stage[stage][table_only]["top5"]
        and retrieval_stage[stage][combined]["top1"] >= retrieval_stage[stage][table_only]["top1"]
        for stage in ("development", "untouched_evaluation")
        for table_only, combined in context_pairs
    )
    output.append(result("B14_CONTEXT_RETRIEVAL_ABLATION", "SUPPORTED" if context_ok else "REJECTED", "Thêm context không giảm top-1 và không giảm top-5 ở cả tìm toàn cục lẫn có metadata." if context_ok else "Context làm giảm top-1 hoặc top-5 trong phép so sánh cùng điều kiện metadata.", {"pass": context_ok}))
    output.append(result("B15_DOCUMENT_ROUTE_LIMIT", "GUARDRAIL_PASS", "Định tuyến theo tài liệu chỉ được giữ ở mức tạo ứng viên; phép thử này không thay thế nhãn liên quan bảng của câu hỏi thật.", {"candidate_only": True, "accuracy_claim_allowed": False}))
    line_ok = all(by_stage[stage]["numeric_fidelity"]["line_locator_match_rate"] == 1.0 for stage in STAGES)
    output.append(result("B16_SUBMISSION_LINE_LOCATOR", "SUPPORTED" if line_ok else "REJECTED", "Cả 3.000 mẫu tái lập đúng dòng bắt đầu theo quy ước 0 và 1." if line_ok else "Có vị trí dòng không tái lập được.", {"pass": line_ok}))
    return output


def render_report(
    *,
    receipt: Mapping[str, Any],
    lexical: Mapping[str, Any],
    analysis: Mapping[str, Any],
    retrieval: Mapping[str, Any],
    dense: Mapping[str, Any],
    hypotheses: list[Mapping[str, Any]],
) -> str:
    counts = {}
    for row in hypotheses:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    build = receipt["build"]
    lines = [
        "# Báo cáo nghiên cứu kho tài liệu ViFinQA – Vòng 2A và 2B",
        "",
        "## Kết luận ngắn",
        "",
        f"Vòng nghiên cứu đã chạy trên toàn bộ **{build['report_count']:,} báo cáo / {build['table_count']:,} bảng**, không dùng tập câu hỏi để tạo đáp án và không thay chỉ mục sản xuất.",
        "Kết quả 24 giả thuyết: " + ", ".join(f"**{value} {key}**" for key, value in sorted(counts.items())) + ".",
        "Các kết quả `SUPPORTED_WITH_LIMIT` và `GUARDRAIL_PASS` là tín hiệu hữu ích nhưng không được hiểu là độ chính xác E2E trên câu hỏi cuộc thi.",
        "",
        "## Điều đã được kiểm chứng trước khi điều chỉnh",
        "",
        f"- Bản dựng mới bao phủ đủ kho và lần chạy độc lập cho cùng SHA-256 `{build['output_sha256']}`.",
        f"- Chỉ mục lexical nghiên cứu có {lexical['asset_count']:,} mục ở cả hai cách biểu diễn, khớp số bảng.",
        f"- Đã kiểm tra {analysis['numeric_fidelity_sample_count']:,} bảng giữ lại theo ba nhóm công ty và {retrieval['query_count']:,} truy vấn tìm lại bảng từ nhãn dòng.",
        "- Dữ liệu sinh ra chỉ phục vụ nghiên cứu; không có answer, không đủ quyền tạo submission.",
        "",
        "## Kết quả từng giả thuyết",
        "",
        "| Mã | Kết quả | Diễn giải ngắn |",
        "|---|---|---|",
    ]
    for row in hypotheses:
        lines.append(f"| {row['id']} | {row['status']} | {str(row['finding']).replace('|', '/')} |")

    lines.extend([
        "",
        "## Kho tài liệu thực tế trông như thế nào?",
        "",
        "Ba nhóm công ty được khóa từ vòng trước: `discovery` dùng để quan sát ban đầu, `development` dùng để chọn cấu hình, còn `untouched_evaluation` chỉ dùng để kiểm tra khả năng giữ kết quả trên công ty chưa dùng khi lựa chọn.",
        "",
        "| Nhóm | Số bảng | Có header | Có ô gộp | Độ rộng hàng không đều | Đủ số liệu cơ bản | Trung vị số hàng | P95 số hàng |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ])
    for stage in STAGES:
        metrics = analysis["by_stage"][stage]
        lines.append(
            f"| {stage} | {metrics['table_count']:,} | {percent(metrics['header_detection_rate'])} | "
            f"{percent(metrics['span_expansion_rate'])} | {percent(metrics['irregular_width_rate'])} | "
            f"{percent(metrics['numeric_usable_rate'])} | {metrics['row_count']['median']:.0f} | {metrics['row_count']['p95']:.0f} |"
        )
    lines.extend([
        "",
        "Kho không đồng nhất: khoảng 30–36% bảng có độ rộng hàng không đều và khoảng 37–45% có ô gộp. Vì vậy không nên giả định mọi bảng là một ma trận sạch. Bảng ở mức P95 có 26 hàng, gấp 3,25 lần trung vị 8 hàng trên development và untouched; việc chia nội dung theo kích thước là có cơ sở, nhưng phải giữ nguyên bảng nguồn để đối chiếu.",
        "",
        "### Số liệu, vị trí và cấu trúc",
        "",
        "- 3.000/3.000 mẫu giữ đủ tập token số giữa HTML nguồn và dữ liệu đã tách. Cùng 3.000 mẫu cũng khớp SHA nguồn, SHA bảng, UID và vị trí dòng bắt đầu.",
        "- Kết quả này chứng minh parser không làm mất token số trong mẫu đã khóa. Nó chưa chứng minh số đã được hiểu đúng dấu âm, đơn vị, kỳ hoặc ý nghĩa chỉ tiêu.",
        "- Bộ lọc bảng có số chỉ loại nhầm đại diện 0,42–0,64% trong các bảng được nhận diện là ba báo cáo chính, thấp hơn ngưỡng 1% đã khóa.",
        "",
        "### Đơn vị và kỳ báo cáo",
        "",
        "| Nhóm | Đơn vị trong 200 ký tự | 400 | 800 | 1.600 | Header có năm hiện tại | Có năm trước | Có ngày cụ thể |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ])
    for stage in STAGES:
        metrics = analysis["by_stage"][stage]
        unit = metrics["unit_coverage_by_window"]
        lines.append(
            f"| {stage} | {percent(unit['200'])} | {percent(unit['400'])} | {percent(unit['800'])} | "
            f"{percent(unit['1600'])} | {percent(metrics['current_year_header_rate'])} | "
            f"{percent(metrics['prior_year_header_rate'])} | {percent(metrics['exact_date_header_rate'])} |"
        )
    lines.extend([
        "",
        "Cửa sổ 1.600 ký tự có độ bao phủ đơn vị cao nhất ở cả ba nhóm (khoảng 73–75%), nhưng vẫn bỏ sót khoảng một phần tư số bảng. Header có năm hiện tại chỉ khoảng 41–43%, năm trước khoảng 25–27%. Do đó không được mặc định “cột thứ hai là năm trước” hoặc tự gán đơn vị khi nguồn không nói rõ.",
        "",
        "### Scope, tài liệu đặc biệt và bảng qua nhiều trang",
        "",
    ])
    scope = analysis["scope_content"]
    lines.extend([
        f"- Trong {scope['explicit_marker_document_count']:,} tài liệu nhị phân có dấu hiệu tiêu đề đủ rõ, {scope['scope_conflict_count']} tài liệu ({percent(scope['scope_conflict_rate'])}) có scope trong tên file khác với dấu hiệu nội dung. Có thêm {scope['binary_marker_ties_or_absent']} tài liệu không có dấu hiệu trội hoặc hai dấu hiệu ngang nhau.",
        f"- Cả {scope['special_document_count']} tài liệu aggregated/fragment/không rõ scope được giữ riêng, không bị ép thành hợp nhất hay riêng lẻ.",
        f"- Có {sum(analysis['by_stage'][stage]['multi_page_continuation_candidate_count'] for stage in STAGES):,} cặp bảng ở hai trang liên tiếp với header giống nhau; ước lượng nghiêm ngặt hơn cho thấy khoảng 77–79% có độ giống header cao. Đây vẫn không phải nhãn đúng/sai để tự nối.",
        "",
        "Một số tài liệu cần kiểm tra thủ công vì tên file và nội dung có dấu hiệu khác nhau:",
        "",
    ])
    for example in scope["scope_conflict_examples"][:12]:
        lines.append(
            f"- `{example['document_id']}`: tên file = {example['filename_scope']}, "
            f"dấu hiệu nội dung = {example['internal_marker_scope']} "
            f"({example['consolidated_mentions']} hợp nhất / {example['separate_mentions']} riêng)."
        )
    lines.extend([
        "",
        "### Bảng trùng và cùng khuôn",
        "",
        f"Có {analysis['duplicates']['exact_duplicate_group_count']:,} nhóm trùng hoàn toàn, chứa {analysis['duplicates']['exact_duplicate_instance_count']:,} bảng; sau khi che các con số, có {analysis['duplicates']['template_duplicate_group_count']:,} nhóm cùng khuôn, chứa {analysis['duplicates']['template_duplicate_instance_count']:,} bảng. Điều này giải thích vì sao tìm toàn cục dễ trả về nhiều bảng gần giống nhau. Không nên xóa trùng chỉ dựa trên khuôn vì cùng một mẫu có thể thuộc công ty, năm hoặc scope khác.",
        "",
        "## So sánh tìm kiếm bảng",
        "",
    ])
    for stage in STAGES:
        lines.append(f"### {stage}")
        lines.append("")
        lines.append("| Cách tìm | Top-1 | Top-5 | Top-10 | Số bảng cạnh tranh trung bình |")
        lines.append("|---|---:|---:|---:|---:|")
        for mode, metrics in retrieval["by_stage"][stage].items():
            lines.append(f"| {mode} | {percent(metrics['top1'])} | {percent(metrics['top5'])} | {percent(metrics['top10'])} | {metrics['mean_match_count']:.1f} |")
        lines.append("")

    lines.extend([
        "Cấu hình tốt nhất được chọn chỉ trên development là `table_only_metadata_filtered`. Nó đạt top-5 93% trên development nhưng giảm còn 89% trên untouched, dưới ngưỡng 95%. Metadata giúp rất rõ: số bảng cạnh tranh trung bình giảm từ hàng nghìn xuống khoảng 4–6. Ngược lại, ghép thêm context làm nội dung dài và nhiễu hơn, khiến cả top-1 lẫn top-5 giảm trong phép so sánh cùng điều kiện.",
        "",
        "## Bốn giả thuyết bị bác bỏ nói lên điều gì?",
        "",
        "1. **Dense trên CPU không phù hợp với ngân sách vòng này.** Phép đo 256 bảng dự báo khoảng 249 phút cho toàn kho, vượt xa giới hạn 30 phút. Điều này không chứng minh dense kém về chất lượng; chỉ chứng minh máy hiện tại không phù hợp để dựng đầy đủ theo cấu hình đã thử.",
        "2. **Scope từ tên file không tuyệt đối đáng tin.** Tỷ lệ 1,86% vượt ngưỡng 1%; 34 tài liệu phải được gắn cờ kiểm tra thay vì âm thầm tin tên file hoặc âm thầm sửa theo bộ dò.",
        "3. **Lexical retrieval chưa đạt mục tiêu 95%.** Ngay cả cấu hình tốt nhất cũng chỉ đạt top-5 89% trên nhóm chưa dùng. Cần xem các ca hụt và thử cải thiện nhỏ trước khi coi retrieval là đủ mạnh.",
        "4. **Context hiện tại làm retrieval xấu đi.** Không nên đưa nguyên context vào chỉ mục như hiện tại. Nếu thử lại, cần giới hạn hoặc chọn context có mục đích, rồi kiểm tra trên untouched.",
        "",
        "## Giới hạn cần giữ nguyên",
        "",
        "- 900 truy vấn retrieval được tạo từ nhãn dòng có sẵn trong bảng. Chúng đo khả năng tìm lại nguồn, không đo việc hiểu 1.012 câu hỏi ViFinQA.",
        "- Nhận diện bảng chính, bảng nối trang và scope là phép đo đại diện; chưa có nhãn tay toàn kho để gọi là độ chính xác.",
        "- Token số được kiểm tra theo tập giá trị nhìn thấy, chưa chứng minh đúng ý nghĩa tài chính, đúng đơn vị hoặc đúng phép tính.",
        "- Dense benchmark là phép đo tài nguyên trên máy hiện tại, không phải so sánh chất lượng với lexical.",
        "- Kiểm tra scope dựa trên số lần xuất hiện cụm tiêu đề trong 12.000 ký tự đầu. 34 ca xung đột là danh sách nghi vấn, chưa phải bằng chứng chắc chắn rằng BTC gắn nhãn sai.",
        "",
        "## Các giả thuyết vẫn còn thiếu trước thay đổi lớn",
        "",
        "- Cần một mẫu nhỏ do người đọc xác nhận: câu hỏi thật ↔ bảng thật. Đây mới là phép kiểm retrieval gần E2E; source-derived không thay thế được.",
        "- Cần đọc tay một mẫu ca scope xung đột và bảng nối trang để ước lượng độ chính xác của hai bộ dò.",
        "- Cần kiểm tra ngữ nghĩa số: dấu âm trong ngoặc, đơn vị nghìn/triệu/tỷ, cột năm, số đầu kỳ/cuối kỳ và phạm vi hợp nhất/riêng.",
        "- Cần xem lỗi top-5 của `table_only_metadata_filtered` theo nhóm nguyên nhân: nhãn dòng quá chung, bảng lặp, OCR sai, sai năm/scope hay truy vấn quá dài.",
        "",
        "## Quyết định trước khi điều chỉnh",
        "",
        "Có đủ bằng chứng để điều chỉnh **phần bao phủ dữ liệu, kiểm tra hoàn tất và chiến lược retrieval có metadata**. Chưa đủ bằng chứng để tự động nối bảng nhiều trang, tự suy kỳ/đơn vị, hoặc dùng kết quả source-derived như điểm E2E. Các thay đổi sau vòng này nên nhỏ, có thể bật/tắt và phải chạy lại trên nhóm `untouched_evaluation`.",
        "",
        "Thứ tự điều chỉnh hợp lý sau khi người dùng duyệt báo cáo:",
        "",
        "1. Bắt buộc receipt hoàn tất đủ 1.973 tài liệu / 146.246 bảng và từ chối index dạng prefix.",
        "2. Dùng table-only + lọc công ty/năm/scope làm baseline retrieval; giữ context ngoài chỉ mục cho bước kiểm tra sau truy hồi.",
        "3. Gắn cờ 34 tài liệu nghi vấn scope; không tự sửa và không dùng scope như bằng chứng duy nhất.",
        "4. Chưa bật tự nối bảng nhiều trang và chưa dựng dense toàn kho trên CPU.",
        "",
        "## Khả năng tái lập",
        "",
        f"- Asset build: {build['elapsed_seconds']:.1f} giây; lexical build: {lexical['elapsed_seconds']:.1f} giây.",
        f"- Dense benchmark: {json.dumps(dense, ensure_ascii=False, sort_keys=True)}",
        "- Mọi file kết quả đều có SHA-256 trong `manifest.json` của thư mục báo cáo.",
        "",
    ])
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--ticker-split", type=Path, required=True)
    parser.add_argument("--build-dir", type=Path, required=True)
    parser.add_argument("--raw-inventory", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--report-only", action="store_true")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = args.output_dir / "manifest.json"
    if args.report_only:
        if not manifest_path.exists():
            raise SystemExit(f"cannot refresh missing analysis: {manifest_path}")
        receipt = read_json(args.build_dir / "build_receipt_v1.json")
        lexical = read_json(args.output_dir / "lexical_build_v1.json")
        analysis = read_json(args.output_dir / "asset_analysis_v1.json")
        retrieval_report = read_json(args.output_dir / "retrieval_report_v1.json")
        dense = read_json(args.output_dir / "dense_cpu_benchmark_v1.json")
        hypotheses = read_json(args.output_dir / "hypothesis_results_v1.json")
        report_path = args.output_dir / "document_corpus_round2_report_vi.md"
        report_path.write_text(
            render_report(
                receipt=receipt,
                lexical=lexical,
                analysis=analysis,
                retrieval=retrieval_report,
                dense=dense,
                hypotheses=hypotheses,
            ),
            encoding="utf-8",
        )
        manifest = read_json(manifest_path)
        manifest["outputs"][report_path.name] = {
            "sha256": sha256_file(report_path),
            "size_bytes": report_path.stat().st_size,
        }
        write_json(manifest_path, manifest)
        print(json.dumps({"status": "REPORT_REFRESHED", "path": str(report_path)}, ensure_ascii=False))
        return
    if manifest_path.exists():
        raise SystemExit(f"refusing to overwrite completed analysis: {manifest_path}")

    protocol = read_json(args.protocol)
    ticker_split = read_json(args.ticker_split)
    receipt_path = args.build_dir / "build_receipt_v1.json"
    asset_path = args.build_dir / "full_table_assets_v1.jsonl"
    index_path = args.output_dir / "full_lexical_ablation_v1.sqlite"
    lexical_path = args.output_dir / "lexical_build_v1.json"
    if index_path.exists() and lexical_path.exists():
        lexical = read_json(lexical_path)
    else:
        lexical = build_lexical_research_index(asset_path, index_path)
        write_json(lexical_path, lexical)

    analysis, fidelity_rows, retrieval_queries = analyze_full_assets(
        asset_path=asset_path,
        raw_inventory_path=args.raw_inventory,
        ticker_split=ticker_split,
        protocol=protocol,
    )
    retrieval_rows, retrieval_report = evaluate_retrieval(index_path, retrieval_queries)
    try:
        dense = {"benchmark_completed": True, **benchmark_dense_cpu(asset_path, analysis["table_count"])}
    except Exception as exc:
        dense = {"benchmark_completed": False, "error": f"{type(exc).__name__}: {exc}"}

    receipt = read_json(receipt_path)
    hypotheses = evaluate_hypotheses(
        build_receipt=receipt,
        lexical=lexical,
        analysis=analysis,
        retrieval=retrieval_report,
        dense=dense,
    )
    outputs = {
        "asset_analysis_v1.json": analysis,
        "numeric_fidelity_samples_v1.jsonl": fidelity_rows,
        "retrieval_queries_v1.jsonl": retrieval_queries,
        "retrieval_results_v1.jsonl": retrieval_rows,
        "retrieval_report_v1.json": retrieval_report,
        "dense_cpu_benchmark_v1.json": dense,
        "hypothesis_results_v1.json": hypotheses,
    }
    for name, value in outputs.items():
        path = args.output_dir / name
        if name.endswith(".jsonl"):
            write_jsonl(path, value)
        else:
            write_json(path, value)
    report_path = args.output_dir / "document_corpus_round2_report_vi.md"
    report_path.write_text(
        render_report(
            receipt=receipt,
            lexical=lexical,
            analysis=analysis,
            retrieval=retrieval_report,
            dense=dense,
            hypotheses=hypotheses,
        ),
        encoding="utf-8",
    )
    output_files = [index_path, lexical_path, *[args.output_dir / name for name in outputs], report_path]
    manifest = {
        "protocol": "vifinqa_document_corpus_round2_analysis_manifest_v1",
        "inputs": {
            "protocol": {"path": str(args.protocol.resolve()), "sha256": sha256_file(args.protocol)},
            "ticker_split": {"path": str(args.ticker_split.resolve()), "sha256": sha256_file(args.ticker_split)},
            "build_receipt": {"path": str(receipt_path.resolve()), "sha256": sha256_file(receipt_path)},
            "assets": {"path": str(asset_path.resolve()), "sha256": sha256_file(asset_path)},
            "raw_inventory": {"path": str(args.raw_inventory.resolve()), "sha256": sha256_file(args.raw_inventory)},
        },
        "outputs": {path.name: {"sha256": sha256_file(path), "size_bytes": path.stat().st_size} for path in output_files},
        "source_contract": protocol["source_contract"],
    }
    write_json(manifest_path, manifest)
    print(json.dumps({"status": "ANALYZED", "tables": analysis["table_count"], "hypotheses": len(hypotheses), "output_dir": str(args.output_dir)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
