# ViFinQA pipeline completion handoff v6

Ngày chạy: 2026-08-30. Đây là handoff của lane source-first/semantic-contract
sạch; không nhập đáp án từ research/model sidecar của agent khác.

## Kết luận điều hành

| Cổng | Kết quả | Ý nghĩa |
|---|---:|---|
| Build population | PASS | 1.012/1.012 câu được phát, replay 1.012/1.012, `errors=[]` |
| Package completion gate | PASS | ZIP đọc được, 1.012 CSV + `submission.json`, ID duy nhất và đúng thứ tự |
| Code reproducibility | PASS | builder và `source_first_lookup` giữ nguyên hash trong toàn bộ build |
| Canonical `run-e2e` | COMPLETE | Đã tạo binding, execution replay, certificates, readiness receipt |
| Strict authorization | BLOCKED | 0 câu có complete Answer Certificate; `release_authorized=false` |
| Regression | PASS | `566 passed, 1 skipped` |

`PASS` ở completion gate là PASS về tính đầy đủ/kỹ thuật của artifact, không
phải xác nhận độ chính xác leaderboard. Không có gold/official scorer cục bộ,
nên không được suy diễn accuracy từ các con số dưới đây.

## Bản build sạch đã khóa

Builder được chạy từ snapshot mã bất biến:

- builder SHA-256: `3fafd53281efd46e57d8e543890de9a81131a45de414b8784cc76fe52a42c490`;
- `source_first_lookup.py` SHA-256:
  `23805052c5893c6032eba8b8126b743cd92c7071fcbe16f8b3e6a5dbde66290b`;
- structured asset SHA-256:
  `617ae044499907cf302519b7ff6fb96fb86ab70ff08e6fdcb0e6a3f8df5251f7`;
- source line map: 146.246 entries, thiếu 0; local ordinal fallback 0;
- candidate closure: 29.428 UID trong corpus 146.246 dòng, quét đủ corpus,
  bỏ qua 116.818 dòng ngoài frozen review-packet candidate closure;
- `--disable-research-fusion` và `--disable-candidate-validity`;
- research-selected questions: 0; model-answer questions: 0.

Kết quả prediction lane: 976 candidate đã replay từ structured table, 36 câu
fallback zero được đánh dấu riêng; tất cả 1.012 câu vẫn có trong submission.
Các candidate này là best-effort prediction, không phải `VERIFIED`.

Artifacts:

- [build_report.json](../../artifacts/runs/vifinqa_answer_optimization_20260830/local_source_first_semantic_contract_v6/build_report.json)
- [submission.json](../../artifacts/runs/vifinqa_answer_optimization_20260830/local_source_first_semantic_contract_v6/submission.json)
- [v6 ZIP](../../artifacts/runs/vifinqa_answer_optimization_20260830/local_source_first_semantic_contract_v6.zip)
- [run_manifest_v1.json](../../artifacts/runs/vifinqa_answer_optimization_20260830/local_source_first_semantic_contract_v6/run_manifest_v1.json)
- [immutable code snapshot](../../artifacts/runs/vifinqa_answer_optimization_20260830/semantic_contract_v6_code_snapshot_wmXRBP)

## Canonical E2E receipt

Profile dùng path tường minh tại
[deterministic_replay_v1_local_source_semantic_v6.yaml](../../configs/e2e/deterministic_replay_v1_local_source_semantic_v6.yaml).
Profile này trỏ vào candidate ledger v6 và không tự dò sidecar.

Run ID: `6a38b5845dc7854b46681676fddd7e3df293b781e7d32fdbaebc7ac1178c91dc`.

- run status: `complete_answer_capable`;
- question count: 1.012;
- candidate prediction count: 976;
- complete answer count: 0;
- certificate status: `ABSTAIN=1.012`;
- evidence binding: 884 packet rows, trong đó 55 binding-ready ở execution lane;
- execution replay-ready: 55; binding conflict: 8; route incomplete: 949;
- `machine_semantic_binding_count=0`;
- release authorization: `false`;
- reviewer inputs used: `[]`.

Các blocker chính được receipt ghi nhận là unresolved variable/entity/period,
lineage hash, unit conversion, formula compatibility và counterfactual checks.
Đây là chặn đúng thiết kế fail-closed; không được nâng candidate lên answer
authority chỉ vì Decimal replay thành công.

Artifacts:

- [grounded_e2e_run_v1.json](../../artifacts/runs/vifinqa_answer_optimization_20260830/canonical_e2e_local_source_semantic_v6/grounded_e2e_run_v1.json)
- [authorization_readiness_v1.json](../../artifacts/runs/vifinqa_answer_optimization_20260830/canonical_e2e_local_source_semantic_v6/authorization_readiness_v1.json)
- [answer_certificates_v1.jsonl](../../artifacts/runs/vifinqa_answer_optimization_20260830/canonical_e2e_local_source_semantic_v6/answer_certificates_v1.jsonl)
- [evidence_bindings_v1.jsonl](../../artifacts/runs/vifinqa_answer_optimization_20260830/canonical_e2e_local_source_semantic_v6/evidence_bindings_v1.jsonl)
- [grounded_execution_replay_v2.jsonl](../../artifacts/runs/vifinqa_answer_optimization_20260830/canonical_e2e_local_source_semantic_v6/grounded_execution_replay_v2.jsonl)

## Audit độc lập theo family

Audit r8 chỉ lấy 172 câu registry-clean từ independent review slice, hydrate
các UID trong candidate packet, dùng builder snapshot v6, không đọc research
candidate, model answer hay gold.

- 172/172 câu được align;
- semantic candidate vượt guard: 136;
- `SEMANTIC_CANDIDATE_CHANGED`: 102;
- `SEMANTIC_CANDIDATE_RETAINED`: 34;
- `SEMANTIC_ABSTAIN`: 36;
- mọi abstain đều có explicit reason code;
- không có Q-ID-specific exception;
- strict verification count: 0.

Diễn giải nghiên cứu ở mức family/pattern, không biến từng Question ID thành
patch riêng:

1. Lỗi period/column là nhóm lớn: chọn nhầm start/end, prior year hoặc header
   không khớp năm; semantic contract đã loại các cell này trước Decimal replay.
2. Lỗi metric/row binding là nhóm lớn tiếp theo: cash/receivable/provision,
   lending-versus-borrowing, total/component, derivative, investment và
   industry loan. Các rule mới yêu cầu đúng row phrase, aggregate binding,
   direction, scope và column context.
3. Lỗi scope/entity/currency/unit vẫn là blocker thực: related-party table
   không có perimeter, ticker khác, domestic/foreign currency lẫn nhau và
   thiếu conversion provenance. Giữ abstain ở đây là kết quả đúng, chưa phải
   khoảng trống để điền bằng model/research.
4. Comparator xác nhận 172/172 matched, policy exclusion 0, reason code lạ 0,
   và không so sánh answer/value. Đây là bằng chứng contract có trace, không
   phải accuracy estimate.

Artifacts:

- [audit summary r8](../../artifacts/research/independent_qna_review_v1_20260830_r8/summary.json)
- [audit per-question JSONL](../../artifacts/research/independent_qna_review_v1_20260830_r8/semantic_contract_reviews.jsonl)
- [audit TSV](../../artifacts/research/independent_qna_review_v1_20260830_r8/semantic_contract_reviews.tsv)
- [r1-to-r8 comparison](../../artifacts/research/semantic_audit_comparison_v1_20260830_r1_to_r8_v5/semantic_audit_comparison.json)

## Code/test handoff

- [semantic-cell contract tests](../../tests/e2e/test_semantic_cell_contract.py)
- [memory-bounded filter tests](../../tests/e2e/test_memory_bounded_structured_filter.py)
- [completion gate](../../scripts/research/run_pipeline_completion_gate_v1.py)
- [audit runner](../../scripts/research/run_semantic_contract_audit_v1.py)
- [audit comparator](../../scripts/research/compare_semantic_contract_audits_v1.py)
- [pipeline review của agent](./AGENT_PIPELINE_COMPLETION_REVIEW_V1.md)

Commands đã xác nhận:

```bash
PYTHONPATH=.:src .venv/bin/pytest -q

.venv/bin/python scripts/research/run_pipeline_completion_gate_v1.py \
  --output-dir artifacts/runs/vifinqa_answer_optimization_20260830/local_source_first_semantic_contract_v6 \
  --expected-count 1012

PYTHONPATH=.:src .venv/bin/python -m finance_query.cli run-e2e \
  --config configs/e2e/deterministic_replay_v1_local_source_semantic_v6.yaml \
  --output-dir artifacts/runs/vifinqa_answer_optimization_20260830/canonical_e2e_local_source_semantic_v6
```

Bước tiếp theo hợp lệ là lấy 36 abstain và các blocker family-level trong
receipt làm queue review/hydration có provenance; không nhập các output đang
chạy của job khác vào lane v6 và không gọi completion kỹ thuật là release
strict.
