# OCR Quality Profile V1

## Mục tiêu

OCR Quality Profile là sidecar **quan sát** cho từng bảng. Nó trả lời “bảng
này có tín hiệu OCR/layout nào cần thận trọng?” thay vì cố trả lời “giá trị
đúng là gì?”. Profile được sinh từ immutable raw table qua V2 structure và V3
context; raw OCR string, số, hàng, cột và header provenance đều không bị sửa.

```text
raw table → V2 (lossless grid) → V3 (canonical header provenance)
                                      └→ OCR Quality Profile V1 (triage only)
```

Vì vậy profile không nằm trên đường evidence: `evidence_eligible=false`,
`training_eligible=false`, `may_repair_ocr=false` và
`may_change_candidate_rank=false`. Evidence binder vẫn phải xác nhận exact V2
row/cell và V3 header khi dùng số.

## Các signal được đo

- số lượng cell số source parse được / không parse an toàn;
- mã cảnh báo parser (`multiple_numeric_groups`, `invalid_decimal`, …) cùng
  tọa độ raw `(row_index, column_index)`, **không copy raw value** vào profile;
- coverage canonical source-header V3 cho cột số;
- layout cột số theo dòng. Cột `Mã số`/`Thuyết minh` bị loại khỏi phép so sánh;
  dòng thiếu một giá trị là subset của layout đầy đủ thì không bị coi là lệch.
  Chỉ layout không-nested mới là alignment anomaly.

Triage có ba mức:

| Action | Ý nghĩa | Hành động được phép |
| --- | --- | --- |
| `normal` | Chưa thấy tín hiệu profile cần chặn. | Vẫn phải grounding theo V2/V3 như cũ. |
| `review_required` | Có signal cụ thể nhưng có thể vẫn chứa cell tốt. | Ưu tiên exact-cell validation; không tự sửa. |
| `quarantine` | V3 blocked hoặc không có cell số source an toàn. | Không dùng để tự bind số; điều tra source/OCR trước. |

## Build và validate

```bash
python scripts/build_ocr_quality_profiles.py \
  --bundle-dir ~/ViFinQA_review/run_full_metadata_support_v1
```

Script validate manifest V2/V3 trước, ghi atomically
`ocr_quality_profiles_v1.jsonl` và manifest hash-bound, và không rebuild
Kaggle corpus, SQLite FTS, E5/FAISS hoặc OCR.

## Snapshot hiện tại

Với `run_full_metadata_support_v1` (29.509 bảng), V1 tạo:

| Triage | Bảng |
| --- | ---: |
| `normal` | 24.479 |
| `review_required` | 4.854 |
| `quarantine` | 176 |

Lý do không phải `normal` được đếm theo signal, nên có thể chồng lấp: 1.435
cột số thiếu canonical source header, 1.396 bảng có cell số unreliable, 493
bảng có layout số non-nested và 176 bảng không có cell số an toàn. Đây là
baseline diagnostic, chưa được dùng để alter retrieval hay labels.

## Bước kế tiếp

Semantic Catalog có thể dùng profile này làm metadata để hiển thị/gộp canary
evaluation, nhưng không được biến nó thành evidence truth hoặc hard-reject một
candidate còn exact cells tốt. Chỉ sau canary precision/recall mới xem xét một
retrieval penalty riêng, và penalty đó vẫn không thay thế grounding.
