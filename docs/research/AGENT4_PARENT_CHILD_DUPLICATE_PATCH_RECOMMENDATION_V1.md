# Khuyến nghị patch riêng cho Agent 4

Phạm vi này chỉ là khuyến nghị tích hợp gate vào route/binding của Agent 4. Không sửa source-period module của Agent 1 và không chuyển artifact nghiên cứu thành answer, evidence, training hay submission.

## 1. Gate phân cấp dòng

Sau discovery và trước exact-cell binding, không coi `row_label` là khóa duy nhất. Với bảng có mã dòng:

- xác nhận `row_label_column_index`, `row_prefix`, `row_code` và `row_profile.role=data`;
- suy ra parent bằng dòng trước gần nhất có prefix level thấp hơn;
- với dòng tổng, xác nhận child trực tiếp có đúng mã và nhãn chi tiết kỳ vọng;
- nếu câu hỏi hỏi tổng, loại candidate có cùng nhãn nhưng là child/detail;
- nếu không xác nhận được parent hoặc có từ hai parent hợp lý, fail-closed.

Q98/Q340 cho thấy `Hàng tồn kho` xuất hiện ở cả dòng tổng và dòng `1. Hàng tồn kho`; gate phải giữ cả cấu trúc `IV.`/mã tổng và `1.`/mã chi tiết trong provenance, không dựa vào retrieval rank.

## 2. Gate document scope và nội dung OCR

Đối chiếu độc lập:

1. `document_id` và scope khai báo;
2. heading/title scope quan sát được trong vùng OCR gần `char_start` của bảng;
3. `source_sha256` đã kiểm tra;
4. `table_sha256` và UID của bảng.

Nếu scope khai báo và scope quan sát không khớp, trả `DOCUMENT_SCOPE_CONTENT_CONFLICT` và quarantine. Không dùng suffix file để sửa hoặc đoán lại document. Q98 phải bị khóa với dữ liệu hiện tại vì tên `separate`/`consolidated` không nhất quán với heading nội dung.

## 3. Gate duplicate provenance

Nhóm duplicate khi cùng table/row fingerprint hoặc cùng `table_sha256`, nhưng khác `document_id` hoặc `source_sha256`. Khi nhóm có nhiều bản mà không có marker authoritative/primary trong manifest nguồn:

- trả `DUPLICATE_PROVENANCE_UNRESOLVED`;
- không chọn bản đầu tiên, bản có rank cao hơn hoặc bản có tên đẹp hơn;
- giữ source/table hashes và diff descriptor để tái lập;
- chỉ cho phép tiếp tục sau khi có authority ngoài nhóm duplicate được ghi rõ.

Q104 phải giữ blocked vì hai bản MBB separate có target table cùng hash nhưng source hash/document ID khác và OCR toàn file khác.

## 4. Gate period/table/function

Sau các gate trên mới kiểm tra `table_function`, `table_section`, report heading, unit và canonical header. Cột kỳ phải là một cột duy nhất và phải có numeric profile đáng tin cậy cho đúng row. Bảng thuyết minh có cùng metric chỉ là explanatory competitor; không được thay thế statement row nếu câu hỏi là direct lookup.

Q340 là mẫu có thể chuyển qua candidate-only: đúng document PVT separate, đúng balance-sheet table, đúng dòng mã tổng, child mã chi tiết đã xác nhận, và chỉ một cột `Số cuối năm`. Đây vẫn chưa phải quyền evidence/answer.

## 5. Contract và test bắt buộc

Patch không được thêm phê duyệt thủ công hoặc LLM vào quyền cấp phép. Mọi output trung gian phải giữ contract:

```json
{
  "candidate_only": true,
  "evidence_eligible": false,
  "may_materialize_answer": false,
  "training_eligible": false,
  "submission_eligible": false,
  "promotion_allowed": false
}
```

Bổ sung test tối thiểu cho:

- cùng nhãn nhưng parent/detail khác mã và prefix;
- thiếu hoặc sai immediate parent;
- duplicate cùng table hash nhưng khác source hash;
- duplicate OCR khác nội dung nhưng không có primary basis;
- document scope khai báo khác heading OCR;
- retrieval rank thay đổi không làm thay đổi quyết định gate;
- artifact không chứa numeric cell value, answer hoặc submission payload.

Artifact nghiên cứu và bằng chứng chi tiết: `artifacts/research/parent_child_duplicate_research_v1_20260827_r1/`.
