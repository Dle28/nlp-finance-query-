# Kaggle entrypoint

Đối với workflow review mới, **chỉ chạy file này trên Kaggle**:

```text
kaggle/export_review_bundle.py
```

Normal export khi artifacts đã tồn tại:

```python
%cd /kaggle/working/AI_guru
!git pull --ff-only origin main
%run kaggle/export_review_bundle.py --top-k 20 --force
```

Chỉ thêm `--build-missing` khi bạn chủ động muốn Kaggle rebuild artifacts bị thiếu/stale:

```python
%run kaggle/export_review_bundle.py --top-k 20 --build-missing --force
```

Output tải về local:

```text
/kaggle/working/vifinqa_review_bundle.tar.gz
```

Checksum:

```text
/kaggle/working/vifinqa_review_bundle.tar.gz.sha256
```

Hướng dẫn đầy đủ Kaggle -> Local -> calibration:

```text
docs/KAGGLE_TO_LOCAL_REVIEW.md
```

Các file Kaggle khác trong thư mục này là implementation/internal helpers hoặc workflow cũ; reviewer không cần gọi trực tiếp chúng cho handoff mới.
