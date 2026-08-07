# Kaggle GPU Workflow

This workflow assumes the baseline has already been built locally:

```text
artifacts/table_assets.jsonl
artifacts/lexical_index.sqlite3
artifacts/dense.index
artifacts/dense_uids.jsonl
artifacts/dense.index.meta.json   # optional but recommended
```

The goal is to avoid rebuilding those assets on Kaggle and spend GPU quota only on model work.

## 1. Package the completed local baseline

From the repository root:

```bash
git pull origin main
bash scripts/package_kaggle_baseline.sh YOUR_KAGGLE_USERNAME
```

The script creates:

```text
kaggle_upload/vifinqa-baseline-artifacts/
├── dataset-metadata.json
├── sha256sums.txt
└── vifinqa_baseline_artifacts.tar.gz
```

The archive contains the derived baseline artifacts and, when present, `artifacts/question_router/`.

Verify locally:

```bash
cd kaggle_upload/vifinqa-baseline-artifacts
sha256sum -c sha256sums.txt
```

## 2. Upload as a private Kaggle Dataset

Install the official Kaggle CLI and authenticate it using your Kaggle account credentials/token.

```bash
python -m pip install -U kaggle
kaggle --help
```

Then create the dataset:

```bash
kaggle datasets create -p kaggle_upload/vifinqa-baseline-artifacts
```

Do not add `--public`. Kaggle Dataset creation is private by default.

For later updates:

```bash
kaggle datasets version \
  -p kaggle_upload/vifinqa-baseline-artifacts \
  -m "Update ViFinQA baseline artifacts"
```

## 3. Create a Kaggle Notebook

Recommended settings:

```text
Accelerator: GPU
Internet: On for the initial repository/model download
```

Kaggle provides GPU under a weekly quota. Use GPU only for embedding/model training; corpus parsing and SQLite indexing do not benefit materially from it.

Attach the private dataset `vifinqa-baseline-artifacts` as Notebook Input.

## 4. Clone the repository

Notebook cell:

```bash
!git clone https://github.com/Dle28/nlp-finance-query-.git /kaggle/working/AI_guru
%cd /kaggle/working/AI_guru
!python -m pip install -e . -q
```

Check CUDA:

```python
import torch
print("CUDA:", torch.cuda.is_available())
print("GPU:", torch.cuda.get_device_name(0) if torch.cuda.is_available() else "CPU")
```

The first line must be `True` before running GPU training.

## 5. Restore the local baseline artifacts

```bash
!python kaggle/bootstrap.py
```

The bootstrap script:

1. finds `vifinqa_baseline_artifacts.tar.gz` under `/kaggle/input`;
2. verifies the SHA-256 sidecar when present;
3. extracts the archive into `/kaggle/working/AI_guru`;
4. checks that `table_assets.jsonl` and `dense_uids.jsonl` contain the same number of records.

Verify:

```bash
!ls -lh artifacts/dense.index artifacts/lexical_index.sqlite3
!wc -l artifacts/table_assets.jsonl artifacts/dense_uids.jsonl
!python -m unittest discover -s tests -v
```

The two line counts must match.

## 6. Do not rebuild E5 dense index on Kaggle

The local baseline is already complete. Re-running:

```bash
finance-query build-dense --config configs/baseline.yaml
```

would only repeat work and consume time. The existing E5 index should be used as the zero-shot baseline.

## 7. Train the weak question router

This is the only model that can be trained immediately from the public question file, because its labels are weak observational question-family labels.

The public raw question file is not included in the baseline artifact archive by design. Download the public ViFinQA corpus or upload the question file separately before running this step.

If Internet is enabled:

```bash
!python data/process/extract_data.py
```

Then:

```bash
!python scripts/train_question_router.py \
    --questions data/ViFinQA/questions/questions.jsonl \
    --model intfloat/multilingual-e5-small \
    --output-dir artifacts/question_router \
    --batch-size 64 \
    --device cuda
```

The router is a baseline only. Its reported score measures agreement with weak ID-range labels, not official ViFinQA accuracy.

## 8. Benchmark BGE-M3 before committing GPU hours

Do not immediately build a full BGE-M3 index. Measure the actual Kaggle GPU first:

```bash
!python scripts/benchmark_runtime.py \
    --assets artifacts/table_assets.jsonl \
    --model BAAI/bge-m3 \
    --batch-size 4 \
    --max-seq-length 512 \
    --train-pairs 1000 \
    --epochs 3 \
    --device cuda
```

Read:

```text
estimated_full_dense_index_hours
estimated_training_hours
```

These are hardware-throughput estimates, not quality estimates.

## 9. Required labels before dense fine-tuning

Do not fine-tune the table retriever from public question IDs or guessed table offsets.

Create verified labels locally using:

```bash
python scripts/annotate_retrieval.py \
  --config configs/baseline.yaml \
  --output data/labels/retriever_verified.jsonl \
  --start-id 1 \
  --limit 50 \
  --top-k 10
```

A retriever training row must contain actual internal table UIDs:

```json
{
  "question": "...",
  "positive_table_uids": ["verified_internal_uid"]
}
```

Upload the reviewed label file as a second private Kaggle Dataset, or place it in the notebook working directory.

## 10. Fine-tune E5 first

E5-small is the first scientific training experiment because it is fast enough for repeated ablations.

```bash
!python scripts/train_dense_retriever.py \
    --train-jsonl /kaggle/input/YOUR_LABEL_DATASET/retriever_train.jsonl \
    --asset-db artifacts/lexical_index.sqlite3 \
    --model intfloat/multilingual-e5-small \
    --epochs 3 \
    --batch-size 32 \
    --device cuda \
    --output-dir artifacts/retriever_e5_finetuned
```

After training, rebuild a new dense index with the fine-tuned checkpoint before evaluating it. Do not overwrite the original baseline index; keep baseline and fine-tuned indexes as separate experiment artifacts.

## 11. Fine-tune BGE-M3 only after E5 ablation

Use BGE-M3 only when verified labels and the E5 experiment justify the additional compute:

```bash
!python scripts/train_dense_retriever.py \
    --train-jsonl /kaggle/input/YOUR_LABEL_DATASET/retriever_train.jsonl \
    --asset-db artifacts/lexical_index.sqlite3 \
    --model BAAI/bge-m3 \
    --epochs 3 \
    --batch-size 4 \
    --max-seq-length 512 \
    --device cuda \
    --output-dir artifacts/retriever_bge_m3
```

Start with 512 tokens and batch size 4. Increase batch size only after checking GPU memory.

## 12. Train the reranker

Reranker training requires positive and hard-negative query/table pairs:

```json
{"question":"...","table_uid":"positive_uid","label":1.0}
{"question":"...","table_uid":"hard_negative_uid","label":0.0}
```

Run:

```bash
!python scripts/train_reranker.py \
    --train-jsonl /kaggle/input/YOUR_LABEL_DATASET/reranker_train.jsonl \
    --asset-db artifacts/lexical_index.sqlite3 \
    --model BAAI/bge-reranker-v2-m3 \
    --epochs 3 \
    --batch-size 4 \
    --max-length 512 \
    --device cuda \
    --output-dir artifacts/reranker_finetuned
```

Hard negatives should preferentially be:

```text
same company/year, wrong table
same metric, wrong period
same year, wrong scope
near-duplicate row label, wrong section
adjacent or related table that lacks the required operand
```

## 13. Evaluate before and after training

Baseline:

```bash
!python scripts/evaluate_retrieval.py \
    --labels /kaggle/input/YOUR_LABEL_DATASET/retriever_verified.jsonl \
    --config configs/kaggle_e5.yaml
```

Track at least:

```text
MRR
Recall@1
Recall@5
Recall@10
Recall@20
Precision@k
F2@k
```

For multi-evidence questions, MRR alone is insufficient; later evaluation must also include evidence-set recall and operand coverage.

## 14. Save outputs before ending the session

Useful outputs should be copied under `/kaggle/working/` and saved as a Notebook version or promoted to a private Kaggle Dataset.

Recommended experiment output layout:

```text
/kaggle/working/AI_guru/experiments/
├── e5_baseline/
├── e5_finetuned/
├── bge_m3/
└── reranker/
```

Do not depend on an interactive Kaggle session as permanent storage.

## Recommended first Kaggle session

Because the local baseline is already complete, the first GPU session should be short:

```text
1. clone repository
2. pip install -e .
3. attach and restore baseline artifact dataset
4. verify CUDA and tests
5. train weak router
6. benchmark BGE-M3 throughput
7. evaluate the existing E5 baseline if verified labels exist
8. save router/benchmark outputs
```

Do not start expensive retriever or reranker training until verified relevance labels exist.
