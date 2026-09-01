# Period-neighbor table navigation ablation (v1, 2026-08-30)

## Kết luận

Đã thêm một lane **opt-in** để truy hồi thêm báo cáo năm `Y+1` khi câu hỏi
hỏi kỳ `Y`. Lane này chỉ bổ sung context cho `relevant_docs` và
`relevant_tables`; nó không được phép đưa candidate năm kế tiếp vào answer
selection, không thay đổi prediction tier, và không mở khóa evidence hoặc
`VERIFIED`.

Kết quả A/B được chấp nhận sau khi chạy lại cùng source-first runtime:

- 1.012/1.012 answer giống hệt baseline.
- 1.012/1.012 prediction tier giống hệt baseline.
- Validation và Decimal pandas-query replay đều pass 1.012/1.012 ở cả hai
  nhánh, `errors=[]`.
- Source-line map giữ đủ 146.246/146.246 entry, không dùng local-ordinal
  fallback.
- Context tăng từ trung bình 2,2490 lên 2,7322 document và từ 3,7174 lên
  4,5158 table/question. Đây là tăng navigation coverage, **chưa phải bằng
  chứng tăng leaderboard score**.

Kiểm tra trên 58 execution-replay-ready target tables hiện có giữ được
53/58 ở cả baseline và ablation (91,38%); ablation chưa tạo thêm target hit
trên tập proxy này. Vì vậy lane được giữ để nghiên cứu tiếp ở trạng thái
opt-in, nhưng chưa được gọi là cải thiện Answer Accuracy, Execution Accuracy,
hay Table F2 chính thức.

Mốc leaderboard gần nhất được ghi trong artifact nghiên cứu trước là r2:
Answer Accuracy `0,1126`, Execution Accuracy `0,1126`, Docs F2-macro `0,6545`
và Tables F2-macro `0,2389`. Đây chỉ là snapshot lịch sử trong workspace;
vòng này chưa có official scorer hoặc gold độc lập để xác nhận một delta mới.

## Protocol

Hai nhánh dùng cùng:

- 1.012 câu hỏi và cùng thứ tự ID;
- full structured-table asset 146.246 bảng;
- full dense index `intfloat/multilingual-e5-small`, 384 chiều;
- cùng replay, research fusion, direct evidence replay và candidate-validity
  model;
- cùng source-line map bắt buộc;
- cùng `--dense-candidate-limit 50`, CPU và batch size 64.

Baseline không bật period neighbor:

```text
artifacts/runs/vifinqa_period_neighbor_ablation_20260830/baseline_v2/submission
```

Ablation bật:

```text
--period-neighbor-offset 1
--period-neighbor-table-slots 2
```

Artifact cuối:

```text
artifacts/runs/vifinqa_period_neighbor_ablation_20260830/neighbor_final_corrected/submission
```

Các run `neighbor_final`, `neighbor_isolated` và `neighbor_gated_v2` không
dùng cho kết luận. Chúng được tạo trước khi cố định thứ tự ưu tiên
exact-year cho câu hỏi nhiều năm, nên có thể làm thay đổi answer pool.

## Kết quả A/B đã kiểm tra

| Chỉ số local | Baseline v2 | Neighbor corrected | Delta |
| --- | ---: | ---: | ---: |
| Question records | 1.012 | 1.012 | 0 |
| Non-zero answers | 989 | 989 | 0 |
| Answer value delta | — | 0 câu | không đổi |
| Prediction tier delta | — | 0 câu | không đổi |
| Verification class | PARTIAL 1.010, UNRESOLVED 2 | PARTIAL 1.010, UNRESOLVED 2 | không đổi |
| Validation replay | 1.012/1.012, errors=[] | 1.012/1.012, errors=[] | không đổi |
| Source-line map | 146.246/146.246, fallback 0 | 146.246/146.246, fallback 0 | không đổi |
| Average relevant docs/question | 2,2490 | 2,7322 | +0,4832 |
| Average relevant tables/question | 3,7174 | 4,5158 | +0,7984 |
| Questions có table refs thay đổi | — | 778 | navigation delta |
| Questions có doc refs thay đổi | — | 563 | navigation delta |
| Dense exact+neighbor requests | 2.535 | 4.493 tổng | +1.958 neighbor |
| Neighbor hits hydrated | 0 | 86.963 | +86.963 |
| Neighbor table refs emitted | 0 | 769 | +769 |

`relevant_tables` tăng tổng cộng từ 3.762 lên 4.570 reference; phần tăng này
được giới hạn bởi tối đa hai neighbor slot ở emitter và vẫn bị chặn bởi
giới hạn output context hiện hữu.

## Proxy target check

Tập kiểm tra độc lập khả dụng trong workspace gồm 58 câu có
`execution_replay_ready` và 58 target table references lấy từ replay. Kết quả:

| Proxy | Baseline v2 | Neighbor corrected |
| --- | ---: | ---: |
| Câu có ít nhất một target table | 53/58 | 53/58 |
| Câu giữ đủ target tables | 53/58 | 53/58 |
| Target references giữ lại | 53/58 | 53/58 |

Proxy này không phải gold của cuộc thi. Nó thiên về các route đã replay được;
chỉ có Q292 trong tập này cần report year `2020` để lấy cột so sánh `2019`,
nhưng baseline đã giữ được target Q292, nên ablation không thể hiện thêm hit
trên proxy hiện tại.

## Thay đổi kỹ thuật và regression đã sửa

1. `expand_review_items_with_dense` có thể tạo request exact-year và
   report-year-neighbor cho cùng một năm trong câu hỏi nhiều kỳ. Code hiện
   đăng ký toàn bộ exact-year request trước, sau đó mới thêm neighbor request;
   nếu identity trùng, exact provenance luôn thắng.
2. Neighbor candidates được giữ trong navigation item nhưng bị loại khỏi
   `answer_review_items`. Vì vậy source-first, direct replay, candidate
   validity và model reranker không được dùng candidate `Y+1` để thay đổi
   answer.
3. Candidate cap giữ nguyên exact-year pool rồi mới nối một neighbor tail;
   neighbor không thể đẩy candidate exact ra khỏi answer pool trước bước
   lọc.
4. Emitter giữ bounded neighbor prefix cho context. Mọi row vẫn phải hydrate
   từ V2 table asset; dense score và period metadata không phải numeric
   evidence.

Test regression mới bao phủ trường hợp năm chồng lấn `Y=2020, 2021`, trong đó
`2021` vừa là năm hỏi chính vừa là `Y+1` của `2020`. Bộ test liên quan chạy
thành công:

```text
20 passed in 0.12s
```

## Quyết định và queue tiếp theo

- Giữ implementation ở dạng opt-in; không bật mặc định cho production
  submission và không upload/submit leaderboard.
- Đánh dấu đây là cải thiện navigation có bounded recall hypothesis, chưa
  phải score gain đã xác nhận. Workspace hiện không có official scorer/gold
  độc lập để quy đổi thành Table Precision/Recall/F2 hoặc Answer/Execution
  Accuracy.
- Ưu tiên vòng tiếp theo ở answer/execution bottleneck: phân loại và source
  binding cho các route multi-operand/incomplete-plan có thể replay độc lập.
  Chỉ các candidate vượt semantic, period, unit, scope và Decimal replay gate
  mới được đưa vào best-effort answer lane; không nâng thành `VERIFIED` nếu
  thiếu certificate hoặc human semantic approval.
