# Q261 financial-liability maturity rebase audit v1

## Outcome

This audit independently replays the generic financial-liability maturity
family for Q261, but it does **not** add a new answer to the current
integrated candidate. Q261 already had the variant answer `174052754.0` in
the Q73, Q74, and Q77 lineage. The newly materialized q78 package is
therefore a byte/value-level no-op relative to Q77 and is retained as an
audit artifact, not counted as a score gain or a new replacement.

## Required handoff fields

**Hypothesis/family:** A question asking for a contractual financial-liability
maturity total should bind a financial-note liability schedule, the requested
date header, the `TỔNG CỘNG` row, the `Tổng cộng` column, consolidated scope,
and the million-VND unit. The reusable rule rejects comparative-date columns,
asset-maturity tables, component rows, and scope/unit mismatches. It is a
family contract; Q261 is tracking metadata only and does not control routing
or selection.

**Control fingerprint:** The frozen family control is
`artifacts/runs/vifinqa_answer_optimization_20260830/financial_liability_total_ab_v2/control/submission/`.
Its submission SHA-256 is
`67b6a57620bff84d4c278a707811d1bb7d9ba90d0d2ed6c95fce5b6bb8d353ec`, and its
build-report SHA-256 is
`5a05162de61e64d066eecd6cfc27c5dc2e5c66d9fd70d01994ebdd4ea67e954f`.
The frozen builder SHA is
`09a63768f06e5762ae5c1de1c7569d67f0b130d6f10c407c710297400b2d2d84`; the
source-first lookup SHA is
`c4c8c6495e9e232e2e44c4febe09852d6765d9c04a0308fa95cd0f3b27d2995b`.

**Candidate fingerprint:** The full-population family variant is
`artifacts/runs/vifinqa_answer_optimization_20260830/financial_liability_total_ab_v2/variant/submission/`.
Its submission SHA-256 is
`eb6f8c17513e41f5a3fa6ffdbb186502ea990b27fd0de50f776ba92086567084`, and its
build-report SHA-256 is
`1234ebbc2091796401caf66f4c888dd9e7112c354f07e1087cf4862d5d7c1841`.
The independent checker is
`scripts/research/replay_financial_liability_q261_v1.py` with SHA-256
`43d7eca991d10f998e9ccfcaccd009c05ea8b77d959ea93b60d35617188ffe14`.

**Population and split:** The family A/B uses the complete frozen ViFinQA
population of 1,012 records. No question-ID tuning or held-out population was
used in the family comparison. The integrated rebase starts from the
77-replacement Q77 candidate and has the same 1,012 records.

**Scorer/gold:** No independent gold set or official scorer is available for
this local comparison. `ANSWER_ACCURACY=NOT_MEASURED` and
`EXECUTION_ACCURACY=NOT_MEASURED`. Deterministic source replay is provenance
evidence, not an accuracy scorer.

## Family A/B result

The frozen family control uses the prior component-row answer
`16496708.0`. The variant uses the contextual maturity total
`174052754.0`:

| Population comparison | Result |
|---|---:|
| Changed answers | 1 (Q261: `16496708.0 -> 174052754.0`) |
| Unchanged answers | 1,011 |
| Missing records | 0 |
| Errors | 0 |
| Non-target changed records | 0 |
| `ANSWER_ACCURACY` | `NOT_MEASURED` |
| `EXECUTION_ACCURACY` | `NOT_MEASURED` |

Both family arms validated and replayed 1,012/1,012 records with
`errors=[]`. The source candidate changed Q261 to tier
`source_first_reclassified_direct_v1`; this tier transition is diagnostic and
is not an accuracy claim.

## Independent source replay

The replay artifact is
`artifacts/runs/vifinqa_answer_optimization_20260830/financial_liability_total_ab_v2/independent_replay_q261_v1.json`
with SHA-256
`4d71405c0c6f695b83b191e314393d953ec2627617761a23a216f8174aa07afc`.
It reports `status=PASS`, `failures=[]`, `authority_status=CANDIDATE_ONLY`,
`promotion_allowed=false`, and no official scorer. It independently checks the
exact table UID, source/table hashes, byte and character coordinates, source
line, financial-note context, liability section, maturity headers, requested
date, total row, total column, and million-VND unit.

The replayed source is:

```text
document_id: BVH_financial_statements_2019_consolidated
internal_table_uid: d3eff4436ee4bd8227a656ad9b18952a1a49f08dba8c86f03f0c13e4534e528f
source_line: 2958
source_sha256: 9e9762cd6c305ea70e40561255b3528230b9684e449667bce693576f710a990e
table_sha256: 67c05eec239e176e6d3a38c9e515bdcd5bc189595f115411e7cccad7bd2d4687
report_year: 2019
scope: consolidated
row: TỔNG CỘNG
row_index: 8
column: Tại ngày 31 tháng 12 năm 2019
column_index: 6
raw_value_million_vnd: 174.052.754
replayed_value_million_vnd: 174052754
```

## Integrated rebase audit

The no-op package is
`artifacts/runs/vifinqa_answer_optimization_20260830/integrated_candidate_q78_financial_liability_after_q77_generic_v1.zip`
with SHA-256
`1653d54edceda804844a4999cb985997a6276425b1123c020f3861b5a4a1129e`.
Its submission SHA-256 is
`85c03ca842064b29ab93aede3e9c74dbc69aca35205f66942c16499b7a7d5faf`, the
same value as the Q77 base submission. Its report SHA-256 is
`cdb7c68603dfbcbad8e50d149efeacfed79d9bbc84343396eff614e4ac49789f`.

The generic materializer records
`replacement_ids_are_explicit=false` and
`replacement_selection_basis=independent_replay_source_uid_exact_set`, with
one independently replayed UID closure. It preserves all unselected rows and
introduces no arithmetic. However, comparison of Q77 and q78 shows:

| Integrated comparison | Result |
|---|---:|
| Changed answers | 0 |
| Unchanged answers | 1,012 |
| Missing records | 0 |
| Non-target changed records | 0 |
| ZIP members / evidence CSVs | 1,013 / 1,012 |
| Source validation | 1,012/1,012, `errors=[]` |
| Final validation | 1,012/1,012, `errors=[]` |
| `unzip -t` | PASS |

The one recorded q78 route override is metadata describing the independently
replayed Q261 source; it is not a new answer delta because the same answer and
evidence were already inherited by Q77. The package is consequently
superseded as an integration step while remaining useful as provenance for
the Q261 replay audit.

## Decision and authority

**Decision:** `KEEP` the family-level source replay evidence and the existing
Q261 candidate already present in the active lineage; do not count q78 as a
new improvement. Continue with a residual family whose answer is not already
present in Q77.

**Authority status:** `CANDIDATE_ONLY`. No human semantic approval, official
score, strict certificate, promotion, or release authorization is established.

## Regression gates

After the generic materializer and checker changes:

```text
pytest -q: 721 passed, 1 skipped
git diff --check: PASS
q78 submission.zip: unzip -t PASS
```
