# Agent 4 — báo cáo nghiên cứu tổng hợp V1

## Phạm vi và trạng thái

Agent 4 đã đối chiếu độc lập baseline, artifact của Agent 1–3, manifest V2/V3 và một lần E2E CPU mới. Kết quả là một union packet có hash, exact binding V2 và Decimal execution replay; toàn bộ vẫn là research-only.

Union packet: `artifacts/research/agent4_candidate_union_v1_20260827_r2/`.

- 1.012 câu đầu vào.
- 65 unique period candidates ở baseline → 68 sau union.
- Ba câu được tích hợp candidate-only: Q156, Q263, Q340.
- Năm câu quay về quarantine: Q98, Q104, Q185, Q242, Q357.
- Q70 được replay như regression unchanged.
- `human_verified=0`, semantic decisions `0`, release `false`.

## Những gì đã được chứng minh

R9 `vifinqa-explicit-ticker-agent4-r9` chạy thành công với `run_status=complete_research_only`:

1. packet union → exact V2 cell binding;
2. exact binding → Decimal/Pandas execution replay;
3. execution/evidence/authorization receipts;
4. fail-closed khi thiếu semantic/entity/variable/provenance/period gate.

Có `58` câu đạt technical execution replay. Đây không phải số câu được phép trả lời: evidence binding vẫn `884 BLOCKED`, authorization `blocked`, answer certificates `1.012 ABSTAIN`, release `false`.

## Bảng nguyên nhân blocker

| Nhóm | Hiện trạng | Không thể sửa bằng dữ liệu hiện tại vì |
| --- | --- | --- |
| Route | 460 packet route-incomplete; 949 execution route-incomplete | Overlay chưa materialize đủ exact route/stage/operand; điểm retrieval không phải source authorization. |
| Period | 853 unresolved; Q156/Q263 table function chưa authorizing; Q340 as-of date chưa duy nhất | Có tọa độ replay không đồng nghĩa period semantic đã được chứng minh. |
| Entity/variable | Evidence entity và variable chưa pass | Ticker alias/metric label cần chứng cứ nguồn và semantic review, không thể suy ra từ arithmetic. |
| Provenance | Q98 document provenance, Q104 duplicate provenance; nhiều downstream lineage invalid | Thiếu lineage độc lập hiện hành; không được thay bằng bản sao hoặc hash cũ. |
| Header/table | Q185 expected table/header mismatch; Q242 canonical header conflict; Q357 inherited header không có trong V3 | Candidate không khớp canonical V2/V3 nên phải quarantine. |
| Human gate | Không có semantic decision nào | Không được tự tạo `human_verified`; queue rỗng là chủ ý fail-closed. |

## Kết luận bắt buộc

### Pipeline đã hoàn thiện

Hoàn thiện ở mức kỹ thuật research E2E và tái lập được bằng receipt/hash. Chưa hoàn thiện ở mức answer-capable hoặc submission-capable vì authorization gate vẫn blocked.

### Số câu có thể replay

`58/1.012` có exact-cell + Decimal/Pandas execution replay trong r9. `0/1.012` có answer/evidence authorization.

### Số câu vẫn phải abstain

`1.012/1.012` answer certificates là `ABSTAIN`. Năm candidate mới thất bại không bị giữ lại dưới dạng “gần đúng”; chúng ở quarantine.

### Điều kiện cần trước submission

Phải có, cho từng câu được đề xuất:

- exact V2/V3 coordinates, table/header/period/unit và document lineage ổn định;
- route đầy đủ, không còn incomplete/conflict;
- variable, entity, scope và period evidence pass;
- semantic review có thẩm quyền, hash-bound, với `human_verified` đúng nghĩa;
- evidence binding và independent audit pass;
- authorization/release gate bật rõ ràng và submission compilation được phép.

Không đủ điều kiện submission hiện tại. Không nộp Dashboard; không dùng Kaggle/dense index cho recheck này.
