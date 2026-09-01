# Script E2E và submission

Các script dưới đây phục vụ pipeline canonical; chúng không tạo một pipeline
trả lời cạnh tranh thứ hai.

| File/entrypoint | Vai trò |
| --- | --- |
| finance-query run-e2e | Authority path deterministic, hash-bound |
| finance-query build-submission | Proposal/coverage path và package submission |
| run_deterministic_replay.py | Wrapper của run-e2e |
| build_competition_submission_v1.py | Implementation duy nhất của builder |
| build_*_candidate_queue.py | Candidate/review queue, không là proof |
| train_candidate_validity_model_v1.py | Ranking candidate, không cấp answer authority |
| build_full_corpus_lexical_v1.py | Navigation index |
| search_full_corpus_lexical_v1.py | Tìm candidate table |
| validate_full_corpus_retrieval_v1.py | Kiểm shape/hash/navigation contract |

Builder có thể đọc research, dense và model artifacts, nhưng phải hydrate/replay
candidate từ bảng V2. Queue, rank, score và model output không tự thành evidence.
Mọi output run/submission phải ghi vào directory mới.

## Tọa độ bảng cho submission

`tables_structured_v2.jsonl` dùng `local_ordinal` để join nội bộ. Trường
`relevant_tables` của competition lại cần tọa độ dòng bắt đầu của bảng trong
OCR report, có dạng `document_id|<1-based-table-start-line>`. Hai tọa độ này
không được dùng thay thế cho nhau.

Trước khi chạy trên Kaggle, tạo map từ source closure và V2 provenance:

```bash
PYTHONPATH=src .venv/bin/python scripts/e2e/build_source_line_map_v1.py \
  --tables <runtime>/tables_structured_v2.jsonl \
  --output <runtime>/source_line_map.json
```

Leaderboard/runtime phải truyền cả `--source-line-map` và
`--require-source-line-map`. Builder sẽ từ chối chạy nếu map không phủ đủ
internal table UID hoặc nếu bất kỳ tọa độ nào rơi xuống
`local_ordinal + 1`. Fallback chỉ còn được phép với cờ
`--allow-local-ordinal-fallback` cho diagnostic build và không được dùng để
đóng gói submission.

Xem [pipeline contract](../../docs/PIPELINE.md) và
[E2E runbook](../../docs/e2e/README_VI.md).
