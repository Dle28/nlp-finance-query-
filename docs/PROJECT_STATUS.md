# Project status

## What the project does

ViFinQA answers Vietnamese financial-report questions only when every operand
can be traced to its raw source table, row, column and unit. Retrieval and
metadata may suggest where to look; they never prove a numeric value.

## Current state

| Area | State | Evidence / boundary |
| --- | --- | --- |
| Corpus and retrieval bundle | Ready for the pinned Kaggle snapshot | Raw `TableAsset`, FTS/FAISS and Review Bundle V3 are immutable inputs; hierarchy-aware scoring is an opt-in soft candidate rank and remains disabled by default pending a safety-preserving ablation gate. |
| V2/V3 normalization | Ready | V2 preserves raw-cell geometry; V3 canonicalizes headers, period and unit. |
| Data-first preprocessing V2 | Implemented; review gate remains blocked | Creates a hash-bound canonical view, repair ledger, reason-stratified samples and quarantine without changing raw source. No output is training-eligible. |
| Certified Canonical Layer | Bounded smoke completed; no promotion | `certified_canonical_v1_run_003` validates all 29,509 source/cell lineages and creates finite Evidence Relation Graphs. The audited V3.3 full route has 270/270 coverage: 263 `VALID_PROPOSAL_ONLY`, 7 `INVALID_UNRESOLVED`. The source-first rerun covers all 211 table-semantic claims with deterministic context and immutable table structure: 12 statement-title profiles, 166 literal note components and 33 exact source-cell/relation contexts. The earlier five-item blind review produced 3 abstentions. The post-smoke v36 review produced 2/5 human abstentions; Qwen and Mistral each matched the human primary component on 3/5 items, while exact supporting-component matches were 0/5 and 1/5 respectively. Both calibrations are `scored_not_promoted`; neither establishes table semantics, training eligibility or certification. The exact-source navigation overlay flags 73 `NỘI DUNG | TRANG` page-index grids, including the VGC contents page previously misread as cash-flow context. `v36_navigation_provenance_run_001` has 198 packets, 12 deterministic bypasses and one blocked contents page. The named exception was used exactly once: two private datasets and the private [Qwen3-8B P100 kernel](https://www.kaggle.com/code/dungle2810/vifinqa-ccl-phase-5-v36-smoke-run-v1) completed its five requests. The downloaded output passes `ccl_phase5_component_selection_smoke_audit_v1` at `artifacts/research/ccl_phase5_component_selection_smoke_v36_kernel_output_001/ccl_phase5_component_selection_smoke_audit_v1.json`: 5/5 `VALID_COMPONENT_SELECTION_ONLY`, unchanged hash lineage, numeric guard clean, and both promotion flags false. |
| Grounded deterministic path | Ready in research mode | Route/period packets → exact V2 cells → 14 hash-bound numeric tokens → sandboxed Decimal replay and telemetry is runnable with `finance-query run-grounded-e2e`; the execution JSONL remains byte-identical to its pinned reference. |
| Legacy retrieval/binding | Diagnostic only | `legacy-binding-probe` (including deprecated `answer-direct`) returns candidates but never a numeric answer, training record, or submission candidate. |
| Query understanding | Partial | 599 typed plans complete; 77 non-executable; 336 abstain. Question IDs are artifact keys only and cannot select a semantic family. |
| Exact bindings and authorization | Partial; V12 fail-closed replay verified | All 882 planned operand receipts are materialized. Twenty-three original row/cell decisions retain `human_verified` provenance. Eleven residual parent-role gaps now have exact relational source proof after distinct ChatGPT proposer/critic agreement on one source line and all six semantic checks. The new role receipts remain `chatgpt_verified`; reporting scope `separate` is never treated as proof of role `parent`, and only the deterministic executor reads values. V12 yields 26 campaign-only complete certificates and 986 ABSTAIN, with 5/5 locked artifacts matching. |
| Independent V12 campaign audit | Candidate semantics approved; not released | ChatGPT reopened all 26 complete V12 candidates with zero numeric-value exposure and zero blockers. The authority receipt grants human-equivalent gate weight while preserving `chatgpt_verified` provenance. Release, promotion, training and submission remain false. |
| Navigation remediation | Q167 promoted with bounded authority | All 38 remediation items retain their original review receipts. Only Q167 gains an additional hash-bound promotion receipt. It does not infer sector or change the question plan; it authorizes exact-row/header/table-role/entity-role review only, while value selection, execution, promotion and release remain false. |
| Production release | Blocked | 55/1,012 independent-audit pass; no full production execution ledger. |
| V13 claim-requirement shadow audit | Hardened V7 authoritatively verified; non-promotable | All 1,012 claims have a config/builder/verifier/code-hash-bound requirement set and Semantic Coverage Certificate V2. The verifier distinguishes structural replay from authoritative verification and requires the pinned official config SHA trust root for the latter. Proposition-bound IDs, document-co-referenced typed anchors, date/year consistency and deterministic replay reject forged PASS; missing comparison fields remain `UNRESOLVED`, not false `FAIL`. Ambiguous raw entity parses fail closed and eight parent-reporting claims no longer lose their role obligation to a subsidiary counterparty phrase. The first-blocker partition remains 595 composed, 353 route, 38 temporal and 26 V12 candidates; temporal is 27 instant and 11 duration. No record is internally complete or claim-complete, and release remains blocked. |
| V13 evidence-closure workbench | Implemented; all outputs pending independent review | The V7 lock now drives 501 formula, 94 operand/compatibility, 250 table/metric, 37 route-operator, 66 route-cause, 38 temporal and 26 V12-recertification receipt-intake queues, plus 1,012 independent-requirement packets and 1,012 blocked production-ledger intake rows. Existing typed plans and Formula EvidenceSets are linked only as non-authorizing diagnostic candidates. |
| Active-learning control plane V1 | Shadow cycle replay-verified; no policy promoted | The frozen cycle partitions 1,012 questions into 184 proof-policy clusters and selects 64 active-learning plus 32 independently probability-sampled audit packets. The model graph is open-source-only and strictly below 14.7B: Qwen3-8B proposer plus Mistral-Nemo-12B critic, with exact revisions/weight hashes still required before execution. ChatGPT is excluded from training/inference/ensembles and may only perform bounded `chatgpt_verified` human-equivalent review. Audit is frozen before active selection; a single review cannot enter training, agreement remains non-materializable, and calibration counts independent correctness rather than acceptance. Current adjudicated training records: 0; provisional policies: 0; release remains blocked. |
| Retrieval fine-tuning | Experimental and gated | A candidate must pass issuer-held-out evaluation and be promoted explicitly before any index is rebuilt. Machine-silver training additionally requires >=200 pairs plus hash-bound independent-audit precision, CI, fingerprint coverage, and leakage checks. |

## Current label inventory boundary

The preprocessing configuration pins **30 unique current benchmark
supervision labels**: 1 `human_verified` record and 29
`machine_calibrated` records. The 10,000 synthetic execution examples are a
separate training curriculum, not 10,000 additional benchmark labels. Legacy
pilot exports and 27 pending metadata approval proposals are reported
separately and must not be summed into the active label count.

## Preprocessing V2 verified checkout

The current review checkout is
`artifacts/research/preprocessing_v2_run_003`. It processes the pinned V10
review bundle's 29,509 tables with 100% V2/V3 UID coverage: 7,564 are
`review_ready`, 21,945 are `needs_review`, and none are quarantined. The larger
45,888-row `artifacts/table_assets.jsonl` belongs to a different extraction
lineage and is corpus inventory only; it must not be silently joined to the
V10 sidecars.

The remaining preprocessing blockers are semantic rather than numeric-source
corruption: generic table semantics (15,150), ambiguous headings (9,316),
possible concatenated words (6,042), generic value-column headers (2,497), and
duplicate canonical headers (1,618). Counts overlap because one table can have
multiple reason codes.

`artifacts/research/report_navigation_overlay_v1_run_001` is a separate,
full-coverage source-layout guard. It identifies 73 literal contents-page grids
from `NỘI DUNG | TRANG` plus page locators and sets only their
`table_semantic_dispatch_allowed` field to false. It neither repairs V3's
historic semantic label nor changes source values, evidence eligibility,
training eligibility or certification.

The grounded replay is intentionally `research_only`: it cannot promote a
label, create a submission answer, train a model, or bypass the release gate.
Its tracked identity is `vifinqa-grounded-e2e-replay-v1`.

## Certified Canonical Layer: verified graph-contract run

`artifacts/research/certified_canonical_v1_run_003` processed the same 29,509
V10 tables and left every immutable input SHA-256 unchanged. All 29,509 source
identity and cell-lineage certificates passed; the in-memory mutation suite
also passed. The run deliberately assigns all tables `UNRESOLVED`: Phase 0–2
does not claim table semantics, so it cannot certify or promote a training
record.

The fixed Phase 3 packet set contains 270 tables across six deterministic
buckets. The next semantic-work routing starts from the evidence packets, not
from a mutable hand-edited queue. Current primary blockers are generic table
semantics (9,724), ambiguous headings (9,316), text repair (1,470), unit
context (1,381), period context (1,046), column-path context (766), heading
context (886), and 4,920 records awaiting semantic certification. These are
primary issue counts, not overlapping preprocessing reason counts.

The graph-contract Phase 3 job uses Qwen3-8B on all 270 packets and Gemma 3
12B only as an independent challenger on 225 nontrivial packets. Each proposal
must select finite anchor and relation IDs; arbitrary quotation is invalid. The
visual route remains disabled pending hash-bound page images. Raw GPU responses
remain proposal-only; Phase 4–5 still need source-rule verification.

The first Kaggle attempt exposed an environment fault, not a data or model
quality result: its P100 had 16 GiB VRAM but the base PyTorch wheel omitted
P100 `sm_60` kernels. A second attempt caught a dependency installer replacing
the pinned SM60 wheel before model loading; a third caught its incompatible
preinstalled CUDA-12.8 `torchvision` counterpart. Version 5 pins the matching
CUDA-11.8 PyTorch/torchvision pair first, installs remaining model libraries
without dependency resolution, validates `sm_60`, disables Qwen3 thinking for
the exact-one-JSON contract, and records progress every ten packets. It
completed all 270 Qwen requests on P100, then stopped at the final receipt
because its preflight runtime omitted `cuda_available` while the executor
recorded it. The notebook contract is now aligned, but this legacy lexical run
remains non-promotable. Its schema is superseded by the graph-contract job and
can never enter certification or fine-tuning.

The completed V3.3 route also revealed a semantic-schema failure: its 211
table-semantic proposals produced zero controlled matches against literal Phase
4/5 financial-statement profiles. The separate V3.4 five-packet smoke fixed
the format contract (5/5 valid enum-plus-heading proposals on P100) but only
2/5 enums agreed with the independent source profile: 40% semantic agreement.
This proves the source-profile rule is the correct baseline for literal
financial-statement headings; do not scale that LLM classification task or
promote its rows directly.

## Review in this order

1. Read [ARCHITECTURE.md](../ARCHITECTURE.md) for component boundaries.
2. Run the deterministic replay in [OPERATIONS.md](OPERATIONS.md).
3. Inspect the generated `grounded_e2e_run_v1.json`; it records every input and
   output SHA-256 plus status counts.
4. Read the release gate before any submission work. A `blocked` gate is a
   correct no-go result, not a task to override.

## Current next work

### Entity-role semantic gate

The V5 campaign audit found that 22 of 23 legacy campaign certificates contain
an explicit “công ty mẹ” claim while Evidence Binding V1 certifies only issuer
identity and `separate` reporting scope. Those legacy certificates remain
immutable but are blocked by `entity_role_issue_briefs_v1`; the campaign status
is `semantic_revision_required`. Evidence Binding V2 adds an independent
`entity_role` field, and campaign approval is fail-closed until source-bound
role provenance is reviewed and new certificates are generated.

The campaign UI now exposes an authorized `chatgpt_verified` review lane beside
the independent-human lane. Under the owner's explicit grant, ChatGPT reviewed
all 22 entity-role briefs and returned `needs_revision`; the validator accepts
that decision only with distinct AI provenance and keeps release and promotion
disabled. This does not relax semantic materialization or training gates that
still explicitly require human or calibrated provenance.

V6 preserved all 23 original human row/cell decisions and proved Q730 from an
explicit source-title role literal. V7 then searched the exact extracted source
document for each of the remaining 21 claims and froze 59 candidate lines from
14 documents; seven documents contained no role literal. ChatGPT reviewed the
hash-bound candidates under the owner's explicit gate-equivalence grant: 10
were approved because the line directly identifies the issuer as parent, while
11 candidate sets were rejected for no literal, generic definitions, or a
literal that names a different parent. Together with Q730, 11 entity roles now
PASS; Q256 remains role-not-applicable. V7 has 12 campaign-only complete
certificates, 1,000 ABSTAIN, and 5/5 reproducibility matches. Original row/cell
`human_verified` provenance remains separate and unchanged; release and
promotion remain disabled.

### Independent V7 whole-campaign audit

The V7 handoff now contains only the 12 post-remediation complete candidates
and zero unresolved entity-role briefs. An independently authorized
`chatgpt_verified` reviewer reopened every raw row, exact coordinate,
entity-role anchor and replay hash without receiving numeric literals. Eleven
candidates pass. Q702 fails strict variable semantics: the question asks for
`Thu nhập khác thuần`, the selected row is gross `Thu nhập khác`, and the same
table separately exposes `Lợi nhuận khác (40 = 31 - 32)`. The campaign verdict
is therefore `needs_revision`, not approval. This new finding does not rewrite
the original human decision; it is a separate, hash-bound campaign-audit
conflict that must be repaired and reviewed before a new replay.

### V8 Q702 semantic correction and campaign audit

The correction is implemented as a separate `chatgpt_verified` sidecar. It
preserves the original Q702 human decision hash, exposes only non-value row
cells and hashes, and grants ChatGPT authority to select a semantic row—not a
number, binding mutation, formula, release or promotion. The deterministic
materializer reopens the exact CEO table and binds `other_profit` at row 14 for
the same 2018 value column.

Candidate and locked V8 runs match on bindings, execution, Evidence Bindings,
Answer Certificates and readiness. A V7→V8 regression diff changes only Q702;
the other 1,011 question records are unchanged. The independent V8 audit now
returns 12 `approved_candidate`, zero blockers and zero numeric-value exposure.
This is `campaign_approved_not_released`: the independent production release
gate remains a separate blocked requirement.

### Operation-graph semantic review gate

The graph-review queue now contains 82 questions after 49 bounded
route-context promotions. Generic semantic fingerprints now block Q819 for a
non-homogeneous population, Q973 for filter-without-count, and Q982 for
ranking-before-ratio before typed review. Structural validation therefore
accepts only Q746 and Q750; the other 80 candidates retain specific hash-bound
blockers. All decisions remain
`reviewed_not_materialized`; `may_execute_formula`, promotion, and release are
false at the graph-review gate.

### Cross-entity exact-operand review

A numeric-value-free downstream research lane now resolves the exact source
coordinates for all four operands in Q746 and Q750. ChatGPT has explicit
review-gate equivalence for seven semantic checks, with provenance retained as
`chatgpt_verified`; it cannot see/select a value or execute the formula. Q750
passes identity, parent role, separate scope, period, unit, variable and
operand order. Q746 now also passes after a distinct proposer and critic agree
that KHG parent role is proven by issuer co-reference plus the source statement
that the Company directly invests in two subsidiaries. Separate scope remains
an independent gate. The deterministic executor privately reopens each pair of
hash-bound cells and emits research-only replay receipts. The locked V9→V10
diff is isolated to Q750, and the locked V10→V11 diff is isolated to Q746.
Neither result authorizes release, training or submission.

### Route-context literal review gate

The semantic-axis V3 route-context queue fixes false positives at their
deterministic source rather than as question-ID overrides. `công ty mẹ` no
longer proposes `scope=separate`; “so với năm” is not growth inside an absolute
difference; and “trong số các năm” is not a company population even when later
text contains ticker-like tokens. ChatGPT reviews all 564 hash-bound items: 49
complete operation-contract sets with 259 accepted labels and 515 incomplete
literal blockers. A new materializer applies the owner's gate-equivalence grant
only to `controlled_operation_contract`, creating 49 promotion receipts and
opening those questions for graph review. It cannot change plan, route, scope,
operand or value, and cannot execute a formula.

1. Preserve the completed v36 smoke as a one-time, non-promotable measurement.
   Independent-model agreement and the five-item post-smoke calibration are
   complete, but supporting-component agreement remains inadequate and the
   only exact closed-world agreement item received a human abstention. The next
   gate is an explicit policy review of calibration error and sample size,
   followed by a larger stratified calibration before any bounded pilot. The
   73-table navigation overlay must continue to gate every financial-table
   semantic dispatch; none of these artifacts is a table-semantic label.
2. Keep the four merged-header endpoint mismatches unresolved. The audited
   literals show model relation-selection errors, not missing suffix patterns:
   `Năm nayVND`/`Năm trướcVND`, `2021VND`/an unrelated note heading,
   `31.12.2019VND`/`31.12.2020VND`, and `31/12/2024 VND`/`1/1/2024 VND`.
   Add a new split pattern only with an allow-listed suffix and an exact
   endpoint match; the raw header is immutable.
3. Preserve the 33 row-label-only cases as exact source-cell/relation context
   until a table-structure join is validated. The previous 15 heading gaps are
   now covered by literal enumerated-note rules; this is not a table-semantic
   classification.
4. Do not repair, promote or fine-tune V2/V3/V3.1/V3.2 or V3.3 full output.
   Every row remains proposal-only until an independent profile validator
   passes and campaign certification is implemented.
5. Evaluate dynamic candidate-set document/page/table/operand recall before
   tuning retrieval; do not use a fixed Top-10 threshold.
6. Promote a hash-bound subset only after CCL campaign certification; do not
   train directly from preprocessing V2.
7. Preserve the typed-operation fingerprint regressions for homogeneous
   population aggregation, filter-then-count, and ratio-then-ranking. Do not
   add question-ID exceptions. The two approved subtract graphs still require
   exact-source operand coverage before grounded execution materialization.
   Q746 and Q750 now have the required locked grounded integration and an
   independent V11 campaign audit. Keep both certificates campaign-only until
   the separate production release ledger and release gate pass; do not
   generalize this promotion to other multi-stage questions without exact
   evidence and independent consensus.
8. Preserve the semantic-axis route-context rules and their regression tests.
   Future patterns must prove their axis generically and may not add
   question-ID exceptions.
9. Materialize a full independent audit and production execution ledger only
   after all upstream exact-source gates pass.
