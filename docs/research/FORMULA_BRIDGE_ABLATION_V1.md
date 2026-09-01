# Formula bridge full-population A/B v1

Date: 2026-08-31

## Hypothesis/family

For the reusable formula family represented by the controlled formula sidecar,
an independently table-bound and Decimal-replayed formula plan may recover
answers that the current semantic-cell / heuristic routes miss. The candidate
must use exact current-table coordinates, a known AST, and source provenance;
it must not copy a stored sidecar answer. Formula family and operand contracts,
not `question_id`, define the rule.

## Control fingerprint

- immutable snapshot:
  `artifacts/runs/vifinqa_answer_optimization_20260830/contextual_score_lift_ab_v4/snapshot`
- builder SHA-256:
  `ac3a7a798e792dc1368b800a6bbf831cf951a10f43a34f7e7d49db8361974a3d`
- source-first lookup SHA-256:
  `72fd26a0d4ab715ec883811e4038aebd854d540b77fed781658701ce432f2bc9`
- answer-level selector SHA-256:
  `aded34a7aa7e68a6a5dbb57c1dc873b6af33425c59b5a362baf852464e07df29`
- formula bridge module SHA-256:
  `71dec6825d7b2158c109423f54c20e8caf141defb8a95be76ef717e75a2d4a8c`
- proposal verifier SHA-256:
  `0179d78644ee7acb7bd9c0b7cd94264c8ed82dd852dab6ab25dcbb1e67bb42f4`
- formula bridge flag: disabled

## Candidate fingerprint

The candidate uses the identical code snapshot and all identical inputs/route
flags. Its only experimental difference is:

- `--enable-formula-evidence-bridge`
- sidecar:
  `artifacts/kaggle_runs/notebook5554bd790d_v10_20260811/vifinqa_review_bundle/formula_evidence_sets_typed_v1.jsonl`
- sidecar SHA-256:
  `7fdc680f3fba224b72161c6dec768b45d0d77064a6020abfd66d6c81bd9188d2`

The answer-level selector remained enabled in both arms with a maximum pool of
64. Candidate sidecar answers were not copied; the bridge replayed bound cells
with the Decimal executor.

## Population and split

- complete ViFinQA population: 1,012 questions
- split: no tuning split in this final A/B; both arms ran the complete input
- questions SHA-256:
  `64a428d90a8c5ad5d36a397d2de3b6e3aa4e4c1224dcdcb118fe3a4fca056ff0`
- full structured table asset SHA-256:
  `617ae044499907cf302519b7ff6fb96fb86ab70ff08e6fdcb0e6a3f8df5251f7`
- source-line map SHA-256:
  `533cc779f9565f43dfc961ad03d7bdff65adbd3728ee7a60b61651d495c4c615`

## Scorer/gold

An independent scorer or gold binding for these exact two output hashes was not
available locally. A historical official receipt for a different submission
hash was deliberately not reused. Scorer command and exit status:
`NOT_MEASURED`.

ANSWER_ACCURACY: control=NOT_MEASURED candidate=NOT_MEASURED delta=NOT_MEASURED

EXECUTION_ACCURACY: control=NOT_MEASURED candidate=NOT_MEASURED delta=NOT_MEASURED

## Full-population A/B result

Both arms passed the local delivery/replay gates:

| measure | control | candidate |
|---|---:|---:|
| questions | 1,012 | 1,012 |
| predicted | 970 | 970 |
| fallback-zero | 42 | 42 |
| nonzero answers | 957 | 957 |
| validation records | 1,012 | 1,012 |
| replayed queries | 1,012 | 1,012 |
| missing records | 0 | 0 |
| errors | 0 (`errors=[]`) | 0 (`errors=[]`) |
| source-line-map entries | 146,246/146,246 | 146,246/146,246 |
| evidence files | 1,012 | 1,012 |
| diagnostics lines | 1,012 | 1,012 |
| audit lines | 1,012 | 1,012 |
| ZIP test | exit 0 | exit 0 |
| verifier class `PARTIAL` | 968 | 968 |
| verifier class `UNRESOLVED` | 44 | 44 |

Serialized answer comparison over all 1,012 records:

- improved: `NOT_MEASURED` (no gold)
- regressed: `NOT_MEASURED` (no gold)
- unchanged answers: 1,003
- changed answers: 9
- unresolved: 44 in each arm by local verifier class
- non-target answer changes: 0; all 9 changes are in the formula candidate set

Changed answers (diagnostic only; not correctness claims):

| question | formula family | control | candidate |
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

Question 644 changed tier only: its serialized answer remained
`-52.401960937079814`, while its tier changed from
`program_growth_heuristic` to `formula_evidence_replay_v1`.

## Family-level results

| formula family | built | selected | answer changes | tier changes | affected IDs |
|---|---:|---:|---:|---:|---|
| `percentage_change` | 2 | 2 | 1 | 2 | 586, 644 |
| `net_finance_result` | 2 | 2 | 2 | 2 | 666, 677 |
| `explicit_stated_fraction` | 2 | 2 | 2 | 2 | 672, 705 |
| `long_term_investment_to_equity` | 1 | 1 | 1 | 1 | 675 |
| `current_liabilities_to_equity` | 2 | 2 | 2 | 2 | 678, 710 |
| `net_other_income` | 1 | 0 | 0 | 0 | 702 |
| `operating_cash_flow_argmax_period` | 1 | 1 | 1 | 1 | 960 |
| **total** | **11** | **10** | **9** | **10** | — |

The sidecar had 78 rows. It rejected 67 rows before candidate construction,
including 52 incomplete operand-coverage rows, 6 unsupported formulas, 4
ambiguous scopes, and other missing/undefined operands. This is diagnostic
coverage, not an accuracy estimate.

## Selector/verifier finding

The bridge selected 10 formula proposals in the legacy proposal pool, but the
whole-question answer-level selector abstained on each affected formula plan.
The recorded rejection reasons included operation-AST, period, entity, and
unit mismatches against the existing question contract. The selected formula
proposals nevertheless remained `PARTIAL` locally: source binding and
provenance passed, while semantic, temporal, scope/entity, unit, formula, and
deterministic-authority checks were unresolved. This identifies the next
implementation bottleneck: reconcile formula plans with the family-level
question contract and make the selector decision the single selection gate,
without using a Question-ID exception or granting authority from replay alone.

## Diagnostic-only metrics

- candidate formula rows read: 78
- formula candidates built: 11
- formula candidates selected: 10
- stored sidecar answers used: false
- formula bridge selected tiers: 10
- answer-level selector: control selected 295 / abstained 717; candidate
  selected 294 / abstained 718
- local verifier: `PARTIAL=968`, `UNRESOLVED=44` in both arms
- no strict certificate was supplied; no proposal became `VERIFIED`

## Artifact paths and hashes

Contract:
`artifacts/runs/vifinqa_answer_optimization_20260830/formula_bridge_ab_v4/EXPERIMENT_CONTRACT.md`

Control:

- submission:
  `artifacts/runs/vifinqa_answer_optimization_20260830/formula_bridge_ab_v4/control/submission/submission.json`
  SHA-256 `81168a73f7748b3193aade069bbb1cfaf8d64ffab99bb416ec84ddfa161d0bb6`
- ZIP:
  `artifacts/runs/vifinqa_answer_optimization_20260830/formula_bridge_ab_v4/control/submission.zip`
  SHA-256 `b9eb4ccdee92ff98a9a717e29892a4765a0346a26969f4768790c2951a50f61d`
- build report:
  `artifacts/runs/vifinqa_answer_optimization_20260830/formula_bridge_ab_v4/control/submission/build_report.json`
  SHA-256 `ae37d60417785b17ae3cac1eedaa2e39311a4de795094a303c786911517d80c4`
- run manifest:
  `artifacts/runs/vifinqa_answer_optimization_20260830/formula_bridge_ab_v4/control/submission/run_manifest_v1.json`
  SHA-256 `81b98ab4ea91c2cb559cecaaf546049edae6ddd3e0810149602d6657a2d6173b`

Candidate:

- submission:
  `artifacts/runs/vifinqa_answer_optimization_20260830/formula_bridge_ab_v4/candidate/submission/submission.json`
  SHA-256 `c917b0f5438c68477a5517f4fd2bbc7d59d33bdf36bfbeaafc306928467b24aa`
- ZIP:
  `artifacts/runs/vifinqa_answer_optimization_20260830/formula_bridge_ab_v4/candidate/submission.zip`
  SHA-256 `20437b7fec32efca7495a92ead6ec937bca924198cb99b6f8100bf42a9e4830f`
- build report:
  `artifacts/runs/vifinqa_answer_optimization_20260830/formula_bridge_ab_v4/candidate/submission/build_report.json`
  SHA-256 `f0972ab36f8c543ec894c3980f340683d6fc3333d118e8fbc0d101b7780226b3`
- run manifest:
  `artifacts/runs/vifinqa_answer_optimization_20260830/formula_bridge_ab_v4/candidate/submission/run_manifest_v1.json`
  SHA-256 `e03f5e8712c5a721cff0772be84af537c7a125366c057f8bed9cecbfa1d040a7`

## Decision

Decision: `INVESTIGATE_FURTHER`

Reason: the candidate is full-population and structurally reproducible, and it
changes nine formula-family answers, but no independent scorer is bound to
these exact artifacts. In addition, the selector/verifier mismatch makes the
current bridge unsuitable for a keep/promotion decision until the formula
plan contract is reconciled and the exact candidate is independently scored.

Authority status: `CANDIDATE_ONLY`
