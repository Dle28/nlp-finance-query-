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

## Đánh giá issuer-held-out

Không chạy lại notebook fine-tune để đánh giá. Dùng notebook GPU độc lập
`notebooks/vifinqa_bge_m3_issuer_heldout_evaluation_v1.ipynb`, gắn bốn input:

1. `dungle2810/vifinqa-synthetic-finance-curriculum-v1`;
2. `dungle2810/vifinqa-baseline-artifacts`;
3. `dungle2810/vifinqa-synthetic-retriever-source-v1`;
4. kernel output của `dungle2810/vifinqa-bge-m3-synthetic-retriever-v1-run`.

Evaluator chấp nhận duy nhất split `validation` và `test`, hash-check curriculum,
manifest, table corpus và fine-tuned `model.safetensors`, rồi so sánh cùng corpus
giữa `BAAI/bge-m3` base và model fine-tuned. Output gồm `evaluation_manifest.json`
và ranking JSONL theo model/split, với Recall@K, MRR, cùng breakdown Top-1
`wrong_entity`, `wrong_year`, `wrong_scope`, `wrong_document`.

`promotion_status` luôn là `offline_evaluation_complete_not_promoted`. Việc
promotion phải là quyết định tách biệt sau khi kiểm tra Recall@K tăng mà lỗi
year/scope không tăng vượt ngưỡng đã duyệt.

## Chẩn đoán khi candidate không qua held-out

Khi held-out Recall@K suy giảm mạnh, không dùng split `train` để thay thế hay
để hợp thức hóa kết quả. Chạy diagnostic độc lập dưới đây để phân biệt hai
trường hợp: model chỉ nhớ synthetic train distribution, hoặc training làm hỏng
quan hệ query/table ngay cả trên train.

```bash
rtk .venv/bin/python scripts/diagnose_synthetic_retriever_v1.py \
  --curriculum /kaggle/input/vifinqa-synthetic-finance-curriculum-v1/synthetic_finance_curriculum_v1.jsonl \
  --manifest /kaggle/input/vifinqa-synthetic-finance-curriculum-v1/synthetic_finance_curriculum_v1.manifest.json \
  --bundle-tables /kaggle/input/vifinqa-baseline-artifacts/tables.jsonl \
  --model base=BAAI/bge-m3 \
  --model finetuned=/kaggle/input/vifinqa-bge-m3-synthetic-retriever-v1-run/bge_m3_synthetic_v1 \
  --output-dir /kaggle/working/bge_m3_synthetic_v1_diagnostic \
  --diagnostic-allow-train \
  --device cuda:0 --passage-batch-size 16 --query-batch-size 32 --max-seq-length 384
```

Flag `--diagnostic-allow-train` là bắt buộc vì train split chỉ được mở cho chẩn
đoán. Script hash-check/replay toàn bộ curriculum giống evaluator, lập lại
Recall@K/MRR trên train, validation, test và đo cosine `query-positive`,
`query-hard-negative`, cùng margin cho từng row. Nó ghi
`diagnostic_manifest.json` và `<model>_<split>_diagnostic.jsonl`.

`diagnostic_status` luôn là `complete_not_for_promotion_or_submission`.
Không dùng train metrics để select/promotion model; kết quả hợp lệ duy nhất vẫn
là issuer-held-out validation/test từ evaluator độc lập.
