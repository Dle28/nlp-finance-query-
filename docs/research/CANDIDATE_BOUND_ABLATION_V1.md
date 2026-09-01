# Candidate-bound source replay ablation V1

Ngày: 2026-08-30  
Phạm vi: 1.012 câu ViFinQA; route direct lookup có source boundary lấy từ
candidate navigation mạnh, nhưng giá trị vẫn phải replay từ structured table
hiện tại.

## Kết luận kiểm soát

Vòng này so sánh cùng một pipeline khi tắt/bật riêng
`source_first_candidate_bound_v1`. Route được phép thử tối đa candidate hạng
1–3 có `review_score >= 0,60`, trước hết trong đúng table rồi trong cùng
document/scope. Nó chỉ được nhận kết quả sau contextual source replay; rank,
review score và model score không cung cấp giá trị số.

Kết quả:

- 6 candidate-bound proposal replay thành công; 4 ở table boundary và 2 ở
  document boundary.
- Chỉ 3 record được chọn vào output vì 3 record còn lại vẫn bị route ưu tiên
  khác giữ lại.
- 3 answer records thay đổi: Q102, Q141, Q306; query replay của cả hai arm
  vẫn khớp answer.
- Hai arm đều có 999 answer khác 0 trên 1.012 record. Đây là kết quả source
  binding/candidate coverage, chưa phải Answer Accuracy hoặc Execution
  Accuracy chính thức.

## Snapshot và A/B contract

Snapshot immutable dùng cho hai arm:

- root: `/tmp/vifinqa-lane-ab.tDxDZU`
- builder SHA-256: `bae9fcd35008b3cb542fba58d0c12b15ee2bc71aab9db66f457b819987b0b33f`
- source-first SHA-256:
  `1b1318143d611a7abbed2ee7d5cfa1c4ec8581bba2c48bae35af7279f65bc2f2`
- structured table asset SHA-256:
  `617ae044499907cf302519b7ff6fb96fb86ab70ff08e6fdcb0e6a3f8df5251f7`
- source-line map: 146.246/146.246 entries, missing 0, fallback 0.

Control và variant dùng cùng questions, review bundle, full structured corpus,
replay, source-line map, direct-evidence replay, research candidates,
route-overlay, model-answer candidates và candidate-validity model. Chỉ khác
biệt có chủ đích là `--disable-source-first-candidate-bound` ở control.
Period-neighbor, temporal, conditional-temporal và cross-entity source-first
đều bị tắt trong cả hai arm.

Artifacts:

- control report:
  `artifacts/runs/vifinqa_answer_optimization_20260830/candidate_bound_conditional_ab_v1/control/submission/build_report.json`
- candidate report:
  `artifacts/runs/vifinqa_answer_optimization_20260830/candidate_bound_conditional_ab_v1/candidate_bound/submission/build_report.json`
- control submission:
  `artifacts/runs/vifinqa_answer_optimization_20260830/candidate_bound_conditional_ab_v1/control/submission/submission.json`
- candidate submission:
  `artifacts/runs/vifinqa_answer_optimization_20260830/candidate_bound_conditional_ab_v1/candidate_bound/submission/submission.json`
- control ZIP:
  `artifacts/runs/vifinqa_answer_optimization_20260830/candidate_bound_conditional_ab_v1/control/submission.zip`
- candidate ZIP:
  `artifacts/runs/vifinqa_answer_optimization_20260830/candidate_bound_conditional_ab_v1/candidate_bound/submission.zip`

Cả hai report đều ghi `changed_during_build=false`, 1.012 records, validation
`valid=true`, 1.012 queries replayed và `errors=[]`. Verification vẫn là
`PARTIAL=1.010`, `UNRESOLVED=2`, không có strict certificate. Cả hai ZIP đều
qua `unzip -t` không lỗi.

## Ba answer changes được replay

| Q | Control | Candidate-bound | Source replay | Nhận định |
|---:|---:|---:|---|---|
| 102 | `-232221` | `933246` | `MBB_financial_statements_2018_consolidated`, dòng `Tại ngày cuối năm`, cột `Quyền sử dụng đất`, đơn vị triệu đồng | Candidate-bound loại cell dòng lưu chuyển tiền của control; scope câu không ghi rõ nên vẫn cần semantic review. |
| 141 | `124747.488` | `299716.827` | `HAG_financial_statements_2021_separate`, dòng `TỔNG CỘNG` của bảng trái phiếu thường, đơn vị ngàn VND | Khớp exact machine candidate UID và đúng total bond row trong raw corpus. |
| 306 | `2977510` | `156564919` | `EIB_financial_statements_2020_separate`, bảng tài sản tài chính, tổng giá trị ghi sổ, đơn vị triệu đồng | Replay đúng bảng tổng tài sản tài chính; machine candidate cũ cùng document nhưng là bảng risk row khác, nên chưa coi là gold. |

Các giá trị candidate-bound được tính từ cell hiện tại trong full structured
asset và được ghi lại trong `data/q0102_evidence.csv`,
`data/q0141_evidence.csv`, `data/q0306_evidence.csv`; không copy từ retrieval
text. Q102 và Q306 là các case nên ưu tiên human/gold review vì table boundary
và semantics của target chưa được gold xác nhận.

## Navigation proxy

Đối chiếu với machine review hierarchy V3 (silver navigation proxy, không phải
gold):

| Segment | Control UID | Candidate-bound UID | Control document | Candidate-bound document |
|---|---:|---:|---:|---:|
| `machine_calibrated` (62) | 62/62 (100%) | 62/62 (100%) | 62/62 (100%) | 62/62 (100%) |
| `machine_provisional` (364) | 144/364 (39,560%) | 145/364 (39,835%) | 275/364 (75,549%) | 275/364 (75,549%) |
| `needs_human` (586) | 58/586 (9,898%) | 58/586 (9,898%) | 186/586 (31,741%) | 187/586 (31,911%) |

Tín hiệu tích cực nhỏ là +1 target UID ở `machine_provisional` và +1 target
document ở `needs_human`, trong khi calibrated segment không đổi. Không có
official scorer/gold answer local để chuyển các proxy này thành score claim.

## Disposition và backlog

Giữ route ở `authorized_best_effort_submission_candidate` với
`promotion_allowed=false`. Không đưa 3 answer changes vào strict VERIFIED,
training hay release chỉ dựa trên candidate-bound replay. Thứ tự kiểm tra
tiếp theo:

1. Q102: xác nhận scope và cột `Quyền sử dụng đất` của bảng tài sản cố định vô
   hình.
2. Q141: xác nhận total bond row có đúng ý “tổng trái phiếu thường” trong
   competition target.
3. Q306: xác nhận “tổng số tài sản tài chính” là tổng giá trị ghi sổ, không
   phải một risk exposure row cùng document.

Vòng tiếp theo cần đo riêng `conditional_temporal` trên cùng contract; không
hạ global semantic threshold để đổi lấy coverage.

## Verification

```text
.venv/bin/python -m pytest -q tests/e2e/test_source_first_lookup.py tests/e2e/test_submission_integration.py
62 passed
```

