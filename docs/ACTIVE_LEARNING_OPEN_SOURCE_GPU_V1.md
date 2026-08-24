# Open-source GPU cycle V1

## Verified execution state

This workflow runs no ChatGPT model. It uses two open-weight competition routes
strictly below 14.7B parameters:

- proposer: `Qwen/Qwen3-8B`, revision
  `b968826d9c46dd6066d109eabc6255188de91218`;
- blind critic: `mistralai/Mistral-Nemo-Instruct-2407`, revision
  `04d8a90549d23fc6bd7f642064003592df51e9b3`.

Every safetensors shard is pinned by SHA-256 in
`configs/open_source_model_policy_v1.json`. The prepared model job contains 64
active-learning packets. Each packet contains the raw claim, cluster features,
a typed numeric-free closure projection and packet-bound source hashes. It does
not contain a proposed answer, raw financial cell value, prior model response,
or an authorizing status.

Current local evidence:

- active cycle: `active_learning_cycle_v5_20260824`, manifest SHA-256
  `7b4354143851e677f6dd83919ea517abd72410a691d7ba879d407eb300384339`;
- model job: `active_learning_model_job_v3_20260824`, manifest SHA-256
  `33a5e1faadbe15f091b4d6fb613f5c9f7c1449f6177c28d525e086d3cd4c2dfe`;
- portable input: `active_learning_kaggle_bundle_v3_20260824`;
- Kaggle execution: **completed successfully** in private notebook version 1,
  pinned to source commit `362b0ed6d838df215deeb92a052993c52aae7347`;
- Qwen validation: 15 `VALID_PROPOSAL`, 49 non-proposals;
- Mistral validation: 0 `VALID_PROPOSAL`, 64 non-proposals;
- reconciliation: 0 agreements, 64 escalations, 0 training-eligible records;
- downloaded receipt archive: `vifinqa_active_learning_open_source_cycle_v1_362b0ed6.zip`,
  59,576 bytes, SHA-256
  `fbb79bdcb8bce931aeb002c009ea28d1a96c4bf12de080c85930fba3423df7eb`;
- training, certification, promotion and release: **blocked**.

The archive was downloaded to the workstation and passed both SHA-256 and ZIP
integrity verification. The private execution remains available at
[Kaggle notebook](https://www.kaggle.com/code/dungle2810/vifinqa-active-learning-open-source-cycle-v1/output),
with input supplied through the private
[Kaggle dataset](https://www.kaggle.com/datasets/dungle2810/vifinqa-active-learning-open-source-cycle-v1-input).

## Why Kaggle is needed

The current workstation has no CUDA device, so Kaggle supplied the GPU runtime.
The private input bundle and pinned notebook keep the external execution
reproducible without adding Kaggle credentials to the repository.

## Local preparation and verification

```bash
.venv/bin/python scripts/build_active_learning_model_job_v1.py \
  --active-cycle-manifest artifacts/research/active_learning_cycle_v5_20260824/active_learning_cycle.manifest.json \
  --model-policy configs/open_source_model_policy_v1.json \
  --output-dir artifacts/research/active_learning_model_job_v3_20260824

.venv/bin/python scripts/verify_active_learning_model_job_v1.py \
  artifacts/research/active_learning_model_job_v3_20260824/active_learning_model_job.manifest.json

.venv/bin/python scripts/package_active_learning_kaggle_job_v1.py \
  --job-manifest artifacts/research/active_learning_model_job_v3_20260824/active_learning_model_job.manifest.json \
  --output-dir artifacts/research/active_learning_kaggle_bundle_v3_20260824
```

## Kaggle execution order

Use `notebooks/vifinqa_active_learning_open_source_cycle_v1.ipynb` with a GPU
and Internet enabled. Attach the portable bundle as a private Kaggle dataset.
The notebook then performs this fixed order:

1. find exactly one `kaggle_job.manifest.json` and verify every attached hash;
2. check out the pinned repository source commit;
3. install the CUDA inference dependencies;
4. execute Qwen proposer requests;
5. validate Qwen envelopes against their immutable prompts;
6. execute Mistral critic requests without exposing Qwen output;
7. validate critic envelopes;
8. reconcile exact policy agreement and emit the escalation queue;
9. archive only non-authorizing receipts for download.

An agreement is `AGREEMENT_MACHINE_PROVISIONAL`, not a label or certificate.
An abstention, invalid response or disagreement is escalated. ChatGPT may later
review a bounded escalation item with human-equivalent review authority, but it
is outside this GPU graph and its receipt can never become competition training
data.

The first clean run produced no agreements: all 64 packets were escalated. This
is a valid fail-closed outcome, not a failed execution. It proves the transport,
model pinning, validation and reconciliation path works; it does not authorize
fine-tuning. A preceding Qwen attempt was stopped after its first item when an
attention-mask warning was detected. The runner was fixed to require and pass
the tokenizer attention mask, pinned in the source commit above, and the clean
run completed without that warning.
