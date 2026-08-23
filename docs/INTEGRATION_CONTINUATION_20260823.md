# Grounded pipeline continuation — 2026-08-23

## Implemented

This iteration continued from the secure V2 baseline without changing or overwriting it.

1. Route semantics no longer treats a planner family as sufficient proof of a composed operation. Literal single-company rows such as “Tổng cộng tài sản” remain direct lookups, while explicit comparison phrases require a typed difference graph.
2. A typed whole-question graph validator now enforces an operation allow-list, arity, topological order, exact route-stage coverage, final-node identity, source-route hash, and required-operation coverage.
3. A blank operation-graph review queue was generated. It does not invent operand order or authorize execution.
4. All 47 V2 binding conflicts were classified in a hash-bound workbench.
5. One exact document-title unit anchor was integrated through a separate V3 binding protocol and replayed through the existing token, sandbox, Evidence Binding, and Answer Certificate gates.

## Operation graph queue

The refined route overlay contains:

- 597 `composed_execution_required`
- 62 `route_complete`
- 353 `route_incomplete`

The graph review queue contains 597 items:

- 564 are blocked by missing context before a graph can become executable.
- 33 have route stages and are ready for a human-authored/reviewed operation graph.
- Human graph decisions: 0.

Validated graphs remain `validated_not_authorized`; no graph is materialized or executed from this queue.

## Binding conflict workbench

The 47 original conflicts are now separated into:

- 37 requiring upstream navigation candidates.
- 9 requiring exact period-header review.
- 1 requiring an exact unit anchor.

For Q730, the evidence-context source title contains one literal declaration, `Đơn vị: VND`. The V3 binding binds the complete source title, context row, document source, table, and unit scale hashes. This changes only that operand from `unit_missing` to `binding_ready`.

## V3 integration result

The locked V3 replay reports:

- 15 binding-ready operands and numeric tokens, previously 14.
- 15 sandbox executions, previously 14.
- 46 `binding_conflict`, previously 47.
- 951 `route_incomplete`, unchanged.
- 5/5 declared downstream artifacts byte-identical on replay.
- 1,012 `ABSTAIN` Answer Certificates.
- Human semantic approvals: 0.

Q730 is numerically replayable but not answer-authorized: variable, entity, and period evidence remain unresolved. The integration therefore improves exact numeric coverage without bypassing semantic authorization.

## V4 exact source-title period recovery

The next bounded iteration added a separate, immutable period packet overlay. It accepts a generic current-period header only when all of the following hold: one navigation row, one reliable numeric current column, one exact report date in the source title, source-aligned V2/V3 hashes, and literal question/source issuer corroboration. It recovered 16 operands across 14 questions. Q176 remains blocked because the question names Bluemarq Group while the selected source names Đất Xanh and the inferred `DXG` ticker is not literal in the question.

Eight recovered direct questions became fully numeric-binding-ready. The six other recovered questions are composed routes and therefore remain route-incomplete. V4 also recognizes the exact Vietnamese phrase “Năm tài chính kết thúc vào ngày” and one-year comparative columns whose literal header year is exactly one year before the unique report-title date.

The locked V4 replay reports:

- 23 binding-ready operands, numeric tokens, and sandbox executions.
- 23/23 source-integrity, unit, and period fields `PASS`.
- 39 `binding_conflict` and 950 `route_incomplete` executions.
- 5/5 declared downstream artifacts byte-identical on replay.
- 1,012 `ABSTAIN` Answer Certificates and zero human semantic approvals.

## V5 human-verified semantic authorization

The V4 handoff was completed through the review UI and integrated without
changing the immutable queue. The decision sidecar contains 23/23 `approve`
records, all with `human_verified` provenance, checked source coordinates, and
the exact V4 queue SHA-256.

The locked V5 replay reports:

- 23 human semantic approvals and 23 Evidence Bindings with status `BOUND`.
- 23 `ANSWER_CERTIFICATE_COMPLETE_CAMPAIGN_ONLY` certificates and 989
  `ABSTAIN` certificates.
- 23/23 entity, scope, variable, period, unit, and source-integrity fields
  `PASS` for the approved subset.
- 5/5 declared downstream artifacts byte-identical on the locked replay.
- `release_authorized: false`, `promotion_allowed: false`, and
  `may_materialize_answer: false` remain enforced.

Human approval therefore closes the semantic-binding gate for the bounded
23-question subset. It does not bypass the independent campaign audit or the
production release gate.

A hash-bound campaign handoff now materializes the exact 23-question candidate
manifest, source/code hashes, review strata, and a blank whole-campaign human
response. Its status is `awaiting_independent_campaign_review`; it contains no
prepopulated decision and cannot materialize answers or enable promotion.

## Remaining work has been materialized as review artifacts

- The 38 remaining upstream navigation conflicts have a scoped remediation queue: 14 entity, 7 sector, 4 routing-eligibility, and 13 inseparable multi-blocker cases. Automatic materialization eligibility is zero.
- The 33 graph-ready questions have typed provisional graph records. Five single-operator graphs validate structurally but remain explicitly unauthorized; 28 remain blocked by missing operators, arity, or human ordering.
- All 564 missing-context questions have literal-span repair records. Forty-nine have complete machine-provisional operation-contract candidates; 515 still lack at least one literal-safe repair. None are merged into routes.
- The V4 semantic handoff contains 23 exact-cell items; its separate decision
  sidecar is now complete and replayed in V5.

The full integration quality gate now locks the V4 replay and every remediation artifact. The remaining blockers require human or independently source-verified decisions; serving optimization remains deferred.

## V7 exact-document entity-role provenance

The role review no longer stops at source titles. A separate candidate queue
verifies the exact extracted document SHA-256, enumerates every line containing
the parent-role literal, and binds each candidate's line number, raw text hash,
document hash and context. Twenty-one unresolved role claims produced 59
candidates across 14 documents; seven documents had none.

Under the campaign owner's explicit authorization, ChatGPT approved 10 direct
issuer-role propositions and rejected 11 candidate sets. Rejections include
generic accounting definitions and literals referring to a different parent,
so keyword presence alone never creates PASS. The augmentation preserves the
23 original `human_verified` row/cell decisions and records the ChatGPT role
decision separately.

The locked V7 replay reports:

- 11 `ENTITY ROLE = PASS`, plus Q256 `NOT_APPLICABLE`.
- 12 `ANSWER_CERTIFICATE_COMPLETE_CAMPAIGN_ONLY` and 1,000 `ABSTAIN`.
- 12 `BOUND` and 870 `BLOCKED` Evidence Bindings.
- 5/5 bindings, execution, evidence, certificate and readiness artifacts
  byte-identical to the candidate run.
- Release, answer materialization and promotion remain false.

## ChatGPT operation-graph critique

All 33 graph-ready candidates now have a hash-bound ChatGPT review decision.
The review checks operator semantics, population completeness, non-commutative
ordering and final output type against the literal question. Of five candidates
that passed the structural graph validator, only Q746 and Q750 pass semantic
review. Q819, Q973 and Q982 are rejected as semantic false greens; the other 28
retain confirmed blockers. This artifact authorizes only the graph-review
field. It explicitly keeps `eligible_for_materialization=false` and
`may_execute_formula=false`.

The review UI exposes `/campaign` for entity roles and `/graphs` for operation
graphs. Both lanes support ChatGPT-versus-human interaction while exporting
research-only decisions with separate provenance.

## ChatGPT route-context literal critique

The 564-item missing-context queue now has a complete, hash-bound ChatGPT
review sidecar. Of 49 machine-provisional operation-contract sets, 46 pass all
literal labels and three pass only after removing one regex collision each:
Q613 confuses an absolute comparison with growth, while Q842 and Q986 confuse
a year set with a company population. The 515 incomplete-literal records stay
blocked. The `/contracts` UI displays each literal span and supports a separate
human interaction export. `may_change_question_plan`, `may_change_route`, and
`may_execute_formula` remain false.

## ChatGPT exact-row navigation critique

The 38 navigation-remediation items now have frozen, numeric-literal-free
evidence packets and a separate hash-bound ChatGPT decision sidecar. The review
compares the literal question qualifiers with raw row text after checking
issuer identity, reporting scope, year and table type. Only Q167 has a row that
passes all four gates and literally states `Trong đó: Chi phí lãi vay`; this is
a semantic navigation candidate only. Seven candidate sets are rejected because
their broad row concepts do not prove question qualifiers, and 30 remain
upstream-blocked. The `/navigation` UI can load all 38 decisions, switch to an
independent human review and export the interaction without exposing or
selecting a numeric value. No decision may mutate the question plan, route,
formula, answer, promotion or release state.

## Independent V7 whole-campaign audit

A fresh V7 campaign handoff now contains exactly the 12 complete
post-remediation certificates and no entity-role issue briefs. ChatGPT was
authorized as an independent campaign reviewer with separate
`chatgpt_verified` provenance. It reopened all 12 source rows and role anchors,
verified the locked replay hashes, covered both reporting scopes and all four
period-recognition methods, and received zero numeric literals in its review
packet.

Eleven candidates pass. Q702 returns `NET_OTHER_INCOME_NOT_PROVEN`: the claim
asks for `Thu nhập khác thuần`, but the selected row is `Thu nhập khác` before
`Chi phí khác`; the same table contains `Lợi nhuận khác (40 = 31 - 32)`. The
overall decision is `campaign_revision_required`, with release, answer
materialization and promotion still false. `/campaign` remains the V5 role-gap
diagnostic; the new `/audit` route is the post-remediation V7 campaign audit.

## V8 semantic-row correction and re-audit

Q702 now has a numeric-value-free correction packet with separate
`chatgpt_verified` provenance. It preserves the original human decision by
hash, selects only the exact reported row `Lợi nhuận khác (40 = 31 - 32)` and
the controlled concept `other_profit`, and explicitly denies value selection,
binding mutation, formula execution, release and promotion authority. The
deterministic V4 materializer reopens the table and reads the same-period cell.

The locked V8 run matches 5/5 reference artifacts. Regression comparison with
V7 changes only Q702 across bindings, execution, Evidence Bindings and Answer
Certificates; 1,011 question records remain unchanged. The independent V8
campaign audit reopens all 12 candidates, records 12 PASS and zero blockers,
and still reports zero numeric-value exposure. Its status is
`campaign_approved_not_released`.

## V9 Q167 navigation binding promotion

The campaign owner's explicit instruction grants ChatGPT review-gate
equivalence while preserving the truthful `chatgpt_verified` provenance lane.
Q167 is the only navigation item promoted under this grant. The receipt binds
the exact MSR separate income-statement row `Trong đó: Chi phí lãi vay`, the
canonical 2025 `Nghìn VND` header, and the source-document line defining the
Company together with its subsidiaries as the Group. Sector remains unknown
and is not inferred.

The reviewer never receives the selected numeric value and has
`may_select_value=false`, `may_infer_sector=false`,
`may_execute_formula=false`, and `release_authorized=false`. The deterministic
V5 materializer reopens the exact table only after receipt validation. The
locked V9 run matches 5/5 reference artifacts and changes only Q167 relative
to V8 across bindings, execution, Evidence Bindings and Answer Certificates.
It produces 13 campaign-only complete certificates and 999 ABSTAIN. The
independent V9 campaign audit records 13/13 PASS, zero blockers and zero
numeric-value exposure; release and promotion remain false.

## V10 Q750 controlled cross-entity composition

The owner's explicit instruction now grants ChatGPT human-equivalent weight at
the campaign review gate. The receipt keeps the truthful
`reviewer_type=chatgpt_verified` provenance and records
`verification_authority=human_equivalent`; it does not grant release authority.

Q750 is promoted from the numeric-free exact-operand lane into grounded V10
only after validating both immutable source rows, both parent-role source
lines, separate scope, 2024 periods, VND units, operand order and the frozen
`subtract(SAB, DBC)` AST. ChatGPT never receives or selects either numeric
value. The deterministic executor reopens the cells and performs Decimal
normalization and subtraction.

The locked V10 replay matches all five reference artifacts and changes only
Q750 relative to V9. It produces 14 campaign-only complete certificates and
998 abstentions. Q746 remains `ABSTAIN` because `KHG entity.role=parent` is
unproven and no execution graph is materialized. The independent V10 campaign
audit reopens all 14 candidates, including both Q750 sources, and records 14/14
PASS with zero numeric-value exposure. Release, promotion, training and
submission remain false.

## Gate-scoped ChatGPT human-equivalent authority

The owner's grant is now represented by
`configs/reviewer_authority_policy_v1.json` and enforced by the shared
`finance_query.review_authority` validator. Semantic binding decisions may use
the dedicated `vifinqa_semantic_binding_chatgpt_decision_v1` protocol only when
they preserve `reviewer_type=chatgpt_verified`, carry an explicit
`semantic_binding_review_gate_equivalence` grant and include a complete
human-equivalent authority receipt. The legacy loader name remains as a
compatibility entry point but now applies the same authority-aware validation.

The semantic UI defaults to the authorized ChatGPT lane and can still switch
to a named human reviewer. Its generated bundle removes value-cell text,
numeric candidates, raw Decimal candidates and raw source rows. The visible
brief separates entity identity, entity role, variable, reporting scope and
coordinates. It explicitly directs entity-role assertions such as `parent` to
the separate V10 audit instead of treating `separate` as role evidence.

This parity is deliberately local to review eligibility. The receipt fixes
release, training, submission, numeric-value selection and formula-execution
authority to false; no ChatGPT decision may be relabeled `human_verified`.

## V11 Q746 dual ChatGPT review and campaign audit

Q746 now uses a numeric-literal-free exact-operand packet for DXS and KHG.
KHG parent role is proven from immutable source text by chaining issuer
co-reference to the statement that the Company directly invests in two
subsidiaries. Separate reporting scope remains an independent field and is not
used as a parent-role shortcut.

Two distinct ChatGPT identities interact as evidence proposer and adversarial
critic. Both independently return the same seven true semantic checks; a
deterministic reconciler accepts only exact consensus. The resulting provenance
is `chatgpt_verified` with `verification_authority=human_equivalent`, while
value selection, formula execution, release, training and submission remain
false. The locked V11 replay changes only Q746 relative to V10, preserves Q750,
matches all five reproducibility artifacts, and yields 15 campaign-only
certificates plus 997 abstentions. The independent V11 campaign audit records
15/15 PASS, zero blockers and zero numeric-value exposure.

The UI now opens Q746 by default on `/audit`; `/operands` exposes the proposer,
critic and reconciler receipts alongside the seven reviewer prompts.
