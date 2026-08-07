# Diagnostic run_001

Đây là snapshot nhỏ của diagnostic đầu tiên trên bundle schema v2 (60 câu).

Checked in:

- `diagnostic_summary.json`: tổng hợp số lượng risk / diagnosis.
- `manual_audit_queue.jsonl`: 12 canary/audit cases dùng để thiết kế và kiểm tra V3.

Không check in `review_bundle_diagnostics.jsonl` đầy đủ vì file lớn và có thể tái tạo deterministic từ review bundle gốc bằng:

```bash
python local/run_local_review_stage.py diagnose \
  --bundle-archive /path/to/vifinqa_review_bundle.tar.gz \
  --diagnostic-output data/diagnostics/run_001_regenerated \
  --audit-size 12
```

Các canary quan trọng:

- Q13: `ADJACENT_CONTEXT_HIT` — bảng đúng nằm ngay trước candidate bị retrieve do `context_before` leakage.
- Q53: `EVIDENCE_RISK` — candidate đúng concept nhưng direct evidence cũ dừng ở header, chưa nối tới `Số cuối năm` chứa giá trị.

V3 fix được mô tả ở `docs/REVIEW_FIX_V3.md`.
