# Accounting-row alias argmax: independent selection and quarantine

Ngày: 2026-08-31  
Trạng thái: local best-effort candidate; chưa có official leaderboard score.

## 1. Mục tiêu và attribution boundary

Run `argmax_accounting_alias_ab_v1` mở rộng coverage của strict
`arg_extreme_period` cho ba accounting-row families mà extractor đang gắn
khác kind. Run này dùng builder snapshot và source-lookup snapshot riêng:

- builder SHA: `08b6551fa7aa98ae85ac12d3b6afc0e1bdd3365a6a6555afc62b8c7d34bf960b`;
- source-first lookup SHA:
  `fed5d4a674478cb3d3f67f84c47c24749b73bf5d216f334a55aac00ff4b329c6`;
- full structured asset SHA:
  `617ae044499907cf302519b7ff6fb96fb86ab70ff08e6fdcb0e6a3f8df5251f7`;
- source-line map SHA:
  `533cc779f9565f43dfc961ad03d7bdff65adbd3728ee7a60b61651d495c4c615`.

Vì builder/source snapshot không trùng canonical snapshot của các A/B trước,
29 raw diffs của run này không được gán toàn bộ thành causal score gain. Chỉ
những ID vượt independent source replay mới được xem xét đưa vào overlay hiện
tại. Q829 bị quarantine; Q900 và Q971 được chọn trong candidate lane.

## 2. Full-population A/B gates

Control nằm tại:

`artifacts/runs/vifinqa_answer_optimization_20260830/argmax_accounting_alias_ab_v1/control/submission/`

Variant nằm tại:

`artifacts/runs/vifinqa_answer_optimization_20260830/argmax_accounting_alias_ab_v1/variant/submission/`

Cả hai arm đều đạt:

- `validation.valid=true`;
- 1.012 records và 1.012 query replay;
- `errors=[]`;
- source-line-map và ZIP integrity pass.

Variant thấy 43 typed argmax plans, trong đó 29 plan được route chấp nhận,
9 bị loại vì không có row family nhất quán, 4 unsupported-contract và 1
unique-extreme tie. Raw variant/control diff là 29 ID:

`813, 829, 832, 841, 850, 860, 874, 876, 879, 890, 897, 900, 904, 906,
910, 928, 929, 933, 936, 946, 948, 953, 971, 974, 981, 997, 999, 1000,
1008`.

Các ID đã thuộc cohort/overlay trước không được tính lại. Ba ID mới của
family alias là Q829, Q900 và Q971.

## 3. Quyết định independent replay

### Q829 — loại vì scope ambiguity

Question:

> Năm nào TTF có mức Lợi nhuận khác cao nhất trong các năm 2016, 2017, 2023 và 2025?

Variant chọn `2025` từ scope `separate`, nhưng question không nêu `công ty
mẹ` hay `hợp nhất`. Full asset có các dòng `Lợi nhuận khác` của cả hai scope
trong cohort năm; ví dụ các giá trị separate là
`14.353.234.722`, `-17.623.021.251`, `-78.924.918.067` và
`39.908.113.997`, trong khi consolidated có giá trị khác ở các năm tương
ứng. Do đó winner thay đổi theo scope và `scope_selected=separate` không có
đủ semantic authority từ question.

Quyết định: không materialize Q829, không mở alias `Lợi nhuận khác` cho
question không có scope. Đây là negative control cho việc chọn scope theo
ranking/quality.

### Q900 — nhận vào candidate lane

Question yêu cầu MSN theo số liệu công ty mẹ, bốn năm `2016, 2017, 2019,
2021`, và metric `vốn cổ phần đã phát hành (cổ phiếu phổ thông)`. Cả bốn
source tables đều là MSN `separate`, row exact
`Vốn cổ phần đã phát hànhCổ phiếu phổ thông`, column số lượng cổ phiếu và
unit context VND:

| Năm | UID | Row | Col | Giá trị replay |
|---:|---|---:|---:|---:|
| 2016 | `c7c290ffcf602be4e900fd3146d07b325c3ba1dca1fa58715b1ff25db1d24d39` | 3 | 1 | 768075674 |
| 2017 | `2d194351c27be67d80248bbffd722a9883ea269146593cf565310d185578ad68` | 3 | 1 | 1157373974 |
| 2019 | `105963c0502ec9a98c4dd024185bc837f0019340e253cbf2511ba855f482344c` | 3 | 1 | 1168946447 |
| 2021 | `682aeb627c0f7cfc4256f437dbb94d18b4dfb5231e6aeaae00c9c3ab688f2aba` | 3 | 1 | 1180534692 |

Giá trị lớn nhất duy nhất là `1180534692` ở năm `2021`. Không dùng cột VND
giá trị tiền; route bind vào cột số lượng cổ phiếu theo header
`Số lượng cổ phiếu`.

Independent result: 4/4 UID, 4/4 coordinate, 4/4 raw-value, scope/unit và
unique-winner checks pass. Variant đổi Q900 từ `768075674.0` sang `2021.0`.

### Q971 — nhận vào candidate lane có ghi chú semantic

Question chỉ rõ KHG công ty mẹ và hỏi năm cao nhất cho tổng chi phí hoa hồng
môi giới bất động sản trong giai đoạn `2019` đến `2023`. Hai source tables
available trong exact route đều là KHG `separate`, có context `CHI PHÍ PHẢI
TRẢ NGẮN HẠN`, unit VND và row metric exact sau normalize:

| Năm | Raw table kind | UID | Row | Col | Giá trị replay |
|---:|---|---|---:|---:|---:|
| 2019 | `financial_note_detail` | `960ab74fe921aba01046b2d2e2542f1810d14e26c6d63c2d5278115fa57608c1` | 2 | 1 | 18711269101 |
| 2023 | `debt_schedule` | `0b9863e54c8a80c5f8accdadcde06a17f31a08eceabbdb3a0487b8fe7ad90916` | 2 | 1 | 26932187593 |

The wrapper maps both raw kinds to `debt schedule` only when the exact
brokerage-cost row and short-term-payables source context are present. The
winner is unique at `2023`. Independent result: 2/2 UID, 2/2 coordinate,
2/2 raw-value, scope/unit and unique-winner checks pass. Variant changes Q971
from `18711269101.0` to `2023.0`.

Semantic limitation: the source row is an accrued short-term-payable
disclosure, not a generic income-statement expense row. It is therefore kept
as a bounded best-effort route, not generalized to every brokerage/expense
question; the exact source context and row must continue to be required.

## 4. Materialized overlay

Only Q900 and Q971 were copied from the alias variant onto the latest
64-replacement candidate. Q829 is absent from the allow-list and output:

`artifacts/runs/vifinqa_answer_optimization_20260830/integrated_route_overlay_signed_lease_argmax_taxpaid_relatedparty_interest_direct_q994_subsidiary_conditional_provision_named_compensation_accounting_alias_v1/`

ZIP:

`artifacts/runs/vifinqa_answer_optimization_20260830/integrated_route_overlay_signed_lease_argmax_taxpaid_relatedparty_interest_direct_q994_subsidiary_conditional_provision_named_compensation_accounting_alias_v1.zip`

Materializer report:

- explicit replacements: Q900 and Q971 only;
- inherited replacements: 64;
- total unique replacements: 66;
- `new_numeric_arithmetic_invented=false`;
- `human_verified=false`, `promotion_allowed=false`;
- validation/replay: 1.012/1.012, `errors=[]`;
- ZIP members: 1.012 CSV plus `submission.json`;
- ZIP SHA-256:
  `92127eb6c5de01aba31573cf44ce1be7d259ff1e6e3867bbea5b5ee666cccb02`.

Independent comparison against the 64-row base shows exactly two JSON-value
diffs: Q900 and Q971. The final manifest contains no Q829 entry. Focused
argmax/named-route tests pass (`18 passed`), and `unzip -t` passes.

## 5. Score boundary and next research

This artifact is an authorized best-effort submission candidate. It is not a
strict certificate, not human verified, and not an official leaderboard
submission. The workspace has no matching gold/scorer for these 1.012 public
questions, so the recorded official baseline remains Answer Accuracy `0.17`
and Execution Accuracy `0.17` until external score evidence is available.

Next, keep the remaining 9 no-coherent-row-family cases separated by failure
class. The highest-leverage next audit is the explicit-parent ASM
`Lợi nhuận khác` case Q822; it may be recoverable with a source-bound
statement-code/row-family contract, while the unscoped Q829 pattern must stay
closed.

## 6. Subsequent Q829 scope-consensus re-audit

The original alias artifact intentionally quarantined Q829 because the
unscoped question exposed both consolidated and separate TTF statements. That
was the correct conservative decision for that artifact, but the statement
that the winner changes by scope was not the final source result.

A later full-corpus audit found complete, internally coherent row families in
both scopes. The values differ, but both scope cohorts have the same unique
arg-max year: `2025`. A new family-level guard now requires this winner
consensus before selecting the preferred consolidated evidence; it never
mixes scope values and never accepts scope ranking alone. The independent
eight-record replay is recorded at
`artifacts/runs/vifinqa_answer_optimization_20260830/argmax_scope_consensus_ab_v1/independent_replay_q829_v1.json`
with `status=PASS`.

Q829 is therefore not retroactively added to this historical alias variant.
It was evaluated as a separate route-contract experiment and only then
materialized onto the active lineage as documented in
`docs/research/ARGMAX_OTHER_PROFIT_Q829_SCOPE_CONSENSUS_V1.md` and section 33
of `docs/research/RESEARCH_INTEGRATION_LOG_V1.md`. It remains candidate-only,
with no official score or promotion claim.
