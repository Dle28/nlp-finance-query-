# Báo cáo nghiên cứu dòng, kỳ báo cáo, đơn vị và scope

Ngày chạy: 26/08/2026  
Artifact: `artifacts/research/exact_cell_research_v1_20260826_r4`

## Kết luận ngắn

Dense và hybrid đã đưa được hệ thống tới bảng cần kiểm tra. Vòng nghiên cứu này
cho thấy bước tiếp theo không phải là đổi model dense: vấn đề lớn nhất là đọc
đúng cột kỳ báo cáo trong header OCR.

Hệ thống đã tạo candidate dòng cho phần lớn bảng, nhưng chỉ một phần tư bảng có
một cột ghi rõ năm được hỏi. Do đó chưa thể tự động chuyển candidate bảng thành
ô số hoặc pandas query.

## Phạm vi đã nghiên cứu

Với ba bảng ưu tiên cho mỗi trong 1.232 tuyến chỉ tiêu–công ty–năm, nghiên cứu
đã kiểm tra:

1. Có dòng dữ liệu nào có nhãn gần với chỉ tiêu cần hỏi không.
2. Header có đúng một cột mang năm được hỏi không.
3. Header có nêu rõ đơn vị không.
4. Scope của bảng có khớp scope được hỏi không.
5. Các header “Năm nay”, “Số cuối năm” hoặc một cột số duy nhất có thể là một
   fallback để nghiên cứu tiếp không.

Mọi số tiền được ẩn trước khi ghi artifact. Kết quả chỉ là candidate nghiên cứu,
không phải evidence, answer, training data hoặc submission.

## Kết quả

| Nội dung | Số bảng | Tỷ lệ trên 3.696 bảng |
|---|---:|---:|
| Có candidate dòng | 3.603 | 97,5% |
| Không có dòng số khả dụng | 93 | 2,5% |
| Có đúng một cột ghi rõ năm | 959 | 25,9% |
| Có nhiều cột cùng mang năm | 496 | 13,4% |
| Không thấy năm rõ trong header | 2.148 | 58,1% |
| Header nêu được một đơn vị | 2.392 | 64,7% |
| Unit thiếu hoặc mâu thuẫn | 548 | 14,8% |
| Chỉ có `unit_hint`, chưa đủ kiểm chứng | 756 | 20,5% |
| Scope mâu thuẫn với câu hỏi | 18 | 0,5% |

Chỉ 833 bảng thỏa đồng thời: có dòng candidate, có một cột năm rõ ràng, có unit
header duy nhất và scope không mâu thuẫn. Đây vẫn chỉ là **candidate cấu trúc**:
chưa chứng minh dòng đúng về nghĩa, cột là value cell đúng, hay kết quả tính
toán đúng.

## Nghiên cứu fallback kỳ báo cáo

Trong 2.148 bảng không có năm rõ, có:

- 1.229 bảng có đúng một header kiểu “Năm nay” hoặc “Số cuối năm”.
- 105 bảng chỉ có một cột số và không có dấu hiệu “Năm trước/Số đầu năm”.
- 814 bảng còn mơ hồ hoặc không hỗ trợ được.

Khi kết hợp fallback hẹp này với dòng, unit và scope, có thêm 698 candidate cấu
trúc để review. Các candidate này được đánh dấu `RESEARCH_*` và **bị cấm** dùng
để tạo evidence hay answer. Chúng chỉ đặt giả thuyết cần kiểm tra bằng tiêu đề
nguồn và header exact-cell.

## Điều đã học được

- Retrieval bảng hiện đã đủ để chuyển trọng tâm sang cấu trúc dữ liệu.
- Candidate dòng rất dễ tạo, nhưng có thể sai nghĩa khi nhãn giống nhau ở nhiều
  thuyết minh. Vì vậy vẫn cần review hoặc exact-row validator.
- Header OCR thường nêu “Năm nay/Năm trước”, không nêu năm dương lịch. Đây là
  nguồn thiếu coverage lớn nhất.
- 93 bảng không có dòng số là tín hiệu cần lọc mục lục, phần chữ hoặc table OCR
  lỗi trước khi sinh candidate tài chính.
- Scope mismatch tuy ít nhưng nghiêm trọng: 18 candidate phải giữ ở trạng thái
  review, không được tự thay consolidated bằng separate.

## Mẫu review 150 tuyến

Mẫu cân bằng đã tạo sẵn:

- 50 tuyến `agreement_high_proxy`;
- 50 tuyến `standard_review`;
- 50 tuyến `hard_review`.

Mỗi gói có tối đa ba bảng, vị trí và hash nguồn, candidate dòng, header/kỳ,
unit và scope. Không có số tiền. Reviewer ghi một trong ba quyết định ở hệ thống
review bên ngoài artifact: `approve`, `reject`, hoặc `uncertain`.

Checklist cho mỗi candidate:

1. Bảng có đúng chủ đề tài chính không, hay là mục lục/phần chữ.
2. Scope consolidated/separate có đúng câu hỏi không.
3. Dòng candidate có đúng chỉ tiêu, không chỉ gần nghĩa.
4. Header có thực sự biểu diễn năm được hỏi không.
5. Unit có nằm trong chính header hoặc nguồn gốc bảng không.

Chỉ sau khi có quyết định `human_verified` và exact source-cell replay mới được
chuyển sang bước đọc số và pandas.

## File quan trọng

- `table_diagnostics_v1.jsonl`: kết quả dòng/kỳ/unit/scope cho 3.696 bảng.
- `row_candidates_v1.jsonl`: 16.565 candidate dòng, không có giá trị số.
- `stratified_review_sample_v1.jsonl`: 150 gói review đã được làm giàu, có thể
  đọc độc lập.
- `coverage_report_v1.json`: tổng hợp giả thuyết và số lượng.

## Bước nghiên cứu tiếp theo

1. Review thủ công mẫu 150 tuyến để tạo benchmark nội bộ đúng bảng–đúng dòng–
   đúng cột; không cần ghi answer.
2. So sánh exact-year header với fallback `Năm nay/Số cuối năm` trên mẫu đó.
3. Chỉ giữ fallback nếu không có false confidence trong mẫu review và source
   title/header xác thực được.
4. Dùng kết quả để xây exact-row/exact-column validator có thể replay.
5. Cuối cùng mới thử đọc raw cell, chuẩn hóa unit và thực thi pandas.

## Chạy lại

```bash
rtk env PYTHONPATH=.:src .venv/bin/python \
  scripts/research/build_exact_cell_research_v1.py \
  --config configs/research/exact_cell_research_v1.json \
  --hybrid-review-queue artifacts/research/hybrid_retrieval_analysis_v1_20260826_r4/hybrid_review_queue_v1.jsonl \
  --assets artifacts/research/document_corpus_round2_assets_v1_20260826_r2/full_table_assets_v1.jsonl \
  --output-dir artifacts/research/exact_cell_research_v1_NEW

rtk env PYTHONPATH=.:src .venv/bin/python \
  scripts/research/validate_exact_cell_research_v1.py \
  --artifact-dir artifacts/research/exact_cell_research_v1_NEW
```
