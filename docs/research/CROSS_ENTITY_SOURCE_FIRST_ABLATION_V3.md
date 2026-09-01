# Cross-entity source-first alias recovery: ablation V3

Ngày: 2026-08-30  
Protocol: `source_first_cross_entity_v1`  
Mục tiêu: phục hồi planner/entity metadata bị thiếu trong câu hỏi so sánh hai issuer, nhưng không cho alias hoặc retrieval score trở thành numeric authority.

## Kết luận ngắn

Route hiện tại replay được 10/113 câu `cross_entity_comparison`. So với route trước có 9 câu, hai câu mới đến từ incomplete-plan recovery:

- Q770: `GAS` và `POW`, `Vốn chủ sở hữu`, năm 2023.
- Q785: `HUT` và `HHS`, `Số lượng cổ phiếu đang lưu hành`, năm 2020.

Hai issuer mới không được suy ra bằng fuzzy match. Chúng đến từ alias/ticker khớp chính xác với `data/ViFinQA/code_stock.csv`, sau đó mỗi issuer phải có table V2 đúng report year và phải vượt source-first row/column/unit/scope replay độc lập.

Q799 không được nhận: một trong hai nguồn khớp `Vốn đầu tư của chủ sở hữu`, là equity component chứ không phải dòng tổng `Vốn chủ sở hữu`. Đây là một negative safety result có chủ ý.

## Thay đổi thực hiện

Trong `scripts/e2e/build_competition_submission_v1.py`:

1. `_cross_entity_ticker_plan` giữ nguyên planner khi đã có đủ hai ticker.
2. Khi planner có 0 hoặc 1 ticker, route chỉ thử `extract_tickers` trên registry public `code_stock.csv`.
3. Registry có thêm các structural aliases sinh trực tiếp từ cùng tên công ty: bỏ tiền tố `CTCP`, `Ngân hàng`, `Tổng Công ty cổ phần`, hoặc tiền tố `Đầu tư` trong các trường hợp còn ít nhất hai token; alias trùng ticker bị quarantine.
4. Recovery bị từ chối nếu không có cả hai `(ticker, requested_year)` trong source table index.
5. Thêm guard chặn `Vốn đầu tư của chủ sở hữu` cho yêu cầu tổng `Vốn chủ sở hữu`, và xử lý đúng chiều dương cho câu “ít hơn”.
6. Output ghi telemetry `ticker_source_planner_complete` hoặc `ticker_source_exact_code_stock_alias_recovery`.

Alias chỉ điều hướng đến source candidate. Numeric answer vẫn do hai cell hiện tại trong structured V2 table replay bằng `Decimal`; route vẫn là best-effort proposal, không phải human-verified certificate.

## Build artifacts

Asset dùng chung:

- Structured table asset: `artifacts/research/document_corpus_round2_assets_v1_20260829_r1/full_table_assets_v1.jsonl`
- SHA-256: `617ae044499907cf302519b7ff6fb96fb86ab70ff08e6fdcb0e6a3f8df5251f7`
- 146,246 tables; source-line map coverage 146,246/146,246.

Control (route disabled):

- Directory: `artifacts/runs/vifinqa_answer_optimization_20260830/cross_entity_source_first_fixed_d480/control/submission`
- ZIP: `artifacts/runs/vifinqa_answer_optimization_20260830/cross_entity_source_first_fixed_d480/control/submission.zip`
- Builder SHA: `d48001395860009db942022b8926a1ea74cbfa7b223fa9a7859ca6acff2292e1`
- Validation: 1,012 records, 1,012 queries replayed, `errors=[]`.

Candidate (route enabled):

- Directory: `artifacts/runs/vifinqa_answer_optimization_20260830/cross_entity_source_first_fixed_d480/candidate/submission`
- ZIP: `artifacts/runs/vifinqa_answer_optimization_20260830/cross_entity_source_first_fixed_d480/candidate/submission.zip`
- Builder SHA loaded at build start: `6358f3ae275377724eac25d40b46eed9c2e600425c3cdbab754a547fb136d3e0`
- Source-first lookup SHA: `1b1318143d611a7abbed2ee7d5cfa1c4ec8581bba2c48bae35af7279f65bc2f2`
- Validation: 1,012 records, 1,012 queries replayed, `errors=[]`.
- ZIP integrity: `unzip -t` passed.
- Candidate cross route: 10 resolved, 103 unresolved/ambiguous; 8 planner-complete and 2 exact-code-stock-alias recovery.
- All ten cross-route proposals remain `PARTIAL`; no route was upgraded to `VERIFIED` or `promotion_allowed`.

### Snapshot caveat

The candidate build report records `changed_during_build=true`: the workspace file changed after the process loaded the builder (`loaded_at_build_start` is the hash above; `on_disk_at_report` was `43ec18e007aaa137b907f478419e0323afe166bffde59c42b7cca87015615065`). Therefore the control/candidate pair is a valid artifact and replay check, but it is not a clean causal A/B for every unrelated tier change. The alias contribution is attributed only to the route telemetry and the two exact recovered questions; it is not presented as an official leaderboard-score delta.

## Per-question diff

Comparing `submission.json` by `id`:

- 10 prediction tiers changed to `source_first_cross_entity_v1`.
- 7 numeric answers changed; the two changes attributable to this alias lane are:
  - Q770: `2440.734385642` → `35153.747341507`.
  - Q785: `268631965.0` → `6112098.0`.
- 3 existing cross-route rows changed tier only (Q739, Q779, Q807); they are not counted as alias-recovery numeric gains.

Local artifacts do not contain a gold answer key or official scorer, so these numbers demonstrate source-grounded candidate changes, not verified accuracy or a confirmed leaderboard score increase.

## Independent source-cell replay

An independent checker re-read all 20 unique source UIDs from the candidate's cross-route ledger and checked:

- UID, document, ticker, report year, row index and column index;
- raw table cell against the emitted evidence CSV;
- row-label agreement;
- source-unit/output-unit conversion;
- shared scope across the two operands;
- Decimal arithmetic against the emitted query and answer.

Result: `INDEPENDENT_SOURCE_CELL_REPLAY 10/10 PASS; unique_uids=20`.

This is evidence that the candidate is internally source-replayable. It does not replace semantic human approval, a complete E2E certificate, or an external leaderboard evaluation.

## Next research queue

The remaining 103 unresolved cross-entity questions are not opened by bulk fuzzy aliasing. The next family-level experiments should separately measure:

1. incomplete aliases that are not present in `code_stock.csv` (for example brand names such as BIDV/MBBank/Saigonbank), with an explicit ambiguity quarantine;
2. metric rows whose source wording differs structurally from the question, using held-out precision checks;
3. the 39 one-ticker/two-year questions currently misclassified as `cross_entity_comparison`, without allowing a temporal route to bind a different row or reporting scope.

Each experiment must keep source replay, `PARTIAL`/`UNRESOLVED`, human authorization, and official score as separate gates.
