# Local Diagnostic — kiểm tra review bundle trước khi auto-review

Mục tiêu của bước này là **không gắn nhãn ngay**. Ta kiểm tra bundle Kaggle vừa tải về để biết lỗi nằm ở retrieval, evidence extraction hay planner trước khi cho các reviewer agent tự xử lý 60 câu.

## 1. Input

Bạn cần file đã tải từ Kaggle:

```text
vifinqa_review_bundle.tar.gz
```

Có thể chạy trực tiếp trên archive, không bắt buộc giải nén trước.

## 2. Pull code local

```bash
cd ~/Documents/AI_guru
git pull --ff-only origin main
python -m pip install -e .
```

## 3. Chạy diagnostic

Ví dụ archive nằm trong `~/Downloads`:

```bash
python local/diagnose_review_bundle.py \
  --bundle-archive ~/Downloads/vifinqa_review_bundle.tar.gz \
  --output-dir data/diagnostics/run_001 \
  --audit-size 12 \
  --force
```

Nếu đã giải nén bundle:

```bash
python local/diagnose_review_bundle.py \
  --bundle-dir ~/ViFinQA_review/run_001 \
  --output-dir data/diagnostics/run_001 \
  --audit-size 12 \
  --force
```

## 4. Output

```text
data/diagnostics/run_001/
├── diagnostic_summary.json
├── review_bundle_diagnostics.jsonl
├── review_bundle_summary.csv
└── manual_audit_queue.jsonl
```

### `diagnostic_summary.json`

Tổng số câu theo loại rủi ro.

### `review_bundle_summary.csv`

File dễ mở bằng spreadsheet. Mỗi dòng là một question với:

- diagnosis;
- risk;
- candidate rank hệ thống cho là đáng xem nhất;
- table topic;
- concept coverage;
- direct evidence hiện tại;
- suggested numeric evidence row;
- planner warnings.

### `review_bundle_diagnostics.jsonl`

Chi tiết toàn bộ Top-K của cả 60 câu. Dùng khi cần debug sâu.

### `manual_audit_queue.jsonl`

Queue nhỏ để người review kiểm tra trước. Mặc định 12 câu. Q13 và Q53 được giữ làm canary nếu có trong bundle.

## 5. Ý nghĩa diagnosis

### `ADJACENT_CONTEXT_HIT`

Concept đúng xuất hiện trong `context_before` cũ nhưng không nằm trong `rows` của candidate UID.

Ví dụ Q13 có thể rơi vào trường hợp này: bảng tiền và tương đương tiền nằm ngay trước bảng candidate, nên context có answer nhưng candidate table thực tế lại là bảng phải thu.

**Không được auto-label candidate UID này.** Đây là dấu hiệu boundary / adjacent-table retrieval cần sửa.

### `RETRIEVAL_RISK`

Dùng chủ yếu cho `direct_lookup`. Không candidate Top-K nào cover đủ concept cốt lõi của query.

**Không gắn `No relevant`.** Chỉ hiểu là Top-K hiện tại chưa đủ tin cậy.

### `EVIDENCE_RISK`

Bảng có vẻ đúng nhưng `direct_evidence` đang là header hoặc row yếu. Script sẽ thử đề xuất numeric neighbor tốt hơn.

Ví dụ query hỏi giá trị cuối năm, row header là:

```text
Nguyên giá | Hao mòn lũy kế | Giá trị còn lại
```

script có thể đề xuất:

```text
Số cuối năm | 417.860.288.970 | 39.303.347.137 | 378.556.941.833
```

### `PLANNER_RISK`

Planner metric chứa ticker, year, date hoặc entity boilerplate. Không dùng planner metric như gold semantic target.

### `COMPLEX_FAMILY_REVIEW`

Câu thuộc temporal / ratio / comparison / aggregation / conditional. Một table đơn lẻ không đủ để kết luận retrieval fail. Chuyển sang multi-agent review theo operands.

### `AMBIGUOUS_TOPK`

Hai candidate gần nhau, cần challenger hoặc human.

### `LOOKS_REVIEWABLE`

Candidate tương đối coherent. Vẫn chưa phải gold; chỉ là case phù hợp để machine first-pass.

## 6. Trình tự sau diagnostic

Không chạy `baseline` ngay nếu Q13/Q53 hoặc audit queue cho thấy lỗi hệ thống rõ ràng.

```text
review bundle
    ↓
diagnose_review_bundle.py
    ↓
manual_audit_queue (~12)
    ↓
┌───────────────────────────────┐
│ lỗi evidence only             │ -> sửa evidence projection local
│ lỗi planner                   │ -> sửa planner / re-export retrieval
│ lỗi adjacent/boundary         │ -> sửa table boundary/retrieval
│ candidate set nhìn ổn         │ -> chạy multi-agent baseline
└───────────────────────────────┘
```

Nếu audit nhìn ổn để tiếp tục:

```bash
python local/run_local_review_stage.py baseline \
  --bundle-dir ~/ViFinQA_review/run_001 \
  --seed-size 12
```

Sau đó chỉ review human seed nhỏ rồi mới calibrate.

## 7. Quy tắc an toàn

Diagnostic **không tạo gold label** và không thay đổi `retriever_verified_60.jsonl`.

Các trạng thái risk chỉ là routing signal. `RETRIEVAL_RISK` và `ADJACENT_CONTEXT_HIT` tuyệt đối không được chuyển thẳng thành `No relevant`.
