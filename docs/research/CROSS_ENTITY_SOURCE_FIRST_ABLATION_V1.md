# Cross-entity source-first ablation V1

Ngày: 2026-08-30  
Phạm vi: 1.012 câu ViFinQA; controlled candidate lane với hai issuer, một
report year và phép trừ.

## Kết luận

Cross-entity source-first tìm được 9 proposal trên 113 câu thuộc
cross_entity_comparison. Trong cặp control/candidate cùng input và cùng
implementation snapshot lúc process bắt đầu, 9 câu đổi route; 6 câu đổi
answer, 3 câu chỉ đổi source route nhưng giữ nguyên số.

Đây là cải thiện mạnh ở Answer/Execution proposal quality theo source-contract
audit: cả 6 answer changes đều thay source row có dấu hiệu sai hoặc quá rộng
bằng row đúng metric, đúng entity và đúng scope. Tuy nhiên đây chưa phải
Answer Accuracy/Execution Accuracy vì workspace không có gold answer hoặc
official local scorer.

## Controlled comparison

Artifacts:

- Control: artifacts/runs/vifinqa_answer_optimization_20260830/cross_entity_source_first_snapshot_f31/control/submission
- Candidate: artifacts/runs/vifinqa_answer_optimization_20260830/cross_entity_source_first_snapshot_f31/variant/submission

Hai process dùng cùng questions, primary runtime bundle, full table corpus,
replay input, source-line map, calibrated direct replay và cùng một
candidate-validity model được pin rõ bằng `--candidate-validity-model`.
Candidate-validity chỉ là ranking-only và không phải answer authority. Tham số
khác biệt có chủ đích duy nhất là bật/tắt cross-entity route. Loaded builder
SHA-256:
6079e6319ba31c2fc4f22dde190947d12a9f70033757fb515f37afd607cbbeb2.
Loaded source-first SHA-256:
f31feeb6d56f0d6b47d1a1336bf414540fbcaad0ca5f17481b0e0f039a6d22a0.

This f31 pair supersedes the earlier v3 exploratory pair. Both reports record
changed_during_build=false, so this is the release-quality local A/B artifact
for the frozen `f31…` implementation snapshot.

A later `5c6d…` source-first snapshot was also checked under the same
cross-entity flag: it retained 9 route changes, 6 answer changes and 3
provenance-only changes, with valid 1.012/1.012 replay on both sides. That
follow-up confirms that the later direct-lookup guard changes did not create
the cross-entity delta; the f31 pair remains the primary detailed ablation
below.

| Metric | Control | Candidate |
|---|---:|---:|
| Records | 1.012 | 1.012 |
| Non-zero answers | 1.001 | 1.001 |
| Validation | true | true |
| Replayed queries | 1.012 | 1.012 |
| Validation errors | 0 | 0 |
| Cross-entity proposals resolved | 0 | 9 |
| Cross-entity questions considered | 0 | 113 |

Candidate changed 6 answers:

| Q | Control answer | Candidate answer | Source-contract explanation |
|---:|---:|---:|---|
| 746 | -15.467,213219 | -15.503,979213 | DXS exact current-tax row replaces a shorter/wrong row; KHG exact row retained |
| 774 | 1.822.415 | -204.503 | Adds VIB cash-and-gold row and subtracts SHB; both separate, 2022, million VND |
| 776 | 2,354023216989 | -2,267796002924 | Uses P&L after-tax rows instead of retained-earnings rows |
| 794 | 4,5515249 | 40,676771672 | Uses NLG construction-service revenue instead of management-service detail |
| 795 | 2,788579696 | 25,731282261 | Uses exact short-term prepaid-expense rows instead of “other” child rows |
| 798 | -150,09507082 | 67,557218104 | Uses separate tangible-fixed-asset rows instead of unrelated consolidated rows |

The raw full-corpus cells independently replay to the candidate values:

- Q746: DXS `-18.382.997` VND minus KHG `15.485.596.216` VND, divided by
  1.000.000 = `-15.503,979213` million VND.
- Q774: VIB `1.617.912` million VND minus SHB `1.822.415` million VND =
  `-204.503` million VND.
- Q776: MSN `1.725.926.701.022` VND minus MML `3.993.722.703.946` VND,
  divided by 10^12 = `-2,267796002924` nghìn tỷ đồng.
- Q794: NLG `45.228.296.672` VND minus SCR `4.551.525.000` VND, divided by
  1.000.000.000 = `40,676771672` tỷ đồng.
- Q795: GAS `50.699.483.380` VND minus POW `24.968.201.119` VND, divided by
  1.000.000.000 = `25,731282261` tỷ đồng.
- Q798: HHV `99.166.395.728` VND minus VSC `31.609.177.624` VND, divided by
  1.000.000.000 = `67,557218104` tỷ đồng.

The 3 route-only changes were Q739, Q779 and Q807; their candidate source rows
were more directly aligned with the requested metric while the replayed answer
remained unchanged.

## Machine-review proxy

The aggregate machine-review navigation proxy did not improve on this pair:

| Proxy | Control | Candidate |
|---|---:|---:|
| machine_calibrated target UID | 62/62 | 62/62 |
| machine_provisional target UID | 144/364 | 144/364 |
| machine_provisional target document | 84,62% | 84,62% |
| machine_provisional target table | 51,65% | 51,65% |

This is not evidence against the six source-contract corrections: the machine
silver target is a navigation label and does not adjudicate the semantic row
identity in these newly resolved comparisons. It does mean the candidate must
not be promoted from proxy counts alone.

## Authority and promotion

The implementation is in
scripts/e2e/build_competition_submission_v1.py, in
source_first_cross_entity_answer. It requires exactly two planned tickers, one
explicit year, subtract operation, exact report-year lookup, accepted exact or
ordered OCR-gap row match, shared scope, and independent source-row safety
checks. Output remains `source_first_cross_entity_v1` with verification class
`PARTIAL`; it is not a strict `VERIFIED` certificate.

The candidate report records `validation.valid=true`, 1.012 replayed queries,
zero validation errors and `changed_during_build=false`. Both ZIP archives pass
integrity validation. No leaderboard submission or official score claim is
made.

Recommended disposition: keep the route in the authorized best-effort
candidate lane, prioritize semantic review/external scoring for the six answer
changes, and retain the cross-entity disabled control for future A/B tests.
