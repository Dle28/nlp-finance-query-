# Bounded direct-row extension: operating-lease totals

Ngày: 2026-08-31  
Trạng thái: local best-effort candidate; chưa có official leaderboard score.

## 1. Mục tiêu

Vòng này nhắm vào một failure family direct lookup cụ thể: câu hỏi hỏi tổng
cam kết/tiền thuê tối thiểu của **cho thuê hoạt động**, trong khi full
structured asset đã có bảng đúng nhưng resolver cũ không nhận diện được
heading ngữ cảnh hoặc chỉ nhận row tổng quát `TỔNG CỘNG`, không nhận standalone
`Cộng`. Ba câu trong population hiện bị `fallback_zero` dù source table có
cell phù hợp: Q37 (GEX), Q125 (VIF) và Q128 (IJC).

Đây là một route hẹp, không phải nới global semantic ranking. Variant chỉ
được mở khóa khi câu hỏi có một ticker, một năm, scope `separate`, và source
table cùng document/year/scope có đúng ngữ cảnh cho thuê hoạt động và row
tổng. Numeric output vẫn được hydrate từ coordinate replay của builder.

## 2. Contract fail-closed

Variant thay đổi bản sao snapshot của
`src/finance_query/e2e/core/source_first_lookup.py` với hai trường hợp:

1. `cam ket cho thue hoat dong` trong cả question và source context, cùng row
   `tong`/`tong cong`, bind vào `rent_commitment_total`. Việc yêu cầu đúng cụm
   `cho thue` phân biệt schedule công ty **đang cho thuê** với heading
   `Cam kết thuê hoạt động` của schedule công ty **đang thuê**; và
2. câu hỏi có đủ `tien thue`, `toi thieu`, `thue hoat dong`, source context có
   cùng các token, và row là `cong`/`tong`/`tong cong`. Điều này bao phủ bảng
   có row tổng standalone `Cộng` như Q128.

Contract vẫn yêu cầu exact ticker/year/scope, current-period column và source
coordinate replay. Route không đọc answer gold, không dùng model answer để
chọn cell, không suy ra answer từ Question-ID, và không biến replay thành
semantic approval.

## 3. Controlled A/B và fingerprints

Control và variant dùng cùng questions, review bundle, full structured asset,
replay artifact, source-line map, candidate-validity model và builder. Chỉ
khác source-first lookup snapshot.

| Input | Fingerprint / giá trị |
|---|---|
| builder snapshot | `d1edacca286318c6c5d5011b8486a66dc73951aa730f3f762e3e5dffa1e56dd8` |
| control source lookup | `72fd26a0d4ab715ec883811e4038aebd854d540b77fed781658701ce432f2bc9` |
| variant source lookup | `9d5fb7c58b3c1bc4d498d28b2954124c11f74104d57f9667e1eddcaf51da4dd6` |
| full structured asset (146,246 tables) | `617ae044499907cf302519b7ff6fb96fb86ab70ff08e6fdcb0e6a3f8df5251f7` |
| source-line map (146,246 entries) | `533cc779f9565f43dfc961ad03d7bdff65adbd3728ee7a60b61651d495c4c615` |
| candidate-validity model | `19fe579a16c480efa0097e39fa624b600a943bf72b48f164378a073de53a155a` |

Control:

`artifacts/runs/vifinqa_answer_optimization_20260830/operating_lease_total_ab_v1/control/submission/`

Variant:

`artifacts/runs/vifinqa_answer_optimization_20260830/operating_lease_total_ab_v1/variant/submission/`

Cả hai report đều ghi `changed_during_build=false`; builder fingerprint giữ
nguyên. Vì vậy numeric diff quan sát được được quy về route variant trong
controlled run, dù vẫn chưa thể quy đổi thành accuracy score khi không có
gold/scorer.

## 4. Full-population A/B

| Metric local | Control | Variant | Delta |
|---|---:|---:|---:|
| `source_first_exact_row_v1` selected | 78 | 81 | +3 |
| `fallback_zero` | 44 | 41 | -3 |
| predicted questions | 968 | 971 | +3 |
| non-zero answers | 955 | 958 | +3 |
| records | 1,012 | 1,012 | 0 |
| query replay | 1,012 | 1,012 | 0 |
| validation errors | 0 | 0 | 0 |

Diff answer/tier duy nhất giữa hai submission là Q37, Q125 và Q128:

| Q | Control | Variant | Requested unit |
|---:|---|---|---|
| 37 | `0` (`fallback_zero`) | `201.004296672` (`source_first_exact_row_v1`) | tỷ đồng |
| 125 | `0` (`fallback_zero`) | `0.84052528171` (`source_first_exact_row_v1`) | trăm tỷ đồng |
| 128 | `0` (`fallback_zero`) | `27.35` (`source_first_exact_row_v1`) | tỷ đồng |

Control ZIP SHA-256:
`df0a3a492dc98fd6bbe3ca8dfe981b8d539092bee399a7a3e33ccff3bc994013`.

Variant ZIP SHA-256:
`8bf3b40c921511d1acd489676f91662a8149fef10fd5f0ca6f3a2e1794d3fb0d`.

## 5. Independent source replay

Audit độc lập kiểm tra lại full table asset, document/ticker/year/scope, UID,
source-line map, source file hash, row/column, raw cell, header/current-period
column và Decimal/unit transform.

| Q | Source table / UID | Source line-map | Row, col | Raw current-period cell | Unit / Decimal calculation |
|---:|---|---:|---:|---:|---|
| 37 | `GEX_financial_statements_2018_separate` / `58e7885c1fdd7c577ef58ce528b2fe263a3ba8a54a36c9fc6fd6f81b36330523` | 1837 | 5, 1; `TỔNG CỘNG` | `201.004.296.672` VND | `201004296672 / 1e9 = 201.004296672` |
| 125 | `VIF_financial_statements_2024_separate` / `4efbe1fc0af1de200cbeab96ef6b974ed58fbbf473c18d1425ffa40d6d589999` | 1801 | 4, 1; `TỔNG CỘNG` | `84.052.528.171` VND | `84052528171 / 1e11 = 0.84052528171` |
| 128 | `IJC_financial_statements_2015_separate` / `f5815b68a6b5c84328b9ce454f6d266680b4c1da504a52be62f3c711e2d1bc73` | 1604 | 3, 1; `Cộng` | `27.350.000.000` | `27350000000 / 1e9 = 27.35` |

Source file SHA-256 cũng khớp asset cho cả ba document:

- GEX: `3a7a16664bf624f15f5f746e733fc404c5c66450ef82556adc62329cc1bdcd11`;
- VIF: `bef127c875876559c788612d4d1d1fe10f2cca62f4dfd9d5477f5f5e03e7dcae`;
- IJC: `b89056261411857fdeb531c5fb60935b75a990266aaa65fc7b8fae72e1ce11c1`.

GEX line 1835 bind cụm `cho thuê văn phòng`; schedule ngay sau bảng bắt đầu
bằng `Cam kết thuê hoạt động` và `thuê đất`, nên không được chọn cho Q37.
VIF line 1799 bind `cho thuê tài sản`. IJC line 1602 bind đúng câu hỏi về
`thanh toán tiền thuê tối thiểu ... thu được`; bảng không có `unit_hint`, nên
audit ghi nhận anchor `VND` ở line 1606 cùng note. Đây là residual semantic
risk của Q128, không phải strict unit certificate.

Kết quả: `3/3` source-coordinate checks PASS, replay full population PASS,
nhưng cả ba proposal vẫn là `PARTIAL`/best-effort và chưa `VERIFIED`.

## 6. Materialized candidate overlay

Ba dòng được copy explicit trên candidate đã có 60 route overrides:

`artifacts/runs/vifinqa_answer_optimization_20260830/integrated_route_overlay_signed_lease_argmax_taxpaid_relatedparty_interest_direct_q994_subsidiary_conditional_lease_v1/`

ZIP:

`artifacts/runs/vifinqa_answer_optimization_20260830/integrated_route_overlay_signed_lease_argmax_taxpaid_relatedparty_interest_direct_q994_subsidiary_conditional_lease_v1.zip`

Materializer report:

- `replacement_count=3`, `inherited_replacement_count=60`,
  `total_replacement_count=63`;
- route `source_first_exact_row_v1` cho Q37, Q125, Q128;
- `new_numeric_arithmetic_invented=false`;
- 1,012 records, 1,012 replay, `errors=[]`;
- ZIP có 1,012 CSV members và `submission.json`; và
- ZIP SHA-256:
  `e360db42eaae7abce7473891ef398825eb55ee8a32573658a5c087dc903be73f`.

Adapter materializer được mở explicit contract cho ba ID tại
`scripts/research/materialize_validated_route_overlay_v1.py:116` đến `:118`.
Manifest giữ `human_verified=false` và `promotion_allowed=false`.

## 7. Decision và giới hạn score

Giữ family này trong authorized best-effort candidate lane vì nó thay đúng ba
fallback zero bằng source-coordinate replay có raw value và Decimal transform
rõ ràng. Không gọi đây là `Answer Accuracy +3`, `Execution Accuracy +3`, hay
official leaderboard gain. Con số +3 chỉ là số answer rows đổi trong local
controlled A/B; score thật cần upload cùng split hoặc có gold/scorer độc lập.

Regression focused cho source-first/submission integration: `117 passed`.
Full suite hiện tại: `636 passed, 1 skipped, 8 failed`; tám failure là test
integration nền bị chặn bởi thiếu
`finance_query.e2e.core.candidate_plan_selector`, không liên quan route lease
và đã giữ nguyên để không chạm nhánh khác.

## 8. Next queue

Tiếp tục theo family, ưu tiên direct rows còn đang `fallback_zero` hoặc
semantic value rõ ràng sai scale nhưng có source context độc lập. Nhóm cash
/cash-equivalents và payable-note context sẽ chỉ được mở sau khi tách được
scope, kỳ, currency/unit và duplicate schedule; không nới global top-k hay
semantic fallback theo Question-ID.
