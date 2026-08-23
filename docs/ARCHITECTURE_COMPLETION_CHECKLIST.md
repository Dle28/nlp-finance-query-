# Release checklist

This is the release decision checklist. Current detailed status is in
[PROJECT_STATUS.md](PROJECT_STATUS.md); stage permissions are in
[TECHNICAL_CONTRACTS.md](TECHNICAL_CONTRACTS.md).

## Always required

- [x] Raw report is immutable and every table has stable identity.
- [x] V2 preserves exact cell provenance; V3 only canonicalizes context.
- [x] Important artifacts are SHA-256-bound.
- [x] Missing or conflicting evidence fails closed.
- [x] The deterministic route → exact-cell → Decimal replay has a canonical,
  reproducible runner.
- [ ] Every executable family has a positive and fail-closed integration canary.
- [ ] Production audit precision threshold is defined and passed.

## Production release requirements

- [ ] Typed plan is executable for every question entering the ledger.
- [ ] Formula evidence is complete for every non-direct question entering the ledger.
- [ ] Independent audit passes every public question.
- [ ] A full production execution ledger has exact-source lineage for every
  public question.
- [ ] Release gate is `ready_for_submission_compiler` and binds the exact audit
  and ledger hashes.

Until every release item is checked, the correct result is `blocked`; no model,
reviewer, or manual confidence override may create a submission.
