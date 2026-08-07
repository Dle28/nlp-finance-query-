# Review bundle V3 — fix boundary retrieval + evidence projection

## Vì sao cần V3

Diagnostic `run_001` trên bundle 60 câu cho thấy ba lỗi chính:

1. **Adjacent-table/context leakage**: một candidate bị retrieve vì `context_before` chứa nội dung của bảng đứng ngay trước nó. Q13 là canary rõ nhất: candidate hiện tại nói về bảng khác, nhưng `context_before` chứa nguyên bảng `Tiền và các khoản tương đương tiền`.
2. **Evidence header-only**: bảng đúng đã có trong Top-K nhưng `direct_evidence` chọn header thay vì dòng số cần đọc. Q53 là canary: header `Nguyên giá | Hao mòn lũy kế | Giá trị còn lại`, trong khi dòng cần review là `Số cuối năm | ... | 378.556.941.833`.
3. **Planner metric noise**: metric của direct lookup đôi lúc kéo theo ticker/tên công ty/ngày, làm row scoring ưu tiên các bảng có tên entity thay vì concept tài chính.

V3 xử lý ba lỗi này **không cần rebuild dense index**.

---

## Fix 1 — recover bảng đứng trước khi context bị leak

Khi một retrieved candidate có:

```text
metric overlap trong context_before cao
nhưng
metric overlap trong rows của chính candidate thấp
```

V3 dùng `(document_id, local_ordinal)` để lấy bảng ngay trước đó từ SQLite `assets`.

Candidate recovered được ghi provenance:

```json
{
  "candidate_source": "adjacent_previous_due_context",
  "parent_retrieval_rank": 11,
  "original_retrieval_rank": null,
  "local_ordinal": 5
}
```

Không đổi UID và không giả vờ candidate này được BM25/dense retrieve trực tiếp.

---

## Fix 2 — effective metric sạch hơn

Đối với `direct_lookup`, V3 ưu tiên concept trước marker entity.

Ví dụ:

```text
Giá trị còn lại của bất động sản đầu tư
của công ty mẹ IJC ...
```

trở thành:

```text
Giá trị còn lại của bất động sản đầu tư
```

thay vì metric chứa cả `IJC`, tên công ty và ngày.

Field mới:

```text
effective_metric
```

Planner gốc vẫn được giữ nguyên trong `question_plan` để audit.

---

## Fix 3 — evidence = table heading + column header + numeric value row

V2 chỉ chọn một row. Điều này sai với các bảng mà metric nằm ở heading/header còn giá trị nằm ở row `Số cuối năm`.

V3 tách:

```text
anchor_row_index
value_row_index
```

và `direct_evidence` có thể là:

```text
TABLE: 10. Bất động sản đầu tư
||
COLUMNS: Nguyên giá | Hao mòn lũy kế | Giá trị còn lại
||
VALUE: Số cuối năm | 417.860.288.970 | 39.303.347.137 | 378.556.941.833
```

Với bảng có metric nằm ở heading và số tổng nằm ở `TỔNG CỘNG`, V3 ưu tiên total row.

Mọi text vẫn lấy trực tiếp từ source context/table cells.

---

## Fix 4 — hiểu cue thời gian khi chọn value row

V3 nhận biết tối thiểu:

```text
cuối năm
31/12
31 tháng 12
đầu năm
01/01
```

Nếu question hỏi `cuối năm`, row `Số cuối năm` được ưu tiên hơn `Số đầu năm`.

---

# Chạy lại trên Kaggle — KHÔNG rebuild GPU

Nếu session/artifact dataset hiện vẫn có đủ:

```text
artifacts/table_assets.jsonl
artifacts/lexical_index.sqlite3
artifacts/dense.index
artifacts/dense_uids.jsonl
```

chỉ chạy:

```python
%cd /kaggle/working/AI_guru
!git pull --ff-only origin main

%run kaggle/export_review_bundle_v3.py \
    --top-k 20 \
    --max-review-candidates 40 \
    --neighbor-radius 1 \
    --force
```

Không dùng `--build-missing`. V3 chỉ validate + retrieve + recover neighbor + re-project evidence + export.

Output:

```text
/kaggle/working/vifinqa_review_bundle_v3.tar.gz
/kaggle/working/vifinqa_review_bundle_v3.tar.gz.sha256
/kaggle/working/vifinqa_review_handoff_v3.json
```

Tải cả 3 về local.

---

# Local: diagnostic run_002 trước khi cho agent auto-review

```bash
cd ~/Documents/AI_guru
git pull --ff-only origin main

python local/run_local_review_stage.py diagnose \
  --bundle-archive ~/Downloads/vifinqa_review_bundle_v3.tar.gz \
  --diagnostic-output data/diagnostics/run_002 \
  --audit-size 12
```

## Canary cần PASS

### Q13

Mong đợi:

```text
candidate_source = adjacent_previous_due_context
local_ordinal = 5
table topic/evidence có "Tiền" / "tương đương tiền"
direct evidence dùng TỔNG CỘNG chứ không chỉ row "Các khoản tương đương tiền"
```

Không được auto-label bảng kế bên.

### Q53

Mong đợi direct evidence chứa cả:

```text
Bất động sản đầu tư
Nguyên giá | Hao mòn lũy kế | Giá trị còn lại
Số cuối năm | ... | 378.556.941.833
```

Nếu hai canary này chưa pass thì chưa chạy baseline agents.

---

# Khi canary PASS

Chạy:

```bash
python local/run_local_review_stage.py baseline \
  --bundle-dir <EXTRACTED_V3_BUNDLE_DIR> \
  --seed-size 12
```

Bạn review seed nhỏ, sau đó calibrate và chỉ xử lý `needs_human`.

---

# Root fix dài hạn

Nguồn gốc boundary leak nằm ở corpus/index hiện tại: `search_text` có cả raw `context_before`, và `context_before` đôi khi chứa toàn bộ bảng đứng trước. Fix triệt để là chỉ index **current table heading** thay cho raw previous-table HTML/text.

Fix này cần rebuild `table_assets -> lexical -> dense`, nên chưa làm trong vòng repair hiện tại. V3 neighbor recovery cho phép kiểm tra/review trước mà không đốt lại GPU.
