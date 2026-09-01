# Cấu hình E2E

deterministic_replay_v1_locked.yaml là profile vận hành canonical. Nó khai
báo input closure, manifest và metric registry bằng path explicit; reviewer,
LLM và research decision không nằm trong authority closure.

Khi input hoặc source closure thay đổi:

1. cập nhật config có chủ đích hoặc tạo profile thử nghiệm ở namespace riêng;
2. tạo một output directory mới;
3. lưu receipt/hash để có thể replay;
4. không sửa receipt lịch sử để làm run mới pass.

Các config khác trong thư mục này chỉ được dùng khi có mục đích thử nghiệm và
phải được ghi rõ lineage. Không thay profile canonical chỉ vì một artifact có
tên hoặc timestamp mới hơn.
