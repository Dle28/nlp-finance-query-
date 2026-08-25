# Script LLM research

Các script trong folder này chỉ vận hành diagnostic hoặc review sidecar LLM.
Chúng không thuộc pipeline `run-e2e` và không được tạo answer, training record,
promotion hay release authorization.

| File | Chức năng |
| --- | --- |
| `run_diagnostic_lane.py` | Tạo, kiểm tra và đánh giá batch proposer-only bị giới hạn. |
