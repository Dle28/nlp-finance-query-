# Formula-aware EvidenceSet review

## Mục tiêu

Câu hỏi hệ số, tỷ lệ tăng trưởng và chỉ tiêu suy ra không thể được xác minh chỉ
bằng một candidate “có vẻ liên quan”. Review unit là một `EvidenceSet`: công
thức có định nghĩa rõ, đủ operand và mỗi operand trỏ về đúng dòng nguồn.

Lớp này hỗ trợ review retrieval; nó chưa tính answer và không tự tạo gold.

## Luồng hiện tại

```text
question
→ controlled formula rule
→ required operand slots (metric + period + role)
→ candidate matching
→ exact source rows + column labels per operand
→ complete | partial | missing
→ human confirms formula when required
```

Formula rules nằm trong `src/finance_query/financial_metrics.py`. Widget dùng
chúng để trình bày ngữ cảnh và lưu provenance, không dùng LLM để tự viết công
thức hay evidence.

## Contract lưu review

Với câu được nhận diện, record review có thêm:

```text
formula_spec
formula_confirmed
operand_coverage
```

Mỗi phần tử coverage giữ candidate `uid`, `rank`, match score và `source_rows`.
Mỗi source row gồm `row_index`, toàn bộ exact cells và `column_labels` lấy từ
sidecar V2. Trường hợp metric nằm ở exact heading và số nằm ở total/value row
được ghi rõ bằng `binding=context_heading_plus_value_row`.

Không được thay exact cells bằng câu diễn giải.

## Gate hoàn chỉnh

EvidenceSet chỉ complete khi:

1. mọi required operand có ít nhất một grounded candidate;
2. metric và period của operand cùng khớp context nguồn;
3. definition không `ambiguous`;
4. human bật xác nhận công thức;
5. các candidate được chọn thuộc cùng entity/scope hoặc khác biệt đã được
   kiểm tra có chủ đích.

Grid `legacy_bundle_rows` không đủ điều kiện complete. Widget ẩn quick numeric
view, khóa `Accept machine` và hạ lựa chọn có số liệu xuống partial cho đến khi
sidecar V2 được dựng và UID/hash đã được xác minh.

Nếu có positive table nhưng chưa đủ điều kiện, widget lưu
`human_verified_partial`. Partial evidence có ích cho vòng review sau nhưng
không trở thành training gold cho toàn câu.

Với grid V2 đã xác minh, calibrator có thể dùng exact selected candidates của
partial review như positive-only evidence. Các candidate còn lại không có nhãn
negative và partial vẫn không được đưa vào final retrieval training export.

## Formula được hỗ trợ có kiểm soát

Hiện có rule cho percentage change, ROA/ROE, LDR, current ratio, quick ratio,
net service result, net finance result và một số tỷ trọng tài chính xuất hiện
trong tập câu hỏi. Quick ratio và một số tỷ trọng có
`definition_status=review_required`; dividend investment yield là `ambiguous`
nếu câu hỏi không chỉ rõ cơ sở giá trị khoản đầu tư.

Không match rule thì UI giữ workflow table review bình thường; không suy đoán
công thức mới.

Câu có lọc/xếp hạng nhiều giai đoạn không được rút thành keyword công thức đầu
tiên. Q369 hiện có controlled plan riêng:

```text
Quick Ratio theo 4 entity
→ lọc nghiêm ngặt dưới median
→ GPM 2022/2023 cho các entity còn lại
→ argmax của sai phân có dấu
→ Interest Coverage của entity thắng
```

Mỗi stage có EvidenceSet theo entity, ticker, kỳ và allowed table function.
Planner mang `execution_status=stage_binding_required`, nên vẫn fail closed nếu
Top-K thiếu bất kỳ stage nào. Các dạng nhiều giai đoạn chưa có controlled rule
tiếp tục mang `multi_stage_selection_unresolved` và luôn partial/needs-human.

Khi dựng review ledger, `scripts/build_review_ledger.py` đọc sidecar V2 nếu có
và kiểm tra lại UID/rank, exact row cells và column labels của mọi operand.
Coverage bị sửa tay hoặc không khớp source làm ledger dừng thay vì âm thầm nhận
label complete.

## Giới hạn có chủ đích

- Hiện binding dừng ở exact row + column labels; exact-cell binding và unit
  resolver là bước kế tiếp.
- OCR text vẫn có thể sai ký tự/số. V2 sửa cấu trúc HTML, không sửa OCR.
- Một bảng chứa nhiều period có thể hỗ trợ nhiều operand qua cùng một row;
  reviewer phải đọc column labels trước khi xác nhận.
- Candidate không map vào operand bị ẩn khỏi quick numeric view, nhưng full
  grid nguồn luôn còn để human kiểm tra override.

## Chạy local

Sau khi cập nhật code, tái tạo sidecar context V2 rồi mở widget:

```bash
python local/run_local_review_stage.py repair-tables \
    --bundle-dir ~/ViFinQA_review/run_002 \
    --repair-force
```

Trong Jupyter:

```python
%run local/review_bundle_widget.py \
    --bundle-dir ~/ViFinQA_review/run_002 \
    --machine-reviews data/labels/machine_reviews_60.jsonl \
    --queue data/labels/human_seed_queue_12.jsonl \
    --output data/labels/retriever_verified_60.jsonl
```

Không cần rebuild Kaggle corpus, lexical index hoặc dense index.
