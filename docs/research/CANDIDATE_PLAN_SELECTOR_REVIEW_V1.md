# CandidatePlan / Answer-Level Selector — Independent Audit V1

Audit status: **COMPLETE**. Production status: **NOT READY FOR LIVE FLIP**.

This is an independent Agent D review of the current CandidatePlan/answer-level selector design against the v2 control. The audit is read-only for production and existing tests. The only files written are this report and [audit_summary.json](/home/dungle/Documents/AI_guru/artifacts/research/candidate_plan_selector_audit_v1/audit_summary.json).

## Executive conclusion

The brief's target architecture is not present at the actual submission boundary yet. The current path is:

`route if/elif -> one primary answer -> primary plus bounded semantic-cell alternatives -> coordinate/provenance verifier -> (verification class, route_priority, retrieval_score)`

It is not:

`question -> all route-family CandidatePlans -> independent structural/semantic checks -> deterministic replay -> answer-level selector -> submission`.

The material risk is concrete and reproduced in-memory: a composed proposal with answer `999`, an incomplete `sum` AST and only one source cell whose value is `10` receives `PARTIAL` with no failure, then wins over a lower-priority proposal with answer `10` because `route_priority` is compared before formula/operand/replay completeness. This is a selector correctness defect, not a scorer hypothesis.

The v2 control remains the safety baseline at `ANSWER_ACCURACY=0.1917` and `EXECUTION_ACCURACY=0.1917`. The current control submission is candidate-capable but has zero strict authorized answers in its canonical E2E receipt. A CandidatePlan selector may improve the separately labelled best-effort prediction lane, but it must not create strict authority. The safe recommendation is shadow-first, baseline-preserving, family-level evaluation with route priority as a final tie-break only.

## Control and evidence snapshot

The v2 score was read from `/home/dungle/Downloads/scoring_result (9).zip` (SHA-256 `3b3164ca0af931fae8a7e263ff4db96e7ffe050139bf3b45b319c98ae97d02ee`). The paired prediction artifact is `/home/dungle/Downloads/prediction_result (7).zip` (SHA-256 `0b739ed333e1c86652222b69c4a464f218578c079d081057fc1b8de139ab1784`). The local upload-ready ZIP is [vifinqa_full_integrated_subsidiary_investment_best_effort_v2_20260831.zip](/home/dungle/Documents/AI_guru/submission_ready/vifinqa_full_integrated_subsidiary_investment_best_effort_v2_20260831.zip), SHA-256 `18e57850637e7efa1ae012eeaab9d6ef24e9cd0fac82984e9693f756f6db6c3a`.

Control facts:

- 1,012 questions; 971 predicted; 41 fallback; 958 nonzero answers.
- Local proposal verification counts: `PARTIAL=969`, `UNRESOLVED=43`.
- Strict authorized answer count: `0` in the canonical E2E receipt.
- The downloaded prediction's `submission.json` matches the local v2 submission content; the local JSON SHA-256 is `18f898092f761049319b83c7eb020661cab54e5fbf7d6d73b24c8e729d89d9f6`.
- ZIP integrity passed for `submission.json` plus 1,012 evidence CSVs.

The downloaded score is the control evidence for this audit. No local gold labels or scorer outcomes were used to construct a selector or tune a route.

## Actual integration points

### 1. `question_compiler`

Evidence: `src/finance_query/e2e/question_compiler.py:501-623`.

The compiler selects one typed route through a mutually exclusive `if/elif` chain: reported lookup, controlled formula, existing typed plan, simple structure, or abstention. It emits one typed plan with `operation_ast` and operands, but the plan is explicitly `answer_eligible=false`, `training_eligible=false`, `submission_eligible=false`, and `provenance_promotion_allowed=false`.

This is a useful planner/shadow primitive, not a CandidatePlanSet producer. Source scan shows the canonical submission builder does not import it. There is no answer-level union of complete plans at this boundary.

### 2. `decimal_executor`

Evidence: `src/finance_query/e2e/decimal_executor.py:22-68,329-360,536-815`.

The module has the strongest reusable pieces for the proposed design: operator contracts, AST validation, exact bindings, source table/context revalidation, unit/scope checks and Decimal replay. `execute_typed_plan_shadow` accepts one typed plan and intentionally returns non-authoritative shadow output.

The canonical E2E pipeline does not call this executor to select a submission answer. The pipeline imports it for source-code closure/hash context (`src/finance_query/e2e/pipeline.py:25-38,80-145`), while canonical grounded execution uses the separate V2 decimal-sandbox path. The primitive is therefore available for a selector implementation, but it is not currently wired as the answer-level gate.

### 3. `proposal_verifier`

Evidence: `src/finance_query/e2e/core/proposal_verifier.py:156-255`.

The verifier checks that evidence coordinates resolve to a current table/cell and, when supplied, that the claimed raw value matches that cell. Without a complete canonical certificate it can return `PARTIAL`; it does not independently evaluate the proposal's `operation_ast`, operand count/roles, `pandas_query`, or answer arithmetic. The corresponding check fields remain `UNRESOLVED` unless a strict certificate upgrades the whole proposal.

This separation is safe for authority, but insufficient for the brief's answer-level selector. A coordinate match is not a composed-answer proof.

### 4. Canonical E2E pipeline and authorization

Evidence: `src/finance_query/e2e/pipeline.py:227-310,348-362,420-445`; `src/finance_query/e2e/core/grounded_authorization.py:1414-1476,1709-1753`; `src/finance_query/e2e/core/grounded_execution_v2.py:48-57,409-555`.

The canonical path is exact-cell binding, numeric cell-token materialization, grounded Decimal execution and authorization. Its contract keeps candidate output separate from strict authority. On `ABSTAIN`, the authorizer clears strict `answer`/`answer_decimal` fields and retains a candidate only under the explicitly named best-effort lane; release, submission, training and promotion remain false.

This is the minimum boundary the selector must preserve. A selector may choose a better candidate for a labelled prediction lane; it may not turn route metadata, a model score or a shadow replay into an EvidenceBinding or complete Answer Certificate.

### 5. Submission builder

Evidence: `scripts/e2e/build_competition_submission_v1.py:12142-12320,12322-12429`.

The builder initializes a fallback answer, obtains route outputs, and selects the first non-null result in a long `if/elif` chain. Only after that decision does it build proposals: one primary route plus up to the configured bounded semantic-cell alternatives. It then calls `verify_proposed_answer` and `select_best_proposal`.

Therefore the current proposal list is not all route-family answers and not a set of independently replayable CandidatePlans. The selector cannot recover alternatives already discarded by the branch chain.

## Where `route_priority` can make a composed answer wrong

The priority map is at `scripts/e2e/build_competition_submission_v1.py:3089-3120`. The selector comparator is at `src/finance_query/e2e/core/proposal_verifier.py:259-283`:

```text
(verification_class_rank, route_priority, retrieval_score)
```

Because both a coordinate-bound composed proposal and a semantic-cell proposal can be `PARTIAL`, `route_priority` decides between them before the formula contract, operand completeness or deterministic replay has passed. The composed route is assigned `92.25`; `program_multi_entity_plan` is assigned `70.0`.

The read-only behavioral probe used two in-memory proposals against one table cell with value `10`:

```text
bad-composed: answer=999, AST=sum(x0,x1), one evidence cell, route_priority=92.25
lower-program: answer=10, AST=lookup(x0), same evidence cell, route_priority=70.0
```

Observed result:

```text
bad-composed verification=PARTIAL
bad-composed source_binding=PASS
bad-composed formula_contract=UNRESOLVED
bad-composed deterministic_execution=UNRESOLVED
bad-composed failures=0
selected=bad-composed, answer=999
```

This is the exact failure class the new selector contract must prevent. It does not require a Question-ID exception; it requires a family-independent hard gate for AST/operand/replay completeness.

There is a second policy mismatch. The builder's route branch order is at `:12190-12292`, while the numeric map is at `:3089-3120`. For example, the branch can commit to a reclassified direct route before the composed/temporal alternatives are even considered, while the numeric map gives several of those alternatives a higher tie-break value. Since the builder has already discarded the non-primary route objects, the later priority map cannot reconcile the two policies.

## Minimum non-breaking CandidatePlan contract

The first rollout should be additive and shadow-only. A plan is a candidate record, not an authority record.

Required top-level fields:

- `schema_version`, `protocol`, `question_id`, `candidate_id`, `route_family`.
- Canonical `operation_ast` plus a fingerprint of the canonical AST and ordered operand bindings.
- `operands[]`, each with `operand_id`, `source_uid`, `internal_table_uid`, `row_index`, `column_index`, `period`, `entity`, `scope`, `unit`, and `raw_value`.
- `pandas_query` as diagnostic text only.
- `replay_answer_decimal`, recomputed by the deterministic executor from the AST and operands.
- `source_hash`, computed from immutable source/table/coordinate inputs and rechecked at consumption.
- Independently derived `semantic_completeness`, `verification_status`, `eligibility` and machine-readable `reason_codes`.

Before ranking, hard-reject a plan with a missing/extra/duplicate AST leaf, missing coordinate, wrong period/entity/scope/unit, source-lineage/hash mismatch, invalid operator/arity/direction, ambiguous period-extreme winner, raw-cell mismatch, replay mismatch or `REJECTED` verification. A self-reported `answer_decimal`, AST, query, completeness flag or hash cannot satisfy these checks.

Selection order must be:

1. Hard-gate eligibility.
2. Complete canonical certificate / `VERIFIED` status where one exists.
3. Complete AST and operand coverage.
4. Source/provenance integrity.
5. Independent deterministic replay match.
6. Semantic-completeness evidence.
7. `route_priority` only as a final tie-break.
8. Retrieval score only as the final deterministic tie-break after the above.

If no new plan passes, retain the existing v2 primary candidate as a separately labelled best-effort candidate. Do not manufacture zero merely because the selector abstains. If the selector is disabled, the output must be byte-identical to the v2 control.

## Leakage and authority risks

The existing candidate-validity module is appropriately conservative. Evidence at `src/finance_query/e2e/core/candidate_validity.py:577-717,720-806` shows that it computes coverage, group support and ranking metadata; it explicitly does not prove metric/column/condition decomposition or authorize an answer. It also does not use the numeric answer as a feature.

The selector must retain these boundaries:

- Never feed gold answers, external scorer outcomes or post-submission feedback into candidate features, route priority, structural eligibility or AST repair.
- Never treat a model validity probability as semantic proof. The existing model remains ranking-only.
- Never treat `pandas_query` as executable authority; recompute from structured bindings and the canonical AST.
- Never treat `source_hash` as semantic proof. It protects lineage/integrity only.
- Recheck exact source UID/table/row/column/period/entity/scope/unit and quarantine duplicate or ambiguous provenance.
- Evaluate and report by route family and plan shape. Do not add per-Question-ID exceptions.

## A/B acceptance gates

Stage A is the current v2 control with fixed inputs, config and source snapshot. Stage B is the CandidatePlanSet plus selector in shadow mode first.

| Gate | Required evidence | Decision |
|---|---|---|
| Schema/lineage | 1,012 questions; unique candidate IDs; resolvable source hashes; deterministic AST/operand fingerprints; input hashes unchanged; selection reason codes present | Must pass before any score comparison |
| Safety | Zero selected hard-rejected plans; zero selected replay mismatches; zero missing operands on complete plans; strict-authorized count unchanged at 0; ZIP has 1,012 records/data files and passes `unzip -t` | Any failure is immediate rollback/no-op |
| Control parity | Selector disabled/no eligible plan produces byte-identical control output | Required for shadow and fallback behavior |
| External non-regression | Paired scorer B has `ANSWER_ACCURACY >= 0.1917` and `EXECUTION_ACCURACY >= 0.1917` | Do not promote B below either control metric |
| Claimed lift | Policy target is at least `+0.0020` on both metrics, with a repeated/authoritative scorer receipt and no safety regression | Only then claim a meaningful improvement |

There is no local gold set available for this audit. Consequently, semantic-ranking improvement is `NOT_MEASURED` until an external scorer or an independently held-out gold evaluation is run. Results should be reported by route family/plan shape, not by adding Question-ID patches.

## Rollback criteria

Disable the selector feature flag and retain the current v2 route-first builder if any of the following occurs:

- A selected plan violates a hard gate or has a source-hash/replay mismatch.
- Strict authority, release, training, promotion or submission permission changes without an independent complete canonical certificate.
- Disabled-mode or no-eligible output is not byte-identical to control.
- Either external scorer metric falls below `0.1917`.
- Question/data-file count, ZIP integrity, input immutability or deterministic fingerprint checks fail.

Preserve the failed B artifact and reason codes for audit. The rollback is configuration/feature-flag based; no destructive repository operation is required.

## Immediate implementation split

Safe without gold/scorer:

- Add an additive CandidatePlan schema/fingerprint and fail-closed structural validator in a shadow lane.
- Reuse the existing typed-plan and Decimal shadow primitives for independent plan diagnostics.
- Retain every route-family result before selection and record plan-level rejection reasons.
- Move `route_priority` to the final tie-break position after structural, binding and replay gates.
- Add negative tests for AST/operand/period/entity/scope/unit/replay mismatch, the route-priority hazard, control parity and authority invariants.
- Produce A/B manifests and hashes with the selector disabled first.

Requires gold or external scorer:

- Thresholds and weights for semantic completeness and answer-level ranking.
- Whether composed, temporal, argmax/argmin and multi-entity families improve answer accuracy.
- Calibration or promotion of candidate-validity/answer-ranking models.
- Any claim that B improves the `0.1917` control or should replace the control submission.

## Final status

Audit artifact: **complete**.

Audit blocker: **none**.

Safe activation blocker: **present**. The current verifier does not replay formula plans, the builder does not retain all route-family candidates, and there is no local gold set. The next safe move is an additive shadow CandidatePlanSet with hard structural/replay gates, control parity, then a paired external A/B against v2 `0.1917`.

Machine-readable details, probe output, test command/result, control facts and all source/artifact hashes are in [audit_summary.json](/home/dungle/Documents/AI_guru/artifacts/research/candidate_plan_selector_audit_v1/audit_summary.json).
