# QueryProgram shadow V1

## Mục tiêu và ranh giới

`QueryProgram` là IR có giới hạn cho câu hỏi tài chính gồm nhiều bước. Nó nằm
sau Formula EvidenceSet, không thay retrieval, không suy luận scope, không sửa
OCR và không tạo evidence mới.

V1 chỉ allow-list hai mẫu có cấu trúc rõ ràng:

```text
Quick Ratio theo từng công ty
  → lọc nghiêm ngặt q < median(q)
  → argmax thay đổi Gross Margin có dấu
  → Interest Coverage của công ty thắng

CFO theo từng công ty và từng năm screening
  → chỉ giữ công ty có CFO > 0 ở mọi năm đã nêu
  → argmax (Lợi nhuận sau thuế / Doanh thu thuần) ở năm đích
  → xuất tỷ lệ của công ty thắng ở chế độ shadow
```

Mỗi operand trong program vẫn là `operand_id` do Formula EvidenceSet khai báo
và phải đã bind tới exact raw V2 cell. Mapping `(entity, role, year) →
operand_id` được lưu trong program; executor không tự ghép ticker vào tên
operand và không được đọc OCR/raw table. Trước khi chạy, selected bindings còn
phải khớp entity, năm và **một scope chung không rỗng**; executor không chọn
`consolidated`/`separate` thay EvidenceSet. Với Net Margin, tử số và mẫu số của
mỗi entity còn phải có cùng source unit đã khai báo.

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
Nó fail-closed khi thiếu operand, metadata binding không coherent, mẫu số bằng
0, không có entity qua điều kiện lọc, hoặc có tie ở bước xếp hạng. Kể cả khi
arithmetic hoàn tất, output chỉ là trace để test executor, không phải answer/
label và không thể chuyển thành
`machine_calibrated`.

## Input provenance bắt buộc

Lệnh build yêu cầu `--bundle-dir` và xác thực manifest Formula Evidence trước
khi compile. Validator kiểm SHA của review items, raw tables, V2, V3, Formula
sidecar và các source-completion/entity-resolution sidecar được Formula V6
khai báo. Một Formula EvidenceSet partial hoặc không có coherent operand
bindings được xuất hiện trong artifact nhưng trạng thái là `shadow_blocked`.
Riêng một EvidenceSet `partial` chỉ được chạy shadow khi mọi operand đã
selected exact, coverage `complete` và hai reason code còn lại chỉ là hand-off
đã biết: `formula_requires_stage_binding` và
`question_family_requires_composed_execution`.

```bash
python scripts/build_query_program_shadow.py \
  --bundle-dir ~/ViFinQA_review/run_full_metadata_support_v1 \
  --formula-evidence ~/ViFinQA_review/run_full_metadata_support_v1/formula_evidence_sets_context_v3_entity_titles_v1.jsonl \
  --output ~/ViFinQA_review/evaluation_metadata_support_v1/query_program_shadow_v1.jsonl
```

Output có manifest riêng chứa SHA của Formula EvidenceSet và manifest nguồn.
Artifact này cần được đăng ký là dependency của `formula_evidence` trong
workspace ArtifactRegistry.

Mỗi dòng output có `readiness` và `shadow_execution`. `shadow_execution` là
`null` nếu gate chưa thỏa. Manifest đếm tách `readiness_counts` và
`shadow_execution_counts`, nên không thể nhầm “compile được” với “đã chạy” hay
“được phép trả lời”.

## Kết quả canary hiện tại

Trên snapshot Formula EvidenceSet V6 hiện tại, có 5 Formula EvidenceSet khớp
hai template allow-list; 4 bị `shadow_blocked` và 1 `shadow_ready`. Artifact
ghi 1 `shadow_complete`, vẫn với `submission_eligible=false`.

- **Q369** (HPG/HSG/MSR/NKG; Quick Ratio → ΔGPM → Interest Coverage) chỉ
  compile để kiểm tra contract. Nó vẫn blocked: definition `review_required`,
  selected bindings rỗng và không có global scope chung. Audit source cho thấy
  35 bảng đã nằm trong bundle nhưng chưa bind, thêm 1 metric raw không tìm thấy;
  vì vậy source completion không phải cách sửa đúng.
- **Q551** (GEE/GEX/SAM; CFO dương 2022–2024 → NPM 2024) là canary chạy được:
  15 operand đã `cell_bound`, formula `defined`, và EvidenceSet đã chọn scope
  chung `consolidated`. Executor chỉ đọc `binding.parsed_value` của 15 cell đó,
  xuất stage trace để audit và không viết answer/label/training data.

Chi tiết audit và ranh giới của hai canary ở
[`COMPLEX_QUERY_CANARY.md`](COMPLEX_QUERY_CANARY.md).

## Điều kiện mở rộng

Chỉ đưa một QueryProgram ra khỏi shadow sau khi có một fixture canary chứng
minh cho từng stage: raw row/cell exact, cùng entity/year/scope, numeric parse
và expected trace. Trước khi đó, model chỉ được dùng để tìm candidate/route;
rule và provenance phải xác minh, executor Decimal mới tính.
