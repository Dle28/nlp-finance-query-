# Kaggle GPU benchmark

Benchmark này chỉ đo embedding/training throughput và peak VRAM. Nó không
rebuild raw corpus, V2/V3 table, lexical index hoặc dense index.

Trong một cell Kaggle:

```python
from pathlib import Path
import torch

print("CUDA:", torch.cuda.is_available())
if not torch.cuda.is_available():
    raise RuntimeError("Kaggle chưa bật GPU Accelerator")
print("GPU:", torch.cuda.get_device_name(0))
!nvidia-smi
```

Sau khi repo đã có ở `/kaggle/working/AI_guru`:

```python
%cd /kaggle/working/AI_guru
!python -m pip install -q -e .

from pathlib import Path
roots = [Path("/kaggle/input"), Path("/kaggle/working")]
assets = sorted(
    (p for root in roots if root.exists() for p in root.rglob("table_assets.jsonl")),
    key=lambda p: p.stat().st_size,
    reverse=True,
)
if not assets:
    raise FileNotFoundError("Không tìm thấy table_assets.jsonl; không chạy build lại trong cell benchmark")
ASSETS = assets[0]
print("Assets:", ASSETS)
```

Đo encoding/index throughput:

```python
!CUDA_VISIBLE_DEVICES=0 PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True \
  python scripts/benchmark_runtime.py \
  --assets "{ASSETS}" --device cuda:0 --gpu-id 0 \
  --sample-size 256 --batch-size 16 --encode-only
```

Đo thêm training throughput synthetic:

```python
!CUDA_VISIBLE_DEVICES=0 PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True \
  python scripts/benchmark_runtime.py \
  --assets "{ASSETS}" --device cuda:0 --gpu-id 0 \
  --sample-size 256 --batch-size 16 --train-batch-size 2 \
  --train-steps 5 --train-pairs 1000 --epochs 3 \
  --gradient-checkpointing
```

Kết quả cần lưu lại: `device`, `gpu_name`, `tables_per_second`,
`estimated_full_dense_index_hours`, `seconds_per_training_step`,
`estimated_training_hours`, `peak_gpu_memory_allocated_mb` và
`peak_gpu_memory_reserved_mb`.

## Qwen 2.5 14B grounded critic V2

Notebook `notebooks/vifinqa_gpu_benchmark_p2.ipynb` có **section 6** để chạy
Qwen 2.5 14B 4-bit trên các `grounded_critic_packets_v2` đã hash-bound. Đây là
critic trong phạm vi evidence đã đóng kín, không phải executor hay hệ thống trả
lời ViFinQA.

Trước khi chạy, cần đủ cả ba điều kiện sau:

1. Repo Kaggle clone phải chứa phiên bản mới có
   `scripts/run_qwen_grounded_critic_v2.py`,
   `src/finance_query/grounded_critic_qwen_v2.py`, và
   `scripts/build_grounded_critic_results_manifest_v2.py`. Push các file này
   lên nhánh mà notebook clone, hoặc thay source bundle bằng bản mới.
2. Gắn một Kaggle Input có cùng thư mục chứa hai file
   `grounded_critic_packets_v2.jsonl` và
   `grounded_critic_packets_v2.manifest.json`. Không trộn packet với manifest
   của một run khác; script sẽ kiểm SHA-256 trước khi tải model.
3. Bật **GPU Accelerator** (P100 16 GiB hoặc GPU có từ 14 GiB VRAM) và bật
   **Internet** để `transformers` tải `Qwen/Qwen2.5-14B-Instruct`. Nếu dùng
   một model snapshot đã gắn làm Kaggle Input, truyền đường dẫn snapshot đó qua
   `--model` thay vì model ID từ Hugging Face.

Chạy riêng section 6, không cần chạy lại benchmark encoding/training. Kết quả
được ghi tại `/kaggle/working/vifinqa_grounded_critic_v2/`:

- `qwen14_grounded_critic_results_v2.jsonl`
- `qwen14_grounded_critic_runtime_v2.json`
- `qwen14_grounded_critic_results_v2.manifest.json`

Sau khi cell chạy xong, tải cả ba file để audit. Output luôn được đánh dấu
`machine_provisional`; nó không được dùng để sửa OCR, chọn table/cell/value,
thực thi công thức, tạo evidence, train, hay submit cho tới khi có independent
labels và calibration gate độc lập.
