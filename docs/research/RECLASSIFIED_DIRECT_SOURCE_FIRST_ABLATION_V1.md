# Reclassified direct source-first ablation

Ngày: 2026-08-30

## Kết luận

Một nhánh nhỏ đã được thêm cho các plan bị compiler gắn
`multi_entity_or_period_aggregation` nhưng thực tế chỉ có một mã và một năm.
Nhánh không dùng Question-ID allowlist; nó nhận diện theo các mẫu nghiệp vụ
đã quan sát được (tổng tài sản, vốn chủ sở hữu, lợi thế thương mại, cổ phiếu
bình quân gia quyền và các khoản phải thu), sau đó gọi lại source-first row /
period / unit replay hiện hữu.

Kết quả A/B cô lập:

- 9 câu đủ điều kiện; 8 câu resolve được từ structured corpus.
- 6 câu đổi giá trị answer; 2 câu chỉ đổi tier vì answer đã đúng giá trị.
- Q323 bị giữ `UNRESOLVED` do duplicate source chưa có tie-break đủ chắc chắn.
- 8 source cell đều được independent replay từ full corpus; UID, row, column,
  raw cell, unit transform và source-line map đều khớp.
- Đây là `authorized_best_effort_submission_candidate`, không phải
  `VERIFIED`: `promotion_allowed=false`, verifier local không có authority và
  workspace không có gold answer/official scorer.

## Implementation contract

Code nằm trong
`scripts/e2e/build_competition_submission_v1.py`:

- protocol: `source_first_reclassified_direct_v1`
- flag A/B: `--disable-source-first-reclassified-direct`
- eligibility: family multi, đúng một ticker, đúng một report year, operation
  đơn giản và không có grammar population/comparison.
- answer authority: current structured-table Decimal replay.
- scope prior: chỉ các mẫu `total_assets`/`equity` thiếu scope mới dùng
  `consolidated`; thông tin này được ghi rõ trong diagnostics và không được
  nâng thành semantic certificate.
- metric mapping là family/pattern-level, không phải sửa riêng từng ID.

Unit regression sau thay đổi:

```text
58 passed in 0.36s
```

## Isolated A/B

Control và variant dùng cùng snapshot/input:

- snapshot: `/tmp/vifinqa-reclassified-ab.yWKuj1`
- builder SHA-256: `05d0d3e880d2a40af88017ecd61e0dc01dfdaa244fb498e6d1925ab9441b02c0`
- `source_first_lookup.py` SHA-256:
  `1b1318143d611a7abbed2ee7d5cfa1c4ec8581bba2c48bae35af7279f65bc2f2`
- full table asset: 146,246 tables;
  SHA-256 `617ae044499907cf302519b7ff6fb96fb86ab70ff08e6fdcb0e6a3f8df5251f7`
- source-line map: 146,246 entries;
  SHA-256 `533cc779f9565f43dfc961ad03d7bdff65adbd3728ee7a60b61651d495c4c615`
- cả hai report: `changed_during_build=false`, `question_count=1012`,
  `validation.records=1012`, `queries_replayed=1012`, `errors=[]`, map
  coverage 146,246/146,246 và `unzip -t` pass.

Artifacts:

- control:
  `artifacts/runs/vifinqa_answer_optimization_20260830/reclassified_direct_ab_v1/control/submission.zip`
- variant:
  `artifacts/runs/vifinqa_answer_optimization_20260830/reclassified_direct_ab_v1/variant/submission.zip`
- variant report:
  `artifacts/runs/vifinqa_answer_optimization_20260830/reclassified_direct_ab_v1/variant/submission/build_report.json`
- variant diagnostics:
  `artifacts/runs/vifinqa_answer_optimization_20260830/reclassified_direct_ab_v1/variant/submission/diagnostics.jsonl`

Variant telemetry:

```text
questions_considered=9
questions_resolved=8
questions_unresolved_or_ambiguous=1
kind_total_assets=4
kind_equity=1
kind_goodwill=1
kind_weighted_shares=2
kind_receivables=1
questions_with_inferred_scope=4
```

## Prediction changes

`control` tắt lane mới; `variant` chỉ bật lane mới, còn candidate-bound,
conditional-temporal, temporal và cross-entity đều tắt để attribution sạch.

| ID | Control | Variant | Source replay |
|---:|---:|---:|---|
| 36 | `51603.29` / semantic | `624529.172455` | VRE consolidated, `Lợi thế thương mại`, row 24, col 3 |
| 92 | `0.002279945345` / semantic | `12.139247381785` | SSH consolidated, `TỔNG CỘNG TÀI SẢN`, row 22, col 4 |
| 123 | `12901.971536538` / semantic | `7152.567156682` | SSI consolidated, `I. Vốn chủ sở hữu`, row 22, col 3 |
| 143 | `0.000428061985` / semantic | `5.984081185909` | FIT consolidated, `TỔNG CỘNG TÀI SẢN`, row 38, col 5 |
| 204 | `2089651574` / semantic | `2089651574` / reclassified | VNM consolidated, weighted-share row 4, col 1 |
| 206 | `12.78012222792` / semantic | `611.89346991646` | GEX consolidated, `TỔNG CỘNG TÀI SẢN`, row 29, col 4 |
| 210 | `1741476075` / semantic | `1741476075` / reclassified | VNM consolidated, weighted-share row 4, col 1 |
| 258 | `1.373987298256` / semantic | `39.957271617596` | GVR separate, `TỔNG CỘNG TÀI SẢN`, row 23, col 3 |

Q323 được nhận diện là `receivables` nhưng không resolve sau duplicate-source
gate, vì vậy không đổi answer.

## Independent replay

Independent checker đọc lại từng UID trong
`full_table_assets_v1.jsonl`, lấy raw cell tại diagnostics, tính lại
`raw_decimal * source_multiplier / requested_divisor`, đọc lại CSV evidence
và kiểm tra UID trong source-line map. Tất cả 8 dòng đều pass:

| ID | Raw cell | Divisor | Recomputed answer | Map line |
|---:|---:|---:|---:|---:|
| 36 | `624.529.172.455` | `1,000,000` | `624529.172455` | 309 |
| 92 | `12.139.247.381.785` | `1,000,000,000,000` | `12.139247381785` | 230 |
| 123 | `7.152.567.156.682` | `1,000,000,000` | `7152.567156682` | 227 |
| 143 | `5.984.081.185.909` | `1,000,000,000,000` | `5.984081185909` | 262 |
| 204 | `2.089.651.574` | `1` | `2089651574` | 1837 |
| 206 | `61.189.346.991.646` | `100,000,000,000` | `611.89346991646` | 351 |
| 210 | `1.741.476.075` | `1` | `1741476075` | 1688 |
| 258 | `39.957.271.617.596` | `1,000,000,000,000` | `39.957271617596` | 222 |

Các dòng trong bảng trên là replay/provenance evidence; chúng không chứng
minh rằng mọi scope prior hoặc semantic interpretation trùng gold ẩn.

## Score status và quyết định

Không có official answer scorer/gold trong workspace, nên chưa thể báo
`Answer Accuracy` tăng bao nhiêu. Số đo có thể xác nhận hiện tại là:

- answer-value delta trong isolated A/B: `+6` candidate answers, `+2` tier-only
  changes;
- source/replay/coordinate delta: `8/8` pass;
- strict verification delta: `0` — tất cả vẫn `PARTIAL`, không có certificate.

Lane nên được giữ trong best-effort candidate path vì nó thay thế các giá trị
semantic rõ ràng sai bằng cell nguồn có row/column/unit replay. Không nên gọi
đó là leaderboard gain trước khi nộp cùng một ZIP lên official evaluator.

## Hàng đợi tiếp theo

1. Xử lý composition thực sự cho `Dự phòng phải trả` và `Nghĩa vụ nợ tài
   chính`, không đưa chúng vào direct adapter.
2. Nghiên cứu duplicate primary-statement tie-break cho Q323 ở mức table
   function/provenance, giữ fail-closed khi values khác nhau.
3. Sau khi các A/B đang chạy khác hoàn tất, dựng một full best artifact có
   lane này cùng các lane source-first đã được chọn, rồi validate ZIP/replay
   một lần nữa.
