# Cross-entity source-first ablation V2

Ngày: 2026-08-30  
Phạm vi: 1.012 câu ViFinQA; controlled best-effort candidate lane cho hai
issuer, một report year và phép trừ.

## Kết luận

Bản isolated A/B mới nhất dùng cùng một code snapshot bất biến trong cả hai
process. Candidate bật `source_first_cross_entity_v1` và thay route của 9 câu;
6 câu đổi số answer, 3 câu chỉ đổi tier/source nhưng giữ nguyên số. Đây là
evidence cho việc tăng coverage và sửa một số binding heuristic, chưa phải
`Answer Accuracy`/`Execution Accuracy` chính thức vì workspace không có gold
answer hoặc official local scorer.

Route vẫn thuộc authorized best-effort lane: mọi proposal đều có source-cell
replay hiện tại, nhưng verification class là `PARTIAL`, không có strict
certificate/human semantic approval và `promotion_allowed=false`.

## A/B contract

Artifacts:

- Control: `artifacts/runs/vifinqa_answer_optimization_20260830/cross_entity_source_first_isolated_5c6c/control/submission`
- Candidate: `artifacts/runs/vifinqa_answer_optimization_20260830/cross_entity_source_first_isolated_5c6c/candidate/submission`
- Control ZIP: `artifacts/runs/vifinqa_answer_optimization_20260830/cross_entity_source_first_isolated_5c6c/control/submission.zip`
- Candidate ZIP: `artifacts/runs/vifinqa_answer_optimization_20260830/cross_entity_source_first_isolated_5c6c/candidate/submission.zip`

Hai process dùng cùng questions, review bundle, replay, full structured table
asset, source-line map, direct-evidence replay, research candidates, route
overlay và candidate-validity model. Khác biệt có chủ đích duy nhất là cờ
`--disable-source-first-cross-entity`.

Để tránh tiến trình nghiên cứu khác sửa source giữa hai build, builder và
`src/` được chụp vào `/tmp/vifinqa-cross-ab.0GEuxd` trước khi chạy. Fingerprint
được pin trong cả hai report:

- Builder SHA-256:
  `6079e6319ba31c2fc4f22dde190947d12a9f70033757fb515f37afd607cbbeb2`
- Source-first SHA-256:
  `5c6d469079eed39449f8bf61535d8cb7590a9b6938534d28b958368ab7f9cd3b`
- Structured asset SHA-256:
  `617ae044499907cf302519b7ff6fb96fb86ab70ff08e6fdcb0e6a3f8df5251f7`
- Coordinate map SHA-256:
  `533cc779f9565f43dfc961ad03d7bdff65adbd3728ee7a60b61651d495c4c615`

Cả hai report ghi `changed_during_build=false`.

| Metric | Control | Candidate |
|---|---:|---:|
| Records | 1.012 | 1.012 |
| Non-zero answers | 999 | 999 |
| Replayed queries | 1.012 | 1.012 |
| Validation errors | 0 | 0 |
| Source map entries | 146.246 | 146.246 |
| Source map missing/fallback | 0 / 0 | 0 / 0 |
| Cross questions considered | 0 | 113 |
| Cross proposals resolved | 0 | 9 |
| `source_first_cross_entity_v1` selected | 0 | 9 |

Cả hai ZIP đều pass `unzip -t`.

## Per-question delta

Đối chiếu `diagnostics.jsonl` theo `id` cho đủ 1.012 record cho thấy đúng 9
ID thay đổi:

| Q | Control | Candidate | Diễn giải source-contract |
|---:|---:|---:|---|
| 739 | `603.878571429`, program multi-entity | `603.878571429`, cross source-first | Cùng hai dòng trái phiếu thường của IJC/SCR, source route cụ thể hơn |
| 746 | `-15467.213219` | `-15503.979213` | DXS/KHG cùng dòng chi phí thuế TNDN hiện hành, BCTC riêng, năm 2024 |
| 774 | `1822415` | `-204503` | VIB `1.617.912` trừ SHB `1.822.415`, cả hai BCTC riêng, triệu VND |
| 776 | `2.354023216989` | `-2.267796002924` | Dùng dòng lợi nhuận sau thuế TNDN của MSN/MML thay vì retained-earnings row |
| 779 | `5.107` | `5.107` | MCH/MML cùng dòng lãi cơ bản trên cổ phiếu; đổi route, giữ số |
| 794 | `4.5515249` | `40.676771672` | SCR `4.551525` và NLG `45.228296672` tỷ, đúng nguồn thu dịch vụ xây dựng |
| 795 | `2.788579696` | `25.731282261` | GAS/POW cùng dòng chi phí trả trước ngắn hạn trên BCTC riêng |
| 798 | `-150.095070820` | `67.557218104` | HHV/VSC cùng dòng TSCĐ hữu hình trên BCTC riêng |
| 807 | `986` | `986` | SHB/SSB cùng dòng dự phòng rủi ro chứng khoán sẵn sàng để bán; đổi route, giữ số |

Đã replay độc lập 18 selected cells từ full corpus; tất cả 18/18 tồn tại và
khớp ticker, report year, scope, row/column và header. Không có source của
cross lane nào thuộc `governance_roster`. Một regression test riêng cũng chặn
governance/roster table dù nhãn dòng có lexical match.

## Safety boundary

Cross lane chỉ nhận:

- đúng hai ticker trong compiled plan;
- đúng một năm explicit và phép `subtract`;
- lookup từng issuer qua direct source-first resolver;
- exact report year, match mode `exact_contiguous` hoặc
  `ordered_with_ocr_gap`;
- shared reporting scope;
- semantic row guard cho các nhóm dễ nhầm như trái phiếu, chứng khoán đầu
  tư/kinh doanh, dự phòng, lợi nhuận sau thuế, thuế TNDN, dịch vụ xây dựng;
- governance table bị reject trước khi proposal được tạo.

Candidate không nâng `PARTIAL` thành `VERIFIED`; answer/row evidence vẫn cần
semantic review hoặc official evaluator trước khi promotion/release.

## Verification commands

Focused regression suite sau thay đổi:

```text
.venv/bin/pytest -q tests/e2e/test_submission_integration.py tests/e2e/test_source_first_lookup.py
48 passed in 0.26s
```

Sau các thay đổi source-first kế tiếp trong workspace, cùng nhóm test đã đạt
`52 passed`; A/B V2 ở trên vẫn được đánh giá theo snapshot 5c6 đã pin trong
report, không theo source project có thể thay đổi sau đó.

## Disposition and next score queue

Giữ candidate ở best-effort lane, không submit leaderboard tự động. Ưu tiên
review/official scoring cho 6 answer changes; nếu scorer xác nhận, đây là
nhóm có leverage cao vì một proposal sửa đồng thời hai operand và một phép
tính. Giữ control disabled để các vòng sau đo delta sạch.

Các hướng retrieval/dense/period-neighbor vẫn chỉ là navigation: period-neighbor
đã cải thiện số lượng tài liệu/bảng được hydrate nhưng answer/tier byte-identical
trong ablation tương ứng. Nhánh tiếp theo nên tập trung vào composition thiếu
operand và scope/header/ownership conflict, không hạ global semantic threshold.
