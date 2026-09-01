# Formula bridge contract A/B v1b

Date: 2026-08-31

## Outcome

The contract variant passed a clean full-population run when the Python import
path was pinned to the immutable snapshot. It makes the answer-level selector
recognize complete formula plans whose base review plan has no concrete operand
decomposition, and it carries answer-unit semantics separately from raw source
units. The variant did not create an incremental serialized answer or tier
change beyond the already-existing formula-bridge v4 candidate: its serialized
answers and prediction tiers match the v4 candidate for all 1,012 records.

This is structural research evidence, not a score claim. No independent gold
set or official scorer receipt is available for these exact submission hashes.

## Hypothesis/family

For the reusable formula-evidence family, a source-replayed candidate should be
eligible for whole-question selection when its decomposition is complete, even
if the base review plan has only a placeholder AST and no operand list. The
selector must distinguish the answer unit from the raw source unit of grounded
operands. Review question-plan context supplies the answer-unit contract;
source cells remain bound to their exact source unit. Formula family, operand
IDs, AST structure, entity, scope, period, and unit contracts define the rule.
`question_id` is tracking metadata only and never a routing exception.

## Control fingerprint

Both arms use the same contract snapshot and the same loaded source modules;
the control has the formula bridge disabled.

- immutable snapshot:
  `artifacts/runs/vifinqa_answer_optimization_20260830/formula_bridge_contract_ab_v1/snapshot`
- `PYTHONPATH`:
  `artifacts/runs/vifinqa_answer_optimization_20260830/formula_bridge_contract_ab_v1/snapshot/src`
- builder SHA-256:
  `699f706b4820b5d2a0e3fa2a7d92e5694527f07e14e935086bef460a47b7f3a0`
- candidate-plan selector SHA-256:
  `1343e426dd53f0470dc72515d25fc041156d35ead1fe965da560f2edea3b1078`
- formula bridge SHA-256:
  `300b97bcff3121161d5030fe592d6b1b2ece30eae0e262f475a5f416ec53fdb7`
- source-first lookup SHA-256:
  `72fd26a0d4ab715ec883811e4038aebd854d540b77fed781658701ce432f2bc9`
- proposal verifier SHA-256:
  `0179d78644ee7acb7bdc0b7cd94264c8ed82dd852dab6ab25dcbb1e67bb42f4`
- formula bridge flag: disabled

## Candidate fingerprint

The candidate uses the identical code and input closure. Its only configured
experimental difference is the opt-in formula sidecar:

- formula bridge flag: `--enable-formula-evidence-bridge`
- sidecar:
  `artifacts/kaggle_runs/notebook5554bd790d_v10_20260811/vifinqa_review_bundle/formula_evidence_sets_typed_v1.jsonl`
- sidecar SHA-256:
  `7fdc680f3fba224b72161c6dec768b45d0d77064a6020abfd66d6c81bd9188d2`

The focused preflight test exited `0`: a plan with raw operands in `vnd` and
answer unit `%` was selected, a placeholder AST without required operand IDs
was not enforced, and source-coordinate/replay checks remained active.

## Population and split

- population: complete ViFinQA input, 1,012 questions;
- split: no tuning split; both arms ran the complete input;
- questions SHA-256:
  `64a428d90a8c5ad5d36a397d2de3b6e3aa4e4c1224dcdcb118fe3a4fca056ff0`;
- review items SHA-256:
  `774791be629bec6a652b8e29c3408ce2d3b749a0e9ebc0f08360b20eb0ba7ed9`;
- replay SHA-256:
  `ccb61012779a64c96a31e07dc875615fdc04973e4918edee35907a3090112372`;
- full structured table asset SHA-256:
  `617ae044499907cf302519b7ff6fb96fb86ab70ff08e6fdcb0e6a3f8df5251f7`;
- source-line map SHA-256:
  `533cc779f9565f43dfc961ad03d7bdff65adbd3728ee7a60b61651d495c4c615`.

Shared route settings were `structured-table-filter=all`, required source-line
map, direct replay with provisional records, the two existing value-blind
research packets and route overlay, candidate-validity ranking, answer-level
selector maximum 64, and disabled report-year-neighbor, period-extreme, and
cross-entity routes.

## Scorer/gold

No independent gold binding or official scorer receipt was available for the
exact control/candidate submission hashes below. The local completion gate and
Decimal replay prove artifact construction and deterministic replay coverage;
they do not prove answer correctness or leaderboard score.

`ANSWER_ACCURACY: control=NOT_MEASURED candidate=NOT_MEASURED delta=NOT_MEASURED`

`EXECUTION_ACCURACY: control=NOT_MEASURED candidate=NOT_MEASURED delta=NOT_MEASURED`

## Full-population A/B result

| Measure | Control | Candidate |
|---|---:|---:|
| Questions | 1,012 | 1,012 |
| Predicted | 970 | 970 |
| Fallback-zero | 42 | 42 |
| Nonzero answers | 957 | 957 |
| Validation records | 1,012 | 1,012 |
| Replayed queries | 1,012 | 1,012 |
| Missing records | 0 | 0 |
| Errors | 0 (`errors=[]`) | 0 (`errors=[]`) |
| Evidence CSV files | 1,012 | 1,012 |
| Diagnostics lines | 1,012 | 1,012 |
| Audit lines | 1,012 | 1,012 |
| Source-line-map coverage | 146,246/146,246 | 146,246/146,246 |
| Completion gate | `true` | `true` |
| ZIP integrity | exit 0 | exit 0 |
| Local verifier `PARTIAL` | 968 | 968 |
| Local verifier `UNRESOLVED` | 44 | 44 |

Answer-level selector diagnostics, not accuracy:

| Selector result | Control | Candidate |
|---|---:|---:|
| selected | 295 | 304 |
| baseline fallback | 295 | 304 |
| abstain | 717 | 708 |

Formula bridge diagnostics:

- sidecar rows read: 78;
- candidates built: 11;
- final formula-route selections: 10;
- stored sidecar answers used: false;
- all 10 selected formula proposals: local verifier class `PARTIAL`;
- rejected rows: 67, including 52 operand-coverage-incomplete, 6 unsupported,
  4 ambiguous-scope, 2 missing denominator, 1 missing numerator, 1 missing
  service-income operand, and 1 undefined formula.

## Answer and tier comparison

The complete 1,012-record comparison found:

- improved: `NOT_MEASURED` because no gold/scorer is bound;
- regressed: `NOT_MEASURED` because no gold/scorer is bound;
- unchanged answers: 1,003;
- changed answers: 9;
- tier changes: 10;
- tier-only changes: 1 (`Q644`);
- unresolved by local verifier class: 44 in each arm;
- non-target answer changes: 0.

Changed answers are diagnostic only and must not be interpreted as correct:

| Question | Formula family | Control | Candidate |
|---:|---|---:|---:|
| 586 | `percentage_change` | 2570.087275188868 | 335.81896171961 |
| 666 | `net_finance_result` | 1.72744 | 270.996272455 |
| 672 | `explicit_stated_fraction` | 0.17331646326215894 | 17.331646326215893 |
| 675 | `long_term_investment_to_equity` | 5416895027800.0 | 73.15777772467692 |
| 677 | `net_finance_result` | -5.80282562247 | 3.33726664734 |
| 678 | `current_liabilities_to_equity` | 5023901027201.0 | 20.736246136548743 |
| 705 | `explicit_stated_fraction` | 1918935920000.0 | 36.48879381410757 |
| 710 | `current_liabilities_to_equity` | 29013924943549.0 | 47.8936070867124 |
| 960 | `operating_cash_flow_argmax_period` | 1690191227444.0 | 2023.0 |

Question 644 changed tier only: the answer stayed `-52.401960937079814`,
while the tier changed from `program_growth_heuristic` to
`formula_evidence_replay_v1`.

## Family-level results

| Formula family | Built | Final selected | Answer changes | Tier changes | Affected questions |
|---|---:|---:|---:|---:|---|
| `percentage_change` | 2 | 2 | 1 | 2 | 586, 644 |
| `net_finance_result` | 2 | 2 | 2 | 2 | 666, 677 |
| `explicit_stated_fraction` | 2 | 2 | 2 | 2 | 672, 705 |
| `long_term_investment_to_equity` | 1 | 1 | 1 | 1 | 675 |
| `current_liabilities_to_equity` | 2 | 2 | 2 | 2 | 678, 710 |
| `net_other_income` | 1 | 0 | 0 | 0 | 702 |
| `operating_cash_flow_argmax_period` | 1 | 1 | 1 | 1 | 960 |
| **Total** | **11** | **10** | **9** | **10** | — |

## Incremental-effect check

Compared with the prior clean formula-bridge v4 candidate, the new candidate
has 0 answer changes and 0 tier changes. The only observed incremental signal
is selector bookkeeping: `BASELINE_FALLBACK` increased from 295 to 304 and
`ABSTAIN` decreased from 717 to 708 in this run. This happened because the
legacy route-priority selector already selected the formula proposals before
the answer-level selector ran; the contract variant therefore did not add a
new answer lane in the final output.

This identifies the next bottleneck: the formula proposals still need an
independent semantic/temporal/scope/unit proof and a gold-backed selection
policy. Increasing route priority or adding more Question-ID cases would not
be evidence of population improvement.

## Diagnostic-only metrics

The following are engineering evidence only:

- current-table Decimal replay completed for all 1,012 records;
- exact source-line map coverage passed for all 146,246 tables;
- both ZIP archives passed `unzip -t`;
- candidate formula plans carry `answer_unit` separately from `source_unit`;
- no non-target answer changed;
- selected formula candidates remain `candidate_only` with all authority flags
  false.

None of these metrics establishes answer accuracy.

## Artifact paths and hashes

Control:

- submission:
  `artifacts/runs/vifinqa_answer_optimization_20260830/formula_bridge_contract_ab_v1b/control/submission/submission.json`
  SHA-256 `81168a73f7748b3193aade069bbb1cfaf8d64ffab99bb416ec84ddfa161d0bb6`;
- ZIP:
  `artifacts/runs/vifinqa_answer_optimization_20260830/formula_bridge_contract_ab_v1b/control/submission.zip`
  SHA-256 `5c3e989d42bf9e8a7bce6ce67d3e0da3774d108f5b50352f7345c36d5d859545`;
- build report:
  `artifacts/runs/vifinqa_answer_optimization_20260830/formula_bridge_contract_ab_v1b/control/submission/build_report.json`
  SHA-256 `de080b3e01d2ffa3f96bc29d5d5c6e6a05b48d97cb098ac70e6e70d3e40ebcc2`;
- run manifest:
  `artifacts/runs/vifinqa_answer_optimization_20260830/formula_bridge_contract_ab_v1b/control/submission/run_manifest_v1.json`
  SHA-256 `2d95b09f07df476370fd525364d7343ae0162cc141690510febef448b2d5a98e`.

Candidate:

- submission:
  `artifacts/runs/vifinqa_answer_optimization_20260830/formula_bridge_contract_ab_v1b/candidate/submission/submission.json`
  SHA-256 `c917b0f5438c68477a5517f4fd2bbc7d59d33bdf36bfbeaafc306928467b24aa`;
- ZIP:
  `artifacts/runs/vifinqa_answer_optimization_20260830/formula_bridge_contract_ab_v1b/candidate/submission.zip`
  SHA-256 `84f7db00696224d39a1a237158ae9469eff1dd913c617e3d6e6c52118d3ff4a2`;
- build report:
  `artifacts/runs/vifinqa_answer_optimization_20260830/formula_bridge_contract_ab_v1b/candidate/submission/build_report.json`
  SHA-256 `67d2e9bef1dc778f6b56c423c444ec879ad209057245e73ab23bea1163d69076`;
- run manifest:
  `artifacts/runs/vifinqa_answer_optimization_20260830/formula_bridge_contract_ab_v1b/candidate/submission/run_manifest_v1.json`
  SHA-256 `c6d1deac322b4db51383eb4629f40930799494ef65632fafe341989cb24f4480`.

Contract:

- experiment contract:
  `artifacts/runs/vifinqa_answer_optimization_20260830/formula_bridge_contract_ab_v1b/EXPERIMENT_CONTRACT.md`.

## Decision

`INVESTIGATE_FURTHER`

Reason: the generalized contract and import provenance are technically
validated, but the candidate has no independent accuracy measurement, all
formula selections remain `PARTIAL`, and it produces no incremental answer
change over the previous formula bridge candidate. The next experiment should
bind a family-level semantic gold set and improve the verifier/selection
contract before any promotion decision.

## Authority status

`CANDIDATE_ONLY`

No selected value is `VERIFIED`, `PROMOTION_READY`, or
`RELEASE_AUTHORIZED`. No external Dashboard/Kaggle upload was performed.
