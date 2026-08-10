# ViFinQA architecture completion checklist

Tài liệu này là danh sách chốt kiến trúc, không phải nhật ký thử nghiệm. Một issue chỉ được đánh dấu hoàn thành khi có code, artifact hash-bound, test và cơ chế fail-closed tương ứng. Các output mới mặc định chạy ở `shadow mode`: không được tự đổi provenance, đưa vào training hay submission.

## Definition of Done chung

- [x] Bốn provenance hợp lệ được giữ nguyên: `human_verified`, `machine_calibrated`, `machine_provisional`, `needs_human`.
- [x] Raw report là nguồn sự thật; V2 chỉ chuẩn hóa cấu trúc và V3 chỉ bổ sung ngữ cảnh/canonical header.
- [x] Mọi evidence số phải trỏ được về `internal_table_uid`, `row_index`, `column_index` và raw cell.
- [x] Artifact quan trọng có SHA-256 và dependency lineage trong Artifact Registry.
- [ ] Không còn phép tính nào được thực thi từ số do mô hình sinh hoặc từ đoạn tóm tắt.
- [ ] Mỗi family có ít nhất một canary end-to-end và một trường hợp fail-closed.
- [ ] Chỉ output vượt qua independent audit mới được xét nâng từ shadow sang production.

## Trạng thái tám mô-đun

| Mô-đun | Chức năng dễ hiểu | Trạng thái | Issue còn lại / gate để hoàn thành |
|---|---|---|---|
| M1 — Raw ingestion | Giữ nguyên report và provenance nguồn | Hoàn thành MVP | Theo dõi hash/source URI; không sửa raw để “làm đẹp” |
| M2 — Question understanding | Biến câu hỏi thành family, entity, kỳ, scope, unit và phép toán | MVP shadow hoàn thành | Census đủ 1.012; `M2-I2` còn phải tách operand có kiểu; câu lạ abstain |
| M3 — Table Structure V2 | Sửa lệch hàng/cột có kiểm soát, giữ cell order | Hoàn thành MVP | OCR quarantine tiếp tục bị chặn; không suy diễn ô thiếu |
| M4 — Evidence Context V3 | Gắn tiêu đề, kỳ, đơn vị và chức năng bảng | Hoàn thành MVP | Chỉ canonical hóa từ exact source; không dùng summary làm evidence |
| M5 — Evidence binding | Gắn operand vào exact header + exact raw cell | MVP shadow hoàn thành | Direct replay đã chạy 358 câu; `M5-I2` còn tăng Formula EvidenceSet coverage |
| M6 — Retrieval/rerank | Tìm candidate nhưng không quyết định đáp án | Hoàn thành MVP | Dense/Kaggle không rebuild nếu lineage hiện tại còn hợp lệ |
| M7 — Review/calibration | Nhiều critic kiểm tra độc lập và giữ provenance | MVP training gate hoàn thành | Raw replay đã là gate bắt buộc; `M7-I1` còn independent semantic critic |
| M8 — Execution/output | Tính bằng toán tử xác định rồi xuất answer | MVP shadow hoàn thành | Grounded registry đã có; `M8-I2` migrate QueryProgram và `M8-I3` production submission còn mở |

## Issue checklist ưu tiên

### P0 — phải xong để chốt phương pháp

- [x] `M2-I1` Tạo Query Fingerprint Census cho toàn bộ 1.012 câu.
  - Artifact: `query_fingerprint_census_v1.jsonl` + manifest.
  - Gate: đúng một record/question; fingerprint deterministic; route rõ `operator_contract_candidate`, `requires_operand_decomposition` hoặc `abstain_unknown_program`.
- [x] `M8-I1` Tạo typed Operator Registry trên lõi `execute_ast` hiện có.
  - Gate: kiểm tra operator/arity; chỉ nhận binding exact-cell; block thiếu UID, kỳ, scope, unit hoặc cell coordinate.
  - Gate: phép toán cùng đại lượng block unit/scope mismatch; chia cho 0 block.
  - Output luôn `submission_eligible=false`, `review_status_promotion_allowed=false` khi còn shadow.
- [x] `M5-I1` Tạo Direct Evidence Replay độc lập, conflict-aware.
  - Gate: đọc lại raw V2 cell và canonical V3 header thay vì tin giá trị trong review.
  - Gate: một giá trị exact duy nhất mới `shadow_replay_ready`; nhiều nguồn exact khác giá trị phải `shadow_ambiguous`.
  - Output không đổi machine review và không tự nâng provenance.
- [x] Đăng ký fingerprint, replay và replay-gated training input vào Artifact Registry; validate đủ 18 artifact.
- [x] Có unit tests cho happy path và fail-closed paths của ba phần trên.

### P1 — cần xong trước production training/submission

- [ ] `M2-I2` Operand decomposition có kiểu cho 647 câu complex hiện bị planner cảnh báo.
  - Không hardcode theo question ID; dùng template/operator contract và entity-period-role bindings.
  - Unknown structure phải abstain, không đoán công thức.
- [ ] `M5-I2` Nâng Formula EvidenceSet coverage theo fingerprint, ưu tiên nhóm có tần suất lớn.
  - Mỗi operand có exact row/cell/header/entity/year/scope/unit.
  - Không dùng adjacent table nếu grounding guard không chứng minh cùng bảng/kỳ.
- [ ] `M7-I1` Independent critic replay không dùng lại score/decision của reviewer đầu tiên.
- [x] `M7-I2` Training gate chỉ nhận `human_verified` hoặc `machine_calibrated` đã qua exact-source replay gate.
- [ ] `M8-I2` Migrate các QueryProgram đặc thù sang AST + Operator Registry; chỉ giữ template khi thực sự có semantics riêng.
- [ ] `M8-I3` Submission compiler chỉ nhận execution record có complete lineage và production eligibility.

### P2 — vận hành và mở rộng OCR

- [ ] Canary đại diện cho từng fingerprint lớn, mỗi canary có exact expected bindings.
- [ ] Dashboard hiển thị coverage theo fingerprint, replay reason code và bottleneck module.
- [ ] OCR adapter mới phải qua quality tiers `normal`, `review_required`, `quarantine` trước V2.
- [ ] Regression set cho split/merged cell, lệch header, duplicate table và khác scope.
- [ ] Benchmark thời gian/RAM/GPU; preprocessing cấu trúc ưu tiên CPU, embedding/rerank mới dùng GPU.

## Quy tắc chốt kiến trúc

Luồng chuẩn được cố định như sau:

`Raw → V2 Structure → V3 Context → Retrieval → Exact Evidence Binding → Independent Replay → Typed Deterministic Execution → Provenance Gate → Export`

Ba nguyên tắc không được phá vỡ:

1. Retrieval chỉ đề xuất nơi đọc, không tự chứng minh đáp án.
2. Summary chỉ giúp con người/mô hình định hướng, không phải evidence số.
3. Không đủ exact binding hoặc có hai nguồn exact mâu thuẫn thì kết quả là abstain/blocked, không chọn theo confidence.

## Snapshot đã materialize

```text
Query Fingerprint Census: 1.012 câu / 185 fingerprint
  operator_contract_candidate       357
  requires_operand_decomposition    536
  abstain_unknown_program            119

Direct Evidence Replay: 358 câu direct
  shadow_replay_ready                 63
  shadow_ambiguous                    49
  shadow_blocked                     246

Machine silver sau replay gate:       58
Artifact Registry:                    18 record, valid
```

Replay không sửa 62 trạng thái `machine_calibrated` lịch sử. Nó loại bốn record
không vượt kiểm tra hiện tại khỏi training input: một record ambiguous và ba
record blocked. Như vậy provenance lịch sử được giữ, còn consumer mới fail-closed.

## Điều kiện xem là “hoàn tất kiến trúc”

Kiến trúc được xem là chốt khi P0 hoàn thành và chạy lặp lại được. Hệ thống chỉ được xem là production-ready khi toàn bộ P1 hoàn thành, audit độc lập đạt ngưỡng đã định trước, và không còn đường nào từ `machine_provisional`/`needs_human` đi thẳng vào training hoặc submission.
