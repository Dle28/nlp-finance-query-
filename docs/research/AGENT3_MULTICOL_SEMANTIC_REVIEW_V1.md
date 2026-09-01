# Agent 3 — semantic review Q156/Q242/Q263

Ngày kiểm tra: 2026-08-27. Phạm vi là nghiên cứu value-blind cho bảng nhiều cột. Báo cáo này không phải answer, submission, human approval hay evidence authorization.

## Kết luận

| Câu | Trạng thái | Bảng V2 | Ô đề xuất | Cột được xác định | Blocker còn lại |
|---|---|---|---|---|---|
| Q156 | `CANDIDATE_ONLY_READY` | BVH separate, ordinal 58, 9x5 | row 8, column 4 | `Tổng cộng` trên ba bucket chất lượng tín dụng | Không có gate cấu trúc bị fail; đổi đơn vị để Agent 4 replay |
| Q242 | `QUARANTINED` | MBB separate, ordinal 48, 16x4 | row 15, column 3 | `Tổng cộng` của tài sản vô hình | `HEADER_PROVENANCE_NOT_RECONCILED`; V3 canonical header trỏ vào dữ liệu |
| Q263 | `CANDIDATE_ONLY_READY` | SSI separate, ordinal 27, 27x5 | row 26, column 2 | `Số cuối năm · Giá trị hợp lý` | Không có gate cấu trúc bị fail; đổi đơn vị để Agent 4 replay |

`CANDIDATE_ONLY_READY` chỉ có nghĩa là packet cấu trúc đủ để Agent 4 xem xét. Cả ba câu vẫn có `answer_eligible=false` và `submission_eligible=false`.

Các role được áp dụng theo từng bảng, không ép một schema chung lên mọi bảng: kỳ báo cáo của Q156/Q242 nằm trong heading/context, còn Q263 có thêm hai nhóm kỳ ngay trong header; loại tài sản nằm ở row label của Q156/Q263 và ở các cột lớp tài sản của Q242; Q156 dùng bucket chất lượng tín dụng thay vì giá mua/giá hợp lý; Q242 dùng row block `Nguyên giá` và `Giá trị còn lại`; chỉ Q263 có cặp measurement `Giá gốc` (purchase/book-cost basis) và `Giá trị hợp lý`. `Tổng cộng` được xác định riêng theo từng bảng, không suy đoán bằng phép cộng.

## Nguồn và cách đọc

Đã đọc toàn bộ grid V2 của từng bảng đích, toàn bộ `cell_provenance`, `header_row_indices`, `column_labels`, context V3 và các row profile; không chỉ đọc candidate preview. Raw OCR được đọc quanh `char_start` và kéo qua toàn bộ thẻ `<table>...</table>` cùng phần heading/footnote liên quan. Builder kiểm tra source SHA-256 khớp provenance V2, boundary của bảng và các marker ngữ nghĩa trước khi tạo packet.

| Câu | V2 source provenance | Marker raw OCR đã kiểm tra |
|---|---|---|
| Q156 | `data/ViFinQA/financial_statements/BVH/2018/BVH_financial_statements_2018_separate/BVH_financial_statements_2018_separate_extracted.txt`, `char_start=141624` | phân loại chất lượng tín dụng, ngày 31/12/2018, đơn vị VND, Tổng cộng |
| Q242 | `data/ViFinQA/financial_statements/MBB/2021/MBB_financial_statements_2021_separate/MBB_financial_statements_2021_separate_extracted.txt`, `char_start=110232` | tài sản cố định vô hình, biến động năm 2021, Giá trị còn lại, Tại ngày cuối năm, Tổng cộng triệu đồng |
| Q263 | `data/ViFinQA/financial_statements/SSI/2019/SSI_financial_statements_2019_separate/SSI_financial_statements_2019_separate_extracted.txt`, `char_start=110010` | note 7.1 FVTPL, đơn vị VND, Số cuối năm/Số đầu năm, Giá trị hợp lý, Tổng cộng |

Không có số liệu ô thô, dòng raw hay giá trị numeric nào được ghi vào candidate packet. Chỉ ghi coordinate, header provenance, source hash và hash của source cell để replay độc lập.

## Column-role và parent-row diagnostic

### Q156 — BVH, rủi ro tín dụng

- Cột 0 là row label.
- Cột 1 là tài sản chưa quá hạn và không giảm giá trị.
- Cột 2 là tài sản quá hạn nhưng không giảm giá trị riêng lẻ.
- Cột 3 là tài sản bị giảm giá trị riêng lẻ.
- Cột 4 là `Tổng cộng`, tức tổng ngang qua các bucket chất lượng tín dụng; không phải một bucket riêng.
- Kỳ là instant context của bảng tại 31/12/2018, không phải cột kỳ trong grid. Đơn vị nguồn là VND; yêu cầu đầu ra là tỷ đồng nhưng conversion chưa thực hiện.
- Các dòng cha đã xác nhận: `Đầu tư nắm giữ đến ngày đáo hạn` và `Các khoản phải thu`; row `Tổng` là grand total của toàn bộ tài sản tài chính liên quan rủi ro tín dụng.
- Header V2 row 0 và V3 canonical header cùng trỏ đến cột 4; provenance của target là V2 row 0/cell 4.

Giả thuyết:

1. `Q156-H1`: bảng phân loại chất lượng tín dụng, row `Tổng`, cột `Tổng cộng` — được hỗ trợ bởi heading note 27.2.1, câu mô tả phân loại chất lượng, raw header và row/column provenance.
2. `Q156-H2`: bảng cùng mẫu nhưng tại 31/12/2017 — bị loại vì raw OCR xác nhận đây là bảng liền kề của kỳ 2017; table UID và period không trùng kỳ yêu cầu.
3. `Q156-H3`: bảng phân tích maturity/liquidity có cột `Tổng cộng` — bị loại vì heading nói về thời gian đáo hạn, cấu trúc cột khác và đơn vị là triệu đồng, không phải bảng phân loại chất lượng tín dụng nguồn VND.

### Q242 — MBB, giá trị còn lại tài sản vô hình

- Cột 0 là row label.
- Cột 1 là nhóm quyền sử dụng đất.
- Cột 2 là nhóm phần mềm máy vi tính.
- Cột 3 là `Tổng cộng` của hai nhóm tài sản vô hình.
- Row parent semantic là `Giá trị còn lại`; target child là `Tại ngày cuối năm`, tức closing net book value. Các block khác là `Nguyên giá` và `Giá trị hao mòn lũy kế`, không được dùng thay thế.
- Kỳ raw context là 31/12/2021; metadata document thiếu `reporting_period_end`, nên đây vẫn là một risk không authorizing dù raw heading đã xác nhận kỳ. Đơn vị là triệu đồng và không cần conversion.
- Raw V2 row 0 là header rõ ràng và provenance của target trỏ row 0/cell 3. Tuy nhiên `header_row_indices` được khai báo là `[2,3,4]`, còn V3 canonical header của target lại lấy chuỗi dữ liệu số dư từ các row dữ liệu thay vì header row 0. Vì vậy cột có thể hiểu được bằng raw, nhưng header provenance giữa các lớp chưa hợp nhất.

Giả thuyết:

1. `Q242-H1`: row `Tại ngày cuối năm` dưới parent `Giá trị còn lại`, cột `Tổng cộng` — được raw table và parent/child structure hỗ trợ, nhưng chưa materialize vì conflict V2/V3.
2. `Q242-H2`: cùng cấu trúc nhưng bảng năm 2020 — bị loại vì raw OCR ngay sau bảng đích xác nhận heading năm 2020 và table ordinal khác.
3. `Q242-H3`: lấy row `Số dư cuối năm` trong block `Nguyên giá` hoặc `Giá trị hao mòn lũy kế` — bị loại vì đó là cost/gross hoặc accumulated-depreciation block, không phải parent `Giá trị còn lại`.

### Q263 — SSI, FVTPL

- Cột 0 là row label.
- Cột 1 là `Số cuối năm · Giá gốc`.
- Cột 2 là `Số cuối năm · Giá trị hợp lý`.
- Cột 3 là `Số đầu năm · Giá gốc`.
- Cột 4 là `Số đầu năm · Giá trị hợp lý`.
- Row `Tổng cộng` là grand total của note 7.1 FVTPL. Hai header rows được provenance hóa: row 0 span kỳ, row 1 span measurement basis. Target là row 26/column 2.
- `Cuối năm` được bind với cột fair value closing; yêu cầu `nghìn tỷ đồng` chưa được convert từ VND.

Giả thuyết:

1. `Q263-H1`: row `Tổng cộng`, cột `Số cuối năm · Giá trị hợp lý` — được hỗ trợ bởi note 7.1 FVTPL, two-tier header provenance, row total và raw table context.
2. `Q263-H2`: row tổng nhưng cột `Số cuối năm · Giá gốc` — bị loại vì đúng kỳ nhưng sai measurement basis; `Giá gốc` không phải `Giá trị hợp lý`.
3. `Q263-H3`: row tổng, fair value nhưng cột `Số đầu năm` — bị loại vì đúng measurement basis nhưng sai period role; opening không phải closing.

## Artifact research-only

Artifact canonical là:

- [diagnostic JSONL](/home/dungle/Documents/AI_guru/artifacts/research/multicol_semantic_diagnostic_v1_20260827_r2/multicol_semantic_diagnostic_v1.jsonl:1)
- [candidate packets](/home/dungle/Documents/AI_guru/artifacts/research/multicol_semantic_diagnostic_v1_20260827_r2/candidate_packets_v1.jsonl:1)
- [quarantine ledger](/home/dungle/Documents/AI_guru/artifacts/research/multicol_semantic_diagnostic_v1_20260827_r2/quarantine_ledger_v1.jsonl:1)
- [summary](/home/dungle/Documents/AI_guru/artifacts/research/multicol_semantic_diagnostic_v1_20260827_r2/summary.json:1)
- [input/output manifest](/home/dungle/Documents/AI_guru/artifacts/research/multicol_semantic_diagnostic_v1_20260827_r2/manifest.json:1)

Artifact contract cố định:

- `research_only=true`, `candidate_only=true`.
- `evidence_eligible=false`, `may_authorize_evidence=false`.
- `may_materialize_answer=false`, `may_execute_formula=false`, `may_select_final_column=false`.
- `human_verified` không xuất hiện.
- Không có answer/submission/raw numeric value trong candidate hoặc quarantine.

## Regression test

Fixture regression kiểm tra cả đường candidate và đường quarantine khi V3 header conflict:

```text
rtk env PYTHONPATH=.:src .venv/bin/pytest -q tests/research/test_multicol_semantic_diagnostic.py
2 passed
```

Artifact thật đã được validate với input/output hashes và coverage chính xác Q156/Q242/Q263:

```text
rtk env PYTHONPATH=.:src .venv/bin/python scripts/research/validate_multicol_semantic_diagnostic_v1.py \
  --artifact-dir artifacts/research/multicol_semantic_diagnostic_v1_20260827_r2
PASS; candidate=[156,263]; quarantine=[242]; answer_eligible=false; submission_eligible=false
```

## Đề xuất tích hợp cho Agent 4

1. Chỉ đọc candidate packet Q156/Q263 từ artifact r2 sau khi verify manifest và tự reload đúng V2 UID, raw source SHA, table SHA, row/column coordinate và source-cell hash.
2. Không đọc Q242 từ candidate ledger. Chỉ dùng quarantine record để mở một task sửa độc lập cho header extraction/V3 canonicalization; khi chưa có receipt provenance mới thì giữ `BLOCKED`.
3. Sau khi replay source cell độc lập, Agent 4 mới có thể thực hiện Decimal conversion VND → đơn vị câu hỏi. Conversion không được biến research packet thành evidence hoặc answer.
4. Việc semantic authorization, certificate/release gate và answer path vẫn thuộc contract production hiện hành; sidecar này không được import ngược vào `finance_query.e2e`.

Rủi ro còn lại: Q242 thiếu document-level `reporting_period_end` và conflict header phải được sửa/replay; Q156/Q263 vẫn chưa thực hiện unit conversion hoặc semantic authorization; mọi câu hiện vẫn nằm ngoài answer/submission path.
