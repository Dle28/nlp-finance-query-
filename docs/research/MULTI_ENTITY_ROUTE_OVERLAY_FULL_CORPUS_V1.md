# Full-corpus source-first route overlay — 2026-08-30

## Kết luận vận hành

Đã tạo hai candidate ZIP 1.012 câu từ cùng một baseline local đã được
replay. Candidate chính chỉ thay 21 câu bằng các route source-first đã pass
replay độc lập; candidate sensitivity thay thêm Q858 theo chính sách lấy trị
tuỵệt đối của expense, tổng cộng 22 câu. 991/990 câu còn lại giữ nguyên giá
trị baseline tương ứng.

Đây là `authorized_best_effort_submission_candidate`, không phải strict
`VERIFIED` release và chưa có official score mới. Không được diễn giải số câu
thay đổi thành số câu đúng.

| Lane | ZIP | Số row thay | SHA-256 |
|---|---|---:|---|
| Primary, giữ dấu nguồn cho Q858 | `artifacts/runs/vifinqa_answer_optimization_20260830/integrated_route_overlay_signed_v1.zip` | 21 | `7a0000714cf4989b75d606eb69a69d5aba416cb2605f10ab3e7354d71e34aed8` |
| Sensitivity, `abs(expense)` cho Q858 | `artifacts/runs/vifinqa_answer_optimization_20260830/integrated_route_overlay_expense_magnitude_v1.zip` | 22 | `0b27ff41a219c5b7f89f365a23cf1204586c85df7dfe72e5da9801e38a7087a9` |

Cả hai candidate đều có `submission.json` + 1.012 evidence CSV; lần replay
cuối cùng đều đạt `records=1012`, `queries_replayed=1012`, `errors=[]`, và
`zipfile.testzip() == None`.

## Vì sao cần full-corpus lane

Run legacy dùng `--structured-table-filter candidate_uids` chỉ hydrate
29.428/146.246 table lines. Điều này làm các route multi-entity thiếu bảng
đúng của MSB, SAB, ACB, v.v. và trả `unresolved`, dù A/B logic đã có replay.

Một run cùng snapshot với `--structured-table-filter all` hydrate toàn bộ
146.246 lines và đạt:

| Route | Considered | Resolved |
|---|---:|---:|
| `source_first_multi_entity_direct_aggregation_v1` | 6 | 6 |
| `source_first_multi_entity_selector_v1` | 1 | 1 |
| `source_first_multi_entity_threshold_v1` | 1 | 1 |
| `source_first_multi_entity_ratio_selector_v1` | 3 | 2 |
| `source_first_period_extreme_v1` | 29 | 13 |
| `source_first_multi_entity_conditional_count_v1` | 1 | 1 |

Build nguồn:

- `artifacts/runs/vifinqa_answer_optimization_20260830/integrated_best_legacy_full_v1/`
- builder snapshot SHA-256: `cc8c4fae97181b167cb511da10982b2b3b05abbcdac1f9e2003331274cfb7113`
- source-first core SHA-256: `d68ed471ce73db8fe8f0f9f263b0cfdcc3fb223ce80821980a8603ca61385528`
- full table asset SHA-256: `617ae044499907cf302519b7ff6fb96fb86ab70ff08e6fdcb0e6a3f8df5251f7`
- source-line map SHA-256: `533cc779f9565f43dfc961ad03d7bdff65adbd3728ee7a60b61651d495c4c615`
- full build validation: `valid=true`, `records=1012`, `queries_replayed=1012`, `errors=[]`.

Full-corpus hydration làm thay đổi 48 answer rows so với baseline vì generic
semantic/source ranking cũng nhìn thấy các bảng ngoài shortlist. Vì các thay
đổi generic đó chưa có gold/holdout precision gate, overlay không chép toàn bộ
48 rows; nó chỉ chép allow-list route dưới đây. Đây là isolation boundary của
candidate, không phải bằng chứng rằng 48 rows đều cải thiện score.

## Allow-list thay đổi trong candidate chính

| Nhóm | QID | Baseline → overlay |
|---|---|---|
| Debt/equity selector → interest coverage | 465, 553 | `5690965790904.0` → `8.138020523057806` |
| Equity max → current tax | 536 | `-60.755885161` → `688.075163368` |
| Direct credit-loss mean | 819 | `5625.976` → `7980.8026666666665` |
| Period extreme | 815 | `46.999721794` → `145.113883664` |
| Period extreme | 835 | `449.12019567` → `654.643132429` |
| Period extreme | 838 | `0.0673` → `35.318781` |
| Period extreme | 845 | `2.286` → `2.843` |
| Period extreme | 847 | `0.745801791` → `22.820769751` |
| Period extreme | 859 | `0.5286425` → `335.746014085` |
| Period extreme | 903 | `-0.18272135154` → `0.10660207796` |
| Period extreme | 911 | `0.568436644` → `145.182929479` |
| Period extreme | 914 | `0.35` → `3.375` |
| Period extreme | 969 | `780.977719441` → `830.00008186` |
| Period extreme | 972 | `-40.170176148` → `879.010112441` |
| Period extreme | 988 | `485254333024.0` → `3375.0` |
| Period extreme | 996 | `1047.0` → `4442784.0` |
| Direct L/C mean | 918 | `10350368.0` → `23777619.5` |
| Positive threshold count | 949 | `6.5e-11` → `3.0` |
| Positive cash-flow count | 973 | `2.0` → `1.0` |
| Direct operating-cash-flow sum | 1003 | `1.0` → `-106.069040067` |

Route source records, evidence CSVs, and route tier assertions are recorded in
`route_overlay_manifest.json` inside each output directory. The materializer
is reproducible from:

- `scripts/research/materialize_validated_route_overlay_v1.py`
- accepted base: `artifacts/runs/vifinqa_answer_optimization_20260830/local_source_first_exact_only_final_accepted_r1/submission/`
- full-corpus source: `artifacts/runs/vifinqa_answer_optimization_20260830/integrated_best_legacy_full_v1/`

## Evidence sanity checks for the highest-leverage rows

The following checks use the source rows copied into the candidate evidence
files. They are technical source/table replays; they are not hidden-label
accuracy measurements.

- Q819: `MSB=1924.445`, `BID=20606.172`, `ABB=1411.791`; mean is
  `7980.802666666...` billion VND.
- Q918: `ABB=1634376`, `SSB=2228158`, `BID=62109504`, `MBB=29138440`; mean is
  `23777619.5` million VND.
- Q949: `ACB=1731.886`, `MBB=1756.922`, `EIB=580.096`, `BID=3791.593` billion
  VND. Strict `>1000` count is 3; scope predicate is invariant under the
  consolidated/separate check in the route A/B.
- Q1003: after table-declared unit conversion, the four source operands are
  `-417.067012`, `-45.061179652`, `29.663853542`, and `326.395298043`; sum is
  `-106.069040067` billion VND.
- Q973: HSG is positive while HPG and MSR are negative, so the strict positive
  count is 1.
- Q465/Q553: PLX is the unique maximum debt/equity issuer in both tested
  scopes; replay of `(PBT + interest expense) / interest expense` is
  `8.138020523057805...`.

The independent route-specific ablation records remain the more narrowly
causal evidence:

- `docs/research/MULTI_ENTITY_DIRECT_AGGREGATION_SOURCE_FIRST_ABLATION_V1.md`
- `docs/research/MULTI_ENTITY_SELECTOR_SOURCE_FIRST_ABLATION_V1.md`
- `docs/research/MULTI_ENTITY_THRESHOLD_SOURCE_FIRST_ABLATION_V1.md`
- `docs/research/MULTI_ENTITY_RATIO_SELECTOR_SOURCE_FIRST_ABLATION_V1.md`
- `docs/research/MULTI_ENTITY_CONDITIONAL_COUNT_SOURCE_FIRST_ABLATION_V1.md`
- `docs/research/PERIOD_EXTREME_SOURCE_FIRST_ABLATION_V1.md`

## Q858 sign policy

Q858 is intentionally excluded from the primary overlay's replacement list.
Its source cells are:

```text
SAB = -1.446841604384
DBC = -0.083645537443
MCH =  2.060648420988
```

The signed source-cell sum is `0.530161279161`, which is the value retained by
the primary candidate. The sensitivity candidate instead copies the explicit
`expense_magnitude_abs` route and emits `3.591135562815`.

There is no local gold answer/program for this evaluation population. The
primary choice therefore follows the reported-cell arithmetic already used by
the accepted direct route; the absolute-expense interpretation is kept as a
separate sensitivity lane rather than silently promoted.

## Q932 lease-maturity extension

The clean A/B for `source_first_multi_entity_lease_threshold_v1` is recorded
in `artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_lease_threshold_ab_v1/`.
It uses the current builder snapshot SHA-256
`3fafd53281efd46e57d8e543890de9a81131a45de414b8784cc76fe52a42c490` and
source-first core SHA-256
`23805052c5893c6032eba8b8126b743cd92c7071fcbe16f8b3e6a5dbde66290b`.
Both arms passed full-population replay and source-line-map coverage. The
variant changed only Q932, from `0.0` to `2.0`, after replaying the four
explicit issuers in the one-year operating-lease maturity bucket:

| Issuer | Raw value (million VND) | Converted value (billion VND) | `> 40` |
|---|---:|---:|---|
| MBB | 31,007 | 31.007 | false |
| HDB | 17,186 | 17.186 | false |
| KLB | 49,649 | 49.649 | true |
| NAB | 79,657 | 79.657 | true |

The route checks the exact operating-lease context, year, maturity bucket,
issuer list, unit, and source provenance. KLB's table header is unitless, so
its million-VND unit is accepted only from the hash-checked source-document
declaration. The evidence is retained in
`multi_entity_lease_threshold_ab_v1/variant/submission/data/q0932_evidence.csv`.
The variant ZIP SHA-256 is
`cad1a6f8646f93ae27ff4679cb87cee70b90562d061638d7c95aa43b29e1bfbd`.

The lease result is now folded into two bounded, inherited overlays. The
latest signed primary candidate contains 22 total explicit replacements
(the previous 21 plus Q932):

`artifacts/runs/vifinqa_answer_optimization_20260830/integrated_route_overlay_signed_lease_v1.zip`

SHA-256:
`4c38fa6f13ab6838614e66e36122ca9754ba3b0c14696a3351b8d3c96b43f84f`.
The expense-magnitude sensitivity contains 23 replacements (including Q858
as `3.591135562815` rather than the primary signed `0.530161279161`):

`artifacts/runs/vifinqa_answer_optimization_20260830/integrated_route_overlay_expense_magnitude_lease_v1.zip`

SHA-256:
`28af95b8c0170a2d45b3607c94b734f8ec4eefe763fe5360ea7b625f16b327ce`.
Both latest ZIPs contain 1,012 prediction CSVs plus `submission.json`, pass
ZIP integrity, and validate as 1,012/1,012 records with `errors=[]`. These
are bounded authorized best-effort candidates, not strict `VERIFIED` releases
or measured score gains.

## Remaining blocker and next experiment

Q539 remains unresolved in the ratio-selector family because the source-first
route does not yet have a unique, scope-invariant output for that question.
The Q932 lease family is covered by the clean A/B and the inherited overlays;
it is not an invitation to generalize the threshold rule to other maturity or
liquidity tables.
The next high-yield experiment is a holdout-gated, generic source-pair
hydration adapter: expose full-corpus tables only to explicit multi-entity
route functions while keeping generic semantic ranking on the frozen candidate
UID universe. That should reproduce the 21-row overlay through the pipeline
itself and avoid the 27 unrelated full-corpus generic changes. Until that
adapter is implemented and replayed, the two ZIPs above remain materialized
best-effort candidates, not a production strict release.
