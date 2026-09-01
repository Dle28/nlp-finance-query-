# Agent 4 — test report V1

Thời điểm chạy: `2026-08-27` (ICT), trong worktree hiện tại. Các lệnh đều dùng Python local/CPU và không gọi Kaggle hay dense retrieval.

## Kết quả

| Nhóm | Lệnh/đối tượng | Kết quả |
| --- | --- | --- |
| Candidate validators | Agent 2 `validate_parent_child_duplicate_research_v1.py` | `PASS`; 21 candidate rows, 0 failure |
| Candidate validators | Agent 3 `validate_multicol_semantic_diagnostic_v1.py` | `PASS`; Q156/Q263 candidate, Q242 quarantine; answer/submission false |
| Candidate validator | Agent 1 `validate_source_period_recheck_agent1_v1.py` | `BLOCKED`; manifest tham chiếu historical r8 receipt thiếu/stale hash. Không dùng trạng thái ready của Agent 1 để authorize. |
| Targeted pytest | 3 research test files + 11 E2E test files | `96 passed in 0.88s` |
| Script syntax | `py_compile` cho `build_agent4_integration_v1.py` và `build_agent4_empty_semantic_queue_v1.py` | PASS |
| Dependency lock | `uv lock --check` | PASS; `Resolved 83 packages in 2ms` |
| Diff hygiene | `git diff --check` | PASS |
| New E2E | `finance-query run-e2e`, config r9, output `artifacts/runs/e2e-explicit-ticker-candidate-replay-20260827-agent4-r9/` | exit 0; `complete_research_only` |
| Invariant audit | receipt/auth/union/queue/target records, including no raw numeric candidate values | `PASS`; all required fail-closed assertions |

## Pytest command

```bash
rtk env PYTHONPATH=.:src .venv/bin/pytest -q \
  tests/research/test_source_period_recheck_agent1.py \
  tests/research/test_parent_child_duplicate_research.py \
  tests/research/test_multicol_semantic_diagnostic.py \
  tests/e2e/test_decimal_execution.py \
  tests/e2e/test_decimal_sandbox.py \
  tests/e2e/test_deterministic_canaries.py \
  tests/e2e/test_deterministic_replay.py \
  tests/e2e/test_exact_cell_bindings.py \
  tests/e2e/test_exact_cell_title_unit_recheck.py \
  tests/e2e/test_grounded_execution_v2_currency_contract.py \
  tests/e2e/test_provenance_selection.py \
  tests/e2e/test_public_contract.py \
  tests/e2e/test_question_compiler.py \
  tests/e2e/test_replay_blocker_queues.py
```

## E2E assertions

Receipt: `artifacts/runs/e2e-explicit-ticker-candidate-replay-20260827-agent4-r9/grounded_e2e_run_v1.json`, SHA-256 `55c38e960ec54867466545fce9c1e18a87c0a4bd7d1459ca3780f78ceec3d4d5`.

- Exact binding manifest: `binding_ready=58`, `binding_blocked=494`, `route_incomplete=460`.
- Execution manifest: `execution_replay_ready=58`, `binding_conflict=5`, `route_incomplete=949`.
- `run_id=ad8bdec7173b17afe99b89460220944f6204ab05e265700bfcad92bd54306fce`.
- `authorization_status=blocked`; 884 evidence bindings, all `BLOCKED`.
- `answer_certificate_status_counts={ABSTAIN: 1012}`.
- `reviewer_inputs_used=[]`, `machine_semantic_binding_count=0`.
- `release_authorized=false`, `submission_compilation_allowed=false`.

## Interpretation of the Agent 1 validator failure

This is a provenance failure, not an authorization success and not a reason to delete or rewrite the artifact. The Agent 1 backup manifest still contains an input descriptor for the historical r8 receipt. The current workspace no longer has the exact historical receipt hash; the newly reconstructed baseline replay has a different receipt hash and is therefore not substituted. Agent 4 independently rechecked the affected Q70/Q185/Q357 paths against current V2/V3 and only retained Q70; Q185 and Q357 remain quarantined.

The generic `validate_period_packet_union_v1.py` was not used as an acceptance test for the Agent 4 union because that output intentionally declares protocol `vifinqa_agent4_candidate_union_v1`, not the older `vifinqa_period_packet_union_v1` protocol. The Agent 4 builder performs its own base-input, V2/V3, candidate, quarantine, and source-contract checks.
