# Kiểm thử E2E

Các test trong nhóm này bảo vệ contract của luồng canonical: question compiler,
exact execution, manifest rõ ràng, input không bị đổi, output không ghi đè và
receipt có thể tái lập.

Chạy nhanh nhóm E2E:

```bash
PYTHONPATH=.:src .venv/bin/pytest -q -m e2e
```

Chỉ test thuộc canonical closure được giữ tại đây. Các test legacy nằm ngoài
closure đã được xóa cùng entrypoint và config tương ứng; historical receipt
vẫn phục hồi được từ Git/artifact storage.
