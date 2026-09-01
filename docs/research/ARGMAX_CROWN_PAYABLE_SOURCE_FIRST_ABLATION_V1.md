# Argmax Crown payable: source-first bounded ablation

Ngày: 2026-08-31  
Trạng thái: local best-effort candidate; chưa có official leaderboard score.

## 1. Mục tiêu và boundary của attribution

Residual audit chọn Q921 vì đây là một `arg_extreme_period` có metric rất
cụ thể nhưng bị từ chối ở lớp row-family coherence. Câu hỏi là:

> Năm nào SAB công ty mẹ có số dư cuối kỳ phải trả cho Công ty Liên doanh
> TNHH Crown Sài Gòn cao nhất trong các năm 2019, 2021 và 2025?

Candidate cũ lấy một ô số tiền ở một bảng không bind được với tên Crown và
trả `113224326586.0`. Audit source-first tìm được một family khác nhất quán:
đúng entity, đúng `separate` scope, đúng ba năm, đúng row Crown và đúng ngữ
cảnh phải trả người bán bên liên quan. Giá trị lớn nhất của family đó là năm
`2021`.

Run A/B đầy đủ được lưu tại:

`artifacts/runs/vifinqa_answer_optimization_20260830/argmax_crown_payable_ab_v1/`

Control và variant cùng dùng full structured asset 146,246 tables, full
source-line map, cùng runtime/candidate inputs, `structured_table_filter=all`,
full-population gate và các source-first route khác bị giữ nguyên. Riêng
`arg_extreme_period` bị tắt trong cả hai arm để đo đúng phần mở rộng bounded
alias; variant bật typed-plan runner với contract Crown payable.

Không có gold label hoặc official scorer cục bộ. Vì vậy, A/B ở đây là
coverage/route attribution và independent source replay, không phải official
score delta.

## 2. Contract hẹp đã áp dụng

Alias chỉ được nhận khi đồng thời thỏa các điều kiện sau:

- raw table kind thuộc `financial_note`, `financial_note_detail` hoặc
  `related_party_schedule`;
- row sau normalize giữ đúng tên `Công ty Liên doanh TNHH Crown Sài Gòn`;
- context/title của source chứa cả `phải trả người bán` và `bên liên quan`;
- question có đúng SAB, `công ty mẹ`/separate scope, metric Crown và tập năm
  hữu hạn;
- mỗi năm bind đúng một source cell, header có đơn vị VND và không có
  conflicting duplicate được chọn theo rank;
- giá trị được lấy từ raw cell qua Decimal replay; route không tự tạo số
  hoặc dùng một row generic phải trả thay cho row Crown.

Normalization chỉ phục vụ navigation/matching. Raw source text, UID, row,
column, value và source-line provenance vẫn giữ nguyên. Contract không mở
toàn bộ `related_party_schedule` cho các argmax khác.

Implementation/test fingerprints sau patch:

| Artifact | SHA-256 |
|---|---|
| `scripts/research/run_arg_extreme_period_variant_v1.py` | `89b03c04c94f0b0891901ebde5906d92800dc20e4212356fc3259d8c97f27756` |
| `scripts/research/materialize_validated_route_overlay_v1.py` | `010754669947d5384cbff9bcbff294494882d550ebf6fca5e16b01be7e1abad6` |
| `tests/research/test_arg_extreme_period_variant_v1.py` | `11219d0d3f16a33c7bb9dba7eede812885b06d37492979ad5fcbd90c303b0e70` |
| immutable builder snapshot | `08b6551fa7aa98ae85ac12e7d6b3afc0e1bdd3365a6a6555afc62b8c7d34bf960b` |
| immutable source-first lookup snapshot | `fed5d4a674478cb3d3f67f84c47c24749b73bf5d216f334a55aac00ff4b329c6` |
| full structured asset | `617ae044499907cf302519b7ff6fb96fb86ab70ff08e6fdcb0e6a3f8df5251f7` |
| full source-line map | `533cc779f9565f43dfc961ad03d7bdff65adbd3728ee7a60b61651d495c4c615` |

The builder/source snapshot is under
`/tmp/aiguru-argmax-921.UXOYHT/` and is recorded in each run manifest. The
temporary path is an immutable run input for this audit; the candidate lane
does not treat it as a promotion artifact.

## 3. Full-population A/B gates

| Measure | Control | Variant |
|---|---:|---:|
| Questions | 1,012 | 1,012 |
| Predicted questions | 970 | 970 |
| Fallback questions | 42 | 42 |
| Non-zero answers | 957 | 957 |
| Validation records | 1,012 | 1,012 |
| Query replay | 1,012 | 1,012 |
| Replay errors | `[]` | `[]` |
| Source-line-map coverage | PASS, 146,246/146,246 | PASS, 146,246/146,246 |
| ZIP integrity | PASS | PASS |
| Wall time | 519 s | 560 s |

The control ZIP is
`argmax_crown_payable_ab_v1/control/submission.zip` with SHA-256
`3eef6e47835390c19b0218ae9fe78fdc29fa8240f480cfd2250538866e414aff`.
The variant ZIP is
`argmax_crown_payable_ab_v1/variant/submission.zip` with SHA-256
`199d3f42370d9d4d0cd92269c62cb3d849931ccbcccef49b7d31bbb5e645033c`.

Variant telemetry recorded 43 typed argmax plans seen, 30 accepted, 8
cohort-rejected, 1 unique-extreme tie and 19 empty year-candidate windows.
The implementation remains candidate-only: `human_verified=false`,
`promotion_allowed=false`, and the model-validity stage remains ranking-only.

## 4. Diff attribution

The fresh control-to-variant comparison has 31 answer diffs and 32 full-record
diffs. That raw count is not claimed as 31 causal gains because the wrapper
has two known non-target effects in this snapshot:

- Q529 is an unrelated wrapper-level selection difference
  (`539.666220767` versus `464.095068931`) and is excluded from the Q921
  materialization;
- Q47 has the same answer `7.320736564998` and differs only in provenance /
  prediction tier, so it is also excluded.

The stronger incremental comparison is against the immediately preceding
accounting-alias candidate. Current variant versus that prior treatment has
exactly one answer/full-record diff: Q921. This isolates the new Crown
contract from the earlier 59-row candidate and is the attribution used for
materialization.

## 5. Independent source replay for Q921

The independent replay checks 3/3 cells, 3/3 source-line-map entries, exact
coordinates, exact raw values, separate scope and unit context. The vector is:

| Năm | Source UID | Row | Col | Raw value | Source line | Raw kind |
|---:|---|---:|---:|---:|---:|---|
| 2019 | `cf23e7edd543e8dd72a735a1feffafb7497de98228dea855711f15d282ce65da` | 3 | 1 | `226245964160` | 1154 | `related_party_schedule` |
| 2021 | `cbdb64e01520979c016e5c15d4bb7a50689c0a3f36c26ff8adf9d926fc63b1ea` | 3 | 1 | `559509431031` | 1201 | `related_party_schedule` |
| 2025 | `5ee8d1df2c3298dea44b40109b80b93219b8c7f6bd77791f57909991d091fa4f` | 3 | 1 | `404695685526` | 1337 | `related_party_schedule` |

All three cells are VND with multiplier 1. The unique maximum is
`559509431031` in 2021, so the argmax answer is `2021`. Replay result:
`independent_replay: PASS`, `failures=[]`.

The variant trace is
`argmax_crown_payable_ab_v1/variant/submission/arg_extreme_period_trace_v1.jsonl`.
It records status `ACCEPTED`, `scope=separate`, the exact Crown metric, all
three source UIDs and selected year 2021. The evidence packet is
`argmax_crown_payable_ab_v1/variant/submission/data/q0921_evidence.csv`.

## 6. Materialization on the best candidate

Only Q921 was copied from the independently replayed variant onto the prior
59-replacement candidate. Output directory:

`artifacts/runs/vifinqa_answer_optimization_20260830/integrated_route_overlay_signed_lease_argmax_taxpaid_relatedparty_interest_direct_q994_subsidiary_alias_q921_v1/`

Output ZIP:

`artifacts/runs/vifinqa_answer_optimization_20260830/integrated_route_overlay_signed_lease_argmax_taxpaid_relatedparty_interest_direct_q994_subsidiary_alias_q921_v1.zip`

Materialization facts:

- base replacements inherited: 59;
- explicit new replacement: Q921 only;
- total unique replacements: 60;
- base submission SHA: `92ca1d892289a3b23ba3537f458fd7041242842b9469988ba83bba6404456a41`;
- source variant submission SHA:
  `d8b95271ac40f3255ace6aa872c5c33753ac4030cda081eb692ba982de18bcfa`;
- final ZIP SHA-256:
  `92752a9e6cf1c4468529c3f11a8438bce814bdc3173e5a9ed40a24cba32997cf`;
- output ZIP size: 549,692 bytes;
- validation: 1,012/1,012 records, 1,012/1,012 replay, `errors=[]`;
- `used_table_uid_count=3` for the explicit Q921 route;
- `new_numeric_arithmetic_invented=false`;
- `human_verified=false`, `promotion_allowed=false`;
- lane: `authorized_best_effort_submission_candidate`.

An independent JSON comparison of the materialized output against its base
finds exactly one answer and one full-record change: Q921
`113224326586.0 -> 2021.0`. The final archive passes `unzip -t` and contains
`submission.json` plus 1,012 evidence CSV members.

## 7. Regression and decision

After the patch:

```text
652 passed, 1 skipped in 8.99s
git diff --check: PASS
```

Decision: retain Q921 as one bounded argmax route in the authorized
best-effort candidate. Do not generalize the Crown alias to generic supplier
payables or related-party schedules, and do not label this as a verified
score gain. The next residual audit is Q989, STB accrued interest from
customer lending, with the same source-first requirement for exact row,
period, unit and scope.

## 8. Re-audit and rebase onto the current candidate

The three-cell replay was rerun independently with
`scripts/research/replay_crown_payable_q921_v1.py`. Its output is
`artifacts/runs/vifinqa_answer_optimization_20260830/argmax_crown_payable_ab_v1/independent_replay_q921_v1.json`.
The checker does not import the argmax route adapter; it rechecks the three
source hashes, table hashes, byte/character coordinates, source-line entries
`1154`, `1201` and `1337`, separate scope, related-party payable context,
exact Crown row, VND columns and the unique maximum at 2021. It reports
`status=PASS` and `promotion_allowed=false`.

To keep the active lineage current, only Q921 was then copied onto the
candidate that already contained Q878 and Q338:

`artifacts/runs/vifinqa_answer_optimization_20260830/integrated_candidate_q71_q878_after_q338_v1/`

The rebased candidate is:

`artifacts/runs/vifinqa_answer_optimization_20260830/integrated_candidate_q72_q921_after_q878_v1.zip`

It contains 71 inherited replacements plus Q921, validates/replays
1,012/1,012 with `errors=[]`, has 1,013 ZIP members / 1,012 evidence CSV
members, passes `unzip -t`, and has SHA-256
`94b292fd79555c00f2d08f071bd4601bfb6d0d67cd698bc0a33356fb944c3c6c`.
An independent base/output comparison finds exactly Q921 changed:
`113224326586.0 -> 2021.0`. The candidate remains an authorized
best-effort submission candidate with `human_verified=false` and
`promotion_allowed=false`; the historical 60-row artifact above is retained
as prior lineage, not the active best candidate.
