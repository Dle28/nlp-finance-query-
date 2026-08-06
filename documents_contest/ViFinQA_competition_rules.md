# QUY ĐỊNH CUỘC THI ViFinQA

## 1. Tên bài toán

**Financial Table Retrieval & Text-to-Pandas Query Generation**

Mục tiêu của cuộc thi là xây dựng hệ thống AI có khả năng:

1. Hiểu câu hỏi tài chính bằng tiếng Việt.
2. Xác định đúng công ty, năm, loại báo cáo và bảng dữ liệu liên quan.
3. Trích xuất hoặc chuẩn hóa bảng dữ liệu từ báo cáo tài chính.
4. Sinh câu lệnh `pandas` có thể thực thi lại.
5. Tính đúng đáp án số.
6. Cung cấp đầy đủ tài liệu, bảng và dữ liệu bằng chứng.

---

## 2. Bối cảnh dữ liệu

Bộ dữ liệu ViFinQA gồm:

- **1.012 câu hỏi tài chính bằng tiếng Việt**.
- **1.973 báo cáo tài chính OCR**.
- **100 công ty niêm yết**.
- Giai đoạn báo cáo từ **2015 đến 2025**.
- Báo cáo được lưu dưới dạng file `.txt`.
- Nội dung có thể chứa văn bản OCR và bảng ở dạng HTML inline.

Cấu trúc dữ liệu:

```text
ViFinQA/
├── code_stock.csv
├── questions/
│   └── questions.jsonl
└── financial_statements/
    └── TICKER/
        └── YEAR/
            └── DOCUMENT/
                └── DOCUMENT_extracted.txt
```

### 2.1 `code_stock.csv`

Dùng để ánh xạ mã chứng khoán với tên công ty.

Ví dụ:

```csv
Mã CK,Tên công ty
HPG,CTCP Tập đoàn Hòa Phát
VCB,Ngân hàng TMCP Ngoại thương Việt Nam
```

### 2.2 `questions/questions.jsonl`

Mỗi dòng có cấu trúc:

```json
{
  "id": 1,
  "question": "Lãi tiền gửi năm 2018 của công ty mẹ CTCP Hàng không Vietjet (VJC) là bao nhiêu triệu đồng?"
}
```

Dataset công khai không cung cấp:

- Đáp án chuẩn.
- Gold document.
- Gold table.
- Gold evidence.
- Gold pandas query.
- Nhãn độ khó.
- Train/dev/test split chính thức.

### 2.3 `financial_statements/`

Mỗi báo cáo nằm trong một đường dẫn có dạng:

```text
financial_statements/<TICKER>/<YEAR>/<DOCUMENT>/<DOCUMENT>_extracted.txt
```

Tên báo cáo thường chứa một trong các loại:

- `consolidated`: báo cáo tài chính hợp nhất.
- `separate`: báo cáo tài chính riêng hoặc công ty mẹ.
- `aggregated`: báo cáo tổng hợp.
- Một số file khác có tên chung hoặc không xác định rõ loại.

---

## 3. Nhiệm vụ của hệ thống

Hệ thống cần giải quyết hai nhiệm vụ chính.

### 3.1 Table Retrieval

Xác định đúng các bảng có chứa một phần hoặc toàn bộ dữ liệu cần thiết để trả lời câu hỏi.

Hệ thống cần nhận diện:

- Công ty hoặc mã chứng khoán.
- Năm hoặc thời điểm báo cáo.
- Phạm vi báo cáo:
  - Công ty mẹ / riêng.
  - Hợp nhất.
  - Không xác định rõ.
- Chỉ tiêu tài chính.
- Các điều kiện bổ nghĩa:
  - Ngành.
  - Đối tác.
  - Công ty con.
  - Cá nhân.
  - Tài sản.
  - Công trình.
  - Kỳ hạn.
  - Loại tiền.
  - Nhóm hoặc loại khoản mục.
- Đơn vị được yêu cầu.

Một câu hỏi có thể cần một hoặc nhiều bảng.

### 3.2 Text-to-Pandas

Từ các bảng đã truy hồi, hệ thống phải:

1. Chuẩn hóa bảng thành file CSV.
2. Gán mỗi bảng cho một biến DataFrame.
3. Sinh câu lệnh `pandas`.
4. Thực thi câu lệnh.
5. Trả về đúng kết quả.
6. Bảo đảm câu lệnh có thể chạy lại được.

---

## 4. Dữ liệu ngoài và mô hình được phép sử dụng

### 4.1 Dữ liệu ngoài

Được phép sử dụng dữ liệu ngoài, nhưng phải:

- Trích dẫn rõ nguồn.
- Cung cấp thông tin để Ban Tổ chức kiểm tra.
- Chỉ sử dụng dữ liệu hợp pháp.

### 4.2 Mô hình ngôn ngữ

Được phép sử dụng mô hình huấn luyện trước và LLM nếu thỏa mãn đồng thời:

- Mô hình được công khai.
- Có thể lấy từ Hugging Face hoặc nguồn tương tự.
- Được phát hành trước ngày **01/06/2026**, theo giờ Việt Nam.
- Kích thước không vượt quá **14B tham số**.
- Có thể mô tả rõ cách tải và sử dụng để tái lập kết quả.

Không được sử dụng mô hình đóng trong pipeline chính thức, ví dụ:

- GPT-4o.
- Gemini.
- Các mô hình đóng tương tự.

---

## 5. Phương pháp đánh giá

Hệ thống được đánh giá theo ba nhóm tiêu chí:

1. Truy hồi thông tin.
2. Độ chính xác đáp án.
3. Độ chính xác thực thi pandas query.

Các chỉ số được tính theo **macro-average**, tức là tính cho từng câu hỏi rồi lấy trung bình.

---

## 6. Đánh giá truy hồi thông tin

### 6.1 Precision

```text
Precision =
Số bảng truy hồi đúng
/
Tổng số bảng đã truy hồi
```

Precision được tính riêng cho từng câu hỏi rồi lấy trung bình.

### 6.2 Recall

```text
Recall =
Số bảng truy hồi đúng
/
Tổng số bảng liên quan thực tế
```

Recall được tính riêng cho từng câu hỏi rồi lấy trung bình.

### 6.3 F2-score

```text
F2 = (5 × Precision × Recall) / (4 × Precision + Recall)
```

F2 ưu tiên Recall nhiều hơn Precision.

Điều này có nghĩa:

- Bỏ sót bảng đúng bị phạt mạnh.
- Truy hồi dư một số bảng có thể ít nghiêm trọng hơn bỏ mất bảng cần thiết.
- Tuy nhiên truy hồi quá nhiều bảng vẫn làm giảm Precision.

---

## 7. Đánh giá đáp án

### Answer Accuracy

```text
Answer Accuracy =
Số câu có kết quả khớp đáp án chuẩn trong ngưỡng sai số
/
Tổng số câu hỏi
```

Hệ thống phải bảo đảm:

- Đúng giá trị.
- Đúng đơn vị.
- Đúng kỳ báo cáo.
- Đúng công ty.
- Đúng phạm vi riêng hoặc hợp nhất.

---

## 8. Đánh giá pandas query

### Execution Accuracy

```text
Execution Accuracy =
Số câu có code chạy được và cho kết quả đúng
/
Tổng số câu hỏi
```

Một query chỉ được coi là đúng khi:

1. Code thực thi được.
2. Các DataFrame tham chiếu tồn tại.
3. Các cột và hàng được truy cập hợp lệ.
4. Kết quả trả về đúng đáp án.

---

## 9. Định dạng file kết quả

Mỗi bài nộp phải có một file JSON duy nhất.

Cấu trúc:

```json
[
  {
    "id": 1,
    "question": "Doanh thu thuần của Công ty CP Sữa Việt Nam (VNM) năm 2023 là bao nhiêu?",
    "answer": 63075000000.0,
    "relevant_docs": [
      "VNM_financial_statements_2023_consolidated"
    ],
    "relevant_tables": [
      "VNM_financial_statements_2023_consolidated|350"
    ],
    "evidence": [
      {
        "variable": "df1",
        "csv_path": "data/VNM_financial_statements_2023_consolidated_table_350.csv"
      }
    ],
    "pandas_query": "float(df1.loc[df1['item'] == 'Doanh thu thuần', '2023'].iloc[0])"
  }
]
```

---

## 10. Giải thích từng trường

### `id`

- Kiểu: integer.
- Mã định danh của câu hỏi.
- Phải giữ nguyên theo bộ test.

### `question`

- Kiểu: string.
- Nội dung câu hỏi gốc.
- Phải giữ nguyên.

### `answer`

- Kiểu: float.
- Kết quả cuối cùng sau khi thực thi `pandas_query`.
- Phải đúng đơn vị câu hỏi yêu cầu.

### `relevant_docs`

- Kiểu: danh sách string.
- Chứa ID của các báo cáo liên quan.
- ID lấy từ tên thư mục hoặc tên file báo cáo, bỏ phần mở rộng `.txt`.

Ví dụ đường dẫn:

```text
financial_statements/VJC/2018/VJC_financial_statements_2018_separate/VJC_financial_statements_2018_separate_extracted.txt
```

Document ID:

```text
VJC_financial_statements_2018_separate
```

### `relevant_tables`

- Kiểu: danh sách string.
- Mỗi phần tử có dạng:

```text
<document_id>|<table_position>
```

Ví dụ:

```text
VJC_financial_statements_2018_separate|350
```

### `evidence`

- Kiểu: danh sách object.
- Chứa các CSV thực sự được sử dụng trong `pandas_query`.

Mỗi evidence gồm:

```json
{
  "variable": "df1",
  "csv_path": "data/table.csv"
}
```

Yêu cầu:

- `variable` phải là tên biến Python hợp lệ.
- Không được trùng trong cùng một câu hỏi.
- `variable` phải xuất hiện trong `pandas_query`.
- `csv_path` phải bắt đầu bằng `data/`.
- File CSV phải tồn tại trong ZIP.

### `pandas_query`

- Kiểu: string.
- Là câu lệnh pandas có thể chạy lại.
- Phải sử dụng đúng các biến khai báo trong `evidence`.
- Phải trả về đúng đáp án.

---

## 11. Quy tắc về đơn vị

Đơn vị trong báo cáo có thể là:

- Đồng.
- Nghìn đồng.
- Triệu đồng.
- Tỷ đồng.
- Trăm tỷ đồng.
- Nghìn tỷ đồng.
- Phần trăm.
- Cổ phần.
- Đơn vị khác.

Nếu đơn vị bảng khác đơn vị câu hỏi, `pandas_query` phải thực hiện chuyển đổi.

Ví dụ:

Nguồn là triệu đồng, câu hỏi yêu cầu tỷ đồng:

```python
value_billion = value_million / 1000
```

Nguồn là đồng, câu hỏi yêu cầu triệu đồng:

```python
value_million = value_vnd / 1_000_000
```

Nguồn là tỷ đồng, câu hỏi yêu cầu triệu đồng:

```python
value_million = value_billion * 1000
```

Không được đổi đơn vị chỉ trong trường `answer` mà không thể hiện logic trong `pandas_query`.

---

## 12. Quy tắc về kỳ báo cáo

Hệ thống phải phân biệt:

### Trong kỳ

Ví dụ:

```text
trong năm 2023
năm 2023
```

Thường tương ứng với các cột:

```text
Năm nay
Kỳ này
2023
```

### Cuối kỳ

Ví dụ:

```text
cuối năm 2023
đến ngày 31/12/2023
```

Thường tương ứng với:

```text
Cuối năm
31/12/2023
```

### Đầu kỳ

Ví dụ:

```text
đầu năm 2023
ngày 01/01/2023
```

Thường tương ứng với:

```text
Đầu năm
01/01/2023
31/12/2022
```

Không được tự động coi mọi cột `Năm trước` là đầu kỳ mà không xét ngữ cảnh bảng.

---

## 13. Quy tắc về báo cáo riêng và hợp nhất

### Báo cáo riêng

Các cụm thường tương ứng:

```text
công ty mẹ
báo cáo riêng
báo cáo tài chính riêng
separate
```

### Báo cáo hợp nhất

Các cụm thường tương ứng:

```text
hợp nhất
toàn tập đoàn
consolidated
```

Nếu câu hỏi không nêu rõ, hệ thống phải giữ trạng thái chưa xác định hoặc sử dụng chiến lược retrieval phù hợp. Không nên mặc định luôn chọn hợp nhất.

---

## 14. Quy tắc về bảng bằng chứng

Mỗi bảng trong `evidence` phải:

- Có nguồn gốc từ báo cáo thật.
- Chứa dữ liệu thực sự được dùng.
- Có đường dẫn CSV hợp lệ.
- Không được tạo dữ liệu giả.
- Không được tham chiếu CSV không tồn tại.
- Không được đưa bảng không liên quan chỉ để tăng Recall một cách vô lý.

Một câu hỏi có thể có nhiều evidence nếu cần nhiều bảng để tính.

Ví dụ ROE có thể cần:

- Lợi nhuận sau thuế.
- Vốn chủ sở hữu đầu kỳ.
- Vốn chủ sở hữu cuối kỳ.

---

## 15. Quy tắc về pandas query

Pandas query phải:

- Là chuỗi Python hợp lệ.
- Có thể chạy lại.
- Chỉ sử dụng các DataFrame được khai báo trong `evidence`.
- Trả về một giá trị cuối cùng.
- Không phụ thuộc vào dữ liệu bên ngoài không được đóng gói.
- Không chứa giá trị đáp án hard-code.

Không hợp lệ:

```python
63075000000.0
```

Không nên dùng vị trí hàng/cột cố định nếu schema có thể thay đổi:

```python
df1.iloc[5, 2]
```

Ưu tiên truy vấn theo tên hàng và cột:

```python
float(
    df1.loc[
        df1["item"] == "Doanh thu thuần",
        "2023"
    ].iloc[0]
)
```

---

## 16. Cấu trúc file ZIP

Bài nộp phải được đóng gói:

```text
submission.zip
├── submission.json
└── data/
    ├── table_1.csv
    ├── table_2.csv
    └── ...
```

Yêu cầu:

- `submission.json` nằm trực tiếp ở thư mục gốc của ZIP.
- `data/` nằm trực tiếp ở thư mục gốc của ZIP.
- Không được đặt tất cả trong một thư mục cha khác.
- ZIP chỉ được chứa một file kết quả `.json`.
- Mọi `csv_path` phải bắt đầu bằng `data/`.
- Tất cả CSV được tham chiếu phải tồn tại.

Cấu trúc sai:

```text
submission.zip
└── my_submission/
    ├── submission.json
    └── data/
```

Cấu trúc đúng:

```text
submission.zip
├── submission.json
└── data/
```

---

## 17. Quy định về số lần nộp

### Public phase

- Tối đa **10 bài mỗi ngày cho mỗi đội**.

### Private phase

- Tối đa **5 bài tổng cộng cho mỗi người dùng**.

Cần chọn bài private cẩn thận vì số lần nộp bị giới hạn mạnh.

Các bài bị thiếu file hoặc thiếu câu sẽ không được đánh giá và không bị tính vào số lần nộp tối đa, theo thông tin cuộc thi đã công bố.

---

## 18. Mốc thời gian

Tất cả thời hạn theo giờ Việt Nam, UTC+07:00.

| Thời gian | Sự kiện |
|---|---|
| 01/08/2026 | Mở public test |
| 31/08/2026, 23:59 | Hạn cuối public test |
| 01/09/2026 | Mở private test |
| 03/09/2026, 23:59 | Hạn cuối private test |
| 06/09/2026 | Công bố kết quả chung cuộc |

---

## 19. Working notes paper

Kết quả cuối cùng chỉ được xem là chính thức khi đội thi nộp bài mô tả phương pháp.

Bài viết cần mô tả đầy đủ:

- Pipeline xử lý dữ liệu.
- Cách trích xuất bảng.
- Cách chuẩn hóa.
- Phương pháp retrieval.
- Phương pháp reranking.
- Mô hình sử dụng.
- Dữ liệu ngoài.
- Cách sinh pandas query.
- Cách kiểm tra và sửa lỗi.
- Cách tải mô hình.
- Cấu hình để tái lập kết quả.

---

## 20. Quy định về tính tái lập

Hệ thống phải có khả năng chạy lại.

Đội thi nên lưu:

- Phiên bản code.
- Phiên bản mô hình.
- Nguồn tải mô hình.
- Checkpoint.
- Danh sách thư viện.
- Phiên bản Python.
- Seed.
- Cấu hình preprocessing.
- Cấu hình retrieval.
- Cấu hình inference.
- Cấu trúc dữ liệu đầu vào và đầu ra.

---

## 21. Các lỗi có thể làm bài nộp không hợp lệ

- Thiếu câu hỏi.
- Sai `id`.
- Sai cấu trúc JSON.
- JSON có trailing comma.
- `answer` không phải số.
- `relevant_docs` chứa document không tồn tại.
- `relevant_tables` sai định dạng.
- `csv_path` không bắt đầu bằng `data/`.
- CSV được tham chiếu không tồn tại.
- Biến trong `pandas_query` không có trong `evidence`.
- Query không chạy được.
- ZIP có thêm thư mục cha.
- ZIP chứa nhiều file JSON.
- Sử dụng mô hình không đáp ứng quy định.
- Không công bố dữ liệu ngoài.
- Hard-code đáp án.
- Bịa bảng hoặc nguồn dẫn.

---

## 22. Checklist trước khi nộp

### Kiểm tra dữ liệu

- [ ] Đã có đủ toàn bộ câu hỏi.
- [ ] ID không thiếu và không trùng.
- [ ] Question giữ nguyên.
- [ ] Các document ID tồn tại.
- [ ] Các table position tồn tại.

### Kiểm tra evidence

- [ ] Tất cả CSV tồn tại.
- [ ] Mọi đường dẫn bắt đầu bằng `data/`.
- [ ] Biến DataFrame không trùng nhau.
- [ ] Biến DataFrame xuất hiện trong query.

### Kiểm tra pandas

- [ ] Tất cả query chạy được.
- [ ] Kết quả là scalar.
- [ ] Không trả về `NaN`.
- [ ] Không trả về vô cực.
- [ ] Không hard-code đáp án.
- [ ] Chuyển đổi đơn vị đúng.
- [ ] Chọn đúng năm và kỳ.

### Kiểm tra JSON

- [ ] JSON parse được.
- [ ] Không có trailing comma.
- [ ] `id` là integer.
- [ ] `answer` là float.
- [ ] Các list đúng kiểu dữ liệu.

### Kiểm tra ZIP

- [ ] `submission.json` ở root.
- [ ] `data/` ở root.
- [ ] Chỉ có một file JSON.
- [ ] Không có thư mục cha thừa.
- [ ] Tất cả CSV được tham chiếu đã được đóng gói.

---

## 23. Nguyên tắc triển khai khuyến nghị

Luồng xử lý:

```text
question
→ parse company/year/scope/unit
→ parse metric and qualifiers
→ retrieve documents
→ retrieve top-k tables
→ normalize selected tables
→ bind rows and columns
→ create execution plan
→ generate pandas query
→ execute query
→ validate answer
→ build submission record
→ validate JSON
→ package ZIP
```

Không nên để mô hình ngôn ngữ trực tiếp sinh:

- `answer`.
- `document_id`.
- `table_position`.
- `csv_path`.
- Dữ liệu bảng giả.

Mọi trường phải được kiểm chứng bằng dữ liệu thật và code có thể thực thi lại.

---

## 24. Ghi chú pháp lý và giấy phép dữ liệu

Kho báo cáo gốc được xây dựng từ TiniX Vietnam OCR Annual Financial Statements.

Theo mô tả dataset:

- Giấy phép nội dung nguồn: **CC BY-NC 4.0**.
- Phải ghi nguồn.
- Không được sử dụng trái với điều kiện phi thương mại.
- Cần tôn trọng quyền sở hữu trí tuệ và bảo vệ dữ liệu cá nhân.
- Không nên sử dụng trực tiếp số liệu OCR cho quyết định tài chính quan trọng nếu chưa kiểm tra thủ công.

---

## 25. Nguồn chính

- Dataset ViFinQA trên Hugging Face.
- Repository companion ViFinQA.
- TiniX Vietnam OCR Annual Financial Statements.
- Dashboard cuộc thi AI Guru.
