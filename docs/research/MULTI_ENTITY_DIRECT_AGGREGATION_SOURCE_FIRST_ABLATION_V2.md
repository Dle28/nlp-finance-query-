# Multi-entity direct aggregation: source-first ablation v2 (operating cash flow)

Date: 2026-08-30  
Status: accepted local best-effort A/B; not an official leaderboard score

## Objective

This batch extends the narrow source-first multi-entity aggregation route to
one additional family: operating cash flow (`Lưu chuyển tiền thuần từ hoạt
động kinh doanh`). The target failure pattern is an explicit parent-company
question whose planner emitted a generic `count` plan even though the natural
language asks for a total. Q1003 is the representative case:

> Tổng lưu chuyển tiền thuần từ hoạt động kinh doanh năm 2015 của CTCP Masan
> High-Tech Materials công ty mẹ, CTCP Tập đoàn Hòa Phát công ty mẹ, CTCP Nhựa
> An Phát Xanh công ty mẹ và CTCP - Tổng công ty Phân bón Dầu khí Cà Mau công
> ty mẹ là bao nhiêu tỷ đồng?

The extension keeps the existing fail-closed contract:

- one explicit `mean` or `sum` operation; when the planner is inconsistent,
  the word-level question parser may infer `sum` only from an explicit total
  cue such as `Tổng`;
- at least two unique issuer tickers and exactly one requested reporting year;
- explicit parent-company/separate-reporting scope;
- no ranking, conditional, ratio, comparison, or threshold semantics;
- every issuer must replay from its own current structured table, with matching
  ticker, year, scope, statement kind, metric row, unit, and numeric cell;
- an ambiguous issuer, row, period, or column rejects the whole aggregate.

For tables that contain both `Tập đoàn` and `Công ty` columns, the route uses
the `Công ty` column only when the same table explicitly pairs that column with
the requested year and the parent/group header. This is important for Q1003:
the MSR group value is `41.706084` billion VND, while the requested parent
company value is `-417.067012` billion VND. The route also accepts the bounded
OCR/spacing variant `từhoạt động kinh doanh` for this metric; it does not use
unbounded fuzzy row matching.

The result remains a prediction candidate backed by current-table replay. It
does not turn retrieval metadata, deterministic arithmetic, or a proposal into
strict answer authority.

## Frozen A/B snapshot

Both arms used the same persisted snapshot before execution:

```text
builder_sha256: c2cb91fbb10c52e635052a86191d893d7b1c8d0d7ba77f6bf421ac767ae1231d
source_first_lookup_sha256: d68ed471ce73db8fe8f0f9f263b0cfdcc3fb223ce80821980a8603ca61385528
corpus_sha256: 617ae044499907cf302519b7ff6fb96fb86ab70ff08e6fdcb0e6a3f8df5251f7
source_line_map_sha256: 533cc779f9565f43dfc961ad03d7bdff65adbd3728ee7a60b61651d495c4c615
changed_during_build: false
```

The snapshot is preserved at
`artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_direct_aggregation_ab_v4/snapshot/`.
The path and implementation hashes are recorded in
`snapshot_dir.txt` and `snapshot_sha256.txt` in the same run directory.

The control disabled only
`--disable-source-first-multi-entity-direct-aggregation`; the variant enabled
the route. Both arms also disabled the report-year-neighbor and existing
multi-entity-threshold lanes and used identical input artifacts.

The frozen builder also contains a concurrently developed `lc_commitment`
specification. It was considered once but remained unresolved. It is not
counted as a CFO improvement and is not attributed to this extension. This
disclosure matters because the v4 snapshot is the authoritative same-snapshot
comparison, while a v3-to-v4 comparison would not be a clean incremental
measurement.

## Result

An independent comparison of the two v4 `submission.json` files, keyed by
question ID, found exactly five numeric answer changes:

| Question | Family and operation | Control | Variant | Entity set | Value policy |
|---|---|---:|---:|---|---|
| Q819 | credit-loss provision, mean | `5625.976` | `7980.8026666666665` in JSON; exact ledger `7980.8026666666666666666666666666666667` | MSB, BID, ABB | expense magnitude |
| Q827 | tax payable, mean | `6.387361021955` | `602.37564375975` | SNZ, VIC, DXS, HPX | source-signed |
| Q858 | selling expense, sum | `0.015537986326` | `3.591135562815` | SAB, DBC, MCH | expense magnitude |
| Q927 | tax payable, mean | `2.542217408` | `37.2454051892` | VPI, DIG, VRE, DXG, PDR | source-signed |
| Q1003 | operating cash flow, sum inferred from `Tổng` | `1.0` | `-106.069040067` | MSR, HPG, AAA, DCM | source-signed |

The first four rows are the previously accepted direct-aggregation family
inside this v4 snapshot. Q1003 is the new operating-cash-flow row. The five
rows are an attribution of the control/variant pair, not a claim that the
v3-to-v4 snapshot delta contains only Q1003.

Variant telemetry was:

```text
questions_considered: 6
questions_resolved: 5
questions_unresolved_or_ambiguous: 1
questions_skipped_not_eligible: 1006
operation_mean: 3
operation_sum: 2
kind_credit_loss_provision: 1
kind_tax_payable: 2
kind_selling_expense: 1
kind_operating_cash_flow: 1
kind_lc_commitment: 1  # concurrent spec; unresolved and not counted as a gain
```

The five resolved records use confidence tier
`source_first_multi_entity_direct_aggregation_v1`. The full variant audit
ledger remains `PARTIAL=1010`, `UNRESOLVED=2`, with
`local_verifier_authority=none`; no record was upgraded to `VERIFIED`.

## Q1003 source replay evidence

The variant replayed the requested parent-company CFO cell for each issuer:

```text
MSR  document MSR_financial_statements_2015_separate
     table 8c54e8157910e4de6e8b85e8b67623f79ca82e0d86aba65631fa3f4af0639051
     row 19, column 4, "Lưu chuyển tiền thuần từhoạt động kinh doanh"
     raw -417067012, source multiplier 1000

HPG  document HPG_financial_statements_2015_separate
     table ea3b071736ce9d596ebf5d024abf7ed7deeb94db91df45e628c00d6129407cf8
     row 18, column 3, "Lưu chuyển tiền thuần từ hoạt động kinh doanh"
     raw -45061179652, source multiplier 1

AAA  document AAA_financial_statements_2015_separate
     table e9b5f8c598e108486a9a8022635e0e84b5931b70820302ae339cf120ab9144c0
     row 18, column 2, "Lưu chuyển tiền thuần từ hoạt động kinh doanh"
     raw 29663853542, source multiplier 1

DCM  document DCM_financial_statements_2015_separate
     table 5e95e73f9b838eb174dbae088271c4c2e5427228b230aef133f6a85699ffbecb
     row 19, column 2, "Lưu chuyển tiền thuần từ hoạt động kinh doanh"
     raw 326395298043, source multiplier 1
```

After unit normalization to billion VND, the operands are:

```text
-417.067012 - 45.061179652 + 29.663853542 + 326.395298043
= -106.069040067
```

The exact cells, coordinates, raw values, and transformed operands are
preserved in `q1003_evidence.csv` and the prediction audit ledger. The
parent/group column guard is the key safety condition: selecting MSR's group
column would produce a different value and would silently change the
question's reporting scope.

## Integrity gates

Both v4 arms passed the local submission gates:

```text
records: 1012
queries_replayed: 1012
validation.errors: []
source-line map entries: 146246
map coverage: 146246 / 146246
source-line status: PASS
changed_during_build: false
control submission.zip: unzip -t PASS; 538614 bytes
variant submission.zip: unzip -t PASS; 538605 bytes
```

The independent arithmetic check used Decimal precision 38 and verified all
five changed answers from their evidence operands. Every evidence table UID
was present in the source-line map; the check returned
`LINE_MAP_CHANGED_EVIDENCE PASS`.

## Artifacts

- [v2 research report](../../docs/research/MULTI_ENTITY_DIRECT_AGGREGATION_SOURCE_FIRST_ABLATION_V2.md)
- [control build report](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_direct_aggregation_ab_v4/control/submission/build_report.json)
- [variant build report](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_direct_aggregation_ab_v4/variant/submission/build_report.json)
- [control submission ZIP](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_direct_aggregation_ab_v4/control/submission.zip)
- [variant submission ZIP](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_direct_aggregation_ab_v4/variant/submission.zip)
- [variant Q1003 evidence](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_direct_aggregation_ab_v4/variant/submission/data/q1003_evidence.csv)
- [variant prediction audit ledger](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_direct_aggregation_ab_v4/variant/submission/prediction_audit_ledger_v1.jsonl)
- [persisted snapshot hashes](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_direct_aggregation_ab_v4/snapshot_sha256.txt)

## Verification boundary and next gate

This batch shows five locally replayable candidate changes in a controlled
same-snapshot A/B pair. It does not establish an official score increase:
there is no local authoritative scorer or verified gold-answer artifact for
this public prediction setup. The candidates remain in the authorized
best-effort lane with `promotion_allowed=false`.

The next high-leverage family is explicit-scope multi-entity `count`/threshold
questions whose natural-language operator is recoverable but whose requested
rows can still be replayed independently. The first probe is positive CFO
count (Q973), with the same per-issuer and scope guards. It will be accepted
only after a separate test and same-snapshot A/B; missing or ambiguous cells
will remain unresolved.
