# Synthetic Finance Curriculum V1

## Mục tiêu

Tạo dữ liệu fine-tune không phụ thuộc `human_verified` để dạy retriever và
planner nhận ra đúng report, năm, scope, bảng, dòng và cột. Mỗi example bắt đầu
từ cell nguồn có thể parse lại được; phép tính thuộc allow-list rồi được
executor tính hai lần. Không có answer do LLM tự tạo.

Artifact này chỉ là supervision cho training. Nó không được nâng provenance,
không là evidence và không được đưa trực tiếp vào submission.

## Đầu vào và điều kiện an toàn

Builder chỉ đọc `tables.jsonl`; không đọc `review_items.jsonl` hay câu hỏi
benchmark. Tuy nhiên phải xác nhận report corpus được luật đánh giá cho phép
dùng làm training trước khi chạy. Vì vậy CLI mặc định dừng, và chỉ chạy khi có:

```bash
--source-data-role permitted_report_corpus
```

Không dùng source report/test questions nếu điều đó vi phạm quy định của cuộc
thi. Nếu không có bộ train report riêng, chỉ build code và dùng FinQA/public
financial reports cho curriculum đầu tiên.

## Tạo curriculum

```bash
rtk .venv/bin/python scripts/build_synthetic_finance_curriculum_v1.py \
  --tables artifacts/kaggle_runs/notebook5554bd790d_v10_20260811/vifinqa_review_bundle/tables.jsonl \
  --output-dir artifacts/research/synthetic_finance_curriculum_v1_run_001 \
  --source-data-role permitted_report_corpus \
  --max-examples 10000 \
  --hard-negatives-per-example 6 \
  --min-hard-negatives 2
```

Output bất biến gồm:

```text
synthetic_finance_curriculum_v1.jsonl
synthetic_finance_curriculum_v1.manifest.json
```

Mỗi dòng giữ `source_bindings`, `operation_ast`, `answer_decimal`,
`hard_negative_table_uids`, `split` và SHA-256 input tables. Template V1 gồm
`direct_lookup`, `same_row_difference`, `same_row_ratio`. Hard negative ưu tiên
sai scope, sai năm, cùng document, cùng metric ở context khác, sai entity, rồi
background candidate.

Split được xác định ổn định theo ticker, nên cùng một issuer không xuất hiện ở
cả train và validation/test.

## Fine-tune retriever trên Kaggle

Sau khi upload hai artifact output và bundle tables vào Kaggle Input:

```bash
rtk .venv/bin/python scripts/train_synthetic_retriever_v1.py \
  --curriculum /kaggle/input/<curriculum>/synthetic_finance_curriculum_v1.jsonl \
  --manifest /kaggle/input/<curriculum>/synthetic_finance_curriculum_v1.manifest.json \
  --bundle-tables /kaggle/input/<bundle>/tables.jsonl \
  --output-dir /kaggle/working/bge_m3_synthetic_v1 \
  --split train --model BAAI/bge-m3 --epochs 3 --batch-size 8 \
  --device cuda:0 --gpu-id 0 --gradient-checkpointing
```

Notebook đã đóng gói cùng các preflight gate là
`notebooks/vifinqa_bge_m3_synthetic_retriever_v1.ipynb`. Nó yêu cầu hai Kaggle
Input riêng: `dungle2810/vifinqa-synthetic-finance-curriculum-v1` và
`dungle2810/vifinqa-baseline-artifacts`, cùng Kaggle Secret `GITHUB_TOKEN` hoặc
`GIT_TOKEN` để clone đúng revision source. Notebook không đọc benchmark
questions và không tạo submission.

Trainer kiểm JSONL SHA, tables SHA, provenance, table IDs, hard negatives và
replay lại từng phép tính trước khi tải model. Mỗi training item có dạng
`(question, positive_table, explicit_hard_negative)` cộng với in-batch
negatives. File `training_metadata.json` giữ hash đầu vào của model artifact.

Chỉ đánh giá trên `validation`/`test` issuer-held-out. Không promote retriever
nếu Recall@K cải thiện nhưng wrong-scope hoặc wrong-year tăng.
