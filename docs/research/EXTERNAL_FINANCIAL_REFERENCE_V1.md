# External financial reference v1

## Kết luận thiết kế

ViFinQA chỉ cung cấp test và BCTC, vì vậy pipeline không được học từ đáp án
ẩn hoặc dùng leaderboard làm dev loop. Thước đo phát triển độc lập gồm:

1. FinQA cho bài toán sinh chương trình DSL và execution accuracy trên báo cáo
   tài chính.
2. TAT-QA cho chọn ô/span, scale và symbolic aggregation trên bảng + văn bản.
3. BGE-M3 cho retrieval tiếng Việt theo dense + sparse + multi-vector; kết quả
   model chỉ là candidate, không phải numeric evidence.
4. Qwen3-8B là proposer/planner mở, phát hành trước hạn luật thi và dưới 14B;
   mọi con số cuối vẫn phải quay về exact source cell và Decimal execution.

## Nguồn khóa

- FinQA paper: https://aclanthology.org/2021.emnlp-main.300/
- FinQA code/data: https://github.com/czyssrs/FinQA
- TAT-QA paper: https://aclanthology.org/2021.acl-long.254/
- TAT-QA code/data: https://github.com/NExTplusplus/TAT-QA
- BGE-M3 paper: https://arxiv.org/abs/2402.03216
- BGE-M3 model: https://huggingface.co/BAAI/bge-m3
- Qwen3 release: https://qwenlm.github.io/blog/qwen3/
- Qwen3-8B model: https://huggingface.co/Qwen/Qwen3-8B

Commit nguồn dữ liệu và firewall nằm trong
`configs/research/external_financial_reference_sources_v1.json`.

## Baseline đã chạy

Artifact `artifacts/research/external_financial_reference_v1_20260825/` chứa
24.827 record có provenance:

- 8.281 FinQA;
- 16.546 TAT-QA;
- 19.466 discovery;
- 2.551 development;
- 2.810 untouched evaluation, hiện chỉ được index và chưa dùng để chọn thay đổi.

Arithmetic-kernel baseline:

| Split | PASS | FAIL | UNSUPPORTED | Coverage | Accuracy trên supported |
| --- | ---: | ---: | ---: | ---: | ---: |
| FinQA discovery | 6.122 | 0 | 129 | 97,94% | 100% |
| FinQA development | 872 | 0 | 11 | 98,75% | 100% |

`greater` và `exp` chưa có trong kernel. `multiply` chạy đúng ở math kernel
nhưng grounded shadow vẫn chặn vì chưa chứng minh đại số đơn vị. Đây là ranh
giới đúng: không mở phép nhân tùy ý chỉ để tăng coverage.

## Pipeline phát triển

```text
external gold benchmark
  -> retrieval/component baseline
  -> freeze one-change hypothesis
  -> discovery
  -> development
  -> freeze prediction + thresholds
  -> untouched external evaluation
  -> run ViFinQA frozen full population
  -> exact-source replay + submission validator
  -> one official Dashboard submission
```

Không dùng output có provenance từ GPT/ChatGPT/Gemini trong model graph hoặc
gói thi. Candidate lịch sử có nguồn như vậy phải bị loại, dù số học chạy đúng.
