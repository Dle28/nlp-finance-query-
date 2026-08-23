# ViFinQA grounded financial QA

ViFinQA is a fail-closed system for Vietnamese financial-report QA. It returns
or exports a number only after the number is tied to the correct company,
report, scope, year, table, row, column and unit.

```text
RAW -> V1 -> V2 -> V3 -> lexical/dense/hierarchy candidates -> exact cell
    -> numeric cell token -> Evidence Binding -> Binding Certificate
    -> Formula Contract -> sandboxed Decimal replay -> answer/abstain
```

Retrieval, layout and semantic metadata help locate evidence. They are never
numeric evidence by themselves.

## Start here

| Need | Read / run |
| --- | --- |
| Understand the project | [ARCHITECTURE.md](ARCHITECTURE.md) |
| See current readiness and blockers | [docs/PROJECT_STATUS.md](docs/PROJECT_STATUS.md) |
| Run the canonical deterministic path | [docs/OPERATIONS.md](docs/OPERATIONS.md) |
| Review the next preprocessing/fine-tune architecture | [docs/CERTIFIED_CANONICAL_LAYER.md](docs/CERTIFIED_CANONICAL_LAYER.md) |
| Inspect stage permissions | [docs/TECHNICAL_CONTRACTS.md](docs/TECHNICAL_CONTRACTS.md) |
| Inspect V13 claim completeness | [docs/CLAIM_REQUIREMENT_V13.md](docs/CLAIM_REQUIREMENT_V13.md) |
| Validate artifact lineage | [docs/ARTIFACT_REGISTRY.md](docs/ARTIFACT_REGISTRY.md) |
| Run GPU benchmarks | [docs/KAGGLE_GPU_BENCHMARK.md](docs/KAGGLE_GPU_BENCHMARK.md) |

## Commands

```bash
.venv/bin/python -m unittest discover -s tests -q

.venv/bin/python -m finance_query.cli run-grounded-e2e \
  --config configs/grounded_e2e_v1.yaml \
  --output-dir artifacts/runs/vifinqa-grounded-e2e-replay-v1_YYYYMMDD

.venv/bin/python scripts/run_certified_canonical.py \
  --config configs/certified_canonical_v1.yaml \
  --output-dir artifacts/research/certified_canonical_v1_run_XXX
```

The end-to-end replay is deterministic and research-only. It verifies the
route/period → exact-cell → hash-bound numeric-token → sandboxed
Decimal-execution linkage, writes a separate diagnostic telemetry sidecar, then materializes
typed Evidence Bindings and Answer Certificates without changing its inputs.
The V8 campaign replay preserves 23 original `human_verified` row/cell
decisions and adds a separate, hash-bound `chatgpt_verified` entity-role lane.
Eleven explicit parent-role propositions pass, producing 12 campaign-only
complete certificates and 1,000 abstentions. This gate equivalence never
renames AI output as human evidence and does not authorize release, promotion,
training, or submission. Reproducibility requires all five pinned outputs—V2
bindings, Decimal execution, Evidence Bindings, Answer Certificates and
Authorization Readiness—to match their reference hashes.

Q702 is corrected through a second `chatgpt_verified` semantic-row receipt:
the reviewer selects `Lợi nhuận khác (40 = 31 - 32)` for the qualifier
`thuần`, while a deterministic executor alone reopens the numeric cell. The
original human decision remains immutable. The locked V8 replay changes only
Q702 across binding, execution, Evidence Binding and certificate artifacts;
the other 1,011 questions are unchanged.

V9 applies the owner's human-gate-equivalent review grant to Q167 without
changing provenance. A separate `chatgpt_verified` receipt proves the exact
MSR interest-expense row, the 2025 `Nghìn VND` header, the separate income-
statement role, and a document line defining the Company together with its
subsidiaries as the Group. It does not infer sector and does not expose or
select the numeric value. The locked V9 replay changes only Q167, yields 13
campaign-only certificates and 999 abstentions, and matches all five pinned
outputs.

V10 extends the same fail-closed authority model to Q750. Two independent
`chatgpt_verified` operand receipts prove SAB and DBC identity, parent role,
separate reporting scope, 2024 period, unit and exact net-income row. A
controlled `subtract(SAB, DBC)` graph is hash-bound before the deterministic
executor reopens either value. The locked replay changes only Q750 relative to
V9, yields 14 campaign-only certificates and 998 abstentions, and matches all
five pinned outputs. The independent campaign audit reopens both source rows
and records 14/14 PASS with `verification_authority=human_equivalent`; the
truthful provenance remains `chatgpt_verified` and release remains false.

V11 resolves Q746 without conflating reporting scope with entity role. A
numeric-free packet proves DXS and KHG operands; KHG parent role is derived
from issuer co-reference plus the source statement that the Company directly
invests in two subsidiaries. Distinct ChatGPT proposer and critic identities
must agree on all seven semantic checks before a deterministic reconciler can
emit a human-equivalent receipt. The locked V10→V11 diff is isolated to Q746,
produces 15 campaign-only certificates and 997 abstentions, and the independent
campaign audit records 15/15 PASS with zero numeric exposure. Release remains
false.

V12 closes the 11 residual parent-role gaps left by the older direct-literal
review. A new exact-source queue accepts only issuer/subsidiary relational
anchors; it never treats `reporting_scope=separate` as role evidence. Distinct
ChatGPT proposer and critic identities select the same exact line and agree on
all six semantic checks before deterministic reconciliation. The locked
V11→V12 authorization diff is isolated to Q6, Q10, Q27, Q145, Q168, Q181,
Q184, Q249, Q292, Q316 and Q325. V12 produces 26 campaign-only certificates
and 986 abstentions with all five reproducibility checks passing. Its campaign
audit records 26/26 PASS, zero answer-value exposure and zero blockers;
release, promotion, training and submission remain false.

V13 adds a non-mutating claim-requirement shadow audit. It distinguishes
internal completeness from semantic claim completeness, types the 38 temporal
blockers, and separates the composed and routing blocker classes. The 26 V12
certificate candidates remain immutable: 16 are internally complete under the
expanded rules, while 10 expose an unresolved `accounting.basis` obligation.
All 1,012 records remain `CLAIM_COMPLETENESS_UNESTABLISHED`, so release stays
blocked.

The local review UI exposes `/campaign` for V5 entity-role diagnosis, `/audit`
for the V7→V8→V9→V10→V11→V12 whole-campaign audit, `/roles` for the 11
relational parent-role proposer ↔ critic dialogues, `/graphs` for operation-graph
critique, `/contracts` for literal operation-contract review, and `/navigation`
for exact-row navigation critique. `/operands` shows the Q746 proposer ↔ critic
interaction over the seven semantic assertions needed for exact cross-entity
operands. The V12 audit reopens all 26 complete certificate candidates without
exposing answer values: all 26 pass candidate semantics, including the 11
relational roles, corrected Q702, promoted Q167 and composed Q746/Q750. Release and promotion
remain false. Route-context V3 fixes semantic-axis collisions at the rule
source: parent role no longer implies separate scope, an absolute year
comparison no longer implies growth, and a year population no longer implies
multiple companies. Forty-nine reviewed operation contracts are materialized
only into graph-review eligibility. Generic semantic fingerprints now reject
heterogeneous population aggregation, filter-without-count and
ratio-without-ranking sequences before typed review. The graph lane covers 82
questions: two typed semantic passes and 80 confirmed blockers. All remain
non-executable in the grounded pipeline until exact-source operands and
downstream gates pass. A separate research-only operand lane now proves all
four exact row/header/unit coordinates for Q746/Q750 without showing financial
values to ChatGPT. Q746 and Q750 now each have a complete
`chatgpt_verified` operand set, a controlled grounded composition, and a
campaign-only certificate. They remain research-only and
not eligible for release, training, promotion or submission.

The Certified Canonical command implements Phase 0–2 only: immutable-input
inventory, identity/cell-lineage certificates, mutation checks, assertion
contracts, issue DAG and a Phase 3 bake-off packet set. It has no model call,
training path or promotion path.

## Repository map

```text
src/finance_query/     core schemas, retrieval, grounding and execution
scripts/               artifact producers and release validators
configs/               pinned model and end-to-end run configurations
artifacts/             hash-bound outputs; never overwrite a completed run
tests/                 contract, fail-closed and integration regression tests
docs/                  current review and operation documentation
```

## Repository hygiene

- `artifacts/` and `data/labels/` are local, provenance-sensitive outputs. Do
  not bulk-stage them; publish only an explicitly validated, manifest-bound
  release with a deliberate `git add -f <exact-path>`.
- `review_ui/` is an independent nested Git repository for the local review
  application. Its dependencies and build outputs are not vendored into this
  Python repository.
- `uv.lock` is the reproducible Python dependency lock and is intentionally
  tracked even though transient `*.lock` cache files are ignored.
- Before every public commit, inspect `git diff --cached`, run the relevant
  tests, and verify `git diff --check`.

## Non-negotiable rules

1. Raw reports remain the source of truth.
2. Missing, conflicting or ambiguous evidence is `blocked`/`abstain`.
3. `machine_provisional` cannot become training or submission data by score.
4. Only the release gate can permit the submission compiler to start.
5. Routing metadata may nominate a table but may never authorize a numeric
   operand. Only a complete, source-anchored Evidence Binding can do that.
6. A valid binding is evidence only: it cannot authorize training, promotion,
   serving, or submission. Those decisions have separate gates.
