# Báo cáo vòng nghiên cứu lexical + dense trên toàn bộ câu hỏi

Ngày chạy: 26/08/2026  
Artifact chính: `artifacts/research/hybrid_retrieval_analysis_v1_20260826_r4`

## Kết luận ngắn

Dense có ích, nhưng không nên thay thế lexical. Hai cách tìm bảng nhìn vấn đề
khác nhau và chỉ chọn cùng bảng ở hạng 1 trong 380/1.232 tuyến, tương đương
30,8%. Cách phù hợp hơn là giữ cả hai, ưu tiên các bảng được hai cách cùng ủng
hộ, rồi dùng nhãn dòng để phá hòa khi điểm hợp nhất bằng nhau.

Vòng này chưa chứng minh bảng nào là đáp án đúng vì cuộc thi không cung cấp
train/dev/gold. Kết quả chỉ giúp thu hẹp và sắp thứ tự bảng cần kiểm tra. Không
có candidate nào được phép tự tạo evidence, answer hoặc submission.

## Dữ liệu đã dùng

- 1.012 câu hỏi, trong đó 642 câu có kế hoạch phân rã đầy đủ.
- 1.232 tuyến cần tìm bảng, mỗi tuyến tương ứng một chỉ tiêu, công ty và năm.
- 146.246 bảng thuộc 1.973 báo cáo và 100 mã cổ phiếu.
- Candidate lexical đã khóa từ vòng trước.
- Dense index 146.246 × 384 đã dựng trên Kaggle GPU và kiểm tra lại cục bộ.

370 câu chưa có kế hoạch đầy đủ vẫn được giữ ở trạng thái chưa xử lý. Dense
không được dùng để đoán công ty, năm hoặc phép tính còn thiếu.

## Các giả thuyết và kết quả

### Giả thuyết 1: Dense sẽ cứu các tuyến lexical không tìm thấy bảng

Chưa thể kiểm tra trên nhóm đủ điều kiện. Lexical đã có lane OR bù recall nên
cả 1.232 tuyến đều có candidate. Điều này không có nghĩa lexical đã tìm đúng;
nó chỉ có nghĩa không có tuyến rỗng.

### Giả thuyết 2: Dense bổ sung góc nhìn cho các tuyến lexical yếu

Có tín hiệu ủng hộ nhưng chưa phải bằng chứng accuracy:

- 557 tuyến lexical chỉ tìm được bằng lane OR bù recall;
- 537/557 tuyến vẫn có ít nhất một bảng trùng với dense trong top 10;
- dense bổ sung tổng cộng 6.894 lượt bảng chưa xuất hiện trong top 10 lexical;
- trung bình mỗi tuyến có khoảng 5,6 bảng dense khác với lexical.

Dense vì vậy hữu ích để mở rộng danh sách kiểm tra, nhất là khi câu hỏi và tên
dòng trong báo cáo không dùng đúng cùng một cách diễn đạt.

### Giả thuyết 3: Đồng thuận lexical–dense giúp ưu tiên review

Có thể dùng để xếp hàng review, chưa thể coi là xác nhận đúng:

- cùng top-1: 380/1.232 tuyến, đạt 30,8%;
- có ít nhất một bảng trùng trong top 10: 1.194/1.232 tuyến;
- không có bảng nào trùng trong top 10: 38 tuyến.

38 tuyến không giao nhau là nhóm cần xem sớm vì hai cách tìm đang hiểu câu hỏi
khác nhau hoặc báo cáo có nhiều bảng gần nghĩa.

### Giả thuyết 4: Dense có thể bù kế hoạch câu hỏi chưa hoàn chỉnh

Không chấp nhận. 370 câu thiếu ticker, năm, chỉ tiêu hoặc cấu trúc phép tính
vẫn phải dừng. Dùng dense để đoán các trường còn thiếu sẽ làm tăng nguy cơ tìm
nhầm công ty hoặc nhầm kỳ báo cáo.

### Giả thuyết 5: Hợp nhất hai cách tìm cải thiện độ khớp tên chỉ tiêu

Có tín hiệu nhẹ theo thước đo nội tại, nhưng thước đo này không phải gold. Thử
nghiệm chỉ so tên chỉ tiêu trong câu hỏi với nhãn dòng, hoàn toàn không nhìn số
tiền:

| Cách chọn top-1 | Điểm trung bình | Trung vị | Số tuyến điểm 0 | Số tuyến từ 0,5 |
|---|---:|---:|---:|---:|
| Lexical | 0,4111 | 0,3846 | 290 | 510 |
| Dense | 0,3964 | 0,3333 | 233 | 466 |
| Hợp nhất ban đầu | 0,4217 | 0,4000 | 263 | 517 |
| Ưu tiên review sau phá hòa | 0,4333 | 0,4000 | 246 | 531 |

Phá hòa bằng nhãn dòng chỉ làm đổi top-1 của 42/1.232 tuyến. Thay đổi nhỏ này
loại bỏ việc phá hòa gần như ngẫu nhiên bằng UID và tăng điểm nội tại. Nó chỉ
được dùng để sắp thứ tự review, không được dùng để tự chọn evidence.

## Ba nhóm review đã tạo

Artifact có đúng một gói review cho mỗi tuyến, tổng cộng 1.232 gói:

| Nhóm | Số tuyến | Cách hiểu |
|---|---:|---|
| `agreement_high_proxy` | 222 | Hai cách cùng chọn top-1 và nhãn dòng khớp khá rõ |
| `standard_review` | 737 | Có giao nhau một phần hoặc cần phân biệt các bảng gần nghĩa |
| `hard_review` | 273 | Không giao nhau hoặc top-1 không khớp nhãn dòng |

Mỗi gói chỉ giữ tối đa ba candidate, gồm UID bảng, tài liệu, scope, thứ hạng
lexical/dense, vị trí nguồn và SHA-256. Raw numeric value không được đưa vào
hàng đợi này.

## Những điều quan sát được từ trường hợp khó

- Một chỉ tiêu có thể xuất hiện trong nhiều bảng của cùng báo cáo. Ví dụ “Tổng
  tài sản” có thể khớp hoàn toàn ở cả hai lane nhưng hai lane chọn hai bản sao
  bảng khác nhau.
- Khi câu hỏi không nói rõ consolidated/separate, hai lane có thể chọn hai loại
  báo cáo khác nhau. Đây là rủi ro thật, không nên giải quyết bằng điểm similarity
  đơn thuần.
- Dense tốt hơn ở một số cách diễn đạt gần nghĩa như “lãi thuần từ hoạt động
  dịch vụ”, nhưng lexical tốt hơn khi câu hỏi gần như trùng nguyên văn nhãn dòng.
- Có tuyến mà cả hai top-1 đều có điểm nhãn dòng bằng 0. Các tuyến này cần xem
  cấu trúc bảng, tiêu đề nhiều tầng và thuyết minh; không nên tiếp tục tự động
  xuống cột số.

## Phần đã triển khai

- Batch dense: mã hóa toàn bộ truy vấn trong một lần tải model/index.
- Giữ bộ lọc bắt buộc ticker và năm; scope chỉ khóa khi câu hỏi nêu rõ.
- Hợp nhất lexical–dense và ghi rõ lane nào ủng hộ mỗi candidate.
- Nối UID candidate về đúng bảng gốc, source path, vị trí và hash.
- Phá hòa bằng độ khớp nhãn dòng không chứa giá trị số.
- Sinh hàng đợi review ba mức khó.
- Validator kiểm tra hash, đủ 1.012 câu, đủ 1.232 tuyến, không trùng gói, không
  rò raw value và không thay đổi quyền phát hành.

## Giới hạn còn lại

- Không có gold nên chưa tính được recall@k hoặc accuracy thật.
- Điểm nhãn dòng chỉ là tín hiệu; bảng đúng có thể dùng tiêu đề nhiều tầng hoặc
  tên viết tắt khiến điểm thấp.
- Chưa xác nhận cột kỳ báo cáo, đơn vị, phạm vi hay exact cell.
- Chưa xử lý 370 câu có kế hoạch chưa đầy đủ.
- Chưa tạo pandas query hay submission.

## Quyết định cho vòng tiếp theo

Không chọn riêng lexical hoặc dense. Dùng hàng đợi hybrid làm đầu vào cho bước
exact-row và exact-column theo thứ tự:

1. Kiểm tra trước 273 tuyến `hard_review` để tìm lỗi hệ thống và các bảng nhiều
   trang/trùng lặp.
2. Với 222 tuyến đồng thuận cao, thử xác định dòng và cột theo header/kỳ nhưng
   vẫn yêu cầu validator exact source-cell.
3. Với 737 tuyến thông thường, so tối đa ba bảng và giữ abstain nếu scope hoặc
   kỳ không phân biệt được.
4. Chỉ sau khi exact cell, đơn vị và phép tính đều có thể replay mới sinh pandas
   query. Không dùng điểm retrieval để cấp quyền answer.

## Cách chạy lại

```bash
rtk env PYTHONPATH=.:src .venv/bin/python \
  scripts/research/build_hybrid_retrieval_analysis_v1.py \
  --config configs/research/hybrid_retrieval_analysis_v1.json \
  --plans artifacts/research/question_compiler_shadow_20260825/typed_operand_plans.jsonl \
  --lexical-candidates artifacts/research/full_corpus_candidate_retrieval_v1_20260826_r2/table_candidates_v1.jsonl \
  --dense-index-dir artifacts/kaggle_runs/vifinqa_full_dense_index_gpu_v1_20260826/vifinqa_full_dense_v1 \
  --assets artifacts/research/document_corpus_round2_assets_v1_20260826_r2/full_table_assets_v1.jsonl \
  --output-dir artifacts/research/hybrid_retrieval_analysis_v1_NEW \
  --device cpu

rtk env PYTHONPATH=.:src .venv/bin/python \
  scripts/research/validate_hybrid_retrieval_analysis_v1.py \
  --artifact-dir artifacts/research/hybrid_retrieval_analysis_v1_NEW
```
