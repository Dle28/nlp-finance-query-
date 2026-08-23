# Auditable research review

This is a **research-governance** contract for evaluating an idea before it is
implemented in ViFinQA. It borrows three useful practices from the reviewed
Hypergraph tutorial: freeze the protocol before search, record source-specific
evidence instead of snippets, and distinguish source facts from synthesis and
hypotheses.

It is deliberately separate from the answer-capable path in
[ARCHITECTURE.md](../ARCHITECTURE.md). A successful validation report is not a
label, a training example, an answer certificate, a model-quality result, or a
submission authorization.

## Contract

The frozen policy in
[`configs/auditable_research_review_v1.json`](../configs/auditable_research_review_v1.json)
limits the review to five project axes:

- `audit_protocol`
- `exact_source_binding`
- `semantic_certification`
- `retrieval_diagnostics`
- `experiment_design`

Every matrix record is one of four types:

| Type | Meaning | Required proof |
| --- | --- | --- |
| `evidence` | A claim attributable to a source | HTTPS primary/declared source, review status, provenance state, and a specific locator such as page/section/table/figure. |
| `synthesis` | A reviewer conclusion across evidence | At least two IDs of `source_verified` evidence rows. |
| `gap` | A bounded research gap | At least two verified supports and one verified counterexample. |
| `hypothesis` | A proposed experiment | At least one verified support; it remains `proposal_only`. |

All rows must set `promotion_allowed=false`. Derived rows intentionally leave
source fields blank: they point to evidence IDs rather than masquerading as a
new source. `VALIDATION PASSED` establishes only matrix shape, references,
locators and research-only policy. It does not fetch sources, verify a venue,
prove a scientific claim, or weaken the existing release gate.

## Verify the included demo

The demo matrix captures the two current project contracts plus the reviewed
workflow document. It is a traceable example, not a literature survey or a
recommendation to adopt Hypergraph Neural Networks.

```bash
run_dir=artifacts/research/auditable_research_review_demo_$(date +%Y%m%d)
.venv/bin/python scripts/validate_auditable_research_review.py \
  --protocol configs/auditable_research_review_v1.json \
  --matrix examples/auditable_research_review_v1_demo.csv \
  --report "$run_dir/validation_report.json"
```

The command should produce `VALIDATION PASSED` and a SHA-256-bound report. It
refuses to overwrite an existing report. To run the regression tests:

```bash
.venv/bin/python -m unittest discover -s tests \
  -p 'test_auditable_research_review.py' -v
```

## Use it for a new research direction

1. Copy the demo matrix into a new, dated research directory; never edit a
   validated matrix in place.
2. Freeze the question and taxonomy in a new protocol version before searching.
3. Add `evidence` rows only after opening the cited primary source. Search
   snippets are leads, not evidence.
4. Add `synthesis`, `gap`, or `hypothesis` rows only with the required
   evidence IDs. A gap must include a counterexample.
5. Validate the matrix, then create a separate experiment plan. That plan must
   still satisfy the contracts in [TECHNICAL_CONTRACTS.md](TECHNICAL_CONTRACTS.md)
   and may not promote any artifact automatically.
