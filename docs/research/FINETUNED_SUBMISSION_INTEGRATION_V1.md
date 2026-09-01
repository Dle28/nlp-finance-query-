# Nối fine-tune/research vào primary submission ViFinQA

## Luồng đã nối

`finance-query build-submission` gọi cùng implementation
`build_competition_submission_v1.py`. Từ thư mục fine-tune, nó tải
`reranker_finetuned`, chuyển mỗi bảng V2 thành semantic passage (tên báo cáo,
mục, header, tên dòng), rồi xếp hạng lại shortlist trước khi chọn ô. Các
coordinate packet từ research cũng được hydrate từ V2.

```text
review + research candidates -> fine-tuned CrossEncoder rerank
                              -> deterministic row/header selection
                              -> optional staged model result replay
                              -> CSV + pandas replay -> submission.zip
```

Retriever và generator vẫn được giữ trong manifest để theo dõi. Một staged
model result chỉ được ghi vào submission sau khi mọi citation replay khớp V2
và independent critic khớp giá trị cuối. Checkpoint `fast_dev_run=true` được
phép chạy ablation nhưng `promotion_allowed=false`.

## Chạy local khi đã tải model

```bash
PYTHONPATH=.:src .venv/bin/python -m finance_query.cli build-submission \
  --questions data/ViFinQA/questions/questions.jsonl \
  --bundle artifacts/kaggle_runs/notebook5554bd790d_v10_20260811/vifinqa_review_bundle \
  --replay artifacts/runs/e2e-explicit-ticker-candidate-replay-20260827-agent4-r9/grounded_execution_replay_v2.jsonl \
  --reranker-policy use_finetuned_reranker \
  --finetuned-model-root /path/to/vifinqa_rag_finetune_v1 \
  --model-device cuda \
  --output submissions/vifinqa_finetuned_rerank_v1
```

Research artifacts local được tự phát hiện. Có thể truyền thêm
`--research-candidate PATH` hoặc `--model-answer-candidate PATH`. Report bắt
buộc có `research_fusion`, `model_answer_candidates`,
`fine_tuned_model.reranked_questions` (khi bật model), validation phải replay
đủ 1.012 câu và ZIP chỉ chứa `submission.json` cùng thư mục `data/`.
