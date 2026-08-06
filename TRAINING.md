# Training and Indexing Guide

## 1. What can be trained from the public release

The public `questions.jsonl` contains only `id` and `question`. It does not contain gold document, table, cell, answer, or program labels.

Therefore:

| Component | Public data sufficient? | Current implementation |
|---|---:|---|
| Question-family router | Partially | Weak labels from observed question-ID blocks |
| Lexical BM25/FTS index | Yes | No training required |
| Zero-shot dense index | Yes | Pretrained sentence-transformer embeddings |
| Dense retriever fine-tuning | No | Requires verified `positive_table_uids` |
| Cross-encoder reranker training | No | Requires verified query/table relevance labels |
| Cell binder and operation planner | No | Requires manually reviewed operands and AST labels |

Do not report weak-label router accuracy as official task accuracy.

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

The first runnable system is retrieval-only. It deliberately stops before row/cell binding rather than fabricating an answer.

## 3. Train the question router

```bash
python scripts/train_question_router.py \
  --questions data/ViFinQA/questions/questions.jsonl \
  --model intfloat/multilingual-e5-small \
  --output-dir artifacts/question_router
```

This encodes 1,012 questions once and trains a logistic-regression head. The labels come from observed ID ranges and remain weak supervision.

## 4. Fine-tune the dense retriever

Create verified training rows:

```json
{"question":"...","positive_table_uids":["internal_uid_1"]}
```

Then run:

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

Create labeled pairs:

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

Prefer hard negatives with the same company/year but wrong table, wrong scope, wrong period, or a near-duplicate row label.

## 6. Runtime estimates

These are engineering estimates, not measured benchmarks for the user's exact machine. They assume approximately 1,973 reports and about 146k raw table assets. OCR length, storage speed, sequence truncation, batch size, thermal throttling, and CUDA support can change runtime substantially.

### 6.1 Preprocessing and indexing

| Stage | Laptop CPU, no CUDA | NVIDIA T4 16 GB | RTX 3060/4060 class | A100 40 GB |
|---|---:|---:|---:|---:|
| Build table assets | 15–60 min | 15–60 min | 15–60 min | 15–60 min |
| Build SQLite lexical index | 5–25 min | 5–25 min | 5–25 min | 5–25 min |
| Dense index, multilingual-E5-small | 5–14 h | 45–120 min | 30–90 min | 15–40 min |
| Dense index, BGE-M3 | 24–72 h | 4–10 h | 3–8 h | 1–3 h |

Dense-index construction is inference, not training, but it is usually the longest initial job because every table view must be encoded.

### 6.2 Model training

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

Ten thousand dense-retriever pairs take roughly ten times the optimizer work of one thousand pairs, although data loading and warmup prevent perfectly linear scaling.

## 7. Practical recommendation

On a laptop without NVIDIA CUDA:

1. build the table assets and lexical index locally;
2. train the weak router locally;
3. test the lexical retrieval baseline;
4. build the fast multilingual-E5 dense index overnight if necessary;
5. use a cloud GPU for BGE-M3 indexing, retriever fine-tuning, and reranker training.

Do not begin expensive training before producing at least a small verified set of table and operand labels. Without gold evidence, the system can build a zero-shot retrieval baseline, but it cannot measure or improve table retrieval scientifically.
