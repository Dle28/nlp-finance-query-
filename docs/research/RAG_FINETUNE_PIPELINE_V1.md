# ViFinQA RAG và fine-tune pipeline v1

## Kết luận từ hai lần nộp

| Metric | r1 | r2 | Thay đổi |
| --- | ---: | ---: | ---: |
| Answer Accuracy | 0,0870 | 0,1126 | +0,0256 |
| Execution Accuracy | 0,0870 | 0,1126 | +0,0256 |
| Docs F2-macro | 0,4714 | 0,6545 | +0,1831 |
| Tables F2-macro | 0,1601 | 0,2389 | +0,0788 |
| Docs MRR@5 | 0,7273 | 0,8090 | +0,0817 |
| Tables MRR@5 | 0,2589 | 0,3127 | +0,0538 |

R2 đúng khoảng 114/1.012 câu, tăng khoảng 26 câu so với r1. Việc mở rộng
danh sách candidate đã tăng mạnh recall tài liệu và bảng. Vì vậy giả thuyết
RAG nhiều tầng có ích đã được leaderboard ủng hộ. Tuy nhiên Answer Accuracy
vẫn thấp hơn nhiều so với Docs F2: tìm được báo cáo chưa đồng nghĩa tìm đúng
bảng, đúng dòng, đúng cột và đúng phép tính.

## Pipeline được triển khai

```text
Câu hỏi
  -> tách ticker, năm, scope, đơn vị, toán hạng
  -> parent RAG: mục lớn của báo cáo
  -> hybrid retriever: lexical + dense fine-tuned
  -> multilingual reranker trên top 30-50 bảng
  -> row/header linker: dòng, năm, cột, đơn vị
  -> Qwen Coder LoRA sinh JSON AST, không sinh số
  -> compiler deterministic tạo pandas_query
  -> executor chạy trên CSV nguồn và kiểm tra đơn vị/provenance
  -> submission
```

Không đưa số tài chính vào text dùng để fine-tune retriever. Số chỉ xuất hiện
khi executor đọc ô nguồn. Thiết kế này hạn chế mô hình học thuộc hoặc bịa số.

## Chunk dùng cho RAG

1. **Parent section:** mục lớn, tối đa tám bảng liên tiếp. Tầng này chọn vùng
   báo cáo và xử lý trường hợp ý nghĩa bảng nằm ở tiêu đề/mục cha.
2. **Table semantic chunk:** tên tài liệu, mục, header và row label, không có
   giá trị số. Đây là đơn vị fine-tune retriever.
3. **Row/header packet:** một row label, header năm, đơn vị và các dòng gần
   đó. Reranker dùng packet này để chọn đúng dòng/cột.
4. **Numeric evidence:** CSV trích từ bảng gốc. Chỉ executor được sử dụng.

Giữ lane năm báo cáo kế tiếp cho các câu hỏi dùng cột so sánh. Diagnostic cũ
cho thấy đây là khác biệt quan trọng đối với bảng năm `T+1` chứa cột `T`.

## Mô hình được chọn

| Vai trò | Mô hình | Lý do |
| --- | --- | --- |
| Retriever CPU | `intfloat/multilingual-e5-small` | Nhỏ, 384 chiều, đã có trong code hiện tại; fine-tune và chạy CPU được |
| Teacher/ablation | `BAAI/bge-m3` | Multilingual, context dài; dùng benchmark hoặc distillation, không bắt buộc production |
| Reranker | `BAAI/bge-reranker-v2-m3` | Cross-encoder đa ngôn ngữ cho shortlist |
| Sinh AST | `Qwen/Qwen2.5-Coder-7B-Instruct` | Mô hình code mở 7B, phù hợp QLoRA trên Kaggle T4 |

Các model đều có trang model chính thức và được phát hành trước ngày
01/06/2026. Không dùng mô hình đóng.

## Dữ liệu tự giám sát đã tạo

Nguồn là 29.509 bảng V2, SHA-256
`583e18bbeac02b484d666eb45f45170b45b92d127ccc4934c213d16c7db4c749`.
Builder không đọc file 1.012 câu hỏi test.

| Dataset | Train | Validation | Test |
| --- | ---: | ---: | ---: |
| Retriever triplets | 63.381 | 6.946 | 8.149 |
| Reranker pairs | 126.762 | 13.892 | 16.298 |
| Program AST SFT | 76.441 | 9.102 | 9.463 |

Split theo ticker: ticker trong test không xuất hiện ở train hoặc validation.
Retriever dùng hard negative cùng tài liệu trước, sau đó cùng ticker/năm.
Program curriculum hiện gồm lookup, subtract và percentage change. Các công
thức chọn doanh nghiệp theo điều kiện vẫn cần bổ sung sau khi ba tầng cơ bản
đạt metric offline.

## Promotion gate

- Retriever chỉ được dùng nếu Recall@5 và MRR@5 cùng không thấp hơn baseline
  trên ticker giữ lại.
- Reranker phải xếp positive cao hơn hard negative; theo dõi pair accuracy và
  MRR, không chỉ loss.
- Generator phải xuất JSON hợp lệ và AST đúng; không được xuất trực tiếp
  `answer`.
- Cuối cùng phải tạo submission ablation và vượt đồng thời baseline r2:
  Answer Accuracy `0,1126` và Tables F2 `0,2389`.

## Artifact đã sẵn sàng

- Config: `configs/training/vifinqa_rag_finetune_v1.json`
- Curriculum builder: `scripts/training/build_vifinqa_self_supervised_v1.py`
- Full curriculum: `artifacts/training/vifinqa_self_supervised_curriculum_v1_20260828_r1/`
- Kaggle notebook: `notebooks/vifinqa_rag_finetune_kaggle_v1.ipynb`
- Kaggle package: `artifacts/kaggle_packages/vifinqa_rag_finetune_v1_20260828_r4/vifinqa_rag_finetune_kaggle_v1.zip`
- Reranker hook: `src/finance_query/e2e/core/learned_rag.py`

## Cách chạy Kaggle

1. Upload package ZIP thành private Kaggle Dataset và attach vào notebook GPU.
2. Mở notebook nằm trong ZIP.
3. Chạy `FAST_DEV_RUN=True` để kiểm tra dependency, CUDA, load dữ liệu và một
   vòng training ngắn.
4. Tạo session mới, đổi `FAST_DEV_RUN=False`, chạy toàn bộ.
5. Tải `/kaggle/working/vifinqa_rag_finetuned_v1.zip` về workspace.
6. Chạy ablation local: baseline retriever, retriever fine-tuned, thêm reranker,
   rồi thêm AST generator. Không thay cả ba tầng trong một lần nộp đầu tiên.

Ước lượng T4: smoke 30-60 phút; full retriever 30-60 phút; reranker 1-2 giờ;
Qwen QLoRA 2-4 giờ. Tổng khoảng 4-7 giờ tùy tốc độ tải model và GPU.

## Trạng thái hiện tại

Builder, curriculum, notebook, package và inference hook đã hoàn thành; unit
test và kiểm tra cú pháp đã pass. Chưa chạy full GPU training, vì workspace
local không có GPU Kaggle trong phiên này. Do đó chưa có model fine-tuned và
chưa có submission r3; không được coi artifact code là kết quả model.

## Tham khảo chính thức

- https://huggingface.co/intfloat/multilingual-e5-small
- https://huggingface.co/BAAI/bge-m3
- https://huggingface.co/BAAI/bge-reranker-v2-m3
- https://huggingface.co/Qwen/Qwen2.5-Coder-7B-Instruct
