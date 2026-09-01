# Dataset research map v1

Pipeline này trả lời hai câu hỏi tách biệt:

1. Dataset chứa loại câu hỏi nào?
2. Baseline khóa hiện tại xử lý từng loại như thế nào?

`dataset_taxonomy_v1.jsonl` chỉ đọc text câu hỏi. Classifier không nhận
Question ID và không đọc route, prediction, certificate hay blocker của mô
hình. ID chỉ được thêm sau đó để theo dõi artifact. `baseline_by_question_v1`
mới join certificate vào taxonomy đã tạo xong.

Các chiều hiện có gồm question/operation family, temporal semantics, entity
cardinality và role, reporting scope, value cardinality, expected evidence
topology, output type, unit, semantic signature và wording template. Actual
cell/row/table/report topology luôn là `UNRESOLVED_WITHOUT_GOLD_BINDING` thay
vì suy đoán từ retrieval của baseline.

## Build

Trước hết replay baseline khóa vào một output directory mới:

```bash
.venv/bin/python -m finance_query.cli run-e2e \
  --config configs/e2e/deterministic_replay_v1_locked.yaml \
  --output-dir artifacts/runs/e2e_dataset_research_baseline_YYYYMMDD
```

Sau đó build map từ public questions và certificate vừa replay:

```bash
.venv/bin/python scripts/research/build_dataset_research_map_v1.py \
  --questions data/ViFinQA/questions/questions.jsonl \
  --entity-aliases data/ViFinQA/code_stock.csv \
  --baseline-certificates artifacts/runs/e2e_dataset_research_baseline_YYYYMMDD/answer_certificates_v1.jsonl \
  --output-dir artifacts/research/dataset_research_map_v1_YYYYMMDD
```

Artifact directory là immutable: builder từ chối overwrite và manifest khóa
SHA-256 của input/output.

## Validate

```bash
.venv/bin/python scripts/research/validate_dataset_research_map_v1.py \
  --artifact-dir artifacts/research/dataset_research_map_v1_YYYYMMDD
```

Public question file không có gold answers. Vì vậy báo cáo không gán `PASS` hay
`FAIL` và không tuyên bố accuracy, false-confidence hoặc calibration. Các
metric đó giữ trạng thái `NOT_MEASURABLE_*` cho tới khi có gold/evaluator độc
lập, hash-bound và không làm rò rỉ development/evaluation split.

Contract cho các vòng model-change tiếp theo nằm tại
`configs/research/generalization_experiment_protocol_v1.json`. Kiểm tra bằng:

```bash
.venv/bin/python scripts/research/validate_generalization_experiment_protocol_v1.py \
  --config configs/research/generalization_experiment_protocol_v1.json
```

Validator bắt buộc discovery/development/untouched/full theo đúng thứ tự,
company/wording/metric/composition holdout, ablation, 19 section của report và
10 overfitting checks. Thiếu evaluator độc lập thì contract dừng trước model
change.

Hypothesis temporal đầu tiên đã được viết trước experiment tại
`configs/research/experiments/temporal_point_semantics_v1.json`. Freeze split:

```bash
.venv/bin/python scripts/research/freeze_generalization_split_v1.py \
  --hypothesis configs/research/experiments/temporal_point_semantics_v1.json \
  --taxonomy artifacts/research/dataset_research_map_v1_20260825_r8/dataset_taxonomy_v1.jsonl \
  --output-dir artifacts/research/temporal_point_semantics_v1_split_20260825_r2

.venv/bin/python scripts/research/validate_generalization_split_v1.py \
  --artifact-dir artifacts/research/temporal_point_semantics_v1_split_20260825_r2
```

Split dùng question-text SHA cùng public seed, không dùng Question ID để chọn.
Artifact vẫn ghi `model_change_allowed=false` và `full_dataset_allowed=false`
cho tới khi evaluator độc lập được khóa.
