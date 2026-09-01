# Multi-entity direct aggregation: source-first ablation v1

Date: 2026-08-30  
Status: accepted local best-effort A/B; not an official leaderboard score

## Objective

The existing `program_multi_entity_plan` fallback could produce an answer for
multi-entity `mean`/`sum` questions without independently replaying the same
metric for every requested issuer. This ablation adds a narrow
source-first route for a small, high-precision family:

- one explicit `mean` or `sum` operation;
- at least two unique issuer tickers;
- exactly one requested reporting year;
- explicit parent-company/separate-reporting scope;
- no ranking, conditional, ratio, or comparison semantics;
- one of the currently admitted metric families: `Thuế và các khoản phải
  nộp Nhà nước`, `Chi phí bán hàng`, or `Chi phí dự phòng rủi ro tín dụng`.

The route creates a one-issuer proxy question for each requested ticker and
replays that proxy through the existing source-first direct lookup. It accepts
the aggregate only when every issuer has a current-table source with matching
ticker, year, scope, statement kind, row binding, unit, and numeric cell. A
missing or ambiguous issuer rejects the whole aggregate. This prevents a
partial entity set from silently becoming a multi-entity answer.

For expense families, the source cell's signed value is retained in
`raw_value`, while the query operand uses its absolute magnitude. The reason
is that the source tables represent expenses inconsistently: some income
statements use parentheses/negative values even though the formula subtracts
the expense, while another source can expose the same expense as a positive
value. The emitted answer therefore represents the expense amount, not the
presentation sign. This is an explicit semantic policy and remains a
best-effort candidate until independent semantic or gold validation is
available.

The route is a prediction improvement backed by source replay. It does not
turn retrieval metadata, deterministic arithmetic, or a proposal into strict
answer authority.

## Frozen A/B snapshot

Both builds used the same persisted snapshot before execution:

```text
builder_sha256: d429ffae13eeb92f322b446a3acca374ccfe8ac47dc5bb0432ec78b5b53e3513
source_first_lookup_sha256: d68ed471ce73db8fe8f0f9f263b0cfdcc3fb223ce80821980a8603ca61385528
corpus_sha256: 617ae044499907cf302519b7ff6fb96fb86ab70ff08e6fdcb0e6a3f8df5251f7
source_line_map_sha256: 533cc779f9565f43dfc961ad03d7bdff65adbd3728ee7a60b61651d495c4c615
changed_during_build: false
```

The snapshot is preserved at
`artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_direct_aggregation_ab_v3/snapshot/`.
The recorded path and implementation hashes are in
`artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_direct_aggregation_ab_v3/snapshot_dir.txt`
and
`artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_direct_aggregation_ab_v3/snapshot_sha256.txt`.

Control disabled only
`--disable-source-first-multi-entity-direct-aggregation`. Variant enabled the
route. Both builds also held the report-year-neighbor and existing
multi-entity-threshold lanes disabled, with all other inputs and flags held
constant.

## Result

An independent comparison of `control/submission/submission.json` and
`variant/submission/submission.json`, keyed by question ID, found exactly four
numeric answer changes:

| Question | Family and operation | Control | Variant | Entity set | Value policy |
|---|---|---:|---:|---|---|
| Q819 | credit-loss provision, mean | `5625.976` | `7980.8026666666665` in JSON; exact ledger `7980.8026666666666666666666666666666667` | MSB, BID, ABB | expense magnitude |
| Q827 | tax payable, mean | `6.387361021955` | `602.37564375975` | SNZ, VIC, DXS, HPX | source-signed |
| Q858 | selling expense, sum | `0.015537986326` | `3.591135562815` | SAB, DBC, MCH | expense magnitude |
| Q927 | tax payable, mean | `2.542217408` | `37.2454051892` | VPI, DIG, VRE, DXG, PDR | source-signed |

The JSON submission uses ordinary numeric serialization for Q819, which is
why its displayed value is shorter than the exact Decimal value retained in
the audit ledger and evidence CSV. The full Decimal calculation is the
authoritative diagnostic representation for this research artifact.

Variant route telemetry:

```text
questions_considered: 4
questions_resolved: 4
questions_skipped_not_eligible: 1008
operation_mean: 3
operation_sum: 1
kind_credit_loss_provision: 1
kind_tax_payable: 2
kind_selling_expense: 1
promotion_allowed: false
machine_artifact_is_not_human_verified: true
```

The route changed four confidence-tier records to
`source_first_multi_entity_direct_aggregation_v1`. It did not upgrade them to
`VERIFIED`; the audit ledger class for the proposals is `PARTIAL` with
`authority=none`.

## Source replay evidence

The variant evidence files contain one row per entity, including document ID,
table UID, row/column coordinates, raw source cell, source multiplier, and the
transformed operand value.

### Q819 — credit-loss provision mean

The replayed source rows are:

```text
MSB  b287a07d5e33be65af6fb5afb92bc936880371cb34aa0ec9a016e516b1ca0edb
     row 16, column 2, "Chi phí dự phòng rủi ro tín dụng", raw -1924445
BID  3247be90831a1a31455559068bb92101f96764edea26cd1f24dbea006a468069
     row 17, column 3, "X. Chi phí dự phòng rủi ro tín dụng", raw -20606172
ABB  c5680f126fd82341057881f046196026a3f339edfeebf30ede1bf45d903266de
     row 16, column 3, "X Chi phí dự phòng rủi ro tin dụng", raw -1411791
```

All three documents are 2024 separate financial statements and use a
million-VND source multiplier. The operand magnitudes are
`1924.445`, `20606.172`, and `1411.791`; their mean is the exact Decimal
answer recorded above.

### Q827 and Q927 — tax payable means

All requested issuers replayed the row family
`Thuế và các khoản phải nộp Nhà nước` from their own-year separate statement.
The complete coordinates and raw cells are preserved in the evidence CSV and
audit ledger. The resulting operands are:

```text
Q827: SNZ 15.344409381; VIC 2050.099; DXS 185.442713325; HPX 158.616452333
Q927: VPI 2.542217408; DIG 26.323895303; VRE 35.068093552;
      DXG 69.976249971; PDR 52.316569712
```

The source multipliers are retained per entity because the corpus includes
both raw VND cells and million-VND cells. The replay normalizes each operand
to the question's requested unit before applying `mean`.

### Q858 — selling-expense sum

The source rows are:

```text
SAB  713257432e477374b8c8f9881fe097431045e6c580924ec3a65722259b0dc7a8
     row 9, column 3, "Chi phí bán hàng", raw -1446841604384
DBC  81cd27fc3fa474877106252b28c64937c1494a9cfc5d6f60fb1c1b09bc3382b2
     row 9, column 3, "8. Chi phí bán hàng", raw -83645537443
MCH  e382549af3d06f825b550de31e2cc3b66bb646fad7e70bcbd54d16be3b547055
     row 9, column 3, "Chi phí bán hàng", raw 2060648420988
```

The normalized expense magnitudes are `1.446841604384`,
`0.083645537443`, and `2.060648420988` nghìn tỷ, summing to
`3.591135562815`. The evidence and ledger preserve the negative SAB/DBC
source cells rather than hiding the presentation-sign transformation.

## Integrity gates

Both control and variant passed the local build gates:

```text
records: 1012
queries_replayed: 1012
validation.errors: []
source-line map entries: 146246
map coverage: 146246 / 146246
source-line status: PASS
changed_during_build: false
control submission.zip: unzip -t PASS; 538545 bytes
variant submission.zip: unzip -t PASS; 538481 bytes
```

Artifacts:

- [control build report](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_direct_aggregation_ab_v3/control/submission/build_report.json)
- [variant build report](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_direct_aggregation_ab_v3/variant/submission/build_report.json)
- [control submission ZIP](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_direct_aggregation_ab_v3/control/submission.zip)
- [variant submission ZIP](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_direct_aggregation_ab_v3/variant/submission.zip)
- [variant Q819 evidence](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_direct_aggregation_ab_v3/variant/submission/data/q0819_evidence.csv)
- [variant Q827 evidence](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_direct_aggregation_ab_v3/variant/submission/data/q0827_evidence.csv)
- [variant Q858 evidence](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_direct_aggregation_ab_v3/variant/submission/data/q0858_evidence.csv)
- [variant Q927 evidence](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_direct_aggregation_ab_v3/variant/submission/data/q0927_evidence.csv)
- [variant audit ledger](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_direct_aggregation_ab_v3/variant/submission/prediction_audit_ledger_v1.jsonl)

The variant report records 1,010 `PARTIAL` and 2 `UNRESOLVED` verification
classes, `local_verifier_authority=none`, and no strict certificate. These
figures describe the full submission artifact, not only the four changed
questions.

## Scope, limitation, and next gate

This experiment demonstrates four source-replayable candidate changes. It does
not establish that all four are correct against a hidden gold set, and it does
not establish an official score increase. The public/local setup used for
this ablation has no authoritative scorer or verified gold answer artifact.
The route is intentionally labeled
`authorized_best_effort_submission_candidate`, with
`promotion_allowed=false`.

The main residual risk is semantic interpretation of the requested metric and
expense sign. The next gate is independent semantic review or gold-backed
evaluation of these four rows, followed by a separate submission decision.
The next research family should remain family-level and fail-closed: inspect
the other explicit-scope multi-entity metrics only when every entity has an
unambiguous current-table replay. Questions with missing issuers, unspecified
scope, or mixed annual/monthly semantics stay unresolved rather than being
opened by fuzzy matching.
