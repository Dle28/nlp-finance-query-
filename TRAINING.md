# Training and Indexing Guide

## 1. What can be trained from the public release

The public `questions.jsonl` contains only `id` and `question`. It does not contain gold document, table, cell, answer, or program labels.

| Component | Public data sufficient? | Current implementation |
|---|---:|---|
| Question-family router | Partially | Weak labels from observed question-ID blocks |
| Lexical FTS index | Yes | No training required |
| Zero-shot dense index | Yes | Pretrained sentence-transformer embeddings |
| Dense retriever fine-tuning | No | Requires verified `positive_table_uids` |
| Cross-encoder reranker training | No | Requires verified query/table relevance labels |
| Cell binder and operation planner | No | Requires reviewed operands and AST labels |

Weak-label router accuracy is not official task accuracy.

## 2. Recommended execution sequence

```bash
source .venv/bin/activate
pip install -e .

python data/process/extract_data.py
finance-query build-assets
finance-query build-lexical
finance-query build-dense --config configs/baseline.yaml

finance-query retrieve \
  --config configs/baseline.yaml \
  --question-id 1 \
  --question "Lãi tiền gửi năm 2018 của công ty mẹ VJC là bao nhiêu triệu đồng?"
```

The runnable baseline is retrieval-only. It stops before row/cell binding rather than fabricating an answer.

## 3. Train and use the question router

```bash
python scripts/train_question_router.py \
  --questions data/ViFinQA/questions/questions.jsonl \
  --model intfloat/multilingual-e5-small \
  --output-dir artifacts/question_router
```

Then set:

```yaml
router_model_dir: artifacts/question_router
```

inside the selected configuration. The runtime will load the trained router while retaining deterministic ticker/year/scope/unit extraction.

## 4. Fine-tune the dense retriever

Verified training rows:

```json
{"question":"...","positive_table_uids":["internal_uid_1"]}
```

Run:

```bash
python scripts/train_dense_retriever.py \
  --train-jsonl data/labels/retriever_train.jsonl \
  --asset-db artifacts/lexical_index.sqlite3 \
  --model BAAI/bge-m3 \
  --epochs 3 \
  --batch-size 8 \
  --output-dir artifacts/retriever_finetuned
```

The script refuses to infer positives from raw offsets or arbitrary external numeric IDs.

## 5. Train the reranker

Labeled pairs:

```json
{"question":"...","table_uid":"internal_uid_1","label":1.0}
{"question":"...","table_uid":"hard_negative_uid","label":0.0}
```

Run:

```bash
python scripts/train_reranker.py \
  --train-jsonl data/labels/reranker_train.jsonl \
  --asset-db artifacts/lexical_index.sqlite3 \
  --model BAAI/bge-reranker-v2-m3 \
  --epochs 3 \
  --batch-size 8 \
  --output-dir artifacts/reranker_finetuned
```

Prefer hard negatives with the same company/year but the wrong table, scope, period, or near-duplicate row label.

## 6. Measure time on the actual machine

After `finance-query build-assets`, run:

```bash
python scripts/benchmark_runtime.py \
  --assets artifacts/table_assets.jsonl \
  --model intfloat/multilingual-e5-small \
  --batch-size 16 \
  --train-pairs 1000 \
  --epochs 3
```

For the research model:

```bash
python scripts/benchmark_runtime.py \
  --assets artifacts/table_assets.jsonl \
  --model BAAI/bge-m3 \
  --batch-size 4 \
  --max-seq-length 512 \
  --train-pairs 1000 \
  --epochs 3
```

The script measures local encoding throughput and several real optimization steps, then estimates:

```text
estimated_full_dense_index_hours
estimated_training_hours
```

Its synthetic pairs estimate runtime only, not retrieval quality.

## 7. General runtime estimates

These are engineering ranges, not measurements for the user's exact hardware. They assume approximately 1,973 reports and about 146k table assets. OCR length, storage speed, sequence length, batch size, thermal throttling, and CUDA support can change runtime substantially.

### Preprocessing and indexing

| Stage | Laptop CPU, no CUDA | NVIDIA T4 16 GB | RTX 3060/4060 class | A100 40 GB |
|---|---:|---:|---:|---:|
| Build table assets | 15–60 min | 15–60 min | 15–60 min | 15–60 min |
| Build SQLite lexical index | 5–25 min | 5–25 min | 5–25 min | 5–25 min |
| Dense index, multilingual-E5-small | 5–14 h | 45–120 min | 30–90 min | 15–40 min |
| Dense index, BGE-M3 | 24–72 h | 4–10 h | 3–8 h | 1–3 h |

Dense-index construction is inference, not training, but is usually the longest first run because every table view must be encoded.

### Model training

| Training job | Assumed labels | Laptop CPU | T4 16 GB | RTX 3090/A10 class | A100 40 GB |
|---|---:|---:|---:|---:|---:|
| Weak router | 1,012 questions | 3–15 min | under 2 min | under 2 min | under 1 min |
| E5-small dense retriever, 3 epochs | 1,000 pairs | 1–4 h | 8–25 min | 5–15 min | 2–8 min |
| BGE-M3 dense retriever, 3 epochs | 1,000 pairs | 8–24 h | 30–90 min | 15–45 min | 5–20 min |
| BGE reranker, 3 epochs | 10,000 pairs | 1–3 days | 1–4 h | 45–120 min | 20–60 min |

Approximate scaling:

```text
runtime ∝ training pairs × epochs × effective sequence length / batch throughput
```

## 8. Practical recommendation

On a laptop without NVIDIA CUDA:

1. build assets and the lexical index locally;
2. train the weak router locally;
3. evaluate lexical retrieval first;
4. build the multilingual-E5 dense index overnight if needed;
5. use a cloud GPU for BGE-M3 indexing, dense fine-tuning, and reranker training.

Do not start expensive training before producing verified table and operand labels. Without gold evidence, the system can build a zero-shot baseline, but it cannot measure or improve table retrieval scientifically.
