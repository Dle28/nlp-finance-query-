# Artifact registry

Đây là registry vai trò, không phải bảng xếp hạng “file mới nhất”. E2E luôn
đọc đúng các path được khai báo trong config và hash chúng trước khi chạy.

## Main lineage

| Path | Vai trò | Quyền |
| --- | --- | --- |
| artifacts/main_inputs/period_packets_v1/ | Candidate kỳ/cột cho discovery | Candidate only |
| artifacts/main_inputs/route_overlay_v1/ | Route/table overlay | Candidate/context |
| artifacts/kaggle_runs/notebook5554bd790d_v10_20260811/vifinqa_review_bundle/ | V2 tables và V3 evidence context | Source/replay input |
| configs/e2e/deterministic_replay_v1_locked.yaml | Explicit E2E input manifest | Operational selector |
| artifacts/runs/<new-run>/ | Binding, execution, authorization, certificate, receipt | Immutable run output |
| submissions/<new-run>/ | Prediction, CSV/Pandas replay, diagnostics, ZIP, proposal ledger | Competition output |

Tên thư mục ngày/thứ tự chỉ là label. Không được tự thay input bằng cách chọn
file có tên hoặc mtime “mới nhất”. Khi source closure thay đổi, tạo config hoặc
run/output mới có lineage rõ ràng.

## Quyền của artifact

- Candidate/research/index/reranker: điều hướng và xếp hạng.
- V2/V3 source closure: nơi hydrate và replay exact cell.
- Decimal/execution receipt: chứng minh phép tính đã chạy theo AST; chưa tự
  authorize semantics.
- Answer Certificate: kết luận ANSWER hoặc ABSTAIN cho một question theo
  contract.
- Submission ZIP: package để đo competition; không phải release authorization.
- Release ledger: cần full-population audit độc lập; component receipt không
  thay thế được ledger.

Review queue, human decision và research iterations phải ở artifact riêng.
Một queue không được import vào E2E như numeric evidence. Artifact lịch sử
được giữ để audit/replay; không sửa nội dung cũ để làm run mới pass.
