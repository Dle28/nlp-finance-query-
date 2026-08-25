# Architecture

The repository has one answer-capable architecture. Its operating contract is
maintained in [docs/PIPELINE.md](docs/PIPELINE.md).

```text
immutable source
  → typed question compiler
  → candidate discovery
  → exact-cell binding
  → Decimal executor
  → semantic authorization
  → certificate / ABSTAIN
  → independent audit + full ledger
  → release gate → submission compiler
```

`src/finance_query/e2e/` owns this flow. It is deterministic, hash-bound and
does not import any model client.

`src/finance_query/research/` contains two isolated sidecars:

- `proof_policy/`: V13 proof obligations, coverage queues and active-learning
  experiments; it produces candidate policies only.
- `llm/`: open-source model diagnostics only; its outputs are non-promotable.

The sidecars may propose or prioritize work, but every affected question still
must travel through its own exact source binding, semantic authorization and
Decimal replay. They cannot create an answer, release, training record or
submission candidate.

## Invariants

1. Raw source remains the only source of numeric truth.
2. Retrieval rank and model output are candidates, never evidence.
3. Decimal executes only a declared, allow-listed AST.
4. Missing, conflicting or unproven semantics yield `ABSTAIN`.
5. Release requires independently audited, hash-bound lineage for the full
   population; no component score can waive a per-question proof.
