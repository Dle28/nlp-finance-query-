# Cấu hình E2E

`deterministic_replay_v1_locked.yaml` là **immutable baseline**, không phải
candidate có coverage cao nhất. Nó trỏ đến input closure hiện hành, registry
metric và bắt buộc các output chính byte-match baseline. Đây là config E2E
duy nhất được version-control; historical manifests nằm trong Git history,
không còn là config vận hành.

Khi input hoặc source closure thay đổi, tạo receipt mới vào artifact directory
mới và ghi nó vào hồ sơ audit. Không sửa manifest/receipt lịch sử để "làm cho
chạy". Chỉ tạo config replay mới khi input lineage hoặc contract thay đổi.
