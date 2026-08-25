# Open-source LLM diagnostic

Model graph chỉ có Qwen3-8B (8.2B, open-weight). ChatGPT, nếu dùng review
packet, ở ngoài graph và không tạo training data/inference/ensemble. Qwen chỉ
propose rule numeric-free; independent review quyết định có tạo training row.

Kaggle shadow cycle trước đó có **128/128 response bị schema validator từ
chối**, nên không tạo policy hợp lệ hay training record. Đây là lỗi output
contract, không phải evidence về correctness.

Trước bất kỳ GPU batch mới nào, chạy smoke format 8 packet và yêu cầu 8/8:
JSON hoàn chỉnh, enum hợp lệ, source hash thuộc packet. Chỉ sau đó mới chạy
batch lớn hơn.

Lệnh bounded diagnostic:

```bash
.venv/bin/python scripts/research/llm/run_diagnostic_lane.py build \
  --source-model-job-manifest ARTIFACT/active_learning_model_job.manifest.json \
  --control-policy configs/research/llm/llm_lane_control_v1.json \
  --output-dir artifacts/research/llm_diagnostic_YYYYMMDD
```

Đánh giá này không thể mở release, dù diagnostic pass.
