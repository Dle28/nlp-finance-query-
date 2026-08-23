# Operations

## Data-first preprocessing V2

Run this before creating any new fine-tuning dataset. The command reads the
immutable raw table assets plus the pinned V2/V3 sidecars and writes only to a
new review checkout:

```bash
.venv/bin/python scripts/run_preprocessing_v2.py \
  --config configs/preprocessing_v2.yaml \
  --output-dir artifacts/research/preprocessing_v2_run_YYYYMMDD
```

Review in this order:

1. `preprocessing_manifest_v2.json`: verify every input/output SHA-256;
2. `preprocessing_report_v2.json`: inspect coverage and reason-code counts;
3. `quarantine_v2.jsonl`: resolve source identity or numeric-invariance blockers;
4. `review_samples_v2.jsonl`: inspect bounded examples for each non-fatal reason;
5. `label_inventory_v1.json`: confirm active, synthetic, legacy and pending
   records are counted separately.

The pipeline never overwrites raw source, never shifts cells without a V2/V3
source trace, and never emits a training-eligible record. A separate,
hash-bound promotion gate must select an approved subset before fine-tuning.

The next architecture is specified in
[CERTIFIED_CANONICAL_LAYER.md](CERTIFIED_CANONICAL_LAYER.md). It supersedes the
idea of a row-by-row review queue with certified assertions, abstention and a
campaign-level human audit.

## Certified Canonical Layer: Phase 0–2

The Phase 0–2 foundation is implemented. It validates the immutable V10,
V2/V3 and Preprocessing V2 lineage, then emits only research artifacts:
source/cell certificates, explicit assertion states, issue DAG, deterministic
benchmark packets and an in-memory mutation report.

```bash
.venv/bin/python scripts/run_certified_canonical.py \
  --config configs/certified_canonical_v1.yaml \
  --output-dir artifacts/research/certified_canonical_v1_run_XXX
```

The output directory must be new. Review in this order:

1. `release_manifest.json` — every source/output SHA-256 and research-only
   status;
2. `validation_report.json` — lifecycle, assertion and primary-issue counts;
3. `mutation_report.json` — each modeled corruption must be detected;
4. `benchmark_packets_v1.jsonl` — the fixed Phase 3 route-selection packets;
5. `issue_registry.jsonl` — primary versus secondary blockers.

`complete_phase_0_to_2_research_only` is not a data promotion. The command
always writes `training_eligible: false`, creates no LLM proposal and will not
run fine-tuning. Phase 3 must compare model routes on the benchmark packets
before an LLM is permitted to produce semantic proposals.

## Phase 3: GPU bake-off hand-off

The graph-contract Phase 3 job is prepared at
`artifacts/research/ccl_phase3_bakeoff_graph_v2_job_001`. It supersedes the
earlier `ccl_phase3_bakeoff_v1_job_001` lexical-citation packet set. Recreate a
job only when the CCL release manifest changes:

The completed Graph V2 hand-off used private datasets
[`source-v2`](https://www.kaggle.com/datasets/dungle2810/vifinqa-ccl-phase3-source-v2)
and
[`bakeoff-graph-v2`](https://www.kaggle.com/datasets/dungle2810/vifinqa-ccl-phase3-bakeoff-graph-v2),
bound to the [Graph V2 P100 kernel](https://www.kaggle.com/code/dungle2810/vifinqa-ccl-phase-3-graph-bake-off-v2).
Its local package, dataset and deployment hashes are recorded in
`artifacts/research/ccl_phase3_graph_v2_kernel_package_002/KAGGLE_DEPLOYMENT_RECEIPT.json`.
Its downloaded artifacts pass integrity audit but have **0/270**
`VALID_PROPOSAL_ONLY` rows. The diagnostic audit is
`artifacts/research/ccl_phase3_graph_v2_kernel_output_001/ccl_phase3_gpu_run_audit_v2.json`:
207 raw responses were not a single JSON object and the remaining 63 exposed
an underspecified alternative/identity response contract. This is a response
protocol failure, not a quality score and not fine-tuning data.

The completed [Graph V3 minimal-JSON smoke kernel](https://www.kaggle.com/code/dungle2810/vifinqa-ccl-phase-3-graph-bake-off-v3-smoke) preserved all hashes and completed 5/5 requests, but had 0 valid proposals: no completion reached its token cap, four had exactly one terminal JSON character too many, and one had a relation-selection mismatch. The archived audit is `artifacts/research/ccl_phase3_graph_v3_smoke_kernel_output_001/ccl_phase3_gpu_run_audit_v1.json`.

The completed [Graph V3.1 JSON-relation smoke kernel](https://www.kaggle.com/code/dungle2810/vifinqa-ccl-phase-3-graph-bake-off-v3-1-smoke) reduced the terminal JSON failure to one response, but still produced 0 valid proposals because the model could select nonselectable graph nodes or relations outside the field contract. Its audit is `artifacts/research/ccl_phase3_graph_v31_smoke_kernel_output_001/ccl_phase3_gpu_run_audit_v1.json`.

The completed [Graph V3.2 field-scoped menu smoke kernel](https://www.kaggle.com/code/dungle2810/vifinqa-ccl-phase-3-graph-bake-off-v3-2-smoke) passed integrity and yielded 4/5 `VALID_PROPOSAL_ONLY` rows. The remaining row selected a valid relation without its matching anchor. Its audit is `artifacts/research/ccl_phase3_graph_v32_smoke_kernel_output_001/ccl_phase3_gpu_run_audit_v1.json`.

The completed [Graph V3.3 relation-only smoke kernel](https://www.kaggle.com/code/dungle2810/vifinqa-ccl-phase-3-graph-bake-off-v3-3-smoke) passed integrity audit with 5/5 source-anchored `VALID_PROPOSAL_ONLY` rows. It used private
[`source-v33`](https://www.kaggle.com/datasets/dungle2810/vifinqa-ccl-phase3-source-v33)
and
[`bakeoff-graph-v33-smoke`](https://www.kaggle.com/datasets/dungle2810/vifinqa-ccl-phase3-bakeoff-graph-v33-smoke)
inputs. The model selects only a field-compatible finite relation. The validator derives the relation's selectable source endpoint, records the derivation and then validates against the unchanged full graph. Its deployment receipt is `artifacts/research/ccl_phase3_graph_v33_smoke_kernel_package_001/KAGGLE_DEPLOYMENT_RECEIPT.json`.

The completed [Graph V3.3 full kernel](https://www.kaggle.com/code/dungle2810/vifinqa-ccl-phase-3-graph-bake-off-v3-3-full) ran the same relation-only contract over 270 packets on Qwen3-8B / P100. It reuses private [`source-v33`](https://www.kaggle.com/datasets/dungle2810/vifinqa-ccl-phase3-source-v33) and consumes private [`bakeoff-graph-v33-full`](https://www.kaggle.com/datasets/dungle2810/vifinqa-ccl-phase3-bakeoff-graph-v33-full). Its immutable deployment receipt is `artifacts/research/ccl_phase3_graph_v33_full_kernel_package_001/KAGGLE_DEPLOYMENT_RECEIPT.json`; its downloaded audit is `artifacts/research/ccl_phase3_graph_v33_full_kernel_output_001/ccl_phase3_gpu_run_audit_v1.json`. The audit passed with complete 270/270 coverage: 263 `VALID_PROPOSAL_ONLY` and 7 `INVALID_UNRESOLVED`. This remains proposal-only GPU inference, not certification, promotion or fine-tuning.

The completed [Graph V3.4 statement smoke kernel](https://www.kaggle.com/code/dungle2810/vifinqa-ccl-phase-3-graph-v3-4-statement-smoke) is a separate five-packet diagnostic after the V3.3 source-profile gate found zero controlled table-semantic labels. It uses private [`source-v34-statement-smoke`](https://www.kaggle.com/datasets/dungle2810/vifinqa-ccl-phase3-source-v34-statement-smoke) and [`p3-graph-v34-statement-smoke`](https://www.kaggle.com/datasets/dungle2810/vifinqa-ccl-p3-graph-v34-statement-smoke). It permits only `balance_sheet`, `income_statement` or `cash_flow_statement`, bound to `heading_scopes_table`; abstention remains valid. The GPU/validator audit passed 5/5 responses on P100, but only 2/5 (40%) model enums agreed with the independently derived literal source profile. The deployment receipt is `artifacts/research/ccl_phase3_graph_v34_statement_smoke_kernel_package_002/KAGGLE_DEPLOYMENT_RECEIPT.json` and semantic comparison is `artifacts/research/ccl_phase5_source_profile_compatibility_v34_statement_smoke_run_002/phase5_source_profile_compatibility_report.json`. Route literal financial-statement headings through the deterministic profile baseline rather than scaling this LLM task; no result may be promoted or used for fine-tuning.

The older V3.2 smoke used private
[`source-v32`](https://www.kaggle.com/datasets/dungle2810/vifinqa-ccl-phase3-source-v32)
and
[`bakeoff-graph-v32-smoke`](https://www.kaggle.com/datasets/dungle2810/vifinqa-ccl-phase3-bakeoff-graph-v32-smoke)
inputs. It remains an immutable partial-success result.

The older V3.1 smoke used private
[`source-v31`](https://www.kaggle.com/datasets/dungle2810/vifinqa-ccl-phase3-source-v31)
and
[`bakeoff-graph-v31-smoke`](https://www.kaggle.com/datasets/dungle2810/vifinqa-ccl-phase3-bakeoff-graph-v31-smoke)
inputs. It remains an immutable negative result.

The older V3 smoke used private
[`source-v3`](https://www.kaggle.com/datasets/dungle2810/vifinqa-ccl-phase3-source-v3)
and
[`bakeoff-graph-v3-smoke`](https://www.kaggle.com/datasets/dungle2810/vifinqa-ccl-phase3-bakeoff-graph-v3-smoke)
inputs. It remains an immutable negative result. No smoke permits promotion or
certification; a full rerun requires its downloaded artifact set to pass audit.

The retry creates a compact finite subgraph only when a full request exceeds
the declared P100 budget. Source-anchor IDs and relation IDs remain immutable;
omitted anchors are not selectable and a shortened displayed cell is marked as
a preview of its hash-bound full text. The model runner tokenizes every request
before model loading and stops before generation if it exceeds the recorded
token budget.

```bash
.venv/bin/python scripts/build_ccl_phase3_bakeoff.py \
  --config configs/ccl_phase3_bakeoff_graph_v33_full.yaml \
  --output-dir artifacts/research/ccl_phase3_bakeoff_graph_v33_full_job_XXX
```

The full V3.3 job creates 270 Qwen3-8B requests. The model emits only
`proposals` and `unresolved_conditions`; the raw-response envelope retains the
request/model identity independently. This removes redundant long identifiers
from generation without weakening the finite-ID validator. It may be created
only after the V3.3 smoke audit passes. Qwen2.5-VL-7B remains
disabled until page images are attached as hash-bound source artifacts.

On a GPU environment, run one route at a time into a fresh directory. Confirm
the model revision and licence/access terms there before execution.

```bash
python scripts/run_ccl_phase3_model.py \
  --job-manifest /kaggle/input/CCL_JOB/bakeoff_job_manifest.json \
  --requests /kaggle/input/CCL_JOB/llm_bakeoff_requests_v1.jsonl \
  --response-schema /kaggle/input/CCL_JOB/llm_proposal_schema_v1.json \
  --route-id qwen3_8b_primary_graph_v33_full \
  --output-dir /kaggle/working/ccl_qwen3_8b_graph_v33_full_raw
```

Run the validator after every route. It accepts only one JSON response per
request whose selected anchor and relation IDs exist in the supplied packet
graph. Free-text quotes, coordinates and hashes are invalid. A valid result is
still only `VALID_PROPOSAL_ONLY`, never a certified fact or training example.

```bash
python scripts/validate_ccl_phase3_responses.py \
  --job-manifest /kaggle/input/CCL_JOB/bakeoff_job_manifest.json \
  --requests /kaggle/input/CCL_JOB/llm_bakeoff_requests_v1.jsonl \
  --raw-responses /kaggle/working/ccl_qwen3_8b_graph_v33_full_raw/llm_raw_responses_v1.jsonl \
  --output-dir /kaggle/working/ccl_qwen3_8b_graph_v33_full_validated
```

Do not mix routes or edit response JSONL in place. Copy each resulting
manifest back to a new local artifact directory before invoking the Phase 4-5
verifier. The existing Kaggle v5 response uses the legacy lexical schema:
audit it only as a GPU runtime receipt; do not attempt to coerce it into the
graph-contract validator or use it for fine-tuning.

### Phase 4-5 deterministic assertion verification

Run the verifier only on an audit-passed Phase 3 route. It validates the full
receipt chain—source bundle, job, requests, Kaggle receipt, validation manifest
and proposal file—before creating a new output directory. It confirms a
period/unit proposal only when its original value is an exact normalized
substring of a selected header-relation endpoint. It does not repair text,
split merged period/unit headers, certify table semantics or infer a heading
span from a broad context window.

```bash
.venv/bin/python scripts/run_ccl_phase45_semantic_verifier.py \
  --job-manifest artifacts/research/ccl_phase3_bakeoff_graph_v33_full_job_001/bakeoff_job_manifest.json \
  --requests artifacts/research/ccl_phase3_bakeoff_graph_v33_full_job_001/llm_bakeoff_requests_v1.jsonl \
  --source-manifest artifacts/research/ccl_phase3_graph_v33_smoke_source_bundle_001/ai_guru_ccl_phase3_source_v1.manifest.json \
  --validated-proposals artifacts/research/ccl_phase3_graph_v33_full_kernel_output_001/llm_proposals_validated_v1.jsonl \
  --proposal-validation-manifest artifacts/research/ccl_phase3_graph_v33_full_kernel_output_001/proposal_validation_manifest.json \
  --phase3-receipt artifacts/research/ccl_phase3_graph_v33_full_kernel_output_001/ccl_phase3_kaggle_receipt_v1.json \
  --phase3-audit artifacts/research/ccl_phase3_graph_v33_full_kernel_output_001/ccl_phase3_gpu_run_audit_v1.json \
  --route-id qwen3_8b_primary_graph_v33_full \
  --output-dir artifacts/research/ccl_phase45_graph_v33_full_run_XXX
```

The first full run is
`artifacts/research/ccl_phase45_graph_v33_full_run_001`. It preserved all
input hashes and emitted 267 assertion records: 30 `SOURCE_BOUND_PROPOSAL`
period/unit assertions and 237 `UNRESOLVED` assertions. The unresolved set
includes all 211 table-semantic claims, 16 period values merged with a unit,
two unit values merged with a period, seven endpoint mismatches and one heading
claim without a materialized source span. All outputs remain non-promotable and
non-certified.

### Phase 4 literal layout span and Phase 5 table-profile context

Phase 4 joins a Phase 4-5 `table_semantics` assertion back to the immutable
raw/normalized CCL inputs. It emits a heading span only when the selected
`heading_scopes_table` relation is bound to a nonempty `source_heading` with
exactly one literal occurrence in `context_before`. Phase 5 recognizes only
literal balance-sheet, income-statement, cash-flow-statement titles and
numbered note headings. Both stages create layout context; neither validates a
model's topic text nor certifies semantics.

```bash
.venv/bin/python scripts/materialize_ccl_phase4_heading_spans.py \
  --job-manifest artifacts/research/ccl_phase3_bakeoff_graph_v33_full_job_001/bakeoff_job_manifest.json \
  --requests artifacts/research/ccl_phase3_bakeoff_graph_v33_full_job_001/llm_bakeoff_requests_v1.jsonl \
  --phase45-assertions artifacts/research/ccl_phase45_graph_v33_full_run_001/phase45_semantic_assertions_v1.jsonl \
  --phase45-manifest artifacts/research/ccl_phase45_graph_v33_full_run_001/phase45_verification_manifest.json \
  --ccl-input-inventory artifacts/research/certified_canonical_v1_run_003/input_inventory.json \
  --raw-tables artifacts/kaggle_runs/notebook5554bd790d_v10_20260811/vifinqa_review_bundle/tables.jsonl \
  --normalized-tables artifacts/research/preprocessing_v2_run_003/normalized_tables_v2.jsonl \
  --route-id qwen3_8b_primary_graph_v33_full \
  --output-dir artifacts/research/ccl_phase4_heading_spans_v33_full_run_XXX

.venv/bin/python scripts/build_ccl_phase5_table_topic_profiles.py \
  --phase4-heading-spans artifacts/research/ccl_phase4_heading_spans_v33_full_run_001/phase4_heading_spans_v1.jsonl \
  --phase4-manifest artifacts/research/ccl_phase4_heading_spans_v33_full_run_001/phase4_heading_span_manifest.json \
  --route-id qwen3_8b_primary_graph_v33_full \
  --output-dir artifacts/research/ccl_phase5_table_topic_profiles_v33_full_run_XXX
```

The original run materialized 178/211 unique literal heading spans. The
source-first rerun profiles all 178: three balance sheets, eight income
statements, one cash-flow statement and 166 decimal/enumerated note sections.
It materializes all 166 literal note locators/topics and carries the remaining
33 source-cell/relation cases as context only. No profile or context is
training-eligible or certification-eligible.

```bash
.venv/bin/python scripts/materialize_ccl_phase5_numbered_note_context.py \
  --phase4-heading-spans artifacts/research/ccl_phase4_heading_spans_v33_full_run_001/phase4_heading_spans_v1.jsonl \
  --phase4-manifest artifacts/research/ccl_phase4_heading_spans_v33_full_run_001/phase4_heading_span_manifest.json \
  --phase5-profiles artifacts/research/ccl_phase5_table_topic_profiles_v33_full_run_002/phase5_table_topic_profiles_v1.jsonl \
  --phase5-manifest artifacts/research/ccl_phase5_table_topic_profiles_v33_full_run_002/phase5_table_topic_profile_manifest.json \
  --route-id qwen3_8b_primary_graph_v33_full \
  --output-dir artifacts/research/ccl_phase5_numbered_note_context_v33_full_run_XXX

.venv/bin/python scripts/build_ccl_phase5_source_first_semantic_routes.py \
  --phase4-heading-spans artifacts/research/ccl_phase4_heading_spans_v33_full_run_001/phase4_heading_spans_v1.jsonl \
  --phase4-manifest artifacts/research/ccl_phase4_heading_spans_v33_full_run_001/phase4_heading_span_manifest.json \
  --phase5-profiles artifacts/research/ccl_phase5_table_topic_profiles_v33_full_run_002/phase5_table_topic_profiles_v1.jsonl \
  --phase5-manifest artifacts/research/ccl_phase5_table_topic_profiles_v33_full_run_002/phase5_table_topic_profile_manifest.json \
  --phase4-row-label-contexts artifacts/research/ccl_phase4_row_label_context_v33_full_run_001/phase4_row_label_context_v1.jsonl \
  --phase4-row-label-context-manifest artifacts/research/ccl_phase4_row_label_context_v33_full_run_001/phase4_row_label_context_manifest.json \
  --route-id qwen3_8b_primary_graph_v33_full \
  --output-dir artifacts/research/ccl_phase5_source_first_semantic_routes_v33_full_run_XXX

.venv/bin/python scripts/materialize_ccl_phase5_table_structure_context.py \
  --source-first-routes artifacts/research/ccl_phase5_source_first_semantic_routes_v33_full_run_004/phase5_source_first_semantic_routes_v1.jsonl \
  --source-first-manifest artifacts/research/ccl_phase5_source_first_semantic_routes_v33_full_run_004/phase5_source_first_semantic_routing_manifest.json \
  --phase5-note-contexts artifacts/research/ccl_phase5_numbered_note_context_v33_full_run_002/phase5_numbered_note_context_v1.jsonl \
  --phase5-note-context-manifest artifacts/research/ccl_phase5_numbered_note_context_v33_full_run_002/phase5_numbered_note_context_manifest.json \
  --phase4-row-label-contexts artifacts/research/ccl_phase4_row_label_context_v33_full_run_001/phase4_row_label_context_v1.jsonl \
  --phase4-row-label-context-manifest artifacts/research/ccl_phase4_row_label_context_v33_full_run_001/phase4_row_label_context_manifest.json \
  --ccl-input-inventory artifacts/research/certified_canonical_v1_run_003/input_inventory.json \
  --raw-tables artifacts/kaggle_runs/notebook5554bd790d_v10_20260811/vifinqa_review_bundle/tables.jsonl \
  --normalized-tables artifacts/research/preprocessing_v2_run_003/normalized_tables_v2.jsonl \
  --route-id qwen3_8b_primary_graph_v33_full \
  --output-dir artifacts/research/ccl_phase5_table_structure_context_v33_full_run_XXX
```

### Phase 5 merged header components

The component verifier is a separate immutable derivation. For an unresolved
period/unit proposal, it requires that the proposal exactly equal the selected
source header endpoint, then splits only an allow-listed terminal unit suffix
(`VND`, `VNĐ`, `đồng`, `triệu/tỷ/nghìn` plus currency). The remaining literal
prefix must contain a deterministic period marker. It produces one component
for the proposal's own field and never rewrites the original header or creates
the complementary field implicitly.

```bash
.venv/bin/python scripts/verify_ccl_phase5_header_components.py \
  --job-manifest artifacts/research/ccl_phase3_bakeoff_graph_v33_full_job_001/bakeoff_job_manifest.json \
  --requests artifacts/research/ccl_phase3_bakeoff_graph_v33_full_job_001/llm_bakeoff_requests_v1.jsonl \
  --phase45-assertions artifacts/research/ccl_phase45_graph_v33_full_run_001/phase45_semantic_assertions_v1.jsonl \
  --phase45-manifest artifacts/research/ccl_phase45_graph_v33_full_run_001/phase45_verification_manifest.json \
  --route-id qwen3_8b_primary_graph_v33_full \
  --output-dir artifacts/research/ccl_phase5_header_components_v33_full_run_XXX
```

The first run examined 18 merged-header proposals. It emitted 14 source-bound
components (13 period and one unit) and retained four rows unresolved because
the model's proposed text did not exactly equal its selected source endpoint.
All four have now been audited as wrong endpoint selections rather than missing
unit-suffix patterns: `Năm nayVND` vs `Năm trướcVND`, `2021VND` vs an unrelated
note heading, `31.12.2019VND` vs `31.12.2020VND`, and `31/12/2024 VND` vs
`1/1/2024 VND`. Do not repair them by changing source text, fuzzy matching or
adding a general split rule. All outputs are non-promotable and non-certified.

### Phase 5 source-profile/model compatibility diagnostic

This gate hash-binds the Phase 4-5 table-semantic assertions, Phase 4 literal
heading spans and Phase 5 source profiles. It accepts only a controlled
statement label (`balance_sheet`, `income_statement`, or `cash_flow_statement`;
or its exact Vietnamese statement-title literal) that agrees with a literal
financial-statement source heading. A match is still **context only**: it
cannot enter a campaign, training set, repair or certificate. Numbered note
headings are deliberately rejected as complete table semantics.

```bash
.venv/bin/python scripts/verify_ccl_phase5_source_profile_compatibility.py \
  --phase45-assertions artifacts/research/ccl_phase45_graph_v33_full_run_001/phase45_semantic_assertions_v1.jsonl \
  --phase45-manifest artifacts/research/ccl_phase45_graph_v33_full_run_001/phase45_verification_manifest.json \
  --phase4-heading-spans artifacts/research/ccl_phase4_heading_spans_v33_full_run_001/phase4_heading_spans_v1.jsonl \
  --phase4-manifest artifacts/research/ccl_phase4_heading_spans_v33_full_run_001/phase4_heading_span_manifest.json \
  --phase5-profiles artifacts/research/ccl_phase5_table_topic_profiles_v33_full_run_001/phase5_table_topic_profiles_v1.jsonl \
  --phase5-manifest artifacts/research/ccl_phase5_table_topic_profiles_v33_full_run_001/phase5_table_topic_profile_manifest.json \
  --route-id qwen3_8b_primary_graph_v33_full \
  --output-dir artifacts/research/ccl_phase5_source_profile_compatibility_v33_full_run_XXX
```

The original V3.3 result has 211 diagnostics and **zero** compatible controlled
labels: 33 lack a materialized source heading span, 15 have no narrow source
profile, 151 are numbered-note context rather than statement types, and 12
model values do not match their statement profile. The source-first rerun
closes the literal heading-context gaps (178 profiles, 166 note components and
33 source-cell/relation contexts), but it does not alter the zero-match model
diagnosis. The next action is an immutable table-structure join, not promotion,
fine-tuning or another broad semantic-label GPU run.

### Phase 5 closed-world component-selection smoke

For note and row-context tables, the model is not asked to invent a table
meaning. It must select one literal source context plus zero to four literal
headers/row labels, or abstain. The output is a strict three-key JSON object;
all selected IDs are checked against the immutable packet menu. The completed
Qwen3-8B V2 and Mistral-Nemo-12B challenger smokes each produced five valid
selections on P100. Their audits prove the source bundle, job, raw response,
validation manifest, GPU runtime and selected literals form one hash-bound
chain:

```bash
.venv/bin/python scripts/audit_ccl_phase5_component_selection_smoke.py \
  --source-packets artifacts/research/ccl_phase5_component_selection_packets_v33_full_run_002/phase5_component_selection_packets_v1.jsonl \
  --source-manifest artifacts/research/ccl_phase5_component_selection_packets_v33_full_run_002/phase5_component_selection_manifest.json \
  --smoke-job-manifest artifacts/research/ccl_phase5_component_selection_smoke_job_v33_run_003/component_selection_smoke_job_manifest.json \
  --smoke-packets artifacts/research/ccl_phase5_component_selection_smoke_job_v33_run_003/component_selection_smoke_packets_v1.jsonl \
  --smoke-packet-manifest artifacts/research/ccl_phase5_component_selection_smoke_job_v33_run_003/component_selection_smoke_packet_manifest.json \
  --requests artifacts/research/ccl_phase5_component_selection_smoke_job_v33_run_003/component_selection_smoke_requests_v1.jsonl \
  --source-bundle-manifest artifacts/research/ccl_phase5_component_selection_source_bundle_v2_001/ai_guru_ccl_phase3_source_v1.manifest.json \
  --artifact-dir artifacts/research/ccl_phase5_component_selection_smoke_kernel_output_v2_001 \
  --output artifacts/research/ccl_phase5_component_selection_smoke_audit_v2_XXX.json
```

The output is a reviewer-readable literal audit, not a semantic label file.
It always remains `training_eligible: false` and `certification_allowed: false`.
The two audited outputs can be compared only by exact packet IDs:

```bash
.venv/bin/python scripts/compare_ccl_phase5_component_selection_agreement.py \
  --primary-audit artifacts/research/ccl_phase5_component_selection_smoke_audit_v2_001.json \
  --primary-results artifacts/research/ccl_phase5_component_selection_smoke_kernel_output_v2_001/ccl_phase5_component_selection_smoke_validated/phase5_component_selection_results_v1.jsonl \
  --challenger-audit artifacts/research/ccl_phase5_component_selection_challenger_smoke_audit_v1_001.json \
  --challenger-results artifacts/research/ccl_phase5_component_selection_challenger_smoke_kernel_output_v1_001/ccl_phase5_component_selection_smoke_validated/phase5_component_selection_results_v1.jsonl \
  --output-dir artifacts/research/ccl_phase5_component_selection_agreement_smoke_v1_XXX
```

The verified smoke comparison has 3/5 exact selections, 2/5 shared primary
contexts with different supporting components, and zero primary-context
disagreements. This is not an accuracy score. Scale only in bounded batches
after a final-review calibration sample measures both agreement strata; do not
infer table meaning directly from this smoke or use it for fine-tuning.

The first 24-packet Qwen operational pilot stopped fail-closed before emitting
any raw response: four lexically selected packets exceeded the declared
2,048-token input budget. Its V2 replacement preserved that budget, measured
the final Qwen chat prompt with the exact local tokenizer, and passed its
24/24 provenance/format audit. That audit also revealed four numeric data
literals selected under an inherited `column_header` role, so V2 is retained
only as a diagnostic and cannot be used for agreement, calibration or
fine-tuning. Its audit command is:

```bash
.venv/bin/python scripts/audit_ccl_phase5_component_selection_smoke.py \
  --source-packets artifacts/research/ccl_phase5_component_selection_packets_v33_full_run_002/phase5_component_selection_packets_v1.jsonl \
  --source-manifest artifacts/research/ccl_phase5_component_selection_packets_v33_full_run_002/phase5_component_selection_manifest.json \
  --smoke-job-manifest artifacts/research/ccl_phase5_component_selection_pilot_qwen_job_v2_001/component_selection_pilot_job_manifest.json \
  --smoke-packets artifacts/research/ccl_phase5_component_selection_pilot_qwen_job_v2_001/component_selection_pilot_packets_v1.jsonl \
  --smoke-packet-manifest artifacts/research/ccl_phase5_component_selection_pilot_qwen_job_v2_001/component_selection_pilot_packet_manifest.json \
  --requests artifacts/research/ccl_phase5_component_selection_pilot_qwen_job_v2_001/component_selection_pilot_requests_v1.jsonl \
  --token-preflight artifacts/research/ccl_phase5_component_selection_pilot_qwen_job_v2_001/component_selection_pilot_token_preflight.json \
  --source-bundle-manifest artifacts/research/ccl_phase5_component_selection_pilot_qwen_source_bundle_v1_001/ai_guru_ccl_phase3_source_v1.manifest.json \
  --artifact-dir artifacts/research/ccl_phase5_component_selection_pilot_qwen_kernel_output_v2_001 \
  --output artifacts/research/ccl_phase5_component_selection_pilot_qwen_audit_v2_001.json
```

The refreshed numeric-guard source menu deterministically excludes plain
numeric values from `column_header` while retaining dates, four-digit years
and year/unit headers. It excluded 197 candidates across 35/199 packets. The
Qwen3-8B and [Mistral-Nemo-12B smoke](https://www.kaggle.com/code/dungle2810/vifinqa-ccl-p-5-numeric-guard-mistral-smoke-v-1)
each passed the full P100/hash/literal audit at
`artifacts/research/ccl_phase5_component_selection_smoke_numeric_guard_qwen_audit_v1_002.json`
and `artifacts/research/ccl_phase5_component_selection_smoke_numeric_guard_mistral_audit_v1_001.json`.
Their comparison is hash-bound at
`artifacts/research/ccl_phase5_component_selection_numeric_guard_agreement_smoke_v1_001`:
1 exact closed-world agreement, 4 shared-primary/support-difference cases,
and zero quarantined primary disagreements. These remain non-promotable until
a small final-review calibration sample has measured error in both strata.
Do not compare either numeric-guard output to the older five-packet smokes.

### Phase 5 final-review calibration

The numeric-guard comparison is now materialized as five final-review
assignments at
`artifacts/research/ccl_phase5_component_selection_final_review_calibration_v1_001`.
The reviewer-facing assignment contains the immutable component menu, literal
provenance and response contract, but deliberately contains neither model's
selection. The model comparison ledger is separate and must not be given to
the reviewer.

Copy `component_selection_final_review_response_template_v1.jsonl` to a new
response file; never modify the immutable template or assignment. For each
item, supply a nonempty `reviewer_id`, an ISO-8601 UTC `reviewed_at_utc`,
`source_coordinates_checked: true`, and either:

- `SELECT_COMPONENTS` with one context component and up to four permitted
  header/row-label components; or
- `ABSTAIN_UNRESOLVED` with no components and one or more declared unresolved
  conditions.

### Local final-review UI

For the same five blind assignments, run the local-only UI instead of editing
JSONL by hand. It verifies the assignment/template hashes before serving, binds
only `127.0.0.1`, never exposes the model-comparison ledger, and refuses to
overwrite a saved response file. Each item also shows its immutable V2 source
table read-only. The UI separates the report heading linked to that table from
its column headers, and explicitly marks a column with no source header instead
of presenting a fallback label as table meaning. It never changes the frozen
V10/V2/V3 or preprocessing inputs.

When literal cells form `NỘI DUNG | TRANG` plus page ranges, the UI marks the
grid as a table of contents / page index. This is a read-only source-shape hint,
not a rewrite of the frozen semantic sidecars or a promotion decision.

### Report-navigation exclusion overlay

The final-review audit found that a VGC contents page could be mistaken for a
cash-flow table because that report title appeared as an entry in the contents
grid. Build the full-coverage deterministic guard below before a future
financial-table semantic dispatch:

```bash
.venv/bin/python scripts/build_report_navigation_overlay_v1.py \
  --raw-tables artifacts/kaggle_runs/notebook5554bd790d_v10_20260811/vifinqa_review_bundle/tables.jsonl \
  --output-dir artifacts/research/report_navigation_overlay_v1_run_XXX
```

The `v1_run_001` output detects 73 source grids with the exact `NỘI DUNG |
TRANG` plus page-locator shape. They have
`table_semantic_dispatch_allowed: false`; this prevents only a financial-table
semantic dispatch. The overlay is full-coverage, hash-bound to V10, and every
row remains evidence-, training- and certification-ineligible.

All future Phase 5 component-packet builds must consume that overlay. The
builder fails closed when it is missing, changed, incomplete, or blocks a
contents page:

```bash
.venv/bin/python scripts/build_ccl_phase5_component_selection_packets.py \
  --table-structure-contexts artifacts/research/ccl_phase5_table_structure_context_v33_full_run_003/phase5_table_structure_context_v1.jsonl \
  --table-structure-context-manifest artifacts/research/ccl_phase5_table_structure_context_v33_full_run_003/phase5_table_structure_context_manifest.json \
  --navigation-overlay-dir artifacts/research/report_navigation_overlay_v1_run_001 \
  --route-id qwen3_8b_primary_graph_v33_full \
  --output-dir artifacts/research/ccl_phase5_component_selection_packets_v35_navigation_gate_run_XXX
```

The first gated packet set has 198 packets, 12 deterministic profile bypasses,
and one blocked VGC contents table. It is not a replacement for the immutable
packet set that was already used for the five-item human calibration.

`v36_navigation_provenance_run_001` records this requirement directly in its
source manifest. Future final-review packages, pilots, smoke jobs and Kaggle
dataset packaging reject a packet lineage unless the manifest both declares
`navigation_overlay_required: true` and binds the two overlay inputs. The
following five-request smoke is materialized from that v36 lineage only. It
was dispatched once only after the hash-bound policy exception:

```bash
.venv/bin/python scripts/build_ccl_phase5_component_selection_smoke_job.py \
  --config configs/ccl_phase5_component_selection_smoke_v36_navigation_provenance_qwen_v1.yaml \
  --output-dir artifacts/research/ccl_phase5_component_selection_smoke_v36_navigation_provenance_qwen_job_v1_XXX
```

Do not create a Kaggle dataset or execute this job before the explicit
final-review policy decision on the three abstentions and the required sample
size. The v36 Qwen/Mistral pilot configs are retained for a later, separately
approved token preflight; they do not authorize model execution.

```bash
.venv/bin/python local/ccl_phase5_final_review_ui.py
```

Open `http://127.0.0.1:8785`, enter a reviewer ID, review all five literal
component menus, then use **Luu response JSONL**. The server assigns the UTC
timestamp and writes the scorer-compatible response path printed at startup.
Stop it with `Ctrl-C` after saving.

The scorer requires all five responses and the original assignment hashes. It
reports agreement by stratum but cannot promote any item:

```bash
.venv/bin/python scripts/score_ccl_phase5_component_selection_final_review_calibration.py \
  --assignment artifacts/research/ccl_phase5_component_selection_final_review_calibration_v1_001/component_selection_final_review_assignment_v1.jsonl \
  --response-template artifacts/research/ccl_phase5_component_selection_final_review_calibration_v1_001/component_selection_final_review_response_template_v1.jsonl \
  --ledger artifacts/research/ccl_phase5_component_selection_final_review_calibration_v1_001/component_selection_model_comparison_ledger_v1.jsonl \
  --calibration-manifest artifacts/research/ccl_phase5_component_selection_final_review_calibration_v1_001/component_selection_final_review_calibration_manifest.json \
  --responses artifacts/research/ccl_phase5_component_selection_final_review_responses_v1_001.jsonl \
  --output-dir artifacts/research/ccl_phase5_component_selection_final_review_score_v1_001
```

Even a perfect result on this five-item smoke is only a measurement. Scaling
requires an explicit policy decision on error and sample size; it never turns
the selected components into semantic labels or fine-tune data.

The post-smoke v36 blind review is scored separately at
`artifacts/research/ccl_phase5_component_selection_final_review_score_v36_001`.
All five assignment hashes and input hashes passed validation. The human
reviewer abstained on 2/5 items. Qwen and Mistral each matched the reviewed
primary component on 3/5 items; exact supporting-component matches were 0/5
and 1/5 respectively. The sole `EXACT_CLOSED_WORLD_AGREEMENT` item received a
human abstention. The run is therefore
`final_review_calibration_scored_not_promoted`, with zero training-eligible
outputs and `certification_allowed: false`. Its next gate is explicit policy
review of calibration error and sample size before any bounded pilot.

### Phase 5 policy gate

The current score has five reviewed items, three human abstentions, and only
one item in the exact-agreement stratum. The hash-bound policy package at
`artifacts/research/ccl_phase5_component_selection_policy_review_v1_001`
therefore recommends keeping all dispatch blocked and expanding calibration.
It binds that evidence to exactly one prepared v36 five-request smoke job; it
does not bind a pilot, a label set, or any prior packet lineage.

Use the local-only form to make the decision readable rather than editing JSON
by hand:

```bash
.venv/bin/python local/ccl_phase5_policy_review_ui.py
```

Open `http://127.0.0.1:8787`, enter the reviewer ID and rationale, acknowledge
the calibration and immutable lineage, then save. Selecting **Giu chan** is the
recommended outcome. Selecting the exception only records a named human
decision for that exact five-request smoke; it does not run a model, create a
label, or grant certification. Resolve the saved response into the separately
hash-bound gate:

```bash
.venv/bin/python scripts/resolve_ccl_phase5_component_selection_policy_review.py \
  --policy-review artifacts/research/ccl_phase5_component_selection_policy_review_v1_001/component_selection_policy_review_v1.json \
  --policy-template artifacts/research/ccl_phase5_component_selection_policy_review_v1_001/component_selection_policy_response_template_v1.json \
  --policy-manifest artifacts/research/ccl_phase5_component_selection_policy_review_v1_001/component_selection_policy_review_manifest.json \
  --response artifacts/research/ccl_phase5_component_selection_policy_response_v1_001.json \
  --output-dir artifacts/research/ccl_phase5_component_selection_policy_decision_v1_XXX
```

The Kaggle dataset packager now requires both this decision and its output
manifest, and checks that its candidate job-manifest SHA-256 is the same one
the reviewer approved. A `dispatch_remains_blocked` response is rejected. An
authorized dataset is still proposal-only and is not a command to submit or
publish a Kaggle kernel.

The recorded decision at
`artifacts/research/ccl_phase5_component_selection_policy_decision_v1_001`
authorizes only the exact v36 five-request smoke. Its local handoff is ready:
the source snapshot is
`artifacts/research/ccl_phase5_component_selection_smoke_v36_source_bundle_v1_001`,
the proposal-only prepared-job dataset is
`artifacts/research/ccl_phase5_component_selection_smoke_v36_navigation_provenance_qwen_job_dataset_v1_001`,
and the private-kernel package is
`artifacts/research/ccl_phase5_component_selection_smoke_v36_kernel_package_v1_001`.
The authorized dispatch created both private datasets and completed the private
[Qwen3-8B P100 kernel](https://www.kaggle.com/code/dungle2810/vifinqa-ccl-phase-5-v36-smoke-run-v1).
The Kaggle URL uses `phase-5` because Kaggle derives its slug from the human
title; use that actual URL/slug when downloading output, rather than the
historical package ID containing `phase5`.

The downloaded output at
`artifacts/research/ccl_phase5_component_selection_smoke_v36_kernel_output_001`
passes `ccl_phase5_component_selection_smoke_audit_v1`: all five requests are
covered and `VALID_COMPONENT_SELECTION_ONLY`, the numeric-header guard found
zero violations (197 exclusions), the P100 runtime was compatible, and all
artifacts retain `training_eligible: false` and `certification_allowed: false`.
It remains a closed-world literal-selection measurement, not a label, semantic
classification, training input, certificate, or permission to scale a pilot.

The kernel metadata proposes the historic `dungle2810` Kaggle namespace and
two private dataset slugs. Confirm that namespace in a signed-in Kaggle
session before creating either dataset or pushing the kernel; a Kaggle email
address is not an API credential. Do not place an account password in a
notebook, dataset, or repository.

The publisher validates all local hashes and contracts without calling Kaggle
by default:

```bash
.venv/bin/python scripts/publish_ccl_phase5_component_selection_smoke_kaggle.py \
  --source-bundle-dir artifacts/research/ccl_phase5_component_selection_smoke_v36_source_bundle_v1_001 \
  --job-dataset-dir artifacts/research/ccl_phase5_component_selection_smoke_v36_navigation_provenance_qwen_job_dataset_v1_001 \
  --kernel-package-dir artifacts/research/ccl_phase5_component_selection_smoke_v36_kernel_package_v1_001 \
  --policy-decision artifacts/research/ccl_phase5_component_selection_policy_decision_v1_001/component_selection_policy_decision_v1.json \
  --policy-decision-manifest artifacts/research/ccl_phase5_component_selection_policy_decision_v1_001/component_selection_policy_decision_manifest.json
```

After confirming the `dungle2810` namespace and configuring a local Kaggle API
token, append `--execute`. It creates the two private datasets and then pushes
the private kernel in that order. It intentionally uses `datasets create`, so
an existing slug fails instead of overwriting a prior dataset version.

### Kaggle P100 runtime contract

The private [CCL Phase 3 GPU kernel](https://www.kaggle.com/code/dungle2810/vifinqa-ccl-phase-3-gpu-bake-off-v1)
has a preflight for the actual scheduled GPU, not merely its reported VRAM. A
Kaggle P100 is SM60; if the base PyTorch wheel omits `sm_60`, 4-bit loading is
stopped before checkpoint download. For SM60, the notebook installs the pinned
`torch==2.6.0` CUDA-11.8 wheel first, then installs pinned model libraries
with `--no-deps` so they cannot overwrite that wheel. It probes a CUDA tensor,
requires the reported `sm_60` binary architecture, and records the runtime in
both execution manifest and Kaggle receipt.

Because Transformers imports its vision utilities even on this text route, the
same preflight also installs and verifies the official matching
`torchvision==0.21.0` CUDA-11.8 wheel. A mismatched preinstalled `torchvision`
is a route-environment failure, not a model-result signal.

This is an environment-compatibility repair only. It does not change the 495
prepared requests, source/job hashes, response schema, route selection or
non-promotion contract. If the probe fails, stop the route and retain no model
output; do not disable 4-bit mode or substitute a model silently.

Qwen3 is rendered with `enable_thinking=False` for this JSON-only proposal
task, and the runner writes a progress receipt every ten packets. This is not
a reasoning-quality claim; it prevents hidden thinking text from invalidating
the one-object response contract and keeps the run observable.

## Safe deterministic replay

This command is the canonical end-to-end replay for the current grounded
research path. It reads pinned artifacts, writes only to a fresh output folder,
and fails if any of the five outputs differs from its declared reference:
V2 bindings, Decimal execution, Evidence Bindings, Answer Certificates or
Authorization Readiness.

```bash
.venv/bin/python -m finance_query.cli run-grounded-e2e \
  --config configs/grounded_e2e_v1.yaml \
  --output-dir artifacts/runs/vifinqa-grounded-e2e-replay-v1_YYYYMMDD
```

Expected snapshot outcome:

```text
bindings: 14 binding_ready, 538 binding_blocked, 460 route_incomplete
numeric cell tokens: 14 public literal-free, 14 executor-private
execution: 14 execution_replay_ready, 47 binding_conflict, 951 route_incomplete
sandbox: 14 bounded Decimal executions with diagnostic telemetry
authorization: 882 BLOCKED Evidence Bindings, 1012 ABSTAIN Answer Certificates
authorization fields: period PASS 13, scope PASS 13,
                      unit PASS 14, source_integrity PASS 14,
                      variable/entity PASS 0
reproducibility: bindings_match=true, execution_match=true,
                 evidence_bindings_match=true, answer_certificates_match=true,
                 authorization_readiness_match=true
```

`complete_research_only` means the wiring and deterministic replay succeeded.
It does not mean production approval or answer eligibility.

Each new run also writes:

- `numeric_cell_token_view_v1.jsonl`: literal-free model-facing token view;
- `numeric_cell_token_registry_v1.jsonl`: executor-private values and lineage;
- `execution_telemetry_v1.jsonl`: sandbox timings and policy receipts.

Never put the executor-private registry into an LLM prompt or training export.
Telemetry is operational evidence only and is not consumed by Evidence Binding,
Answer Certificate or the release gate.

Every receipt now has `run_name`, a deterministic `run_id`, config SHA-256,
input artifact SHA-256 and the source-code closure SHA-256. Use
`vifinqa-grounded-e2e-replay-v1` consistently in output directory names.

The canonical authorization baseline is
`artifacts/runs/vifinqa-grounded-e2e-replay-v1_20260822-semantic-ledger-v2`;
the matching `-verify` replay proves all five pinned hashes. Its semantic
review bundle contains 14 exact-cell review packets, 868 operand blockers and
491 no-stage blockers. The older `-v3` baseline materialized only single-stage
operand receipts. The still older
`vifinqa-grounded-e2e-replay-v1_20260818` directory is a historical
pre-authorization receipt: its V2 binding and execution payloads are still
byte-identical to the V5 references, but it does not contain Evidence Bindings
or Answer Certificates and must not be presented as a current full-pipeline
replay.

The current role-aware campaign baseline is
`artifacts/runs/vifinqa-grounded-e2e-document-entity-role-v7_20260823-lock1`.
It preserves 23 original `human_verified` row/cell decisions, consumes the
separate hash-bound ChatGPT entity-role augmentation, and verifies all five
locked downstream artifacts. Its receipt reports 11 entity-role `PASS`, 12
`BOUND` Evidence Bindings, 12 campaign-only complete Answer Certificates, and
1,000 `ABSTAIN`. The original decision provenance and the ChatGPT role-review
provenance must never be collapsed into one field.

The current operation-graph review sidecar is pinned at
`artifacts/research/operation_graph_chatgpt_review_v4_semantic_fingerprints_20260823`.
It contains two semantic approvals and 80 confirmed blockers across 82
graph-review items. Three generic fingerprints now block non-homogeneous
population aggregation, filter-without-count and ratio-before-ranking before
typed review. It is a review artifact only:
`eligible_for_materialization=false` and `may_execute_formula=false` are
invariant. The semantic-axis route-context review at
`artifacts/research/route_context_chatgpt_review_v3_semantic_axes_20260823`
covers all 564 missing-context items: 49 literal sets pass fully and 515 retain
incomplete-literal blockers. The 49 matching receipts in
`artifacts/research/operation_graph_review_v3_context_promoted_20260823` open
only graph-review eligibility; they do not change route, scope, operands or
execution authority. Use the
local `/campaign`, `/audit`, `/graphs`, `/contracts`, `/navigation`, and
`/operands` routes
to inspect or export interaction decisions; never treat a UI draft as a
source-sidecar update. `/campaign` diagnoses the V5 role gaps using V7
remediation evidence; `/audit` shows the V7 mismatch, authorized semantic-row
correction, deterministic rebinding and current V8 review of all 12
certificates.

The navigation lane is pinned at
`artifacts/research/navigation_review_evidence_v1_20260823` and
`artifacts/research/navigation_chatgpt_review_v1_20260823`. It exposes raw row
text, source identity, scope, year, table type and hashes, but deliberately
omits numeric value cells. The 38-item review yields one semantic navigation
candidate (Q167), seven qualifier-based candidate-set rejections and 30
confirmed upstream blockers. Even after an authorized ChatGPT review is loaded,
`eligible_for_materialization`, `may_change_question_plan`, `may_change_route`,
`may_select_value`, `may_execute_formula`, promotion and release remain false.

The cross-entity operand lane is pinned at
`artifacts/research/cross_entity_operand_review_q746_q750_v1_20260823`,
`artifacts/research/cross_entity_operand_chatgpt_review_q746_q750_v1_20260823`,
and
`artifacts/research/cross_entity_subtract_materialization_q750_v1_20260823`.
Regenerate it in order with:

```bash
.venv/bin/python scripts/build_cross_entity_operand_review_packets.py \
  --config-path configs/cross_entity_operand_review_q746_q750_v1.json \
  --normalized-tables artifacts/research/preprocessing_v2_run_003/normalized_tables_v2.jsonl \
  --preprocessing-manifest artifacts/research/preprocessing_v2_run_003/preprocessing_manifest_v2.json \
  --graph-decisions artifacts/research/operation_graph_chatgpt_review_v4_semantic_fingerprints_20260823/operation_graph_chatgpt_decisions_v1.jsonl \
  --graph-manifest artifacts/research/operation_graph_chatgpt_review_v4_semantic_fingerprints_20260823/operation_graph_chatgpt_decisions_v1.manifest.json \
  --typed-plans artifacts/kaggle_runs/notebook5554bd790d_v10_20260811/vifinqa_review_bundle/typed_operand_plans_v1.jsonl \
  --typed-plan-manifest artifacts/kaggle_runs/notebook5554bd790d_v10_20260811/vifinqa_review_bundle/typed_operand_plans_v1.manifest.json \
  --repository-root "$PWD" --output-dir <new-packet-dir>

.venv/bin/python scripts/build_cross_entity_operand_chatgpt_decisions.py \
  --packets <new-packet-dir>/cross_entity_operand_review_packets_v1.jsonl \
  --packet-manifest <new-packet-dir>/cross_entity_operand_review_packets_v1.manifest.json \
  --review-spec configs/cross_entity_operand_chatgpt_review_q746_q750_v1.json \
  --output-dir <new-decision-dir>

.venv/bin/python scripts/materialize_cross_entity_subtract.py \
  --packets <new-packet-dir>/cross_entity_operand_review_packets_v1.jsonl \
  --packet-manifest <new-packet-dir>/cross_entity_operand_review_packets_v1.manifest.json \
  --decisions <new-decision-dir>/cross_entity_operand_chatgpt_decisions_v1.jsonl \
  --decision-manifest <new-decision-dir>/cross_entity_operand_chatgpt_decisions_v1.manifest.json \
  --normalized-tables artifacts/research/preprocessing_v2_run_003/normalized_tables_v2.jsonl \
  --preprocessing-manifest artifacts/research/preprocessing_v2_run_003/preprocessing_manifest_v2.json \
  --output-dir <new-materialization-dir>
```

Never copy the executor-private registry or private execution JSONL into UI,
prompt, review export, answer, training, release, or submission paths. The
`/operands` route consumes only the numeric-value-free packet, decision and
public receipt.

The V7 campaign handoff and independent ChatGPT audit are pinned at
`artifacts/research/grounded_campaign_review_v7_document_role_20260823` and
`artifacts/research/grounded_campaign_chatgpt_audit_v7_20260823`. The audit
reopens 12/12 candidate rows, all entity-role anchors and the locked Decimal
replay lineage while exposing zero numeric literals to the reviewer. Eleven
candidate semantics pass. Q702 returns `NET_OTHER_INCOME_NOT_PROVEN` because
the selected row `Thu nhập khác` omits the claim qualifier `thuần`; the same
table contains the distinct net row `Lợi nhuận khác (40 = 31 - 32)`. Validate
the decision with:

```bash
.venv/bin/python scripts/validate_grounded_campaign_human_decision.py \
  --handoff-manifest artifacts/research/grounded_campaign_review_v7_document_role_20260823/grounded_campaign_review_handoff_v1.manifest.json \
  --decision artifacts/research/grounded_campaign_chatgpt_audit_v7_20260823/grounded_campaign_chatgpt_decision_v1.jsonl
```

The V7 status is intentionally `campaign_revision_required`; it is the frozen
pre-correction finding. The current V8 handoff and audit are pinned at
`artifacts/research/grounded_campaign_review_v8_semantic_correction_20260823_lock3`
and `artifacts/research/grounded_campaign_chatgpt_audit_v8_20260823_lock3`.
Validate the current decision with:

```bash
.venv/bin/python scripts/validate_grounded_campaign_human_decision.py \
  --handoff-manifest artifacts/research/grounded_campaign_review_v8_semantic_correction_20260823_lock3/grounded_campaign_review_handoff_v1.manifest.json \
  --decision artifacts/research/grounded_campaign_chatgpt_audit_v8_20260823_lock3/grounded_campaign_chatgpt_decision_v1.jsonl
```

The expected status is `campaign_approved_not_released`: 12/12 candidate
semantics pass, but release and promotion must remain false.

Generate a fresh immutable semantic review campaign from a new exact-binding
baseline with:

```bash
.venv/bin/python -m finance_query.cli build-semantic-review-queue \
  --bindings artifacts/research/production_coverage_iteration_v5/exact_cell_unit_binding_candidates_v2.jsonl \
  --bindings-manifest artifacts/research/production_coverage_iteration_v5/exact_cell_unit_binding_candidates_v2.manifest.json \
  --structured-tables artifacts/kaggle_runs/notebook5554bd790d_v10_20260811/vifinqa_review_bundle/tables_structured_v2.jsonl \
  --evidence-context artifacts/kaggle_runs/notebook5554bd790d_v10_20260811/vifinqa_review_bundle/tables_evidence_context_v3.jsonl \
  --evidence-context-manifest artifacts/kaggle_runs/notebook5554bd790d_v10_20260811/vifinqa_review_bundle/table_evidence_context_v3.manifest.json \
  --output-dir artifacts/research/semantic_binding_review_vNEXT
```

The generated human-decision file is intentionally empty. Only a separate
decision sidecar with `reviewer_type=human_verified`, checked source
coordinates and the exact queue SHA can authorize variable/entity semantics.

Before replaying from a restored or clean checkout, verify all 28 pinned files
(419,572,332 bytes) without mutation:

```bash
.venv/bin/python scripts/verify_grounded_reproducibility_lock.py \
  --lock configs/grounded_reproducibility_lock_v1.json \
  --repo-root .
```

### Artifact impact after the authorization integration

| Artifact class | Current status | Rebuild required? |
| --- | --- | --- |
| Raw/V10 tables and V2/V3 source context | Immutable source inputs; hashes unchanged | No |
| Route V4 and period/binding V5 packets | Still the pinned navigation inputs | No |
| Exact V2 bindings and Decimal V2 execution | Byte-identical to their previous references | No |
| Pre-authorization E2E receipts | Historical provenance only; incomplete for the new pipeline | Rerun for a current receipt |
| Evidence Bindings and Answer Certificates | Additive baseline introduced by the new authorization layer | Already pinned in the 20260822 baseline |
| CCL/Kaggle/review research outputs | Independent lineage; not invalidated automatically | No, unless their own input manifest fails |

Do not delete versioned research, shadow, review or GPU artifacts merely because
a newer normalization contract exists. Delete only after verifying there is no
consumer reference, the payload is duplicated by hash, and no unique manifest
or audit lineage would be lost.

## V12 relational entity-role replay

V12 remediates the 11 residual `parent` role gaps without using reporting
scope as role evidence and without exposing answer values to either reviewer.
Rebuild the bounded queue, two independent decisions, exact consensus, and
role-only augmentation in this order:

```bash
.venv/bin/python scripts/build_entity_role_relational_candidates.py \
  --prior-candidate-queue artifacts/research/entity_role_provenance_candidates_v1_20260823/entity_role_provenance_candidates_v1.jsonl \
  --prior-decisions artifacts/research/entity_role_provenance_chatgpt_decisions_v1_20260823/entity_role_provenance_chatgpt_decisions_v1.jsonl \
  --structured-tables artifacts/kaggle_runs/notebook5554bd790d_v10_20260811/vifinqa_review_bundle/tables_structured_v2.jsonl \
  --repository-root . \
  --output-dir artifacts/research/entity_role_relational_candidates_v1_NEXT

.venv/bin/python scripts/build_entity_role_relational_chatgpt_decisions.py \
  --candidate-queue artifacts/research/entity_role_relational_candidates_v1_NEXT/entity_role_relational_candidates_v1.jsonl \
  --review-spec configs/entity_role_relational_chatgpt_proposal_v1.json \
  --output-dir artifacts/research/entity_role_relational_chatgpt_proposal_v1_NEXT

.venv/bin/python scripts/build_entity_role_relational_chatgpt_decisions.py \
  --candidate-queue artifacts/research/entity_role_relational_candidates_v1_NEXT/entity_role_relational_candidates_v1.jsonl \
  --review-spec configs/entity_role_relational_chatgpt_critic_v1.json \
  --output-dir artifacts/research/entity_role_relational_chatgpt_critic_v1_NEXT

.venv/bin/python scripts/reconcile_entity_role_relational_chatgpt_decisions.py \
  --candidate-queue artifacts/research/entity_role_relational_candidates_v1_NEXT/entity_role_relational_candidates_v1.jsonl \
  --proposal-decisions artifacts/research/entity_role_relational_chatgpt_proposal_v1_NEXT/entity_role_relational_chatgpt_decisions_v1.jsonl \
  --proposal-manifest artifacts/research/entity_role_relational_chatgpt_proposal_v1_NEXT/entity_role_relational_chatgpt_decisions_v1.manifest.json \
  --critic-decisions artifacts/research/entity_role_relational_chatgpt_critic_v1_NEXT/entity_role_relational_chatgpt_decisions_v1.jsonl \
  --critic-manifest artifacts/research/entity_role_relational_chatgpt_critic_v1_NEXT/entity_role_relational_chatgpt_decisions_v1.manifest.json \
  --output-dir artifacts/research/entity_role_relational_dual_chatgpt_v1_NEXT

.venv/bin/python scripts/augment_semantic_entity_role_relational_reviews.py \
  --semantic-queue artifacts/research/semantic_binding_review_v5_entity_role_20260823/semantic_binding_review_queue_v1.jsonl \
  --prior-semantic-decisions artifacts/research/semantic_binding_review_v7_document_role_chatgpt_20260823/semantic_binding_human_decisions_with_document_role_v1.jsonl \
  --relational-queue artifacts/research/entity_role_relational_candidates_v1_NEXT/entity_role_relational_candidates_v1.jsonl \
  --reconciled-decisions artifacts/research/entity_role_relational_dual_chatgpt_v1_NEXT/entity_role_relational_chatgpt_decisions_v1.jsonl \
  --output-dir artifacts/research/semantic_binding_review_v12_relational_role_chatgpt_NEXT
```

The checked-in immutable V12 artifacts use the `20260823` suffix. Replay the
locked pipeline and verify the integration gate with:

```bash
.venv/bin/python scripts/run_grounded_e2e.py \
  --config configs/grounded_e2e_v12_relational_entity_role_locked.yaml \
  --output-dir artifacts/runs/vifinqa-grounded-e2e-relational-entity-role-v12_NEXT

.venv/bin/python scripts/validate_integration_quality_gate.py \
  --policy configs/integration_quality_gate_v1.yaml \
  --repository-root .
```

Expected locked result: five reproducibility matches, 26 campaign-only
certificates, 986 abstentions, and an isolated V11→V12 authorization diff for
Q6, Q10, Q27, Q145, Q168, Q181, Q184, Q249, Q292, Q316 and Q325. The local UI
route `/roles` exposes all 11 numeric-free proposer/critic dialogues and six
review prompts; `/audit` reopens all 26 V12 candidates. A UI export is a review
sidecar only and cannot authorize execution, promotion, release or submission.

## Validation

Rebuild the locked V9 navigation-promotion replay with:

```bash
.venv/bin/python scripts/run_grounded_e2e.py \
  --config configs/grounded_e2e_v9_navigation_promotion_locked.yaml \
  --output-dir artifacts/runs/vifinqa-grounded-e2e-navigation-promotion-v9_YYYYMMDD-lockN
```

The command must report five reproducibility matches, 13 campaign-only
certificates, 999 ABSTAIN, one ChatGPT semantic correction, one ChatGPT
navigation promotion, and 23 unchanged human semantic approvals. The V8→V9
artifact diff must be isolated to Q167. A successful replay still leaves
release and promotion false.

```bash
.venv/bin/python -m unittest discover -s tests -q
.venv/bin/python scripts/build_artifact_registry.py \
  --workspace-root . --validate-only
git diff --check
```

## GPU/Kaggle work

Use GPU only for candidate retrieval, reranking, critic proposals, or a
separately named training experiment. Before accepting a model, verify:

1. source snapshot and input manifests match their SHA-256;
2. the evaluation is issuer-held-out and uses the serving passage/ranking path;
3. the candidate is compared against its baseline;
4. a promotion manifest approves it before a new FAISS index is built.

The active rows-first ablation is an evaluation experiment, not a production
index replacement.

## Fine-tune V2 sequence

Do not start from a mutable JSONL. Freeze a promoted preprocessing manifest,
then split by issuer before generating training pairs. Training must use the
same passage renderer as serving, duplicate-free batches and balanced
hard-negative families. Each checkpoint must pass embedding-variance,
positive-margin and small-canary checks before issuer-held-out Recall@10/NDCG
is compared with the unchanged base model. A winning checkpoint still needs a
separate promotion manifest before an index rebuild.

## Release boundary

The submission compiler is intentionally separate. It requires all 1,012
questions to have an approved independent audit, a full production execution
ledger with complete lineage, and a release gate marked ready. Do not create or
patch a submission while the gate is `blocked`.
