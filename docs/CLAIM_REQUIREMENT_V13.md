# V13 claim-requirement shadow audit

V13 is an additive audit overlay over the locked V12 replay. It does not edit
V12 bindings or certificates and it has no release, training, promotion, value
selection, execution, or submission authority.

## The new boundary

Diagnostic computation may run before semantic authorization, but an answer
may not be authorized until all of these independent contracts pass:

```text
Claim
  -> versioned Claim Requirement Set
  -> source-bound proof obligations
  -> Semantic Coverage Certificate
  -> Formula Definition Contract
  -> Operand Compatibility Matrix
  -> deterministic numeric execution
  -> Answer Certificate
```

The physical execution order is not the safety invariant. The invariant is
that no diagnostic receipt can cross the answer-authorization boundary by
itself.

## Completeness semantics

`INTERNALLY_COMPLETE` requires every required typed proposition to pass and
zero `NOT_CHECKED` dimensions. It does not mean the generator found every
meaning in the claim. `CLAIM_COMPLETE` additionally requires an independent,
versioned basis for the requirement universe. The hardened V13 run establishes
neither status for any record.

Missing meanings are never encoded as absence. Proof obligations retain five
distinct states:

```text
PASS | FAIL | UNRESOLVED | NOT_APPLICABLE | NOT_CHECKED
```

## Verified V13 shadow result

The frozen output is under
`artifacts/research/claim_requirement_coverage_v13_20260824_v7`.

- First-blocker partition: 595 composed, 353 route, 38 temporal, 26 V12
  certificate candidates.
- Composed blockers: 501 formula-definition incomplete and 94 operand-set
  incomplete. Relational compatibility remains a separate class and is not
  asserted before operands exist.
- Route blockers: 250 table-or-metric unresolved, 37 operator-definition
  unresolved and 66 cause-unestablished. Causal attribution remains false
  until a source-search audit proves it.
- Temporal blockers: 27 instant and 11 duration requirements derived from the
  claim independently of the source packet. Source period expressions remain
  unauthorized.
- The 26 V12 candidates were shadow-audited. None is internally complete under
  V13: generic V12 field PASS cannot replace typed metric, unit, formula,
  operand-set or compatibility receipts, and `NOT_CHECKED` blocks completeness.
- Q211 exposes `metric.tax_treatment=before_tax` as a separate unresolved
  proposition; parent role is not treated as reporting scope.
- Entity identity now fails closed as `NOT_CHECKED` when a raw ticker parse is
  incomplete or disagrees with route context; it no longer emits a false exact
  proposition for legal-name collisions such as “CTCP Chứng khoán FPT”. Eight
  claims that mention a subsidiary as the metric/counterparty while naming the
  reporting entity as parent now retain the parent-role obligation.
- Typed proposition anchors must co-refer to the value-cell document. Temporal
  PASS additionally requires internally consistent dates and years; missing
  comparison fields remain `UNRESOLVED`, while `FAIL` is reserved for a fully
  typed contradiction.
- All 1,012 records remain internal-coverage-incomplete and
  claim-completeness-unestablished. Release remains blocked.

## Rebuild

```bash
.venv/bin/python scripts/build_claim_requirement_coverage_v13.py \
  --config configs/claim_requirement_coverage_v13.json \
  --routes artifacts/research/production_coverage_iteration_v10_entity_role/route_completeness_overlay_v6.jsonl \
  --routes-manifest artifacts/research/production_coverage_iteration_v10_entity_role/route_completeness_overlay_v6.manifest.json \
  --periods artifacts/research/production_coverage_iteration_v11_period_title/period_column_candidate_packets_v2_exact_source_title.jsonl \
  --periods-manifest artifacts/research/production_coverage_iteration_v11_period_title/period_column_candidate_packets_v2_exact_source_title.manifest.json \
  --bindings artifacts/runs/vifinqa-grounded-e2e-relational-entity-role-v12_20260823-lock1/exact_cell_unit_binding_candidates_v6.jsonl \
  --bindings-manifest artifacts/runs/vifinqa-grounded-e2e-relational-entity-role-v12_20260823-lock1/exact_cell_unit_binding_candidates_v6.manifest.json \
  --certificates artifacts/runs/vifinqa-grounded-e2e-relational-entity-role-v12_20260823-lock1/answer_certificates_v1.jsonl \
  --certificates-manifest artifacts/runs/vifinqa-grounded-e2e-relational-entity-role-v12_20260823-lock1/answer_certificates_v1.manifest.json \
  --output-dir artifacts/research/claim_requirement_coverage_v13_NEW
```

The builder writes atomically, refuses overwrite, binds the config, builder and
requirement implementation hashes into a definition ID, validates every input
and the certificate-to-binding manifest edge, requires the same 1,012 question
IDs, and rejects overlapping or incomplete first-blocker partitions.
The config pins the exact SHA-256 of all eight locked V12 inputs/manifests; a
self-consistent artifact from another run is rejected. Each obligation ID binds
its full expected proposition, applicability, basis, dependencies and generator
definition—not merely the question ID and dimension.

Verify the frozen bundle independently after every build:

```bash
.venv/bin/python scripts/verify_claim_requirement_coverage_v13.py \
artifacts/research/claim_requirement_coverage_v13_20260824_v7/claim_requirement_coverage_v13.manifest.json
```

The verifier replays input/output hashes, deterministic IDs, dependency links,
all five row protocols, the exhaustive first-blocker partition, summary counts,
non-promotable source contracts and the blocked release invariant. It also
rebuilds the artifact from the verified locked inputs and byte-compares every
row output and the summary, so a forged manifest plus forged false PASS cannot
verify. `AUTHORITATIVE_VERIFIED_V13_SHADOW_ARTIFACT` additionally requires the
pinned official config SHA trust root. A self-consistent alternate run or
`--skip-inputs` can receive only `STRUCTURALLY_VERIFIED_V13_SHADOW_ARTIFACT`;
that status is explicitly untrusted and cannot feed the public snapshot.

## Evidence-closure workbench

V13 V7's blocker partition is operationalized by the separate, non-promotable
[evidence-closure workbench](V13_EVIDENCE_CLOSURE_WORKBENCH.md). It materializes
receipt-intake packets for formula, operands, route, temporal semantics, V12
recertification, an independent-requirement review universe and a blocked
production-ledger intake. This is review preparation only: no packet may turn
an existing diagnostic candidate into a `PASS` or an answer.
