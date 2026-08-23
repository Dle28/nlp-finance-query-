# Kaggle to local review

Kaggle creates an immutable retrieval bundle. Local stages inspect, ground and
audit it. Do not review or edit evidence on Kaggle.

## On Kaggle

Run the export only after its source, index and manifests validate:

```python
%cd /kaggle/working/AI_guru
%run kaggle/export_review_bundle.py --top-k 20 --force
```

Use `--build-missing` only when the corpus/index is genuinely absent. It can
rebuild the expensive corpus, lexical index and FAISS index.

Download these outputs:

```text
/kaggle/working/vifinqa_review_bundle.tar.gz
/kaggle/working/vifinqa_review_bundle.tar.gz.sha256
/kaggle/working/vifinqa_review_handoff.json
```

If artifact counts, manifest hashes, table UIDs or bundle packaging fail, stop:
that output must not enter local review.

## On local

```bash
sha256sum -c ~/Downloads/vifinqa_review_bundle.tar.gz.sha256
mkdir -p ~/ViFinQA_review/run_001
tar -xzf ~/Downloads/vifinqa_review_bundle.tar.gz -C ~/ViFinQA_review/run_001

.venv/bin/python local/run_local_review_stage.py diagnose \
  --bundle-dir ~/ViFinQA_review/run_001
```

The canonical grounding replay is separate from retrieval export:

```bash
.venv/bin/python -m finance_query.cli run-grounded-e2e \
  --config configs/grounded_e2e_v1.yaml \
  --output-dir artifacts/runs/vifinqa-grounded-e2e-replay-v1_YYYYMMDD
```

See [OPERATIONS.md](OPERATIONS.md) for validation and the production release
boundary. A Kaggle or local replay success is never an automatic promotion.
