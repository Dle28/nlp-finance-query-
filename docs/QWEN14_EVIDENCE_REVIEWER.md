# Qwen 2.5 14B: evidence-bounded table reviewer

`Qwen/Qwen2.5-14B-Instruct` is an optional reviewer after document metadata,
table classification, variable normalization, metric routing, and candidate
materialization. It is **not** the query planner, calculator, or final-answer
authority.

```text
question
  -> deterministic metric-stage plan
  -> company/year/table-type/variable filter
  -> small source-cell packet
  -> Qwen 14B selects literal bindings or abstains
  -> deterministic literal verifier
  -> direct replay + reviewer-independent critic
  -> machine-calibrated result, or needs_human
```

## What the model does

For each required `(company, variable)` it receives only a bounded list of
source rows and raw numeric cells. It can:

- select a cited raw cell when its row/header is a literal match;
- return `no_candidate` or `abstain`, with a structured reason, if no listed
  source is sufficient;
- make one self-critique pass to flag a missing citation or unsupported claim.

The deterministic verifier then enforces all of the following:

- every required binding is present exactly once;
- `candidate_id` belongs to the router packet;
- column index, canonical V3 header, and raw value are copied literally;
- for a year-specific stage, the cell has an explicit matching source period;
  reference columns such as `Mã số` and `01/01` opening balances are excluded;
  a non-calendar date is allowed only when the document metadata quotes that
  exact fiscal year-end from the source context. A year-only annual header
  such as `2022 Nghìn VND` is allowed only under that same source-derived
  document-period contract, never by a defaulted date;
- all selected operands must share one resolved `consolidated`/`separate`
  scope. If the question does not state a scope and multiple scopes are fully
  supported, the runner returns `ambiguous_report_scope`; if no one scope
  covers all bindings, it returns `no_common_report_scope_across_required_bindings`.
  Neither case calls Qwen;
- within each company, all operands must come from one resolved report version.
  A comparative/restated column from a later report cannot be mixed with a
  target-year report simply because its header names the same year;
- `final_answer` is always `null`; an attempted calculation or answer is
  rejected as `llm_self_inference_detected`;
- any malformed response, candidate escape, missing operand, or rejected
  self-critique is fail-closed to `needs_human`.

The self-critique is useful for early feedback but is deliberately **not** an
independent audit. A successful model output is only `machine_provisional`.
Run `audit_qwen_staged_review.py` after a non-dry-run, non-fixture Qwen
execution. It reopens each selected cell against immutable V2/V3 data, then
independently executes each stage from all admissible source cells. A binding
with more than one admissible source cell blocks promotion as ambiguous. Only
matching direct replay plus independent source execution can yield
`machine_calibrated`. It never becomes `human_verified` by itself and is never
submission-eligible just because Qwen is confident.

This means Qwen can replace much of human *triage and source-cell review*,
including feedback for absent candidates, but it cannot replace the human
approval label for a claimed verified answer. It also cannot silently broaden
retrieval, guess a year/scope, invent an operand, or skip a multi-stage
calculation.

## Run on Kaggle P100 (16 GB)

Attach the review bundle as a Kaggle input, then copy it into a writable
directory such as `/kaggle/working/vifinqa_review_bundle`; the normalization
sidecars are intentionally written beside the source tables. Keep the attached
input itself read-only. In a fresh GPU session, install the optional 4-bit
inference dependencies first:

```python
!pip install -q -r requirements-llm.txt
```

Validate the packets without downloading the model:

```python
!python scripts/build_report_normalization.py \
  --bundle-dir /kaggle/working/vifinqa_review_bundle \
  --force

!python scripts/build_staged_retrieval_routes.py \
  --questions /kaggle/working/vifinqa_review_bundle/review_items.jsonl \
  --output /kaggle/working/staged_retrieval_routes_v1.jsonl

!python scripts/run_qwen_staged_review.py \
  --bundle-dir /kaggle/working/vifinqa_review_bundle \
  --routes /kaggle/working/staged_retrieval_routes_v1.jsonl \
  --output /kaggle/working/qwen14_stage_review_dry_run_v1.jsonl \
  --dry-run
```

For a full supported multi-stage review, use the staged runner in 4-bit NF4
mode. It supports these exact source-and-calculation contracts:

- `quick_ratio_median_then_net_profit_margin` (Q368): Qwen reviews the source
  cells for Quick Ratio and Net Profit Margin; the median filter and final
  average are deterministic.
- `quick_ratio_gpm_interest_coverage_selection` (Q369): Qwen reviews Quick
  Ratio, Gross Profit Margin for each requested year, and Interest Coverage.
  The change `GPM(2023) - GPM(2022)` and the unique winning company are a
  source-free deterministic transition, then only that company is retrieved
  for Interest Coverage.

For either contract, a stage without a complete source packet produces
`needs_human` / structured feedback rather than an expanded search.

```python
!python scripts/run_qwen_staged_review.py \
  --bundle-dir /kaggle/working/vifinqa_review_bundle \
  --routes /kaggle/working/staged_retrieval_routes_v1.jsonl \
  --output /kaggle/working/qwen14_stage_review_v1.jsonl \
  --model Qwen/Qwen2.5-14B-Instruct \
  --max-tables-per-binding 2 \
  --max-new-tokens 1536
```

Then audit the completed output (the audit rejects `--dry-run` and
`--decision-fixture` output by design):

```python
!python scripts/audit_qwen_staged_review.py \
  --bundle-dir /kaggle/working/vifinqa_review_bundle \
  --routes /kaggle/working/staged_retrieval_routes_v1.jsonl \
  --qwen-output /kaggle/working/qwen14_stage_review_v1.jsonl \
  --output /kaggle/working/qwen14_stage_review_audit_v1.jsonl \
  --question-id 368 \
  --question-id 369
```

For a smaller smoke test, use either `--question-id 368` or `--question-id 369`.
The runner writes:

- `qwen14_stage_review_v1.jsonl`: verified status and bounded feedback;
- `qwen14_stage_review_v1.packets.jsonl`: exact inputs shown to Qwen;
- `qwen14_stage_review_v1.manifest.json`: model/protocol/counts.
- `qwen14_stage_review_audit_v1.jsonl`: direct-replay and independent-source
  critic gates, which may yield `machine_calibrated` but never a submission.

These paths are audit artifacts, not a prediction submission. Do not merge
their records into a human-reviewed dataset or export them as labels without
the downstream replay and independent-critic gates.
