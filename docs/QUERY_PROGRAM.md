# QueryProgram shadow V1

## Mục tiêu và ranh giới

`QueryProgram` là IR có giới hạn cho câu hỏi tài chính gồm nhiều bước. Nó nằm
sau Formula EvidenceSet, không thay retrieval, không suy luận scope, không sửa
OCR và không tạo evidence mới.

V1 chỉ allow-list một mẫu có cấu trúc rõ ràng:

```text
Quick Ratio theo từng công ty
  → lọc nghiêm ngặt q < median(q)
  → argmax thay đổi Gross Margin có dấu
  → Interest Coverage của công ty thắng
```

Mỗi operand trong program vẫn là `operand_id` do Formula EvidenceSet khai báo
và phải đã bind tới exact raw V2 cell. Mapping `(entity, role, year) →
operand_id` được lưu trong program; executor không tự ghép ticker vào tên
operand và không được đọc OCR/raw table.

## Shadow-only contract

Artifact V1 có các cờ cố định:

```json
{
  "execution_mode": "shadow_only",
  "submission_eligible": false,
  "review_status_promotion_allowed": false
}
```

Shadow executor chỉ nhận các value đã coherent và grounded do caller cung cấp.
Nó fail-closed khi thiếu operand, mẫu số bằng 0, không có entity qua điều kiện
lọc, hoặc có tie ở bước xếp hạng. Kể cả khi arithmetic hoàn tất, output chỉ là
trace để test executor, không phải answer/label và không thể chuyển thành
`machine_calibrated`.

## Input provenance bắt buộc

Lệnh build yêu cầu `--bundle-dir` và xác thực manifest Formula Evidence trước
khi compile. Validator kiểm SHA của review items, raw tables, V2, V3, Formula
sidecar và các source-completion/entity-resolution sidecar được Formula V6
khai báo. Một Formula EvidenceSet partial hoặc không có coherent operand
bindings được xuất hiện trong artifact nhưng trạng thái là `shadow_blocked`.

```bash
python scripts/build_query_program_shadow.py \
  --bundle-dir ~/ViFinQA_review/run_full_metadata_support_v1 \
  --formula-evidence ~/ViFinQA_review/run_full_metadata_support_v1/formula_evidence_sets_context_v3_entity_titles_v1.jsonl \
  --output ~/ViFinQA_review/evaluation_metadata_support_v1/query_program_shadow_v1.jsonl
```

Output có manifest riêng chứa SHA của Formula EvidenceSet và manifest nguồn.
Artifact này cần được đăng ký là dependency của `formula_evidence` trong
workspace ArtifactRegistry.

## Kết quả canary hiện tại

Trên bundle hiện có, chỉ một Formula EvidenceSet khớp template. Đó là Q369
(HPG/HSG/MSR/NKG; Quick Ratio → thay đổi GPM → Interest Coverage). Program được
compile để kiểm tra contract, nhưng vẫn **không thực thi**: Formula definition
đang `review_required`, EvidenceSet `partial`, không có coherent bindings và
các operand không có common global scope. Đây là kết quả đúng theo fail-closed
policy, không phải thiếu answer được bù bằng suy luận.

## Điều kiện mở rộng

Chỉ đưa một QueryProgram ra khỏi shadow sau khi có một fixture canary chứng
minh cho từng stage: raw row/cell exact, cùng entity/year/scope, numeric parse
và expected trace. Trước khi đó, model chỉ được dùng để tìm candidate/route;
rule và provenance phải xác minh, executor Decimal mới tính.
