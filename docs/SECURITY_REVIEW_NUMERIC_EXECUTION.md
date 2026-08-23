# Security review: numeric cell tokens and Decimal execution

Date: 2026-08-23

Scope: `numeric_cell_tokens.py`, `execution_sandbox.py`, `grounded_execution_v2.py`, and their E2E wiring.

Disposition: no unresolved critical or high-severity finding; research-only authorization gates remain mandatory.

## Executive summary

The numeric path now separates a literal-free public token view from the executor-private Decimal registry, binds every registry to the exact bindings and manifest hashes, recomputes token identity from source lineage, and executes formula, normalization, and output conversion through a small allow-listed Decimal AST. The interpreter accepts no Python source, imports, filesystem, network, or process primitives.

This is a bounded in-process interpreter, not an operating-system isolation boundary. It is appropriate only while the accepted language remains the current declarative Decimal AST. Any future arbitrary code, dynamic imports, external tools, or untrusted native extension requires a separate process/container boundary with OS-enforced CPU, memory, filesystem, network, and syscall restrictions.

## Findings

### SEC-001 — token replay accepted insufficiently revalidated lineage (High, fixed)

The loader previously relied primarily on the registry file hash. A locally recomputed manifest could have made an altered token structurally loadable. The implementation now validates protocol/schema/token format, binds the registry to the current binding artifacts, verifies document/table/cell coordinates and SHA-256 forms, recomputes the raw-cell hash, recomputes the token ID from the complete lineage, and checks the private Decimal against the current operand. Adversarial tests cover registry/manifest tampering, coordinate drift, raw-cell drift, and stale bindings.

### SEC-002 — unit arithmetic could bypass AST budgets (High, fixed)

Formula evaluation was sandboxed, while source-unit normalization and output-unit conversion still used direct Decimal arithmetic. All three phases now use the same policy-bound allow-list (`multiply`, registry formula operations, then `divide`). The legacy currency helper follows the same path, preventing a secondary bypass.

### SEC-003 — malformed resource policy values (Medium, fixed)

Zero, negative, boolean, or non-integer limits could weaken or break budget behavior. `DecimalSandboxPolicy` now rejects every non-positive/non-integer limit before execution. Tests cover invalid wall-time and Decimal digit limits.

### SEC-004 — cooperative wall-clock timeout (Medium, accepted with constraint)

The timeout is checked during AST traversal and after evaluation. It cannot preempt a single C-level Decimal operation. Existing depth, node, operand, and digit caps make the accepted operation set bounded, and no code/filesystem/network/process primitive is present. This remains acceptable only under the declarative-AST constraint described above.

### SEC-005 — executor-private registry confidentiality (Medium, operational requirement)

The private registry necessarily contains Decimal candidates. It is marked `executor_private: true` and `model_prompt_eligible: false`; the public view omits the Decimal literal. Deployment must enforce filesystem/secret-boundary permissions so prompts and model-facing logs receive only the public view. Artifact labels are policy metadata, not an access-control mechanism by themselves.

### SEC-006 — telemetry is diagnostic only (Low, enforced by contract)

Telemetry contains token IDs, policy/AST hashes, operation counts, depth, and elapsed time. It is non-deterministic because of timing and is not an input to Evidence Binding or Answer Certificates. It remains research-only and non-promotable.

### SEC-007 — document-title unit anchors (Medium, fixed in V3)

The V3 context-unit path accepts a document-level unit only when the structured table and evidence-context sidecar share the same document/table/source hashes, exactly one unit label occurs literally in the source title, the title and complete context row hashes match the binding, and the detected unit scale matches the operand. Evidence Binding receives a standard `document_metadata` anchor rather than trusting the candidate object. Any title, context, lineage, unit, or multiplier drift fails closed.

## Verification coverage

- Token public/private separation and lineage tamper tests: `tests/test_numeric_cell_tokens.py`.
- AST allow-list and resource-budget tests: `tests/test_execution_sandbox.py`.
- Token/sandbox/telemetry E2E wiring tests: `tests/test_grounded_e2e.py`.
- Output equivalence is checked by the immutable grounded replay hashes; timing telemetry is excluded from answer authorization.

## Required deployment controls

1. Expose only the public token view to any model prompt.
2. Keep the private registry and telemetry outside model-readable/log-forwarded locations.
3. Preserve SHA-bound manifests and fail on any stale input or identity mismatch.
4. Do not extend the AST with source evaluation, imports, filesystem, network, process, or reflection primitives.
5. Do not treat a successful numeric replay as authorization; Evidence Binding and Answer Certificate gates remain independent and fail-closed.
