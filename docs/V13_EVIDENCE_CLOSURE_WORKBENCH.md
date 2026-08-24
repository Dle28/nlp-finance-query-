# V13 evidence-closure workbench

This workbench converts V13's first-blocker partition into hash-bound,
non-promotable receipt-intake packets. It is the operational bridge between a
diagnostic certificate and an independently reviewed receipt. It does **not**
create a `PASS`, execute a formula, materialize an answer, or authorize a
submission.

## Inputs and guarantees

The configured input lock binds:

- authoritative V13 V7 manifest and all five output sidecars;
- 1,012 typed operand plans;
- 78 source-only Formula EvidenceSets and their manifests.

Every output remains `research_only`, `submission_eligible=false`, and
`release_authorized=false`. An available Formula EvidenceSet is labeled
`AVAILABLE_NON_AUTHORIZING`; it is evidence for review preparation, never a
formula-definition approval.

## Queues created

| Queue | Count | Required next receipt |
| --- | ---: | --- |
| Formula definition | 501 | AST, semantics, typed slots, rounding, anchors and independent approval |
| Operand set + compatibility | 94 | exact operand cells plus period/scope/unit compatibility matrix |
| Route table/metric binding | 250 | table, row, header, period and unit anchors |
| Route operator definition | 37 | operator semantics and operand roles |
| Route-cause investigation | 66 | source-search audit and retrieval-failure attribution |
| Temporal | 38 | kind/start/end/role and source-expression anchor |
| V12 candidate recertification | 26 | every unresolved or unchecked V13 obligation |
| Independent requirement review | 1,012 | a reviewer derives requirements from the raw claim without using V13 output |
| Production ledger intake | 1,012 | independent universe, audit, execution and production-eligibility gates |

The route rows are deliberately split into 250 + 37 + 66. A missing operator
is not mislabeled as a table/metric failure, and an unestablished cause is not
presented as an established retrieval miss.

## Build and verify

```bash
.venv/bin/python scripts/build_v13_evidence_closure_workbench.py \
  --config configs/v13_evidence_closure_workbench_v1.json \
  --output-dir artifacts/research/v13_evidence_closure_workbench_NEW

.venv/bin/python scripts/verify_v13_evidence_closure_workbench.py \
  artifacts/research/v13_evidence_closure_workbench_NEW/v13_evidence_closure_workbench.manifest.json
```

The verifier checks every pinned input/output hash, redacts prohibited numeric
answer fields, confirms all output contracts remain non-promotable, and
deterministically rebuilds each queue before accepting the manifest.

## Review boundary

Independent reviewers should work from the review packets, raw source anchors
and the typed plan. They must record an approval/rejection decision with a
separate identity and source locator. The current workbench contains no such
decisions, so its release decision is intentionally `blocked`.
