# Artifact cleanup — 2026-08-29

This cleanup keeps the active ViFinQA lineage in `artifacts/` and moves
redundant, stale, or superseded local copies to an external, timestamped
archive. The archive is outside the repository workspace and is recoverable;
it is not an input to the canonical pipeline.

Archive location for this cleanup:
`/home/dungle/.local/share/ai_guru_artifact_archive_20260829/`

After the move, the workspace artifact tree is approximately 5.0 GB; the
recoverable external archive is approximately 18.1 GB. Emptying the archive
later is a separate destructive action and has not been performed.

## Keep set

The following artifact roles remain in the workspace:

- `artifacts/main_inputs/`
- `artifacts/research/` current candidate packets and route/semantic queues
- `artifacts/kaggle_runs/notebook5554bd790d_v10_20260811/vifinqa_review_bundle/`
- `artifacts/kaggle_runs/vifinqa_full_dense_index_gpu_v1_20260826/`
- `artifacts/kaggle_runs/chocomica_vifinqa_primary_rag_finetuned_20260829_v3/`
- `artifacts/runs/e2e-explicit-ticker-candidate-replay-20260827-agent4-r9/`
- `artifacts/runs/e2e-explicit-ticker-candidate-replay-20260827-r8/` as the
  historical receipt referenced by the integration audit
- `artifacts/runs/vifinqa_primary_canonical_migration_20260829_r1/`
- `artifacts/runs/vifinqa_blocked_feedback_gpu_v1_20260829_v3/` for the
  completed invalid-contract audit
- `artifacts/runs/vifinqa_blocked_feedback_gpu_v1_20260829_v5/` because the
  corresponding remote feedback kernel is still running
- `artifacts/kaggle_upload/chocomica_vifinqa_primary_runtime_v1_20260829/`
- `artifacts/kaggle_upload/chocomica_vifinqa_reranker_fp16_v1_20260829/`
- `artifacts/kaggle_upload/vifinqa_full_dense_index_v1/`
- `artifacts/kaggle_upload/vifinqa_blocked_feedback_gpu_v1_20260829/`
- `artifacts/kaggle_upload/vifinqa_blocked_feedback_input_v5_20260829/` for
  the active remote run
- `artifacts/kaggle_upload/vifinqa_blocked_feedback_input_v7_20260829/` as the
  latest compact prompt-contract input
- `artifacts/kaggle_upload/vifinqa_rag_finetune_gpu_v1_v18/` as the latest
  full-finetune staging package; its old remote status is not treated as a
  current completion claim
- `artifacts/kaggle_packages/vifinqa_rag_finetune_v1_20260828_r4/`
- `artifacts/training/vifinqa_self_supervised_curriculum_v1_20260828_r1/`
- `artifacts/review_calibrator.joblib` and its metadata

The small `pilot_candidate_reranker` files are also archived: the current
builder default is `review_calibrator.joblib`, and no code or configuration
consumer points to the pilot files.

The root `dense.index`, `dense_uids.jsonl`, `table_assets.jsonl`, and
`lexical_index.sqlite3` are not in the keep set because byte-identical copies
are already retained under the canonical pulled review bundle or dense-index
artifact, and no current code default references the root copies.

## Moved to external archive

- root duplicate indexes/assets listed above;
- `artifacts/kaggle_runs/notebook5554bd790d_v10_20260811/AI_guru/artifacts/`;
- incomplete/empty or superseded primary and fine-tune run directories;
- the old full-precision reranker upload, since the active primary kernel uses
  the FP16 reranker dataset;
- blocked-feedback input versions v1–v4 and v6, superseded by active v5 and
  corrected v7;
- unrun fine-tune staging versions v12–v17 and their upload ZIPs;
- standalone submission runtimes superseded by the current v18/primary
  runtime packages;
- Kaggle fine-tune package versions r1–r3, superseded by r4;
- historical E2E experiment runs not referenced by the current default
  lineage;
- the curriculum smoke artifact and historical Kaggle control artifacts.
- the old pulled `AI_guru/` wrapper outside the retained review bundle and the
  unused pilot candidate-reranker files.

The archive manifest records the exact source path, byte size, and SHA-256 for
each moved file before the move. No remote Kaggle dataset or kernel is deleted
by this local cleanup.
