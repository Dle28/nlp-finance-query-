# Nghiên cứu các câu đang bị chặn — vòng 2

## Kết luận ngắn

Không có một “filter quá chặt” duy nhất. Hạ ngưỡng khớp tên dòng làm tăng nguồn mơ hồ nhanh hơn nguồn duy nhất, vì vậy không được nới ngưỡng toàn cục. Nút thắt lớn hơn là kế hoạch tính chưa đủ, route chưa materialize thành nguồn chính xác, và một phần sidecar V2/V3 chưa phủ hết kho bảng.

Mọi artifact trong báo cáo này chỉ phục vụ nghiên cứu. Chúng không tạo `human_verified`, không tạo evidence, answer, CSV nộp bài hay quyền release.

## 1. Ảnh chụp hiện trạng

Nguồn: `artifacts/research/uncomputed_e2e_triage_v1_20260826_r15/`.

| Nhóm nguyên nhân chính | Số câu | Ý nghĩa thực tế |
| --- | ---: | --- |
| Route đã tìm được nhưng chưa materialize vào E2E | 313 | Có hướng tới tài liệu nhưng chưa có một ô nguồn đủ chặt. |
| Thiếu graph tính nhiều operand | 282 | Biết đây là câu tính, nhưng chưa có graph biến và phép tính. |
| Kế hoạch câu hỏi chưa đủ operand | 253 | Parser chưa biết cần lấy những chỉ tiêu nào. |
| Chọn nhiều bước chưa compile | 66 | Cần chọn năm/công ty rồi mới tính. |
| Giá trị đã chạy nhưng evidence thiếu trường nghĩa | 36 | Không được phép trả lời dù số đã từng tính. |
| Các nguyên nhân còn lại | 62 | Thiếu điều hướng nguồn, entity, operator hoặc header kỳ. |

Tổng cộng có 1.012 câu. Trong đó 976 câu chưa tính được; 36 câu có execution nhưng vẫn bị chặn đúng theo nguyên tắc dẫn nguồn.

## 2. Filter có đang quá chặt không?

### Câu composition (282 câu, 847 operand)

Nguồn: `artifacts/research/composition_operand_source_gap_audit_v1_20260827_j090_m020/`, `..._j085_m015/`, `..._j080_m010/`.

| Ngưỡng tên dòng / khoảng cách với ứng viên kế | Operand có một nguồn chặt | Câu có đủ mọi operand chặt |
| --- | ---: | ---: |
| 0,90 / 0,20 | 10 | 0 |
| 0,85 / 0,15 | 14 | 0 |
| 0,80 / 0,10 | 22 | 1 |

Ngay cả ở mức nới nhiều nhất, chỉ 1/282 câu có đủ nguồn cho toàn bộ biến. Hạ ngưỡng không giải quyết thiếu graph hoặc thiếu operand; nó chỉ mở thêm ứng viên cần phân biệt.

### Câu direct lookup (313 câu)

Nguồn: `artifacts/research/hydrated_source_gate_probe_v1_20260827_r2/`, `..._r2_j085/`, `..._r2_j080/`.

| Ngưỡng Jaccard | Một nguồn duy nhất | Nhiều nguồn cùng qua cổng | Chưa đủ nguồn | Không thuộc direct lookup hẹp |
| --- | ---: | ---: | ---: | ---: |
| 0,90 | 4 | 6 | 239 | 64 |
| 0,85 | 3 | 7 | 239 | 64 |
| 0,80 | 2 | 12 | 235 | 64 |

Kết quả đi ngược giả thuyết “cứ nới là tốt”: số nguồn duy nhất giảm 4 → 3 → 2, còn mơ hồ tăng 6 → 7 → 12. Giữ 0,90 là hợp lý cho cổng exact source.

## 3. Kiểm tra giả thuyết thiếu sidecar nguồn

Artifact `artifacts/research/source_sidecar_coverage_audit_v1_20260827_r1/` đã kiểm tra lại trực tiếp từ OCR gốc, hash file, hash lát cắt bảng và parse grid:

- 10.859 bảng nằm trong review queue.
- 6.514 bảng đã có đủ V2/V3.
- 4.345 bảng thiếu cả hai sidecar, nhưng **4.345/4.345 đều tái dựng được** đúng từ OCR gốc.

Tuy vậy, khi tái dựng trong bộ nhớ cho lane direct lookup (666 bảng liên quan), số câu có một nguồn duy nhất không tăng: 4 → 4. Phủ sidecar là việc cần làm để hoàn thiện dữ liệu, nhưng không phải đòn bẩy chính cho lane này. Nút thắt còn lại là khớp dòng, cột kỳ, phạm vi và tính duy nhất.

## 4. Hai lỗi/độ lệch đã xác định rõ

### Route-state không nhất quán

Trong 313 câu route chưa materialize có 57 câu đã có đủ hình dạng direct lookup: một entity, một năm, một operand và phép `lookup`. Tuy nhiên overlay nói `reported_value` đã “covered” trong khi route tổng vẫn `route_incomplete`.

Probe nay tách riêng trạng thái này thành `REPORTED_VALUE_COVERED_ROUTE_STILL_INCOMPLETE`, thay vì bỏ qua ngay từ đầu. Kết quả: có thêm 2 câu được phát hiện có một nguồn cấu trúc duy nhất (Q9, Q47), nhưng chúng vẫn là research-only vì chưa có evidence binding.

### Parser nhầm tên dòng báo cáo là phép tính

Ba câu Q106, Q206, Q260 đã được reclassify hẹp thành direct lookup cho các cụm “Tỷ lệ sở hữu” và “Tổng cộng tài sản”. Artifact: `artifacts/research/reported_row_plan_reclassification_v1_20260826_r2/`.

| Câu | Vướng thực tế |
| --- | --- |
| Q106 | Matcher chủ thể+hàng+cột đã tìm được một đường điều hướng duy nhất; còn chờ evidence binding. |
| Q206 | Cột năm 2021 chưa duy nhất trong header. |
| Q260 | Matcher chủ thể+hàng+cột đã tìm được một đường điều hướng duy nhất; còn chờ evidence binding. |

Q106/Q260 cho thấy một lỗi cụ thể của matcher cũ: chủ thể nằm ở cột khác, trong khi nó chỉ nhìn tên dòng ở cột đầu. Hai câu này còn có một điểm dễ nhầm ở header: Q106 biểu diễn kỳ là `31/12/2016`, còn Q260 có hai cột cùng năm (biểu quyết và sở hữu). Matcher mới yêu cầu đồng thời chủ thể, cột sở hữu (không phải quyền biểu quyết), năm trong header, grid V2/V3, tọa độ header và ô số đáng tin. Nó trả về một navigation candidate duy nhất cho mỗi câu, không xuất số liệu.

## 5. Phần đã triển khai trong vòng này

- `source_sidecar_coverage_audit.py`: audit hash-bound, kiểm tra việc thiếu V2/V3 có thực sự tái dựng được từ OCR hay không.
- `hydrated_source_gate_probe.py`: so sánh baseline với sidecar tái dựng trong bộ nhớ, không ghi giá trị số và không mở đường sang E2E.
- Probe mới phân biệt route `reported_value` thiếu thật với route đã ghi covered nhưng vẫn incomplete.
- `embedded_subject_row_probe.py` và `ownership_subject_header_probe.py`: khớp chặt ownership-note theo chủ thể ở mọi cột của hàng, sau đó chọn một header sở hữu theo năm. Kết quả Q106 và Q260 đều có đúng một đường điều hướng nguồn.
- Bổ sung regression test; 24 test liên quan đã qua.

## 6. Ưu tiên khắc phục tiếp theo

1. **Mở rộng có kiểm soát matcher ownership/note.** Mẫu Q106/Q260 đã qua: chủ thể ở mọi cột + header sở hữu + năm + scope + V2/V3. Chỉ áp dụng cho câu cùng cú pháp và phải giữ abstain nếu có hơn một vị trí.
2. **Chuẩn hóa header nhiều tầng.** Q206 và câu tương tự cần phân tích header theo đường dẫn đầy đủ (năm + kỳ + đơn vị), không chọn chỉ vì thấy một token năm.
3. **Tạo graph từ template có kiểm soát.** Ưu tiên 282 câu composition theo các phép `subtract`, `divide`, `mean`, `max`, `percentage_change`, `arg_extreme_period`. Mỗi operand vẫn phải qua exact source riêng; graph không tự tạo evidence.
4. **Kế hoạch cho 253 câu chưa có operand.** Bắt đầu từ mẫu có cú pháp rõ (chênh lệch hai công ty, tăng trưởng hai năm, tỷ suất quen thuộc), rồi đo source coverage từng mẫu trước khi tích hợp.
5. **Dense/RAG chỉ để điều hướng.** E5 section RAG hiện hữu nên dùng để hẹp tập bảng cho câu dài; không dùng embedding score để chọn ô cuối hoặc thay evidence V2/V3. Chỉ mở rộng dense row retrieval khi có tập đánh giá source-grounded đủ tốt.

## 7. Kiểm tra executor cho câu nhiều bước

Không cần thêm một framework tính toán mới ngay lúc này. `arg_extreme_period`,
`subtract`, Decimal executor và graph kiểm soát đã tồn tại trong E2E và đã có
test. Các artifact trước đó xác nhận:

| Mẫu đã có executor | Số ứng viên được thử | Đủ toàn bộ nguồn | Kết quả |
| --- | ---: | ---: | --- |
| Chọn năm có giá trị CFO lớn nhất | 11 | 1 | 1 graph nghiên cứu được materialize; 10 bị quarantine vì thiếu source. |
| Trừ hai chỉ tiêu cùng kỳ | 23 | 1 | 1 graph nghiên cứu được materialize; 22 bị quarantine vì thiếu source. |

Vì vậy CPU/GPU hay một LLM lớn hơn không phải điểm nghẽn trước mắt. Với 66
câu multi-stage chưa compile, thiếu dữ liệu đầu vào cho graph (`operands: []`)
là nguyên nhân trước operator. Việc tiếp theo là compiler template có kiểm
soát cho các câu có cú pháp lặp lại, sau đó mới kiểm tra source coverage.

Phân tích riêng 66 câu này cho thấy 41 câu có bước chọn cao nhất/thấp nhất,
28 câu lọc theo trung vị và 19 câu có điều kiện dương/âm; nhiều câu đồng thời
có nhiều đặc điểm. Chúng chưa thể đi vào `arg_extreme_period` hay executor
hiện có vì chưa định nghĩa các biến đầu vào. Template đầu tiên nên là dạng:
**tập công ty cố định → chỉ tiêu tài chính có công thức đã biết → lọc theo
ngưỡng/trung vị → chọn cực trị → trả chỉ tiêu mục tiêu**. Mỗi template phải
khai báo toàn bộ operand và contract kỳ/phạm vi trước khi chạy retrieval.

## 8. Vòng compiler template đã triển khai

Artifact đã xác thực: `artifacts/research/staged_formula_materialization_audit_v1_20260827_r2/`.

Ba template chỉ nhận dạng câu có đầy đủ cụm từ, tập ticker rõ ràng và kỳ rõ
ràng. Chúng chỉ tạo danh sách biến cần truy hồi cùng các stage; không truy hồi
bảng, không chọn ô, không tính số và không thay đổi quyền trả lời E2E.

| Template | Mẫu logic | Câu mới có kế hoạch | Số biến mỗi câu |
| --- | --- | ---: | ---: |
| Nợ phải trả/vốn chủ sở hữu lớn nhất → hệ số trả lãi | Chọn D/E lớn nhất → `(PBT + chi phí lãi vay) / chi phí lãi vay` | 2 | 12 |
| Lợi nhuận hoạt động dương → CFO/LNHD thấp nhất → biên lợi nhuận ròng | Lọc dương → chọn tỷ lệ thấp nhất → `LNST/doanh thu thuần` | 3 | 12 |
| CFO dương nhiều năm → biên lợi nhuận ròng cao nhất | Hỗ trợ thêm ngữ pháp “Năm mục tiêu, trong nhóm…” | 1 | 15 |

Kết quả chạy lại toàn bộ 1.012 câu: 10 câu khớp ba template; 4 câu đã có
plan staged từ trước, **6 câu mới** chuyển từ `abstain` sang
`typed_non_executable` (Q465, Q492, Q494, Q545, Q553, Q574). Trạng thái mới
có nghĩa là hệ thống đã biết cần truy hồi những chỉ tiêu nào và theo thứ tự
nào; nó **không** có nghĩa là sáu câu này đã có evidence, answer hoặc đủ điều
kiện nộp bài.

Đây cũng là bằng chứng rằng filter nguồn không phải cổng cần nới tiếp theo cho
nhóm này: mỗi câu vừa được materialize cần 12–15 exact operands, sau đó cần
source coverage và stage executor. Nếu chỉ nới Jaccard/score trước, hệ thống
sẽ tăng nhiều lựa chọn cho từng biến nhưng chưa có cơ chế bảo đảm chọn đúng
toàn bộ tập biến.

## 9. Kiểm tra nguồn cho sáu template mới

Các artifact dưới đây chỉ kiểm tra khả năng định vị nguồn. “Một nguồn duy
nhất” ở đây vẫn chỉ là navigation candidate trong research, chưa phải evidence
hay đáp án E2E.

| Phép kiểm ở ngưỡng 0,80 / 0,10 | Một nguồn duy nhất | Nhiều nguồn | Chưa đủ nguồn | Nhận định |
| --- | ---: | ---: | ---: | --- |
| Header năm ghi trực tiếp, nhãn chỉ đọc cột đầu | 5/75 operand | 5/75 | 65/75 | Baseline trước sửa cấu trúc hàng. |
| Header năm ghi trực tiếp, nhãn quét mọi ô chữ trong hàng | 6/75 | 8/75 | 61/75 | Sửa cấu trúc hàng làm lộ 4 đường nguồn, trong đó 3 là mơ hồ. |
| `Năm nay`/`Số cuối năm` + tiêu đề OCR + nhãn đa cột | 14/75 | 14/75 | 47/75 | Số năm thường chỉ nằm ở tiêu đề nguồn, không nằm trong header bảng. |
| Tái dựng 100 sidecar còn thiếu rồi kiểm tra lại header năm | 6/75 | 8/75 | 61/75 | Không đổi sau khi cổng loại bảng loại đúng bảng thuyết minh EPS. |
| Tái dựng 100 sidecar rồi kiểm tra `Năm nay`/`Số cuối năm` | 14/75 | 14/75 | 47/75 | Không thay đổi: các ứng viên current-period đã có coverage cần thiết. |

Quét nhãn đa cột là một sửa lỗi cấu trúc, không phải nới ngưỡng: bảng lưu
chuyển tiền tệ thường có `Mã số` ở cột đầu và tên chỉ tiêu ở cột sau. Bản cũ
chỉ chấm cột đầu nên bỏ lỡ cả khi bảng đúng đã được retrieval. Nhãn vẫn phải
qua cùng ngưỡng, kỳ, loại bảng, V2/V3 và khoảng cách với ứng viên kế tiếp.

Khi gộp hai cách biểu diễn kỳ sau coverage tái dựng, 20/75 operand có đúng một
navigation candidate, 22/75 mơ hồ và 33/75 thiếu nguồn. Không có câu nào trong
6 câu có đủ toàn bộ 12–15 operand, nên không câu nào được mở sang E2E executor.

### Phần lớn mơ hồ là về phạm vi

Toàn bộ 22 operand mơ hồ có một candidate từ báo cáo **riêng** và một từ **hợp
nhất**, thuộc tài liệu khác nhau. Câu hỏi không nêu phạm vi nên hệ thống phải
chặn; tự mặc định chọn hợp nhất hoặc riêng sẽ thay đổi nghĩa câu hỏi. Một bảng
thuyết minh EPS từng tạo mơ hồ giả đã bị loại vì không thuộc loại bảng mà
operand cho phép.

| Câu | Tình trạng sau các phép kiểm | Nguyên nhân còn lại |
| --- | --- | --- |
| Q465 | 2 unique, 3 mơ hồ, 7 thiếu | Phần mơ hồ chủ yếu là riêng/hợp nhất của PLX. |
| Q492 | 5 unique, 2 mơ hồ, 5 thiếu | Hai chỉ tiêu BSR năm 2017 mơ hồ riêng/hợp nhất. |
| Q494 | 2 unique, 4 mơ hồ, 6 thiếu | Vừa có scope ambiguity, vừa còn thiếu khớp dòng/kỳ. |
| Q545 | 5 unique, 2 mơ hồ, 5 thiếu | Cùng cấu trúc nguồn với Q492. |
| Q553 | 2 unique, 3 mơ hồ, 7 thiếu | Cùng cấu trúc nguồn với Q465. |
| Q574 | 4 unique, 8 mơ hồ, 3 thiếu | Quét nhãn đa cột tìm thấy CFO của SAM; phần mơ hồ còn lại đều là scope ambiguity. |

Với Q574, tái dựng sidecar không tự mở khóa câu. Nó làm lộ một bảng thuyết
minh EPS có cùng cụm từ, nhưng cổng loại bảng đã loại nó; phần còn lại vẫn
thiếu hoặc mơ hồ phạm vi. Đây là lý do coverage và loại bảng phải được kiểm tra
trước khi cho bất kỳ unique candidate nào đi xa hơn.

### Dense/RAG đóng góp gì ở vòng này?

Artifact `artifacts/research/staged_formula_hybrid_probe_multicol_v1_20260827_r1/`
dùng index Kaggle hiện có với embedding `intfloat/multilingual-e5-small`.
Trên 75 operand, dense bổ sung 285 table candidate và hybrid có điểm proxy
hàng trung bình nhỉnh hơn lexical (0,722 so với 0,713). Đây chỉ là thước đo
ưu tiên điều hướng, không phải độ đúng đáp án.

Phép đối chiếu exact-source riêng đã kiểm tra 300 bảng hybrid bằng cùng V2/V3,
kỳ, scope và row-margin: lexical và lexical+dense cùng cho 6 unique, 8 mơ hồ,
61 thiếu; dense không mở thêm operand nào và cũng không tạo ambiguity mới.
Dense nên được giữ làm lớp đưa bảng khó vào hàng chờ, nhưng không phải lời giải
cho thiếu nguồn hay scope ambiguity. Chưa có căn cứ để thay embedding hoặc thêm
reranker mới.

### Vòng sửa ngữ nghĩa chỉ số và kỳ báo cáo

Vòng này kiểm tra trực tiếp 33 operand còn thiếu sau các bước trên. Đây là các
thay đổi nhỏ, có lý do rõ ràng từ cấu trúc BCTC; không dùng mô hình mới và không
nới ngưỡng Jaccard toàn cục.

| Vấn đề quan sát được | Cách xử lý đã thử | Kết quả |
| --- | --- | --- |
| Dòng tiêu đề và dòng thành phần cùng chứa “vốn chủ sở hữu” | Chỉ nhận dòng tổng mã `400`; loại “quỹ khác thuộc vốn chủ sở hữu” | Không còn nhầm `400` với `410`. |
| OCR viết “NỘ PHẢI TRẢ”; bảng báo cáo bộ phận có cùng nhãn | Nhận biến thể “nợ phải trả”, yêu cầu mã `300` và chỉ lấy bảng báo cáo chính | Bỏ được bảng bộ phận, tìm được dòng tổng của bảng cân đối. |
| Lợi nhuận sau thuế gồm cả dòng phân bổ cho cổ đông | Loại các dòng “của cổ đông” | Giữ dòng lợi nhuận sau thuế của doanh nghiệp. |
| Kỳ ghi `31/12/2019` thay vì chỉ ghi năm | Chấp nhận đúng một header ngày kết thúc năm trong bảng cân đối, lấy đơn vị từ chính header đó | Tìm được các bảng PLX trước đó bị bỏ sót. |
| Một dòng bị OCR lặp ở hai trang liền kề | Chỉ gộp khi cùng tài liệu, cùng phạm vi/loại bảng, nhãn “chuyển/mang sang trang”, trang kế tiếp và hash vector số giống nhau | Gộp 2 cặp PLX 2017; không gộp theo tên dòng đơn thuần. |

Kết quả trên 75 operand của 6 template:

| Mốc kiểm tra | Một navigation source duy nhất | Nhiều source | Thiếu source |
| --- | ---: | ---: | ---: |
| Sau header hiện có + tái dựng sidecar | 20 | 22 | 33 |
| Sau lớp cụm chỉ số và schema trên | 29 | 46 | 0 |

| Câu | Unique | Mơ hồ | Thiếu | Kết luận |
| --- | ---: | ---: | ---: | --- |
| Q465 | 2 | 10 | 0 | Scope không được nêu. |
| Q492 | 9 | 3 | 0 | Scope không được nêu. |
| Q494 | 3 | 9 | 0 | Scope không được nêu. |
| Q545 | 9 | 3 | 0 | Scope không được nêu. |
| Q553 | 2 | 10 | 0 | Scope không được nêu. |
| Q574 | 4 | 11 | 0 | Scope không được nêu. |

46 trường hợp mơ hồ còn lại đều là lựa chọn báo cáo riêng/hợp nhất. Không câu
nào trong sáu câu nêu phạm vi, vì vậy không có câu nào đủ mọi operand duy nhất
để chạy E2E. Đây là giới hạn ngữ nghĩa của câu hỏi, không phải thiếu dữ liệu,
CPU, dense retrieval hay ngưỡng khớp tên dòng.

Một phép thử coverage-first cũng đã quét toàn bộ bảng có đúng ticker, năm và
loại báo cáo cho các operand còn thiếu ở thời điểm thử. Không tìm được dòng
mới ngoài tập lexical hiện có. Điều này bác bỏ giả thuyết cần tăng top-k hoặc
thay embedding/reranker cho nhóm này.

Các thay đổi trên chỉ ở lane nghiên cứu/navigation. `finance-query run-e2e`
không được cho phép dùng chúng để chọn ô, tạo evidence, answer hay submission:
muốn tích hợp vào E2E cần có scope được nêu rõ trong câu hỏi và evidence binding
V2/V3 độc lập cho từng operand.

### Hướng khắc phục đã chốt

1. Giữ cổng exact source hiện tại; không nới Jaccard toàn cục.
2. Giữ parser phạm vi khi câu hỏi nói rõ “hợp nhất” hoặc “riêng”; khi câu
   không nói rõ thì tiếp tục abstain, không tự chọn scope.
3. Đã triển khai và kiểm chứng alias/row-code/header-date cho 33 operand. Nếu
   mở rộng sang chỉ số khác, mỗi rule mới phải qua cùng phép đo unique/mơ hồ.
4. Backfill V2/V3 theo một pipeline versioned từ OCR gốc, có hash và regression
   test; không lấy sidecar tạm trong research để làm evidence E2E.

Artifact chính của vòng này:

- `artifacts/research/full_corpus_staged_formula_candidates_multicol_v1_20260827_r2/`
- `artifacts/research/staged_formula_operand_source_gap_multicol_v1_20260827_j080_m010_r3/`
- `artifacts/research/staged_formula_hydrated_current_period_probe_multicol_v1_20260827_j080_m010_r1/`
- `artifacts/research/staged_formula_hydrated_source_probe_multicol_v1_20260827_j080_m010_r2/`
- `artifacts/research/staged_formula_hybrid_source_probe_multicol_v1_20260827_j080_m010_r2/`
- `artifacts/research/staged_formula_metric_phrase_probe_v1_20260827_r7/`
- `artifacts/research/staged_formula_metric_phrase_coverage_probe_v1_20260827_r1/`

## 10. Bổ sung entity cho lookup đơn giản

Mười hai câu lookup đơn giản từng bị `abstain` vì planner không gắn được công
ty. Tám trong số đó nêu đúng một ticker viết hoa trong câu (`HT1` hoặc `PC1`),
và ticker này có trong sidecar alias được trích từ tiêu đề báo cáo. Compiler có
thêm fallback hẹp: token phải xuất hiện nguyên vẹn, viết hoa, chỉ khớp với một
ticker trong sidecar và chỉ được dùng cho họ câu một công ty. Nó không suy ra
scope và không tạo evidence.

Overlay được kiểm tra hash đã materialize đúng 8/1.012 plan; 1.004 plan còn
lại giữ nguyên. Sau đó retrieval và source-gate được chạy lại cho đúng 8 câu:

| Ngưỡng dòng / margin | Unique | Nhiều nguồn | Chưa đủ nguồn |
| --- | ---: | ---: | ---: |
| 0,90 / 0,20 | 0 | 1 | 7 |
| 0,80 / 0,10 | 0 | 1 | 7 |

Nới filter không giúp nhóm này. Bước tiếp theo là alias chỉ số theo từng dạng
bảng (ví dụ “vay và nợ thuê tài chính ngắn hạn”) và header đầu/cuối năm; mỗi
alias sẽ tiếp tục phải qua scope, kỳ, unit, V2/V3 và margin. Chưa có target nào
được mở sang answer-capable E2E.

### Độ sâu retrieval: một lỗi có thể sửa, khác với scope ambiguity

Thí nghiệm top-10 ban đầu bỏ sót Q73 vì hai bảng cân đối chứa đúng dòng nằm ở
hạng 12 và 13. Chạy lại cùng lexical index với top-20 (toàn bộ 1.012 câu,
metadata-only) cho thấy:

| Cấu hình candidate | Tổng bảng ứng viên | Q73 sau source gate | Diễn giải |
| --- | ---: | --- | --- |
| top-10 | 12.400 | Thiếu nguồn | Hai bảng đúng bị cắt trước khi kiểm tra dòng. |
| top-20 | 24.747 | Nhiều nguồn | Cả bản riêng và hợp nhất đều chứa dòng khớp; câu vẫn không nêu scope. |

Do đó top-10 là quá nông đối với một phần bảng thuyết minh/bảng cân đối. Đây
là sửa **recall của navigation lane** có căn cứ, không phải nới Jaccard hay
chọn đáp án. Top-20 làm Q73 chuyển đúng từ trạng thái “không thấy bảng” sang
“thấy nhưng chưa đủ entity scope”; E2E vẫn không nhận answer/evidence/submission
từ artifact này.

Artifact top-20 đã qua validator:

- `artifacts/research/full_corpus_explicit_ticker_candidates_top20_v1_20260827_r1/`
- `artifacts/research/explicit_ticker_semantic_source_probe_v1_20260827_top20_r1/`

### Q49 không phải OCR lặp

Q49 có sáu source candidate dù tên dòng khớp hoàn toàn. Kiểm tra hash vector
số và vị trí bảng cho thấy chúng không phải hai trang lặp: chúng thuộc ba ngữ
cảnh khác nhau (chi phí bán hàng, chi phí quản lý doanh nghiệp, và chi phí sản
xuất kinh doanh theo yếu tố), mỗi ngữ cảnh lại có bản riêng/hợp nhất. Câu chỉ
nói `chi phí dịch vụ mua ngoài` nên chưa nêu **nhóm chi phí cha** lẫn scope.
Không có căn cứ để gộp hay chọn một trong sáu dòng này; đây là entity thiếu của
câu hỏi, không phải filter bị quá chặt.

Artifact:

- `artifacts/research/explicit_ticker_plan_recheck_v1_20260827_r1/`
- `artifacts/research/full_corpus_explicit_ticker_candidates_v1_20260827_r1/`
- `artifacts/research/explicit_ticker_source_gap_audit_v1_20260827_j090_m020_r1/`
- `artifacts/research/explicit_ticker_source_gap_audit_v1_20260827_j080_m010_r1/`

## 11. Bảng nhiều ô: kiểm tra theo vai trò đầy đủ của cột

Ảnh review cho thấy một bảng thuyết minh có thể giữ nhiều chỉ tiêu và nhiều
thời điểm trên cùng một bảng. Với dạng này, chọn chỉ theo tên dòng và năm là
không đủ: trong cùng bảng có thể đồng thời có `đầu năm`, `phải nộp trong năm`,
`đã nộp trong năm`, `phải thu cuối năm` và `phải nộp cuối năm`.

Thí nghiệm này không đọc hay xuất số đáp án. Nó chỉ so sánh cấu trúc nguồn của
Q57 và Q283, hai lookup đơn giản trước đó bị chặn.

| Cổng mới | Mục đích | Không được phép làm |
| --- | --- | --- |
| Vai trò thời điểm | Phân biệt rõ `đầu năm` với `cuối năm`; ngày `1/1` hoặc `31/12` không tự đủ nếu ô mang nghĩa `trong năm`. | Không dùng năm báo cáo để đoán ô. |
| Vai trò số dư | Yêu cầu đúng cụm `số phải nộp`; không đổi sang `số phải thu`. | Không dùng ô lân cận hay một nhãn gần giống. |
| Cùng ô/cùng cột có nguồn | Vai trò, đơn vị và kiểu số phải xác minh được trong cùng nguồn V2/V3; chấp nhận header xuất phát từ chính ô giá trị chỉ khi đó là đúng cùng tọa độ. | Không mượn header của một ô khác cùng hàng. |
| Phạm vi | Nếu nguồn còn cả `riêng` và `hợp nhất` mà câu không nói rõ, đánh dấu mơ hồ. | Không mặc định chọn hợp nhất hoặc riêng. |

Kết quả trên nguồn có hash:

| Câu | Đường điều hướng theo vai trò | Phạm vi quan sát được | Kết luận |
| --- | ---: | ---: | --- |
| Q57 — thuế TNDN phải nộp đầu năm | 1 | 2 | Bị chặn vì phạm vi chưa nêu. Bản date-only trước đó có thể lẫn cột `trong năm`; cổng mới loại nhầm lẫn này. |
| Q283 — thuế GTGT phải nộp cuối năm | 1 | 2 | Bị chặn vì phạm vi chưa nêu. Bảng nhiều ô đã định vị được ô theo đúng vai trò, nhưng không được phép suy ra scope. |

Vì vậy filter không “quá chặt” ở điểm cuối: hai câu đã đi qua truy hồi, alias,
vai trò cột, đơn vị, V2/V3 và row-margin. Cổng còn chặn là một thiếu sót thật
trong entity của câu hỏi — **scope báo cáo**. Nới điểm retrieval hoặc thêm
embedding/reranker không thể giải quyết khác biệt về nghĩa này.

Một phát hiện sửa parser nghiên cứu: một số header OCR ghép nhãn và nội dung ô
khiến V3 không gắn cột đó là numeric, dù ô V2 gốc có provenance và token số.
Nhánh nghiên cứu mới chỉ dùng fallback này cho financial note có vai trò đầy
đủ; vẫn yêu cầu exact cell provenance. Nó chưa được nối vào `run-e2e`, evidence
hay submission, do chưa có binding độc lập và scope của hai câu vẫn mơ hồ.

### Kiểm tra lane E2E cho câu đã có source duy nhất

Q58 và Q327 nêu rõ scope nên probe định vị được một navigation source duy
nhất. Nhưng E2E locked hiện tại vẫn trả `ABSTAIN` cho cả hai: input closure của
nó được khóa trước fallback ticker mới, nên còn ghi `MISSING_ENTITY` và không
nhập artifact research. Đây là hành vi fail-closed đúng, không phải lỗi của
source probe. Bước tích hợp hợp lệ là tạo exact-cell binding candidate có hash
từ source riêng biệt, sau đó để authorization replay kiểm tra; không được ghi
đè closure locked hoặc coi navigation candidate là evidence.

Thử adapter direct-lookup generic cho đúng hai câu cho kết quả 0/2 candidate.
Không phải thiếu bảng: Q58 bị loại vì header ghi ngày đầy đủ thay vì chỉ một
nhãn năm; Q327 bị loại vì Jaccard của hàng review tổng quát không giữ được alias
đã xác minh trong probe ngữ nghĩa. Do đó không giảm ngưỡng toàn cục. Adapter
exact-route kế tiếp sẽ chỉ nhận hai dạng header/alias này khi V2/V3, scope,
unit, row margin và exact cell đều độc lập qua cổng.

Artifact đã qua validator:

- `artifacts/research/explicit_ticker_semantic_source_probe_v1_20260827_r1/`
- `artifacts/research/explicit_ticker_role_header_probe_v1_20260827_r5/`

## 12. Lệnh kiểm tra lại

```bash
rtk .venv/bin/python scripts/research/validate_source_sidecar_coverage_audit_v1.py \
  --artifact-dir artifacts/research/source_sidecar_coverage_audit_v1_20260827_r1

rtk .venv/bin/python scripts/research/validate_hydrated_source_gate_probe_v1.py \
  --artifact-dir artifacts/research/hydrated_source_gate_probe_v1_20260827_r2 \
  --expected-question-count 1012

rtk .venv/bin/python scripts/research/validate_staged_formula_materialization_audit_v1.py \
  --artifact-dir artifacts/research/staged_formula_materialization_audit_v1_20260827_r2 \
  --expected-question-count 1012

rtk .venv/bin/python scripts/research/validate_composition_operand_source_gap_audit_v1.py \
  --artifact-dir artifacts/research/staged_formula_operand_source_gap_multicol_v1_20260827_j080_m010_r3 \
  --expected-question-count 1012

rtk .venv/bin/python scripts/research/validate_staged_formula_hydrated_current_period_probe_v1.py \
  --artifact-dir artifacts/research/staged_formula_hydrated_current_period_probe_multicol_v1_20260827_j080_m010_r1 \
  --expected-question-count 1012

rtk .venv/bin/python scripts/research/validate_staged_formula_hydrated_source_probe_v1.py \
  --artifact-dir artifacts/research/staged_formula_hydrated_source_probe_multicol_v1_20260827_j080_m010_r2 \
  --expected-question-count 1012

rtk .venv/bin/python scripts/research/validate_staged_formula_hybrid_source_probe_v1.py \
  --artifact-dir artifacts/research/staged_formula_hybrid_source_probe_multicol_v1_20260827_j080_m010_r2 \
  --expected-question-count 1012

rtk .venv/bin/python scripts/research/validate_hybrid_retrieval_analysis_v1.py \
  --artifact-dir artifacts/research/staged_formula_hybrid_probe_multicol_v1_20260827_r1 \
  --expected-question-count 1012 --expected-route-count 75

rtk .venv/bin/python scripts/research/validate_staged_formula_metric_phrase_probe_v1.py \
  --artifact-dir artifacts/research/staged_formula_metric_phrase_probe_v1_20260827_r7 \
  --expected-question-count 1012

rtk .venv/bin/python scripts/research/validate_explicit_ticker_plan_recheck_v1.py \
  --artifact-dir artifacts/research/explicit_ticker_plan_recheck_v1_20260827_r1 \
  --expected-question-count 1012

rtk .venv/bin/python scripts/research/validate_full_corpus_candidate_retrieval_v1.py \
  --artifact-dir artifacts/research/full_corpus_explicit_ticker_candidates_v1_20260827_r1 \
  --expected-question-count 1012

rtk .venv/bin/python scripts/research/validate_full_corpus_candidate_retrieval_v1.py \
  --artifact-dir artifacts/research/full_corpus_explicit_ticker_candidates_top20_v1_20260827_r1 \
  --expected-question-count 1012

rtk .venv/bin/python scripts/research/validate_explicit_ticker_semantic_source_probe_v1.py \
  --artifact-dir artifacts/research/explicit_ticker_semantic_source_probe_v1_20260827_r1 \
  --expected-question-count 1012

rtk .venv/bin/python scripts/research/validate_explicit_ticker_semantic_source_probe_v1.py \
  --artifact-dir artifacts/research/explicit_ticker_semantic_source_probe_v1_20260827_top20_r1 \
  --expected-question-count 1012

rtk .venv/bin/python scripts/research/validate_explicit_ticker_role_header_probe_v1.py \
  --artifact-dir artifacts/research/explicit_ticker_role_header_probe_v1_20260827_r5 \
  --expected-question-count 1012

rtk .venv/bin/python -m pytest -q \
  tests/research/test_hydrated_source_gate_probe.py \
  tests/research/test_source_sidecar_coverage_audit.py \
  tests/research/test_staged_formula_materialization_audit.py \
  tests/research/test_composition_operand_source_gap_audit.py \
  tests/research/test_staged_formula_current_period_probe.py \
  tests/research/test_staged_formula_hydrated_source_probe.py \
  tests/research/test_staged_formula_hybrid_source_probe.py \
  tests/research/test_staged_formula_metric_phrase_probe.py \
  tests/research/test_staged_formula_metric_phrase_coverage_probe.py \
  tests/research/test_explicit_ticker_plan_recheck.py \
  tests/research/test_explicit_ticker_semantic_source_probe.py \
  tests/research/test_explicit_ticker_role_header_probe.py \
  tests/e2e/test_question_compiler.py
```

## 13. Agent 4 — tích hợp và E2E r9 (2026-08-27)

Agent 4 đã kiểm tra độc lập Git/worktree, E2E v8, hash manifest V2/V3 và artifact Agent 1–3. Không reset, checkout hoặc xóa thay đổi dirty hiện có. Kết quả này là snapshot mới nhất tại `2026-08-27T12:02:45Z` UTC, HEAD `6be0c82f7a3bc01eb6b98b7825a4a451ffae2be6`.

### Quyết định tích hợp

- Tích hợp candidate-only Q156, Q263, Q340 sau khi replay được tọa độ/hash V2/V3.
- Giữ Q70 làm baseline unchanged sau regression replay.
- Đưa Q98, Q104, Q185, Q242, Q357 về quarantine.
- Không tạo `human_verified`, không tạo semantic approval, không cấp evidence/answer/release/submission.

Union packet: `artifacts/research/agent4_candidate_union_v1_20260827_r2/`.

| Artifact | SHA-256 |
| --- | --- |
| `period_column_candidate_packets_v1.jsonl` | `8bd461a50d7a589b674050a4b5b81ee97949f363904d89e42f658dd111c2dd9e` |
| `period_column_candidate_packets_v1.manifest.json` | `7856584e49dbde1f8a6f303eb192abbf1fa6b437492aae66fa0899d59c16d046` |
| `integration_before_after_v1.jsonl` | `586c8dab943bff16e3986fd487feed9a6a0f352da9b4456a8cb4de5f41907703` |
| `quarantine_ledger_v1.jsonl` | `b088b73f190ee4ad714cf56ceff61c1fa93965757e27bf76924ea9661ba7eb5f` |

Union thay đổi `unique=65 → 68`; `packet_blocked=931 → 928`; ambiguous `9` và no-period `7` không đổi. V2 `tables_structured_v2.jsonl` hash `583e18bbeac02b484d666eb45f45170b45b92d127ccc4934c213d16c7db4c749`; V3 context hash `e7b69c5ee3f4a7ffd8b1c81034f50021a379372da464f80ad809884be444d472`, manifest `a9dff3d3aed8423971b0eca591e7d98c6f8984b78de72cd4a7d198c80889bdf1`.

### Bảng blocker sau tích hợp

| Nhóm | Hiện trạng r9 | Lý do chưa thể khắc phục bằng dữ liệu hiện tại |
| --- | --- | --- |
| Route | `460` packet route-incomplete; `949` execution route-incomplete | Route overlay chưa materialize đủ stage/operand exact; retrieval/dense score không phải quyền bind. |
| Period | `853` unresolved; Q156/Q263 table function chưa authorizing; Q340 as-of date không duy nhất | Exact cell/Decimal replay chỉ chứng minh tái lập kỹ thuật, chưa chứng minh period semantic. |
| Provenance | Q98 document provenance; Q104 duplicate provenance; Q185/Q242/Q357 header lineage conflict | Không có nguồn độc lập hiện hành để hòa giải; candidate lỗi phải quarantine. |
| Entity/variable | Evidence entity/variable chưa pass | Ticker alias/metric label không phải evidence và không thể mở khóa bằng arithmetic. |
| Human semantic gate | Queue và decisions đều rỗng, `human_verified=0` | Đây là điều kiện fail-closed bắt buộc; Agent 4 không được tự xác nhận semantics. |

### Receipt E2E mới

R9: `artifacts/runs/e2e-explicit-ticker-candidate-replay-20260827-agent4-r9/grounded_e2e_run_v1.json`, SHA-256 `55c38e960ec54867466545fce9c1e18a87c0a4bd7d1459ca3780f78ceec3d4d5`.

- `run_id=ad8bdec7173b17afe99b89460220944f6204ab05e265700bfcad92bd54306fce`.
- `run_status=complete_research_only`.
- Exact binding: `58 ready`, `494 blocked`, `460 route-incomplete`.
- Execution: `58 replay-ready`, `5 binding-conflict`, `949 route-incomplete`.
- Authorization: `blocked`; evidence `884 BLOCKED`; answer `1012 ABSTAIN`; release `false`.
- `reviewer_inputs_used=[]`, `machine_semantic_binding_count=0`.

Semantic queue rỗng: `artifacts/research/agent4_semantic_queue_v1_20260827_r1/`. Queue và blank decisions cùng SHA-256 `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`; manifest SHA-256 `0d79c7443a521eb68d883f00e9a17264f76386eae68147e7314fed735b582957`.

Chi tiết bảng trước/sau, artifact Agent 1–3, test report và điều kiện submission nằm trong `docs/research/AGENT4_INTEGRATION_REPORT_V1.md`, `docs/research/AGENT4_TEST_REPORT_V1.md` và `docs/research/AGENT4_RESEARCH_CONCLUSION_V1.md`.
