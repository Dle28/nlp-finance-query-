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

1. Gắn private Dataset
   `dungle2810/vifinqa-qwen-grounded-critic-source-v2` phiên bản có đúng hai
   artifact: `ai_guru_grounded_critic_source_v2.bundle` và
   `ai_guru_grounded_critic_source_v2.manifest.json`. Archive dùng suffix
   `.bundle` để Kaggle không tự giải nén trước khi kernel kiểm được hash. Kernel
   phải fail-closed nếu archive/manifest không cùng input, SHA-256 archive sai,
   source identity hoặc bất kỳ source member nào sai, hay source contract cho
   phép reports, labels, evidence, training, submission hoặc promotion.
2. Gắn một Kaggle Input có cùng thư mục chứa hai file
   `grounded_critic_packets_v2.jsonl` và
   `grounded_critic_packets_v2.manifest.json`. Không trộn packet với manifest
   của một run khác; script sẽ kiểm SHA-256 trước khi tải model. Khi clone repo
   cũng có một packet cũ, section 6 luôn ưu tiên Kaggle Input immutable để
   không vô tình chạy cohort cũ.
3. Bật **GPU Accelerator** (P100 16 GiB hoặc GPU có từ 14 GiB VRAM) và bật
   **Internet** để `transformers` tải `Qwen/Qwen2.5-14B-Instruct`. Nếu dùng
   một model snapshot đã gắn làm Kaggle Input, truyền đường dẫn snapshot đó qua
   `--model` thay vì model ID từ Hugging Face.

Chạy riêng section 6, không cần chạy lại benchmark encoding/training. Với
kernel tự động `vifinqa-qwen-grounded-critic-v2-run`, dùng notebook chuyên
dụng `notebooks/vifinqa_qwen_grounded_critic_v2_run.ipynb`: nó chỉ restore
source snapshot, kiểm tra GPU/VRAM và chạy critic, nên không phụ thuộc dataset
baseline của benchmark. Kết quả được ghi tại
`/kaggle/working/vifinqa_grounded_critic_v2/`:

- `qwen14_grounded_critic_results_v2.jsonl`
- `qwen14_grounded_critic_runtime_v2.json`
- `qwen14_grounded_critic_results_v2.manifest.json`

Sau khi cell chạy xong, tải cả ba file để audit. Result manifest phải bind
cả packet/packet-manifest lẫn `inputs.source_bundle` (SHA-256 manifest,
SHA-256 archive và `source_tree_sha256`). Output luôn được đánh dấu
`machine_provisional`; nó không được dùng để sửa OCR, chọn table/cell/value,
thực thi công thức, tạo evidence, train, hay submit cho tới khi có independent
labels và calibration gate độc lập.

Khi kernel có source snapshot lớn, không dùng `kaggle kernels output` làm bằng
chứng rằng đã tải đủ artifact: CLI có thể chỉ trả trang đầu của output listing.
Sau khi trạng thái kernel là `COMPLETE`, tải đúng ba file audit bằng helper có
pagination và không overwrite thư mục đích:

```bash
env KAGGLE_CONFIG_DIR=/path/to/kaggle-config \
  .venv/bin/python scripts/download_kaggle_kernel_critic_artifacts.py \
  --kernel <username>/vifinqa-qwen-grounded-critic-v2-run \
  --output-dir artifacts/research/<run>/vifinqa_grounded_critic_v2
```

Sau đó phải kiểm lại SHA-256 của JSONL với result manifest, đồng thời kiểm
tra manifest đó bind cả packet file và packet manifest đã dùng. Nếu thiếu,
trùng URL, hash sai, hoặc closed-world counter khác không thì dừng audit.

Với cohort có `inputs.source_bundle`, audit phải kiểm cả archive và manifest
source đã tải lại từ private Dataset. Dùng gate tái lập dưới đây; chỉ một output
`audit_passed: true` mới được đưa vào calibration, và output đó vẫn hoàn toàn
`machine_provisional`:

```bash
.venv/bin/python scripts/audit_grounded_critic_gpu_run.py \
  --packets artifacts/research/<run>/grounded_critic_packets_v2.jsonl \
  --packets-manifest artifacts/research/<run>/grounded_critic_packets_v2.manifest.json \
  --results artifacts/research/<run>/kaggle/vifinqa_grounded_critic_v2/qwen14_grounded_critic_results_v2.jsonl \
  --results-manifest artifacts/research/<run>/kaggle/vifinqa_grounded_critic_v2/qwen14_grounded_critic_results_v2.manifest.json \
  --runtime artifacts/research/<run>/kaggle/vifinqa_grounded_critic_v2/qwen14_grounded_critic_runtime_v2.json \
  --source-bundle-manifest /verified/source/ai_guru_grounded_critic_source_v2.manifest.json \
  --source-bundle-archive /verified/source/ai_guru_grounded_critic_source_v2.bundle \
  --output artifacts/research/<run>/kaggle/vifinqa_grounded_critic_v2/grounded_critic_gpu_run_audit_v2.json
```

Gate này từ chối packet/result/runtime hash sai, ID coverage sai, closed-world
counter khác 0, VRAM dưới 14 GiB, runtime/model mismatch, source contract có
quyền promotion, hoặc source bundle khác archive/manifest/tree mà result
manifest đã khai báo.
