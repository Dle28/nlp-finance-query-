# Grounded pipeline integration rollout — 2026-08-23

## Outcome

The numeric pipeline is integrated behind fail-closed gates. Exact-cell candidates are split into literal-free public tokens and an executor-private registry, Decimal arithmetic is resource-bounded, telemetry is diagnostic-only, hierarchy retrieval is disabled by default, and all output authorization still flows through Evidence Binding and Answer Certificates.

No answer was promoted. The immutable E2E baseline contains 1,012 `ABSTAIN` certificates because the semantic review ledger has zero human decisions.

## Hierarchy retrieval decision

Two issuer-held-out ablations were frozen without reading benchmark questions or training/promoting a model:

- Unconstrained hierarchy RRF improved base-model Recall@10 but worsened the test wrong-year top-1 rate from 0.24135 to 0.30257. It is rejected.
- Explicit ticker/year/scope filtering removed wrong-year/scope candidates before hierarchy reordering. On the base model, validation Recall@10 changed from 0.29980 to 0.30785 and test Recall@10 from 0.30524 to 0.30612; NDCG@10 also improved. This is only eligible for a follow-up experiment. The finetuned ranking remained inconclusive with zero recall.
- `ModelConfig.hierarchy_rrf_enabled` therefore remains `false` by default.

## Security boundary

The implementation now rejects stale/tampered binding lineage, recomputes token identity, limits AST nodes/depth/operands/Decimal digits/time, and runs normalization, formula evaluation, and output conversion through the same allow-listed interpreter. The detailed findings and residual operational requirements are in `docs/SECURITY_REVIEW_NUMERIC_EXECUTION.md`.

## Human review boundary

The handoff contains 14 immutable review items and 14 blank response rows. It intentionally contains no `human_verified` provenance and cannot authorize output. A human reviewer must inspect the source coordinates and complete the response rows; only then may `load_human_semantic_approvals` validate and materialize approvals.

## Coverage diagnosis

Current grounded execution status is:

- 951 `route_incomplete`
- 47 `binding_conflict`
- 14 `execution_replay_ready`

The dominant gap is controlled whole-question operation coverage (`WHOLE_QUESTION_OPERATION_UNCOVERED`: 888 questions), followed by exact period/source/unit binding. Remediation queues are candidate-only. They do not fabricate operation graphs, choose source values, or lower authorization thresholds.

## Quality gate

Run the full local gate with:

```bash
python scripts/validate_integration_quality_gate.py \
  --policy configs/integration_quality_gate_v1.yaml
```

CI runs every portable test plus the policy-only safety gate. Regression modules
that require SHA-256-bound ignored research artifacts are reported as skipped in
a clean checkout and run automatically when the local artifact tree is mounted.
The full local gate additionally verifies the E2E receipt, both hierarchy
ablations, coverage diagnostics, and the blank human-review handoff.

## Deferred work

Serving optimization is deliberately deferred. It becomes meaningful only after reviewed typed operation graphs materially reduce route incompleteness, binding conflicts are independently resolved, human semantic decisions are available, and a new immutable E2E replay passes the same authorization and reproducibility gates.
