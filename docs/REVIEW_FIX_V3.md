# Review Bundle V3 boundary note

This retained note explains the legacy diagnostic artifact
`data/diagnostics/run_001`.

V3 fixed three retrieval-review projection issues without changing raw source
values:

1. preserve provenance when recovering an adjacent table after context leakage;
2. remove entity/date noise from direct-lookup metric hints;
3. separate the semantic anchor row from the numeric value row.

The fix is navigation/review support only. A recovered candidate is not
evidence; every final operand must still pass exact V2-cell, header, period,
scope and unit validation. Current operation and release behavior is defined by
[TECHNICAL_CONTRACTS.md](TECHNICAL_CONTRACTS.md) and
[PROJECT_STATUS.md](PROJECT_STATUS.md).
