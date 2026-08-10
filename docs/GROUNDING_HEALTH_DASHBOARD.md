# Grounding Health Dashboard V1

## Mục đích

Dashboard là audit read-only để xác định chính xác bottleneck trước khi train
hoặc thay retrieval. Nó join theo ID/UID đã có giữa machine review, Formula
EvidenceSet, Semantic Catalog và OCR Quality Profile; không dùng score để thay
rank, không tạo answer và không đổi review status.

```bash
python scripts/build_evaluation_dashboard.py \
  --bundle-dir ~/ViFinQA_review/run_full_metadata_support_v1 \
  --machine-reviews ~/ViFinQA_review/evaluation_metadata_support_v1/machine_reviews_1012_hierarchy_v3.jsonl \
  --formula-evidence ~/ViFinQA_review/run_full_metadata_support_v1/formula_evidence_sets_context_v3_entity_titles_v1.jsonl \
  --output ~/ViFinQA_review/evaluation_metadata_support_v1/grounding_health_dashboard_v1.json
```

Builder validate hash/contract của Formula V6, OCR Quality Profile V1 và
Semantic Catalog V1 trước khi ghi JSON atomically.

## Boundary

```json
{
  "read_only": true,
  "answer_eligible": false,
  "evidence_eligible": false,
  "training_eligible": false,
  "may_change_review_status": false
}
```

Các cross-tab là `family × review status`, `family × document role`, `family ×
OCR triage`, `family × formula completeness`, và provenance/status. Chúng là
evaluation metadata, không phải evidence.

## Snapshot hiện tại

Từ 1.012 machine reviews:

| Status | Số câu |
| --- | ---: |
| `machine_calibrated` | 62 |
| `machine_provisional` | 364 |
| `needs_human` | 586 |

Formula sidecar có 3 complete, 133 partial và 876 câu không có Formula
EvidenceSet (không phải lỗi join; các câu đó không thuộc formula sidecar). Tất
cả candidate UID trong review đều có Semantic Catalog và OCR profile tương
ứng.

Tín hiệu quan trọng từ `family × OCR triage`:

| Family | Không có candidate | Candidate normal | Candidate review-required |
| --- | ---: | ---: | ---: |
| conditional analytical | 64 | 44 | 3 |
| cross-entity comparison | 25 | 83 | 5 |
| direct lookup | 1 | 342 | 15 |
| multi-entity / period aggregation | 132 | 154 | 11 |
| ratio / derived | 8 | 82 | 5 |
| temporal change | 14 | 22 | 2 |

Kết luận vận hành: với snapshot này, khó khăn lớn nhất của các câu phức tạp là
**coverage/binding candidate**, không phải bulk OCR corruption. Vì vậy bước kế
tiếp nên ưu tiên source-completion có kiểm chứng và mở rộng QueryProgram từng
family; không nên chạy OCR correction tự động hay rebuild dense index.
