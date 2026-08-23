# Technical contracts

| Stage | Input | Output | May not do |
| --- | --- | --- | --- |
| Corpus | Raw report | immutable raw table locator/grid; legacy retrieval projection | alter source values or replace raw identity with an interpretation |
| Retrieval | question + assets | lexical/dense/hierarchy-ranked table candidates | treat any rank as evidence or hard-filter on model inference |
| Hierarchy candidate rank | bounded retrieval pool + source-derived structure | soft rank and diagnostic `hierarchy_rank` | remove a zero-score candidate, select a cell, or claim semantic grounding |
| Legacy binding probe | retrieval candidates + heuristic row/column matches | diagnostic candidates only | return a numeric answer, promote provenance, or enter training/submission |
| V2/V3 | raw table | exact grid + canonical context | invent a cell/value |
| CCL Phase 0–2 | V10 + V2/V3 + Preprocessing V2 | identity/cell certificates, assertions, issue DAG and bake-off packets | certify semantics, call an LLM, or promote training data |
| Packet evidence graph | one hash-bound CCL packet | finite raw anchor IDs and deterministic header/period/unit/row relations | let an LLM mint a coordinate, quote, hash or relation |
| CCL Phase 3 build | CCL manifest + fixed packets | hash-bound model requests and response schema | download/run a model or change a packet |
| CCL Phase 3 GPU route | one prepared text route | raw response receipt | certify a fact, promote data, or alter source artifacts |
| CCL Phase 3 validator | raw receipts + packet evidence graph | finite-ID, source-anchored proposal-only records | accept a free-text citation, certify semantics, or promote data |
| Candidate-set diagnostics | lexical/dense/metadata candidate pools + declared query coverage | dynamic candidate universe and document/page/table/operand-set recall | regard Top-10, rank, or table hit as sufficient evidence |
| Route/period | question plan + metadata | candidate source coordinates | select a value or infer scope |
| Exact binding | candidate coordinates + V2 | raw-cell/unit bindings | resolve ambiguity by score |
| Numeric cell tokens | exact bindings + V2 source hashes | literal-free public view + executor-private registry | expose private literals to a model or treat a token as semantic evidence |
| Evidence Binding | exact source cell + source-anchored semantic field bindings | field-level variable/period/unit/entity/entity-role/scope/revision/source-integrity certificate | infer a missing field, collapse issuer identity, legal role and reporting perimeter, use a routing decision as evidence, or allow a partial binding to become an operand |
| Binding Certificate | Evidence Binding + hash lineage | immutable binding ID and lineage receipt | make evidence valid imply release, training or serving approval |
| Formula Contract / compatibility | Binding Certificates + formula-specific operand rules | pass or explicit incompatibility/abstention | require all operands to share a period type or revision policy unless that formula declares it |
| Decimal sandbox | token-verified operands + allow-listed formula AST | bounded Decimal result + diagnostic telemetry | execute Python source, import, access files/network, spawn a process, or authorize an answer |
| Decimal replay | exact bindings + token registry + formula registry | replay trace/status | use LLM-generated arithmetic or bypass token lineage checks |
| Answer certificate | typed Binding Plan + exact operands + Decimal trace + rejected alternatives | campaign-only answer-proof candidate or explicit abstention | claim global uniqueness, serving/promotion or training eligibility |
| Machine-silver training | provenance-validated pairs + independent replay/critic + quality gate | experimental checkpoint only | train below 200 pairs, bypass audit precision/CI/fingerprint coverage, or promote a checkpoint |
| Audit/release | all sidecars | approval or explicit block | promote provenance implicitly |
| Claim Requirement Set V13 | claim + versioned deterministic rules | explicit dimensions, dependencies and applicability | claim the generated set is exhaustive or silently omit an unchecked dimension |
| Semantic Coverage Certificate V2 | requirement set + V12 certificate | internal coverage and claim-completeness states | convert internal completeness into claim completeness or release authority |
| Source Truth Tier | source provenance | OCR-derived, visual-source-verified, source-native, or unresolved classification | infer a higher truth tier from source-integrity PASS |

## Canonical grounded replay

`configs/grounded_e2e_v1.yaml` pins the source artifacts. The runner performs
V2 exact binding and Decimal replay, then materializes full-corpus Evidence
Bindings and Answer Certificates. It snapshots every input SHA-256 before and
after execution, writes a fresh run directory, and compares the deterministic
V2 outputs against the declared reference artifacts.

```text
route completeness overlay
  -> period-column packets
  -> exact-cell/unit binding V2
  -> numeric cell tokens -> sandboxed Decimal replay V2 ------+
  -> typed Evidence Binding -> Binding Certificate
       -> Formula Compatibility ------------------------------+-> Answer Certificate or ABSTAIN
  -> research-only run receipt
```

The authorization hand-off remains non-promotable by design. V2 alone does not
prove every semantic binding field. The current V12 replay preserves 23
hash-bound `human_verified` row/cell decisions and records new semantic review
in separate `chatgpt_verified` sidecars under the campaign owner's explicit
authority grant. Eleven residual parent-role assertions pass only through
exact relational source evidence plus independent proposer/critic consensus;
the result is 26 campaign-only complete certificates and 986 `ABSTAIN`
certificates. Gate-equivalent review authority does not merge provenance,
authorize materialization, or satisfy the independent production audit. The
production compiler still accepts only a separately approved full-corpus
ledger and ready release gate.

`numeric_cell_token_view_v1.jsonl` is the only token artifact eligible for
model context and contains no numeric literal. Its paired
`numeric_cell_token_registry_v1.jsonl` is executor-private and contains the
Decimal candidate plus document/table/cell hashes. The executor rejects a
missing token, coordinate drift or Decimal mismatch before formula execution.
Both artifacts remain candidate-only and non-promotable.

`execution_telemetry_v1.jsonl` records token IDs, sandbox policy digest,
operation count, maximum AST depth and elapsed time. Timing is intentionally a
diagnostic observation rather than a deterministic answer input. The canonical
execution JSONL remains byte-compatible with its pinned reference.

## Source of truth

- Source values: raw report and V2 source cell.
- Header/period/unit interpretation: V3 context plus source anchors.
- Artifact identity: SHA-256 dependency graph.
- Release decision: independent audit + full production ledger + release gate.
- Project status: [PROJECT_STATUS.md](PROJECT_STATUS.md).

The additive V13 contract and rebuild command are documented in
[CLAIM_REQUIREMENT_V13.md](CLAIM_REQUIREMENT_V13.md). Its five proof states are
`PASS`, `FAIL`, `UNRESOLVED`, `NOT_APPLICABLE`, and `NOT_CHECKED`; absence is
never interpreted as a semantic pass.

## Evidence Binding

`finance_query.evidence_binding` is the authorization boundary between
candidate discovery and answer authorization. It runs *after* raw
numeric-token parsing: parsing retains the literal raw token/value, while this
layer decides whether that exact cell has the semantic identity required by an
operand. The preceding Decimal replay remains diagnostic and cannot make an
operand eligible or an answer releasable. The binding must independently carry
source anchors and statuses for:

```text
variable, period, unit, entity identity, entity role, reporting scope, revision, source integrity
```

Every field has one of `PASS`, `NOT_APPLICABLE`, `UNRESOLVED`, `CONFLICT` or
`FAIL`. `NOT_APPLICABLE` is legal only when the hash-bound
`requirements.required_fields` contract explicitly marks that field as
non-required. `operand_eligible` is derived only when each field is `PASS` or
contract-valid `NOT_APPLICABLE`; `UNRESOLVED`, `CONFLICT`, `FAIL`, and a
required `NOT_APPLICABLE` remain a reviewable `BLOCKED` artifact.

Evidence Binding V2 treats `entity.identity`, `entity.role` and
`reporting_scope` as independent assertions. A separate financial statement
proves a reporting perimeter, not that its issuer has the group role `parent`.
When a question explicitly claims “công ty mẹ”, `entity_role` is required and
remains `UNRESOLVED` until hash-bound source provenance proves that role.

Campaign review accepts two truthful, non-interchangeable provenance lanes:
`human_verified` for an independent human and `chatgpt_verified` for an AI
reviewer explicitly authorized by the campaign owner. The AI lane must record
its model family, `fail_closed_evidence_bound_v1` policy and authority grant.
Gate equivalence permits a campaign verdict; it never renames AI output as
human evidence, creates missing provenance, or authorizes release/promotion.
For mixed review, the row/cell decision retains its original
`decision_provenance`; role-only augmentation is recorded independently as
`entity_role_decision_provenance`. The migration must prove all stable queue
fields are unchanged before rebinding a prior decision to the role-aware queue.

Route-context gate equivalence is narrower still. The deterministic promotion
accepts only a complete, hash-bound `controlled_operation_contract` review and
may change only graph-review eligibility. It must reject entity, entity-role,
period, scope and value repairs. In particular, `parent` does not imply
`separate`, “so với năm” does not alone imply growth, and “trong số” does not
identify a company population when its literal axis is years. Every promotion
receipt keeps `may_change_question_plan=false`, `may_change_route=false`,
`may_select_value=false`, `may_execute_formula=false` and
`release_authorized=false`.

An independent campaign audit is a second semantic gate over complete
campaign-only certificates. For V7 it must cover all 12 candidates exactly
once, reopen the raw row and entity-role anchors, verify locked execution by
hash, and keep numeric literals out of the ChatGPT packet. Candidate reviews
are hash-bound and may return `approved_candidate`, `semantic_mismatch`, or
`needs_investigation`. One non-approved candidate forces `needs_revision` and
cannot be hidden by the aggregate certificate count. Q702 currently exercises
this invariant: `Thu nhập khác` does not prove the qualifier `thuần` when the
same source table separately reports `Lợi nhuận khác (31 - 32)`.

The V8 correction path keeps three actions separate. ChatGPT may approve a
hash-bound semantic-row transition under
`semantic_binding_review_gate_equivalence`; it may not see or select the
numeric value and may not mutate bindings. A deterministic materializer then
reopens the exact table, reads the value at the already selected period column,
and emits a V4 binding with correction lineage. The effective variable
approval uses `chatgpt_verified_exact_row_label_correction_v1`, while entity,
scope and entity-role evidence retain their original independent provenance.
The superseded human decision is referenced by hash and never rewritten.

The V9 navigation-promotion path is a separate authority scope:
`navigation_binding_review_gate_equivalence`. A reviewer may approve one exact
row, its exact period/unit header, the source-backed primary-statement role,
issuer/scope, and a document-line entity-role proposition. The receipt must
keep `may_infer_sector=false`, `may_select_value=false`,
`may_execute_formula=false`, and `release_authorized=false`. A deterministic
V5 materializer reopens the value cell only after validation. Initial
ChatGPT-approved issuer/scope, variable and entity-role fields retain
`chatgpt_verified` provenance; they are never relabeled `human_verified`.

The cross-entity operand path uses
`exact_source_operand_review_gate_equivalence`. Model-facing packets contain
only question semantics, exact row/header labels, issuer/scope/year identity,
parent-role source lines, coordinates and hashes. They may not contain raw
numeric cells. An approval requires seven true checks: operand order, entity
identity, entity role, reporting scope, period, unit and variable. A missing
role proposition makes the packet structurally unapprovable. The reviewer may
approve semantic locators but must retain `may_select_value=false` and
`may_execute_formula=false`. A deterministic executor then validates packet
and decision canonical hashes, reopens the cells, checks their hashes, parses
Decimal values and runs the allow-listed subtraction. Private registry and
execution files may contain values; public tokens and receipts may not. All
current outputs are `research_only`, non-promotable and unreleased.

The V10 promotion path is narrower than operand review. It accepts only a
hash-bound packet whose two operands have exact source coordinates, explicit
parent-role source lines, compatible period/scope/unit semantics and a frozen
non-commutative stage order. The materialized operation must be exactly
`subtract(stage_1_net_income, stage_2_net_income)` and its binding AST hash must
match the execution receipt. Review authority is recorded as
`verification_authority=human_equivalent` for the campaign gate, while
`reviewer_type` remains `chatgpt_verified`. This equivalence changes eligibility
weight only: it does not grant value selection, formula execution, release,
promotion, training or submission authority. The V10 audit must reopen both
source rows and both role anchors; a single-row review cannot approve a
cross-entity certificate.

The V11 dual-review path adds an adversarial consensus invariant for Q746.
The proposer must use role `authorized_ai_operand_evidence_proposer`; the
critic must use `authorized_ai_operand_evidence_critic`; their reviewer IDs
must differ. Both decisions must bind the same packet hash, return the same
decision and the same seven semantic checks, and every check must be true.
Disagreement cannot be majority-voted or silently resolved: it emits a review
disagreement and blocks deterministic materialization. For KHG, the accepted
role proof is issuer co-reference plus a source line describing direct
investment in subsidiaries. `reporting_scope=separate` is a separate gate and
is never accepted as proof of `entity.role=parent`.

Reviewer authority is a gate-scoped contract, not a global role. The shared
authority validator accepts native `human_verified` decisions or an explicitly
granted `chatgpt_verified` decision with `verification_authority` set to
`human_equivalent`. The protocol, reviewer role, model family, fail-closed
policy, grant scope and authority receipt must agree. The receipt must preserve
ChatGPT provenance and deny release, training, submission, value-selection and
formula-execution authority. Semantic row/cell review therefore has the same
eligibility weight at its own gate without becoming human-labelled evidence or
training supervision.

The Binding Certificate contains the document SHA-256, table SHA-256, raw-cell
SHA-256, deterministic header-evidence SHA-256, binding schema version and
resolver version. Its `binding_id` hashes the entire unsigned record, so a
value cell, header anchor, semantic field, or resolver change invalidates the
receipt. A valid certificate is only evidence: it cannot itself authorize
training, promotion, serving, or submission.

Period binding requires an explicit raw label and source anchor plus typed
period grain, flow/stock, comparison role and dates. A bare year or date never
authorizes an assumption about flow/stock or a fiscal calendar. Unit resolution
honours only explicit source declarations in this order: cell, row, column,
table, document. Equal-specificity disagreement is a conflict; implicit
cell-level inference never overrides a table/document declaration.

Revision selection is query/formula-specific: `latest_valid`, `point_in_time`,
`originally_reported`, or `restated`. The resolver verifies a single
entity/scope/period/statement fact set, then applies publication date,
effective date, audit authority and declared supersession according to the
selected policy. Remaining ties are `CONFLICT`, never a retrieval or
file-order tiebreaker.

Formula compatibility is declarative rather than globally uniform. Each
operand names allowed period grain, flow/stock, comparative basis, semantic
unit and revision policy; cross-operand equality is enforced only when the
formula declares it. For example, ROA may require a fiscal-year flow with
opening and closing instant stocks, while still requiring same entity, scope
and currency.

## Machine-silver quality gate

`scripts/build_retriever_training_quality_gate.py` writes a hash-bound
`vifinqa_retriever_training_quality_gate_v1` decision artifact. It requires a
source-validated machine-silver JSONL, a hash-verified independent audit, a
schema-v2 structural fingerprint census, and an explicit human-reviewed
calibration metric (`precision` plus its Wilson lower bound). A missing metric
is `BLOCKED`; it is never inferred from an empty or aggregate-only score file.
The calibration scorer emits this metric only from independently
`human_verified` labels; independent-AI source reviews remain visible in
stratum diagnostics but cannot contribute to retriever-training quality.

Major structural fingerprints are defined as census buckets with at least ten
questions. Training needs at least 200 pairs, audit precision >= 0.99, Wilson
lower bound >= 0.97, and coverage of >= 90% of those buckets. The dense trainer
accepts only a `READY`, explicitly `training_eligible` gate; neither the gate
nor its inputs can promote an answer, submission, or source provenance.2
