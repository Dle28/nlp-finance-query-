# ViFinQA architecture

## Purpose

The system answers a financial question only if it can prove the chain:

```text
issuer -> report -> scope -> period -> table -> row -> column -> raw value -> unit -> operation
```

The output is therefore either a release-gated grounded replay trace or an
explicit block. Legacy retrieval/binding helpers may expose candidates, but
never an answer.

## Components and boundaries

| Component | Responsibility | Current state |
| --- | --- | --- |
| 1. Corpus | Extract immutable raw table locator/grid; build legacy retrieval projection | Ready for the pinned bundle |
| 2. Question understanding | Entity, period, scope, metric and operator contract | Partial; unknown structures abstain |
| 3. Retrieval | Lexical+dense candidates plus hierarchy-aware soft ranking with metadata constraints | Ready as a candidate generator only |
| 4. V2/V3 normalization | Exact table grid plus canonical header/period/unit context | Ready |
| 5. Route and evidence binding | Constrain candidate operands to exact raw cells, then materialize variable/period/unit/entity identity/entity role/scope/revision bindings | Wired into full-corpus replay; missing source propositions stay blocked |
| 6. Independent audit | Re-check source evidence without answer values or prior verdicts | V12 campaign audit passes 26/26 candidates; production release remains blocked |
| 7. Evaluation/training | Train only from gate-approved labels | Experimental; no automatic promotion |
| 8. Execution/release | Hash-bound numeric tokens, sandboxed Decimal replay, telemetry, full ledger and submission compiler | Replay ready; release blocked |

## Canonical flows

### Only answer-capable path

```text
question -> semantic planner (identity, entity role, reporting scope) -> lexical/dense/hierarchy discovery -> route/period packet --+
raw report -> RawTableAsset -> V2 structure -> V3 derived context -----------+-> exact-cell binding
                                                                                ├-> cell token -> sandboxed Decimal replay --+
                                                                                └-> Evidence Binding -> Binding Certificate
                                                                                     -> Formula Contract + compatibility ----+-> answer certificate
                                                                                                                                -> audit + release gate -> export
```

Only `EXPLICIT_QUERY` or verified `SOURCE_DERIVED` planner fields may remove a
candidate. `MODEL_INFERRED` fields are ranking hints and cannot hard-filter.
Hierarchy scoring is an opt-in experimental soft rank signal: it examines source-derived table
function, section, headers and row labels only inside the already bounded
lexical/dense candidate pool. A hierarchy miss never removes a candidate and a
hierarchy hit never becomes numeric evidence. It remains disabled by default
until a safety-preserving issuer-held-out gate approves a follow-up experiment.
The source plane and assertion plane remain separate: `RawTableAsset` carries
source locators, hashes and extracted raw grid; V2/V3 carry versioned structure
and context assertions. The legacy combined `TableAsset` exists only for
backwards-compatible retrieval bundles and must be split through its
`raw_asset()` and `derived_assertions()` views before evidence work.

`diagnostic Decimal replay` is deliberately not answer-authorized computation:
it creates a research-only execution receipt and cannot make an operand usable,
complete an Answer Certificate, or reach release on its own. An Answer
Certificate needs that receipt *and* bound Evidence Bindings, Binding
Certificates, and formula-specific compatibility to pass; otherwise it emits
`ABSTAIN`.

### Legacy compatibility

```text
question -> legacy retrieval -> heuristic binding -> candidate probe only
```

`finance-query legacy-binding-probe` (and the deprecated `answer-direct`
alias) returns no numeric answer and is never submission/training eligible.
Its score, rank and unit warnings are diagnostics, not proof.

### Retrieval and review bundle

```text
raw reports -> TableAsset -> FTS/FAISS -> retrieval candidates -> Review Bundle V3
```

### Grounded deterministic replay

```text
route completeness + period packets
  -> exact-cell/unit binding V2
  -> literal-free numeric-cell token view + executor-private token registry
  -> resource-bounded Decimal AST sandbox + diagnostic telemetry
  -> authorization replay: Evidence Binding + Formula Compatibility
  -> Answer Certificate or ABSTAIN
  -> hash-bound research-only run receipt
```

Run it with `finance-query run-grounded-e2e`; see
[docs/OPERATIONS.md](docs/OPERATIONS.md). The runner creates a new output
directory and verifies input/output hashes. It never changes the bundle or
promotes any record.

The sandbox does not execute generated Python. It interprets only the declared
formula AST with `Decimal`, bounds AST nodes/depth, operand/result digits and
wall time, and exposes no filesystem, network, import or process primitive.
Runtime telemetry is a diagnostic sidecar and is excluded from answer
authorization. The public numeric-token view contains no numeric literal; the
private registry is consumed only by the executor and is hash-bound to the
exact source coordinate.

### Production release

```text
exact evidence + independent audit + full production execution ledger
  -> ready release gate
  -> submission compiler
```

All three upstream conditions are mandatory. The current release gate is
blocked, so there is no production submission path to execute.

### Fail-closed active-learning control plane

The active learner is a sidecar to, not a replacement for, certification. Its
trainable and inference models must have open weights and stay strictly below
14.7B parameters; the initial policy routes Qwen3-8B and Mistral-Nemo-12B.
ChatGPT is excluded from training, competition inference, distillation and
ensembles. It may only provide bounded external review under truthful
`chatgpt_verified` and human-equivalent authority. The control plane
clusters component proof-policy gaps, freezes an independent probability audit
before information-gain sampling, and emits immutable review packets. ChatGPT
proposal plus independent-critic agreement creates only a non-materializable
`MACHINE_PROVISIONAL` policy. Training eligibility additionally requires a
pinned authorized adjudicator; calibration uses independent correctness labels,
never proposal accept rate. Exact cells, typed semantics, Decimal replay and the
production ledger remain per-question gates. See
[docs/ACTIVE_LEARNING_ARCHITECTURE_V1.md](docs/ACTIVE_LEARNING_ARCHITECTURE_V1.md).

### V13 claim-requirement shadow audit

V13 adds a versioned proof-obligation layer without mutating V12:

```text
claim -> Claim Requirement Set -> proof obligations
      -> Semantic Coverage Certificate -> Formula Definition
      -> Operand Compatibility -> numeric execution -> result
```

`INTERNALLY_COMPLETE` means only that every generated requirement passed.
`CLAIM_COMPLETE` additionally requires an independent requirement universe;
the current deterministic generator does not provide that independent basis.
The hardened V13 shadow run therefore keeps all 1,012 records at both
`INTERNAL_COVERAGE_INCOMPLETE` and `CLAIM_COMPLETENESS_UNESTABLISHED`. A generic
V12 PASS cannot replace a typed proposition receipt; `NOT_CHECKED` dimensions
also block internal completeness. Q211 exposes `metric.tax_treatment` as a
separate unresolved proposition instead of conflating “before tax” with a
general accounting basis. V12 artifacts and release state are unchanged.

### Full-corpus authorization replay

The canonical replay writes `evidence_bindings_v1.jsonl` and
`answer_certificates_v1.jsonl` after V2 exact binding and Decimal replay. This
is an integration and fail-closed verification path, not a semantic backfill.
The resolver can promote a field only from exact source evidence with aligned
artifact hashes. The current V12 replay materializes all 882 planned operand
receipts, preserves 23 original `human_verified` row/cell decisions, and
records ChatGPT's independently authorized semantic decisions under
`chatgpt_verified`. The V12 role lane adds 11 relational parent-role proofs
only after distinct proposer and critic identities select the same source line
and agree on all six semantic checks. It never treats `separate` reporting
scope as evidence of `parent` entity role. The result is 26 campaign-only
complete certificates and 986 `ABSTAIN` certificates, with release,
promotion, training, submission, and answer materialization still false.

### V12 relational entity-role review

```text
11 residual parent-role gaps
  -> numeric-free exact-source candidate queue
  -> ChatGPT proposer (6 semantic checks + exact line)
  -> independent ChatGPT critic (same checks + exact line)
  -> deterministic exact-consensus reconciler
  -> role-only semantic augmentation (`chatgpt_verified`)
  -> locked V11 -> V12 replay
  -> numeric-free 26-candidate campaign audit
```

Identity, legal/group role, and reporting perimeter remain separate fields:
`issuer match != parent-role match != separate-scope match`. The authority
grant gives the AI lane human-equivalent gate weight but does not rename its
provenance, copy answer values into review context, or authorize production
release.

Human and ChatGPT reviewers may issue equivalent campaign verdicts only when
the owner explicitly grants the relevant review scope. Their provenance lanes
remain distinct: AI review cannot become `human_verified`, invent a source
proposition, or override an upstream human decision. Route-context promotion
may clear only the reviewed `controlled_operation_contract` gate; it cannot
edit a question plan, route, scope, operand or value. Forty-nine such receipts
expand graph review from 33 to 82 questions. Generic typed-operation
fingerprints block the three previously false-green graph families before
review. The graph critique accepts only Q746 and Q750 and confirms 80 blockers;
none of the graph-review records may execute a formula. A downstream,
research-only exact-operand lane keeps reviewer and executor authority
separate: the reviewer sees identity, parent-role provenance, scope, period,
unit, row label, coordinates and hashes, while only the deterministic executor
may reopen value cells. Q746 fails closed on missing KHG parent-role evidence;
Q750 passes all seven semantic checks and replays privately. V10 then promotes
only this hash-bound operand set into a controlled two-stage grounded graph.
The deterministic executor reopens both values and applies the ordered
subtraction; the reviewer never sees either numeric literal. Q746 remains
blocked because KHG's parent role is still unproven.

Campaign completeness is not campaign approval. The independent V7 audit
reopens every one of the 12 campaign-only candidates with question text, raw
row text, source/table/value hashes, entity-role anchors and deterministic
replay status. Numeric literals remain outside the ChatGPT review packet. The
audit accepts 11 candidate semantics and rejects Q702: the selected row is
`Thu nhập khác`, while the claim asks for `Thu nhập khác thuần` and the same
table contains `Lợi nhuận khác (40 = 31 - 32)`. Consequently the campaign state
is `campaign_revision_required`; release and materialization remain false.

V8 repairs that conflict without rewriting the human sidecar. A separate
numeric-value-free `chatgpt_verified` correction packet selects only the exact
semantic row and concept `other_profit`. The deterministic V4 binding
materializer then reopens the same hash-bound table, reads the period-aligned
value cell, and records `numeric_value_selected_by_reviewer=false`. Locked
replay proves that only Q702 changes; the independent V8 audit returns 12/12
candidate PASS. This campaign verdict still leaves release, promotion and
answer materialization false.

V9 adds a second, independent overlay for a previously blocked navigation
candidate. Q167's reviewer packet contains the exact non-value row label,
canonical period/unit header, source title, and a hash-bound source line proving
the issuer has subsidiaries. ChatGPT has review-gate equivalence but remains
`chatgpt_verified`; it cannot infer sector, select a numeric value, execute a
formula, or authorize release. The V5 materializer alone reopens the exact
table cell. V8→V9 regression is isolated to Q167, producing 13 campaign-only
certificates and 999 abstentions.

V10 adds a third overlay for exact cross-entity composition. Q750 carries two
independent Evidence Bindings and a hash-bound `subtract(SAB, DBC)` AST. The
locked V9→V10 diff is isolated to Q750 and produces 14 campaign-only
certificates plus 998 abstentions. The independent audit reopens both rows,
both parent-role anchors and the ordered AST. Its authority receipt records
`verification_authority=human_equivalent` while preserving
`reviewer_type=chatgpt_verified`; release and promotion remain false.

## Status and implementation tracking

[docs/PROJECT_STATUS.md](docs/PROJECT_STATUS.md) is the single short status
page. It states what has been implemented, the current quantitative blockers,
and the next work. For detailed stage permissions, read
[docs/TECHNICAL_CONTRACTS.md](docs/TECHNICAL_CONTRACTS.md).

## Invariants

1. Candidate rank, report layout and semantic metadata are not numeric evidence.
2. Every numeric operand needs an exact V2 coordinate and raw-cell provenance.
3. V3 may interpret headers/periods/units but cannot alter raw values.
4. Every operation uses deterministic Decimal logic and an allow-listed formula.
5. Provenance transitions and model promotion require independent gates.
6. The release compiler accepts only a full, hash-bound, approved lineage.
7. Question IDs identify artifacts only; they never influence a semantic plan,
   fingerprint, route or model-training label.
8. `routing_eligible` is a candidate-discovery gate only. An operand is usable
   only when its Evidence Binding has immutable source-cell lineage and every
   field is `PASS`, except an explicitly contract-non-required
   `NOT_APPLICABLE` field.
9. Evidence validity is not deployment approval: evidence may support a
   reviewable answer certificate, but never trains, promotes or serves a model
   without independent release gates.
10. A numeric cell token is de-lexicalization, not semantic grounding. Its
    literal-free public view may enter model context; its executor-private
    registry may not.
11. Sandbox telemetry may diagnose latency or policy failures but may not
    select a cell, authorize an operand or change release eligibility.
12. Passing every self-generated requirement proves internal coverage only;
    claim completeness requires an independent, versioned requirement basis.
13. Diagnostic computation may precede semantic authorization, but answer
    authorization requires Evidence Binding, Formula Definition, Operand
    Compatibility and Numeric Execution to pass.
