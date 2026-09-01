# Research integration log V1

Ngày ghi nhận: 2026-08-30  
Phạm vi: toàn bộ research inventory và các agent session hiện có trong
workspace AI_guru / ViFinQA.

## 1. Mục đích và cách đọc kết quả

Log này là registry quyết định tích hợp, không phải một score report. Mình đã
đối chiếu các nhóm tài liệu trong docs/research, mã trong src/finance_query,
scripts/research, scripts/e2e, configs/research, các artifact A/B và các
agent session của ngày 2026-08-30.

Các kết luận được chia thành ba mức:

1. Tích hợp candidate lane: có A/B cùng snapshot, source-coordinate replay,
   query replay và validation/ZIP integrity. Route được phép tạo best-effort
   proposal trong build-submission, nhưng không được tự tạo VERIFIED.
2. Giữ navigation/provenance: có ích để tìm bảng, hydrate nguồn, sửa line map,
   tạo queue hoặc quarantine; không được thay đổi answer authority.
3. Chưa tích hợp: chỉ có probe, coverage, artifact đang chạy, snapshot race,
   duplicate/provenance conflict hoặc chưa có clean A/B.

Nguyên tắc bắt buộc: numeric truth phải được hydrate từ structured table hiện
hành và replay bằng Decimal/Pandas; retrieval, dense, model, rank, tên file,
research score và technical replay không phải semantic authorization.

## 2. Baseline và trạng thái workspace

- Dataset build có 1.012 câu.
- Baseline gần nhất có score chính thức đã được xác nhận từ archive scorer:
  Answer Accuracy = 0.17 và Execution Accuracy = 0.17. Artifact là
  submission_ready/vifinqa_execution_optimized_v11_20260830.zip, SHA-256
  c7c95e2198da26ac7d7fe1fd1a6ecf28d0010f35d22feccdf26146c957bf91d7.
- V11 build report ghi 1.012/1.012 query replay, validation hợp lệ, 1.010
  PARTIAL, 2 UNRESOLVED, không có strict certificate. Score v11 là baseline
  chính thức để so sánh; chưa có score chính thức cho artifact tích hợp mới.
- Full structured asset hiện hành có 146.246 tables và SHA-256
  617ae044499907cf302519b7ff6fb96fb86ab70ff08e6fdcb0e6a3f8df5251f7.
- Canonical source-line map có 146.246/146.246 entries, không thiếu/không
  thừa, SHA-256
  533cc779f9565f43dfc961ad03d7bdff65adbd3728ee7a60b61651d495c4c615.
- Git vẫn ở main, HEAD đồng bộ origin/main tại 6be0c82. Worktree có nhiều
  thay đổi dùng chung của các agent; không reset, checkout, clean hoặc stage
  hàng loạt trong lượt này.

## 3. Kiểm kê các agent/session

Các session logic đã được đối chiếu gồm:

- source-first broad optimization: direct lookup, metric/period/scope và
  cross-family reclassification;
- composition/aggregate audit: tìm missing operands và kiểm tra Q175;
- disclosed-row negative control: kiểm tra nguy cơ override theo dòng đã công
  bố;
- maturity/liability contextual route: kiểm tra Q261 và asset/liability
  confusion;
- multi-entity/Kaggle: candidate-bound và multi-entity threshold;
- independent scorer/archive audit: xác nhận score chính thức của v11;
- session tích hợp hiện tại: tổng hợp quyết định, artifact và hồi quy.

Do các session dùng chung app-server và có lúc sửa cùng một working tree,
mọi A/B chỉ được nhận nếu report tự ghi implementation fingerprint và
changed_during_build=false. Trạng thái RUNNING, số file tạm hoặc log chưa có
terminal report không được xem là hoàn thành.

## 4. Quyết định theo research family

| Research family / protocol | Bằng chứng quan trọng | Quyết định |
|---|---|---|
| OCR coordinate + full-corpus asset | Full asset 146.246 tables; source-line map 146.246/146.246; canonical OCR table-start coordinates; replay/inventory match | Tích hợp nền tảng source closure và hydration. Không dùng local ordinal fallback cho build production. |
| Exact source-first direct lookup | Exact-year-first, row/column/unit replay, full-corpus index; là source route nền cho v11 và các route mới | Giữ bật ở candidate lane. Numeric value chỉ đến từ current structured table replay. |
| Reclassified direct source-first | Clean A/B: 9 eligible, 8 resolved, 6 answer changes, 2 tier-only; Q323 fail-closed do duplicate source | Tích hợp mặc định trong candidate lane với protocol source_first_reclassified_direct_v1. |
| Composed total | Clean A/B trên 1.012 câu: 1/1 Q175 resolved; đúng hai dòng ngắn hạn + dài hạn trong cùng bảng/năm/scope/unit; answer đổi 235664195.016 → 32.587523656 | Tích hợp mặc định trong candidate lane với protocol source_first_composed_total_v1; không cho phép sum generic/arbitrary rows. |
| Financial-liability maturity total | Clean A/B: Q261 đổi 16496708.0 → 174052754.0; table section liability, header đúng 31/12/2019, row TỔNG CỘNG, unit triệu đồng; asset schedule bị reject | Tích hợp mặc định như pattern hẹp trong reclassified direct candidate lane; không mở generic TỔNG CỘNG. |
| Candidate-bound source-first | Clean A/B: 6 candidate resolved và 3 answer changes; boundary table/document được bind lại qua source replay | Giữ bật candidate lane; candidate-bound không cấp strict authority. |
| Conditional temporal + cash-total guard | Clean A/B: conditional route mở 4 answer; contextual cash-total guard mở thêm Q521, tổng 5 answer/tier changes; guard yêu cầu đúng metric cash + equivalents | Tích hợp candidate lane. Giữ exact-year, one-ticker/three-or-more-year và metric-context guards. |
| Temporal source-first | Clean controlled runs: 2 answer changes ở temporal V2; bản mở rộng có 7 rồi 10 source-first temporal selections tùy snapshot, gồm cross-family single-ticker/two-year | Tích hợp candidate lane theo family; không dùng comparative year hoặc fuzzy row làm authority. |
| Cross-entity source-first | Clean V2 A/B: 9 selected, 6 answer changes, 3 tier-only; 18/18 selected source cells replay pass; shared scope và governance reject | Tích hợp candidate lane với đúng two-ticker/one-year/subtract contract. |
| Temporal cross-family | Có snapshot controlled, exact source replay cho các plan một ticker/hai năm bị gắn nhầm cross-entity; route priority đã được kiểm tra | Giữ trong temporal candidate lane; không nhân rộng sang mọi multi-entity shape. |
| Contextual financial receivables total | Clean A/B trên snapshot f29df1/d68ed: route mở 1 eligible case mới; Q323 đổi 12.971609076 → 16.024974123; source row và requested closing column replay pass | Tích hợp candidate lane trong reclassified direct với contextual note/header/total-column guard; không dùng bare TỔNG CỘNG. |
| Multi-entity positive-threshold | Clean A/B trên snapshot c06d712/8139a6: variant xét 1 nhưng unresolved 1; answer/tier diff = 0, không có source-first threshold answer mới | Không tích hợp vào artifact bàn giao; giữ code/flag cho nghiên cứu tiếp, final build tắt route này. |
| Multi-entity direct aggregation | Clean A/B trên snapshot f29df1/d68ed: 3 eligible, 3 resolved, đúng 3 answer changes (Q827, Q858, Q927); per-entity current-table replay và scope/parent-company guards pass | Tích hợp candidate lane với protocol source_first_multi_entity_direct_aggregation_v1; vẫn giữ promotion_allowed=false. |
| Multi-entity outstanding-share threshold | Clean immutable A/B v2: 2 eligible, 2 resolved, đúng 2 answer changes (Q990, Q1005); four-issuer current-table replay, explicit-parent scope and unqualified scope-invariance pass | Tích hợp narrow candidate lane với protocol source_first_multi_entity_share_threshold_v1; giữ promotion_allowed=false và không gọi là official score delta. |
| Multi-entity operating-lease threshold | Clean immutable A/B: Q932 eligible/resolved, đúng 1 answer change `0.0 → 2.0`; four-issuer maturity replay và source-unit provenance pass | Tích hợp narrow candidate lane với protocol source_first_multi_entity_lease_threshold_v1; KLB chỉ pass nhờ document-unit hash check, promotion_allowed=false. |
| Multi-year argmax/period extreme | Clean immutable A/B v4: 43 typed plans, 23 resolved, đúng 23 answer changes; 93/93 source cells và 23/23 cohorts independent replay pass | Tích hợp narrow candidate lane với protocol source_first_period_extreme_v1; giữ exact-year, row-family, scope, unit và unique-winner gates, promotion_allowed=false. |
| Multi-entity conditional positive-count | Clean immutable A/B v4: Q973 eligible/resolved, đúng 1 answer change `2.0 → 1.0`; 3 current cash-flow cells và strict-positive predicate replay pass | Tích hợp narrow candidate lane với protocol source_first_multi_entity_conditional_count_v1; giữ explicit context/issuer completeness, promotion_allowed=false. |
| Argmax tax-paid alias/context extension | Same-snapshot route-local control rejects Q879 as `REJECTED_NO_COHERENT_ROW_FAMILY`; strict variant accepts exact VNM cash-flow row and selects 2025; full variant remains valid 1.012/1.012 | Tích hợp riêng Q879 vào candidate overlay với adapter `program_arg_extreme_period_v1`; signed-max policy, exact row/context gate, promotion_allowed=false. |
| Period-neighbor / report-year neighbor | Navigation proxy có document/table recall tốt hơn nhưng answer, query, evidence không đổi; các run cũ có race hoặc chỉ là navigation | Giữ opt-in navigation-only. Không bật làm answer authority; integrated clean build dùng disable report-year neighbor. |
| RAG, hybrid lexical+dense, reranker, candidate-validity | Full corpus và dense giúp recall/ranking/navigation; candidate-validity có CV nhưng không phải proof; raw numeric từ dense bị cấm | Giữ làm navigation/ranking/hydration input. Không copy số và không cấp certificate. |
| RAG fine-tune / Kaggle runtime | Có runtime/notebook và các job external, nhưng completion chỉ hợp lệ khi có downloaded report, ZIP, record/replay và lỗi rỗng; không có score mới thay baseline v11 | Giữ như experiment/input preparation; không tuyên bố leaderboard gain và không dùng model output làm authority. |
| Agent 1–4 strict E2E / V2-V3 candidate union | R9 replay được 58/1.012 về kỹ thuật; 884 evidence bindings BLOCKED; 1.012 certificates ABSTAIN; human_verified=0, release=false; Q98/Q104/Q185/Q242/Q357 quarantine | Giữ strict E2E boundary, provenance/quarantine và review queue. Không promote các candidate cũ thành answer/release. |
| Multi-column semantic, duplicate parent-child, ownership/header probes | Có ích để phát hiện table/header/provenance conflict và ngăn governance/duplicate binding | Giữ làm fail-closed gates và research queue; chưa phải answer route độc lập. |
| Blocked-question / missing-operand / staged-formula studies | 313 route chưa materialize, 282 thiếu operand graph, 253 plan thiếu operand, 66 multistep chưa compile, 36 execution thiếu semantic evidence; hạ threshold làm ambiguity tăng | Không tích hợp heuristic toàn cục. Ưu tiên audit từng family và chỉ thêm route sau clean A/B. |
| Multi-entity metric v1r3 | Có CPU/Kaggle artifact và một số strict row-anchor candidate, nhưng chưa có clean causal A/B với baseline | Chưa tích hợp main answer route; giữ research candidate/probe. |
| arg_extreme_period | Clean strict A/B v4: 43 typed plans considered, 23 resolved, 23 answer changes; 93/93 source cells and 23/23 cohorts replay pass; 20 remain fail-closed | Giữ candidate lane với protocol source_first_period_extreme_v1; audit the 20 non-materialized cases by failure family before broadening. |
| Scalar multiply, percentage, staged-formula materialization | Có code/probe/coverage nhưng chưa có completed same-snapshot A/B đủ để đo answer precision | Chưa tích hợp answer route. Không dùng coverage hoặc arithmetic replay đơn lẻ để promote. |
| External financial references | Có thể hỗ trợ context nhưng không thuộc source closure của bài | Không tích hợp numeric/evidence authority. |
| Disclosed-row broad override | Negative control cho thấy override rộng có thể chọn sai dòng | Loại khỏi pipeline. Chỉ chấp nhận contract theo metric/table/period rõ ràng. |
| Race-invalidated A/B, duplicate writer, stale Agent 1 receipt | Fingerprint không ổn định, receipt/hash cũ hoặc source bị sửa giữa hai arm | Loại khỏi score/causal claim; chỉ giữ làm lịch sử và bài học về immutable snapshot. |

Các tài liệu chi tiết được giữ nguyên trong docs/research; log này chỉ ghi
quyết định cuối theo family để các agent sau không phải đoán từ tên artifact.

## 5. Những gì đã được ghi vào pipeline

Đường runtime được kiểm tra là:

    scripts/e2e/build_competition_submission_v1.py
      → source_first_lookup.py
      → current structured table hydration
      → exact row/column/period/unit checks
      → Decimal/Pandas replay
      → candidate submission + audit ledger

Compatibility adapter src/finance_query/e2e/submission_pipeline.py đang gọi
đúng builder này. Các route source-first được bật mặc định nếu không truyền
flag disable tương ứng:

- source_first_reclassified_direct_v1;
- source_first_composed_total_v1;
- financial-liability maturity pattern trong reclassified direct;
- contextual financial receivables-total pattern trong reclassified direct;
- source_first_multi_entity_direct_aggregation_v1;
- source_first_candidate_bound_v1;
- source_first_conditional_temporal_v1;
- source_first_temporal_v1 và temporal cross-family;
- source_first_cross_entity_v1;
- source_first_period_extreme_v1;
- program_arg_extreme_period_v1 strict adapter for the independently replayed
  argmax cohort;
- source_first_multi_entity_conditional_count_v1;
- source_first_multi_entity_lease_threshold_v1;
- source_first_multi_entity_share_threshold_v1.

Route priority hiện tại là direct replay/exact → source-first direct →
reclassified direct → composed total → multi-entity direct aggregation →
candidate-bound → conditional temporal → temporal → cross-entity → period
extreme → strict argmax adapter → multi-entity conditional/lease/share threshold
→ các fallback cũ. Đây là thứ tự proposal coverage,
không phải thứ tự cấp semantic authority.

Mỗi route đã chọn đều phải giữ các trường:

- accepted_answers_are_current_table_replayed = true;
- machine_artifact_is_not_human_verified = true;
- promotion_allowed = false;
- lane = authorized_best_effort_submission_candidate.

Vì code route đã tồn tại trong shared working tree từ các agent, lượt này
không chép lại hoặc tạo bản runtime thứ hai. Thay đổi tích hợp có chủ đích là
đưa quyết định vào log này, xác nhận route wiring/defaults, và tạo full build
immutable phía dưới. Điều đó tránh duplicate implementation trong workspace
đang dirty.

## 6. Full integrated artifact đã kiểm tra

### Snapshot v14 đã hoàn tất

Artifact đóng băng trước khi lượt này chụp snapshot:

- report:
  artifacts/runs/vifinqa_answer_optimization_20260830/local_source_first_exact_only_v14_maturity_final/submission/build_report.json;
- ZIP:
  artifacts/runs/vifinqa_answer_optimization_20260830/local_source_first_exact_only_v14_maturity_final/submission.zip;
- validation: 1.012 records, 1.012 query replay, errors=[];
- source-line map: 146.246/146.246, status PASS;
- changed_during_build=false;
- PARTIAL=1.010, UNRESOLVED=2, certificate=null, promotion_allowed=false;
- ZIP SHA-256:
  e919f00316a60a5d8abd360437d92cdeb7b283e282590e710ef8394323dfef9e9.

Đối chiếu serialized submission với v11 cho thấy đúng hai answer thay đổi:

| Question | V11 baseline | Integrated v14 | Route |
|---:|---:|---:|---|
| Q175 | 235664195.016 | 32.587523656 | source_first_composed_total_v1 |
| Q261 | 16496708.0 | 174052754.0 | source_first_reclassified_direct_v1 |

Q175 evidence nằm tại
artifacts/runs/vifinqa_answer_optimization_20260830/local_source_first_exact_only_v14_maturity_final/submission/data/q0175_evidence.csv:
hai cell cùng bảng VGT 2020, raw VND 5,634,013,216 và 26,953,510,440,
replay thành 5.634013216 + 26.953510440 = 32.587523656 tỷ đồng.

Q261 evidence nằm tại
artifacts/runs/vifinqa_answer_optimization_20260830/local_source_first_exact_only_v14_maturity_final/submission/data/q0261_evidence.csv:
BVH consolidated 2019, UID
d3eff4436ee4bd8227a656ad9b18952a1a49f08dba8c86f03f0c13e4534e528f,
row TỔNG CỘNG, column 6, raw 174.052.754 triệu đồng.

### Build tích hợp hiện hành (shadow, có threshold chưa được chấp nhận)

Sau khi source hiện tại được chụp vào snapshot bất biến
/tmp/vifinqa-integrated-current.nTeguv, full build độc lập đã hoàn tất với
full asset, full source-line map, replay r9, research candidate union,
multicol candidate packets, route overlay, direct evidence replay có
provisional admission và Qwen staged candidates. Lệnh dùng
disable-source-first-report-year-neighbor để không trộn nghiên cứu neighbor
chưa đủ attribution vào artifact bàn giao.

Artifact shadow:

- report:
  artifacts/runs/vifinqa_answer_optimization_20260830/local_source_first_exact_only_current_integrated_r1/submission/build_report.json;
- submission JSON:
  artifacts/runs/vifinqa_answer_optimization_20260830/local_source_first_exact_only_current_integrated_r1/submission/submission.json;
- ZIP:
  artifacts/runs/vifinqa_answer_optimization_20260830/local_source_first_exact_only_current_integrated_r1/submission.zip;
- ZIP SHA-256:
  4246d0f4a5a256821ace85d92b55952eb016202bf1d3fe008b8d2bdb41b198f5;
- implementation snapshot:
  builder SHA-256
  4bd6eda7c28f4578c1a5300da918fb5239ad6bb0a6fece6b02278ba633670b2f;
  source_first_lookup.py SHA-256
  8139a6b5e74adef17d2ef18682eb8b76956df68f26c306f79d7e0fd594a02e82;
- validation: 1.012 records, 1.012 query replay, errors=[];
- source-line map: 146.246/146.246, missing=0, extra=0, status PASS;
- changed_during_build=false;
- nonzero_answers=999, fallback_questions=0;
- PARTIAL=1.010, UNRESOLVED=2, certificate=null,
  promotion_allowed=false;
- ZIP integrity: pass, 1.013 files.

Đối chiếu với v11 baseline cho thấy đúng 5 answer changes trong artifact hiện
hành:

| Question | V11 baseline | Current integrated | Route |
|---:|---:|---:|---|
| Q175 | 235664195.016 | 32.587523656 | source_first_composed_total_v1 |
| Q261 | 16496708.0 | 174052754.0 | source_first_reclassified_direct_v1 |
| Q770 | 2440.734385642 | 35153.747341507 | source_first_cross_entity_v1 |
| Q785 | 268631965.0 | 6112098.0 | source_first_cross_entity_v1 |
| Q949 | 6.5e-11 | 3.0 | source_first_multi_entity_threshold_v1 |

Q770/Q785 có hai operand source-first trong cùng scope/năm và đều ghi đầy đủ
tọa độ trong CSV evidence. Q949 đếm đúng các ngân hàng có lãi thuần ngoại hối
lớn hơn 1.000 triệu đồng; bốn cell ACB/MBB/EIB/BID được replay trong
q0949_evidence.csv. Tuy nhiên A/B negative của
source_first_multi_entity_threshold_v1 cho thấy resolved=0 và answer/tier
diff=0, nên Q949 không được đưa vào artifact bàn giao cuối.

Vì snapshot này còn bật source_first_multi_entity_threshold_v1 trước khi A/B
negative được chốt, artifact current integrated chỉ là shadow candidate. Nó
có 5 thay đổi answer, trong đó Q949 không được đưa vào artifact bàn giao cuối.
Không dùng shadow ZIP để claim Accuracy +5. Official score mới vẫn là PENDING.

### A/B negative cho threshold và A/B positive cho receivables

Artifact multi_entity_threshold_ab_v1 dùng cùng snapshot bất biến
c06d71215259eccd75f0e275b0f1ce6c38c3b2cbe2540244e0fad522940d6674 /
8139a6b5e74adef17d2ef18682eb8b76956df68f26c306f79d7e0fd594a02e82. Cả control
và variant đều validation 1.012/1.012, errors=[] và changed_during_build=false.
Variant xét 1 câu nhưng unresolved=1; serialized answer/tier diff là 0. Vì
vậy không có bằng chứng để bật route threshold.

Artifact receivables_total_ab_v1 dùng snapshot bất biến
f29df1dbfebef0a0884cb65406ef8b3d44ec4faeefe93c84e895e3f2734025cd /
d68ed471ce73db8fe8f0f9f263b0cfdcc3fb223ce80821980a8603ca61385528. Cả hai arm
đều validation 1.012/1.012, errors=[] và changed_during_build=false. Variant
resolve thêm Q323 và đổi đúng một answer; evidence là
receivables_total_ab_v1/variant/submission/data/q0323_evidence.csv, row
Tổng cộng tại SSI separate 2016, raw 16,024,974,123 VND = 16.024974123 tỷ.
Route được giữ candidate-only, không được gọi là verified accuracy.

### Current accepted candidate build

Artifact cuối đã được build trên snapshot bất biến
/tmp/vifinqa-final-accepted.doq8mK với route threshold chưa được chấp nhận bị
tắt:

    --disable-source-first-multi-entity-threshold

Route receivables và multi-entity direct aggregation đã đạt A/B nên được giữ
bật. Artifact bàn giao:

- report:
  artifacts/runs/vifinqa_answer_optimization_20260830/local_source_first_exact_only_final_accepted_r1/submission/build_report.json;
- submission JSON:
  artifacts/runs/vifinqa_answer_optimization_20260830/local_source_first_exact_only_final_accepted_r1/submission/submission.json;
- ZIP:
  artifacts/runs/vifinqa_answer_optimization_20260830/local_source_first_exact_only_final_accepted_r1/submission.zip;
- ZIP SHA-256:
  640e3122db31cdb98fabf1b1baa4e91deed5d96dc925e37c22d0405a39e39d65;
- implementation fingerprint: builder
  9dc7e4087cc52aa1734b74c471fde12ab329e95fe375ceb0ee3720e71073100d;
  source_first_lookup.py
  d68ed471ce73db8fe8f0f9f263b0cfdcc3fb223ce80821980a8603ca61385528;
- validation: 1.012 records, 1.012 query replay, errors=[];
- source-line map: 146.246/146.246, missing=0, extra=0, status PASS;
- changed_during_build=false;
- nonzero_answers=999, fallback_questions=0;
- PARTIAL=1.010, UNRESOLVED=2, certificate=null,
  promotion_allowed=false;
- threshold route report: enabled=false, status=disabled;
- multi-entity direct aggregation: 3 considered, 3 resolved;
- reclassified direct: 10 considered, 10 resolved;
- ZIP contains 1.013 files and passes integrity test.

So với v11, final candidate đổi đúng 8 answer records: Q175 và Q261 từ
composed/liability routes, Q323 từ contextual receivables, Q770/Q785 từ
cross-entity, và Q827/Q858/Q927 từ multi-entity direct aggregation. Đây là
8 source-replayed candidate changes, không phải Answer Accuracy +8; official
score mới vẫn PENDING.

### Follow-up shadow v15 và các A/B v2 chưa hoàn tất

Snapshot `local_source_first_exact_only_v15_integrated_best` đã build được
artifact kỹ thuật hợp lệ (1.012/1.012 replay, `errors=[]`), nhưng nó bật
`source_first_multi_entity_threshold_v1`, nên chỉ là shadow. Q949 đổi từ
`6.5e-11` sang `3.0`; route này không có A/B positive resolution và không
được phép thay thế final accepted.

Snapshot v15 cũng đang thử chính sách `expense_magnitude_abs` cho
`Chi phí bán hàng`, làm Q858 đổi từ `0.530161279161` (candidate signed của
A/B sạch trước đó) sang `3.591135562815`. Đây là một semantic proposal cần
A/B và human review riêng; không dùng v15 để claim cải thiện hoặc thay đổi
final accepted. Hai thư mục A/B v2 tương ứng chỉ có evidence đang materialize,
không có đủ `build_report.json` và ZIP variant, nên bị loại khỏi bằng chứng
causal. Report/ZIP v15 vẫn được giữ như shadow artifact để truy vết:

- report: `artifacts/runs/vifinqa_answer_optimization_20260830/local_source_first_exact_only_v15_integrated_best/submission/build_report.json`;
- ZIP: `artifacts/runs/vifinqa_answer_optimization_20260830/local_source_first_exact_only_v15_integrated_best/submission.zip`;
- builder snapshot SHA-256: `d429ffae13eeb92f322b446a3acca374ccfe8ac47dc5bb0432ec78b5b53e3513`;
- `source_first_lookup.py` SHA-256: `d68ed471ce73db8fe8f0f9f263b0cfdcc3fb223ce80821980a8603ca61385528`.

## 7. Gate không được bỏ qua

Artifact integrated hiện tại là authorized best-effort competition candidate,
không phải strict release. Trạng thái strict vẫn là:

- không có human_verified semantic approval trong các candidate route;
- không có complete canonical Answer Certificate cho các route mới;
- proposal source replay không tự authorize entity, metric, period, scope hoặc
  table semantics;
- official answer/execution score của v14/current integrated chưa có;
- migration của canonical flow vẫn phải tách duplicate legacy execution và
  canonical Decimal re-execution theo docs/PIPELINE.md.

Muốn promote một route thành strict output cần cùng lúc có exact source
closure, semantic review hash-bound, evidence binding độc lập, complete E2E
certificate và release policy pass. Không dùng PARTIAL hoặc “đã replay” thay
cho VERIFIED.

 Sau khi workspace ổn định, full regression suite hiện tại chạy bằng
 `.venv/bin/python -m pytest -q`: `584 passed, 1 skipped`. Đây là kiểm tra
contract/runtime; nó không thay thế official scorer hoặc human semantic review.

## 8. Queue tiếp theo

1. Gửi artifact mới nhất
   `integrated_route_overlay_signed_lease_argmax_taxpaid_v1.zip` cùng split cho
   official scorer và lưu downloaded score/metadata; chỉ khi đó mới nói về
   accuracy delta.
2. Human semantic review cho Q175/Q261 và các answer changes của temporal,
   conditional và cross-entity; ưu tiên kiểm tra row meaning, scope và unit.
3. Review/confirm duplicate provenance và diễn giải semantic của Q323 theo
   table function/provenance; vẫn fail-closed khi các duplicate có giá trị
   khác nhau.
4. Recheck sign/magnitude contract của multi-entity `Chi phí bán hàng` (Q858)
   bằng A/B hoàn chỉnh và semantic review; chưa dùng v15 answer `3.591...`.
5. Giữ multi-entity positive-threshold ở disabled/pending vì A/B sạch nhưng
   resolved=0 và answer/tier diff=0; chỉ mở lại sau khi có contract mới.
6. Tiếp tục audit composition/missing operands và staged formula theo family;
   không hạ global threshold và không mở rộng 19 argmax cases còn
   fail-closed trước khi có contract/A-B riêng.
7. Conditional-count A/B đã đạt terminal gates; giữ route narrow và audit
   tiếp 19 argmax cases còn fail-closed theo failure family (14 row-family,
   4 unsupported formula/composition, 1 tie), chọn tối đa một contract cho
   A/B mới.
8. Q879 đã pass exact source replay nhưng signed-max versus absolute-payment
   semantics vẫn là một explicit review dimension; không mở rộng alias này
   thành generic schedule relaxation.

## 9. Artifact index chính

- Canonical topology: docs/PIPELINE.md.
- Research boundary: docs/research/README_VI.md.
- Official v11 build report:
  submission_ready/vifinqa_execution_optimized_v11_20260830.build_report.json.
- Official v11 ZIP:
  submission_ready/vifinqa_execution_optimized_v11_20260830.zip.
- Clean composed-total A/B:
  artifacts/runs/vifinqa_answer_optimization_20260830/composed_total_ab_v1/.
- Clean financial-liability A/B:
  artifacts/runs/vifinqa_answer_optimization_20260830/financial_liability_total_ab_v2/.
- Integrated v14:
  artifacts/runs/vifinqa_answer_optimization_20260830/local_source_first_exact_only_v14_maturity_final/.
- Multi-entity threshold negative A/B:
  artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_threshold_ab_v1/.
- Receivables positive A/B:
  artifacts/runs/vifinqa_answer_optimization_20260830/receivables_total_ab_v1/.
- Multi-entity direct aggregation positive A/B:
  artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_direct_aggregation_ab_v1/.
- Multi-entity outstanding-share threshold A/B:
  artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_share_threshold_ab_v2/.
- Multi-entity operating-lease threshold A/B:
  artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_lease_threshold_ab_v1/.
- Multi-year argmax/period-extreme A/B:
  artifacts/runs/vifinqa_answer_optimization_20260830/argmax_strict_ab_v4/.
- Multi-entity conditional positive-count A/B:
  artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_conditional_count_ab_v4/.
- Latest bounded lease plus strict-argmax overlay:
  artifacts/runs/vifinqa_answer_optimization_20260830/integrated_route_overlay_signed_lease_argmax_v1/.
  artifacts/runs/vifinqa_answer_optimization_20260830/integrated_route_overlay_signed_lease_argmax_v1.zip.
- Argmax tax-paid alias/context A/B:
  artifacts/runs/vifinqa_answer_optimization_20260830/argmax_tax_paid_ab_v1/.
- Latest bounded lease plus strict-argmax plus Q879 overlay:
  artifacts/runs/vifinqa_answer_optimization_20260830/integrated_route_overlay_signed_lease_argmax_taxpaid_v1/.
  artifacts/runs/vifinqa_answer_optimization_20260830/integrated_route_overlay_signed_lease_argmax_taxpaid_v1.zip.
- Argmax tax-paid research report:
  docs/research/ARGMAX_TAX_PAID_SOURCE_FIRST_ABLATION_V1.md.
- Final accepted candidate:
  artifacts/runs/vifinqa_answer_optimization_20260830/local_source_first_exact_only_final_accepted_r1/.
- Current immutable build snapshot:
  /tmp/vifinqa-final-accepted.doq8mK.

## 10. Superseding clean A/B: multi-entity positive-threshold v3

The earlier threshold result above was a valid negative control for snapshot
`c06d712/8139a6`, but its alias registry was initialized from a copied `/tmp`
script path and therefore could not recover the full issuer list. It must not
be used to reject the corrected route. A new same-snapshot A/B v3 fixed that
reproducibility defect by resolving `code_stock.csv` from the source checkout
when the snapshot-relative default is absent.

The corrected artifact is recorded in
`docs/research/MULTI_ENTITY_THRESHOLD_SOURCE_FIRST_ABLATION_V1.md` and
`artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_threshold_ab_v3/`.
Both arms used builder SHA
`d429ffae13eeb92f322b446a3acca374ccfe8ac47dc5bb0432ec78b5b53e3513` and
source-first SHA
`d68ed471ce73db8fe8f0f9f263b0cfdcc3fb223ce80821980a8603ca61385528`, with
`changed_during_build=false`. The variant resolved one four-issuer question,
replayed both consolidated and separate scopes, and changed exactly Q949 from
`6.5e-11` to `3.0`; both arms passed 1,012/1,012 validation and ZIP
integrity. The independent replay found the same predicate map in both
scopes: ACB/MBB/BID pass and EIB fails, hence count `3`.

Decision: keep `source_first_multi_entity_threshold_v1` enabled in the
authorized best-effort candidate lane, subject to its existing exact alias,
year, source replay and scope-invariance guards. This remains one local
source-replayed answer change, not `Answer Accuracy +1` or `Execution Accuracy
+1`: no strict certificate, human semantic approval or official scorer result
exists. The old negative A/B remains historical evidence; it is superseded for
the route decision by v3, while the threshold-off final artifact remains a
separate conservative release snapshot until an authoritative score is run.

## 11. Clean A/B: multi-entity equity selector then current-tax lookup

The selector family for Q536 is now implemented as
`source_first_multi_entity_selector_v1`. The route recognizes only the bounded
question shape, recovers `GVR/DPM/HT1/NKG` from the source-backed alias
registry, replays equity in both reporting scopes, and requires one invariant
winner before looking up the output metric. The trailing-digit alias fix is
needed for `HT1`; without it the runtime issuer union is incomplete.

The clean A/B artifact is
`artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_selector_ab_v3/`
and the detailed report is
`docs/research/MULTI_ENTITY_SELECTOR_SOURCE_FIRST_ABLATION_V1.md`. Both arms
passed `valid=true`, replayed 1,012/1,012 questions, had `errors=[]`, covered
146,246/146,246 source-line-map entries, and passed ZIP integrity. The
serialized diff is exactly one answer: Q536 changes from
`-60.755885161` / `semantic_cell_heuristic` to `688.075163368` /
`source_first_multi_entity_selector_v1`. Variant telemetry is
`questions_considered=1`, `questions_resolved=1`, four tickers, and one
scope-invariant selector winner.

The selected issuer is GVR: consolidated equity is
`54,977.202916058` billion VND and separate equity is
`43,387.438797510` billion VND, both above DPM, HT1, and NKG. The emitted
output is GVR consolidated current income-tax expense
`688.075163368` billion VND. Because the question does not specify reporting
perimeter and the output tax value is not established as scope-invariant, the
route records `unqualified_question_canonical_consolidated`,
`PARTIAL_SCOPE_ASSUMPTION`, and `promotion_allowed=false`. It is authorized
best-effort candidate evidence only; it is not strict `VERIFIED`.

Decision: keep this narrow route enabled in the best-effort candidate lane,
but do not merge its answer into a strict release or call it an official score
delta. The local workspace still has no gold/scorer for this split and no
Kaggle submission was made. Q465/Q539/Q553 remain pending because their
debt/equity selector and interest-coverage output need a separate safe scope
and row-binding contract.

## 12. Clean A/B: multi-entity debt/equity selector and interest coverage

The follow-up ratio family is implemented as
`source_first_multi_entity_ratio_selector_v1`. It replays total liabilities and
equity for every planned issuer in both consolidated and separate primary
coded statements, requires one unique invariant debt/equity winner, then
replays profit before tax and interest expense for that winner and calculates
`(PBT + interest) / interest`.

The clean artifact is
`artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_ratio_selector_ab_v4/`
with detailed report
`docs/research/MULTI_ENTITY_RATIO_SELECTOR_SOURCE_FIRST_ABLATION_V1.md`.
Both arms passed valid 1,012/1,012 replay, `errors=[]`, complete
146,246-entry source-line-map coverage, and ZIP integrity. Exactly two
answers changed: Q465 and Q553 move from the semantic-cell fallback `0.39` to
`8.138020523057806` using the source-first ratio protocol. Q539 is not changed:
its planner retained only PLX and the alias/list completeness gate leaves it
unresolved rather than answering from a partial PLX/PVT recovery.

Variant telemetry is `questions_considered=3`, `questions_resolved=2`,
`questions_unresolved_or_ambiguous=1`, two three-issuer replays, two invariant
selector winners, and `promotion_allowed=false`. PLX wins the debt/equity
selector in both scopes; its consolidated PBT and interest rows produce
`8.138020523057805...` times. The separate output is
`17.9714760786749...`, so the answer remains an explicit canonical-consolidated
best-effort policy rather than strict semantic verification.

Decision: enable this narrow route in the authorized best-effort candidate
lane, keep Q539 fail-closed, and do not report this as Answer Accuracy +2,
Execution Accuracy +2, or an official score delta. No gold/scorer, complete
strict certificate, human semantic approval, or Kaggle submission is present.

## 13. Clean A/B: multi-entity outstanding-share threshold

The next conditional-count family is implemented as
`source_first_multi_entity_share_threshold_v1`. It recognizes only a bounded
one-year question asking how many named issuers exceed an outstanding-share
threshold at year end. The route requires complete source-backed issuer
recovery, a current-year financial-note/detail/schedule row, exact share-unit
replay, and no duplicate or ambiguous row. `công ty mẹ` selects the separate
scope; an unqualified question must have an identical pass/fail vector in
consolidated and separate scope before the consolidated evidence is emitted.

The clean integrated A/B artifact is
`artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_share_threshold_ab_v2/`
with detailed report
`docs/research/MULTI_ENTITY_SHARE_THRESHOLD_SOURCE_FIRST_ABLATION_V1.md`.
Both arms used the same persisted implementation and input manifest at
`multi_entity_share_threshold_ab_v2/snapshot/`, including builder SHA
`e8f1729d04fc7d62a549064fa4d7743f6a29f11bc0e84ce8668c5b331f847d64`,
source-first SHA
`d68ed471ce73db8fe8f0f9f263b0cfdcc3fb223ce80821980a8603ca61385528`,
questions SHA
`64a428d90a8c5ad5d36a397d2de3b6e3aa4e4c1224dcdcb118fe3a4fca056ff0`,
code-stock SHA
`c2de8de56276f2bc4161fee3270b1cbbd8bec2428f79460a305a782e3d18b626`, full
asset SHA
`617ae044499907cf302519b7ff6fb96fb86ab70ff08e6fdcb0e6a3f8df5251f7`, and
source-line-map SHA
`533cc779f9565f43dfc961ad03d7bdff65adbd3728ee7a60b61651d495c4c615`.
`changed_during_build=false` in both reports. Control disabled only the new
share-threshold route; variant used the same inputs with the route enabled.

The serialized diff is exactly two answer records:

| Question | Control | Variant | Source-first interpretation |
|---:|---|---|---|
| Q990 | `1.0` / `program_multi_entity_plan` | `3.0` / `source_first_multi_entity_share_threshold_v1` | NLG, DXG and SNZ exceed 350 million; VPI does not |
| Q1005 | `2.0` / `program_multi_entity_plan` | `1.0` / `source_first_multi_entity_share_threshold_v1` | Only MWG exceeds 400 million |

Variant telemetry is `questions_considered=2`, `questions_resolved=2`, four
issuers per question, one explicit-parent scope case, one unqualified
scope-invariant case, and pass counts `3` and `1`. Independent Decimal replay
of the variant evidence against the 146,246-table asset reproduced the exact
vectors:

- Q990, 2021 separate: `219,999,780 / 382,940,013 / 596,025,562 /
  376,491,800` → `3` above `350,000,000`;
- Q1005, 2020 consolidated: `452,605,894 / 274,744,063 / 227,442,803 /
  268,631,965` → `1` above `400,000,000`.

For Q1005, consolidated and separate source inventory produce the same
predicate vector (`true, false, false, false`), satisfying the unqualified
scope guard. The relevant coordinates are retained in
[Q990 evidence](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_share_threshold_ab_v2/variant/submission/data/q0990_evidence.csv)
and
[Q1005 evidence](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_share_threshold_ab_v2/variant/submission/data/q1005_evidence.csv).

Both arms passed `valid=true`, 1,012/1,012 query replay, `errors=[]`, and
146,246/146,246 source-line-map coverage with `missing=0`, `extra=0`, status
`PASS`. ZIP integrity passed for both arms; SHA-256 is
`32599f202cd30bd8381e7b9865a4e2b0f98ca37cc834b3202a8d177a2574053` for control
and
`31ba4437ef20b3a7bb6e837b4c2f7b58c2748f70c2e1b24beac21e5c90f900e2` for
variant. The variant remains `PARTIAL=961`, `UNRESOLVED=51`,
`certificate=null`, and `promotion_allowed=false`.

Decision: keep this narrow route enabled in the authorized best-effort
candidate lane. This is exactly two local source-replayed prediction changes,
not Answer Accuracy +2, Execution Accuracy +2, or an official score delta.
There is still no matching gold/scorer, strict certificate, human semantic
approval, or Kaggle submission. Q932 is now covered by the separate clean
lease-threshold A/B in section 14. The next research gate is the existing
metric-specific `source_first_multi_entity_conditional_count_v1` family, with
the same fail-closed requirement for missing/ambiguous issuer evidence.

## 14. Clean A/B: multi-entity operating-lease maturity threshold

Q932 is the next bounded conditional-count family after the share threshold.
The route `source_first_multi_entity_lease_threshold_v1` recognizes one exact
year, a complete four-ticker list, explicit parent-company scope, the operating
lease commitment context, and a one-year maturity bucket. It replays each
current separate-scope source cell in million VND, converts to billion VND,
and applies the strict `> 40` predicate. A generic liquidity maturity table,
missing unit, missing operating-lease context, or incomplete alias list is
rejected.

The clean artifact is
`artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_lease_threshold_ab_v1/`
with detailed report
`docs/research/MULTI_ENTITY_LEASE_THRESHOLD_SOURCE_FIRST_ABLATION_V1.md`.
Both arms used the same persisted snapshot. Builder SHA is
`3fafd53281efd46e57d8e543890de9a81131a45de414b8784cc76fe52a42c490`,
source-first SHA is
`23805052c5893c6032eba8b8126b743cd92c7071fcbe16f8b3e6a5dbde66290b`,
questions SHA is
`64a428d90a8c5ad5d36a397d2de3b6e3aa4e4c1224dcdcb118fe3a4fca056ff0`,
code-stock SHA is
`c2de8de56276f2bc4161fee3270b1cbbd8bec2428f79460a305a782e3d18b626`, full
asset SHA is
`617ae044499907cf302519b7ff6fb96fb86ab70ff08e6fdcb0e6a3f8df5251f7`, and
source-line-map SHA is
`533cc779f9565f43dfc961ad03d7bdff65adbd3728ee7a60b61651d495c4c615`.
Both reports record `changed_during_build=false`. Control disabled only the
lease route; variant enabled it with identical inputs and other candidate
routes.

The serialized submission diff is exactly one record:

| Question | Control answer / tier | Variant answer / tier | Interpretation |
|---:|---|---|---|
| Q932 | `0.0` / `program_multi_entity_plan` | `2.0` / `source_first_multi_entity_lease_threshold_v1` | KLB and NAB exceed 40 billion VND; MBB and HDB do not |

Variant telemetry is `questions_considered=1`, `questions_resolved=1`, four
replayed issuers, `threshold_pass_count_2=1`, and `explicit_scope=1`. The
independent coordinate replay gives:

| Issuer | Row | Raw value | Converted value (billion VND) | `> 40` |
|---|---|---:|---:|---|
| MBB | `- đến hạn trong 1 năm` | 31,007 million | 31.007 | false |
| HDB | `- Đến hạn trong 1 năm` | 17,186 million | 17.186 | false |
| KLB | `Trong vòng 1 năm` | 49,649 million | 49.649 | true |
| NAB | `Đến một năm` | 79,657 million | 79.657 | true |

The four source UIDs, row/column coordinates, and raw values are retained in
[Q932 evidence](../../artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_lease_threshold_ab_v1/variant/submission/data/q0932_evidence.csv).
The replay verified each source file's recorded SHA-256. KLB's table header
does not carry a unit, so the route used its hash-checked document declaration
`Đơn vị tính: triệu VND`; this is why the unit provenance gate is materially
stronger than trusting retrieval metadata.

Both arms passed `valid=true`, 1,012/1,012 query replay, `errors=[]`, and
146,246/146,246 source-line-map coverage with no missing or extra entries.
Both ZIPs passed `unzip -t`; control SHA-256 is
`d2d26c064a054066e2323fb72596adaf3e7330d4f624d7217e7c278242f55713`, and
variant SHA-256 is
`cad1a6f8646f93ae27ff4679cb87cee70b90562d061638d7c95aa43b29e1bfbd`.
The variant remains `PARTIAL=961`, `UNRESOLVED=51`, with no strict
certificate and `promotion_allowed=false`.

Decision: keep this narrow lease route enabled in the authorized best-effort
candidate lane. This is one local source-replayed prediction change, not
Answer Accuracy +1, Execution Accuracy +1, or an official score delta. No
matching gold/scorer, human semantic approval, or Kaggle submission exists.
The next family to audit is the existing metric-specific conditional-count
route, preserving the same exact context, unit, period, and provenance gates.

## 14. Full-corpus source-first route overlay (2026-08-30)

The first integrated full-corpus run exposed an input-boundary confound: the
legacy run hydrated 29,428 candidate UIDs, while the clean multi-entity A/B
runs hydrated all 146,246 structured table lines. With the same immutable
builder snapshot and `--structured-table-filter all`, the source-first direct
aggregation lane resolved 6/6, selector 1/1, threshold 1/1, ratio selector
2/3, period extreme 13/29, and conditional count 1/1. The full run itself
produced 48 answer diffs against the accepted baseline; generic full-corpus
ranking accounted for 27 diffs outside the route allow-list, so those rows
were deliberately not promoted.

The bounded materializer
`scripts/research/materialize_validated_route_overlay_v1.py` keeps the
accepted 1,012-row submission as its base and copies only explicit route rows
whose source build tier matches the expected protocol. The primary overlay
replaces 21 rows; the separate expense-magnitude sensitivity replaces 22 rows
by adding Q858. Both pass 1,012/1,012 query replay, `errors=[]`, 1,012 CSV
members, and ZIP integrity. The detailed evidence and exact hashes are in
`docs/research/MULTI_ENTITY_ROUTE_OVERLAY_FULL_CORPUS_V1.md`.

Primary candidate:
`artifacts/runs/vifinqa_answer_optimization_20260830/integrated_route_overlay_signed_v1.zip`
(SHA-256
`7a0000714cf4989b75d606eb69a69d5aba416cb2605f10ab3e7354d71e34aed8`).
Sensitivity candidate:
`artifacts/runs/vifinqa_answer_optimization_20260830/integrated_route_overlay_expense_magnitude_v1.zip`
(SHA-256
`0b27ff41a219c5b7f89f365a23cf1204586c85df7dfe72e5da9801e38a7087a9`).

Decision: retain the primary overlay as the best local candidate for a later
authorized scorer run, keep the Q858 absolute-expense form separate, and do
not call either artifact a score gain. The workspace still has no matching
gold/scorer for this 1,012-question population, no strict certificate for
these routes, and no Kaggle submission. The next implementation gate is to
make full-corpus hydration available only to the explicit multi-entity/period
route functions, preserving generic candidate ranking on the frozen UID
boundary.

## 15. Integrated lease-threshold overlay extension

The clean operating-lease maturity A/B in section 14 has now been folded into
the bounded full-corpus overlay. Q932 changes exactly once, from `0.0` in the
accepted base to `2.0` under
`source_first_multi_entity_lease_threshold_v1`. The four replayed values are
MBB `31.007`, HDB `17.186`, KLB `49.649`, and NAB `79.657` billion VND; the
strict `>40` count is therefore two. The route's explicit context, period,
unit, issuer-completeness, and provenance gates remain active.

Latest signed primary candidate:

`artifacts/runs/vifinqa_answer_optimization_20260830/integrated_route_overlay_signed_lease_v1.zip`

It inherits the 21 previously allow-listed route replacements and adds Q932,
for 22 total answer changes relative to the accepted base. SHA-256 is
`4c38fa6f13ab6838614e66e36122ca9754ba3b0c14696a3351b8d3c96b43f84f`.

Latest expense-magnitude sensitivity:

`artifacts/runs/vifinqa_answer_optimization_20260830/integrated_route_overlay_expense_magnitude_lease_v1.zip`

It inherits the 22-row expense-magnitude overlay and adds Q932, for 23 total
answer changes. SHA-256 is
`28af95b8c0170a2d45b3607c94b734f8ec4eefe763fe5360ea7b625f16b327ce`.

Both artifacts contain 1,012 prediction CSVs plus `submission.json`, pass
ZIP integrity and full local validation (`valid=true`, 1,012/1,012 records,
`errors=[]`). They remain authorized best-effort prediction candidates only:
there is no matching hidden-label gold/scorer, strict certificate, human
semantic approval, official score, or Kaggle submission. Q858 remains signed
in the primary candidate and absolute-valued only in the sensitivity. The
next research gate is selective full-corpus hydration inside the route layer,
followed by a holdout precision check; Q539 remains fail-closed.

## 16. Clean A/B: multi-entity conditional positive-count

The metric-specific conditional-count route is now backed by a terminal same-
snapshot A/B. `source_first_multi_entity_conditional_count_v1` recognizes only
one exact year, at least two explicitly named issuers, explicit parent-company
scope, the operating-cash-flow metric, and the natural-language positive
(`dương`) predicate. It rejects threshold, ranking, ratio, percentage,
comparison, missing-issuer, duplicate-source, ambiguous-scope, and row/unit
drift cases. Every issuer cell is hydrated from its own current structured
table before the strict `> 0` predicate is applied.

The clean artifact is
`artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_conditional_count_ab_v4/`
with detailed report
`docs/research/MULTI_ENTITY_CONDITIONAL_COUNT_SOURCE_FIRST_ABLATION_V1.md`.
The persisted snapshot records builder SHA
`ed5329192b8f6b5d5c5dd54d205fdb6ba26c674c4232c6536c7edd8e0bd2fd1c`,
source-first SHA
`d68ed471ce73db8fe8f0f9f263b0cfdcc3fb223ce80821980a8603ca61385528`, full
asset SHA
`617ae044499907cf302519b7ff6fb96fb86ab70ff08e6fdcb0e6a3f8df5251f7`, and
source-line-map SHA
`533cc779f9565f43dfc961ad03d7bdff65adbd3728ee7a60b61651d495c4c615`.
Both arms used the same persisted inputs and recorded
`changed_during_build=false`; control disabled only the new route and variant
enabled only that route under the same other flags.

The serialized diff is exactly one answer/tier pair:

| Question | Control | Variant | Source-first interpretation |
|---:|---|---|---|
| Q973 | `2.0` / `program_multi_entity_plan` | `1.0` / `source_first_multi_entity_conditional_count_v1` | HSG positive; HPG and MSR negative |

Variant telemetry is `questions_considered=1`, `questions_resolved=1`,
`questions_skipped_not_eligible=1011`, with three issuer cells and
`positive_count_1=1`. Independent replay of the exact coordinates gives:

| Issuer | Raw cell | Unit multiplier | Normalized value | `> 0` |
|---|---:|---:|---:|---|
| HSG | `1,071,767,875,098` | `1` | `1,071,767,875,098` | true |
| HPG | `-761,380,984,482` | `1` | `-761,380,984,482` | false |
| MSR | `-259,020,090` | `1,000` | `-259,020,090,000` | false |

The vector is therefore `[true, false, false]` and the replayed count is
`1`. The evidence CSV retains the table UIDs, row/column coordinates and
source multipliers; an independent full-asset check verified the three source
file hashes and reproduced the emitted answer.

Both arms passed `valid=true`, 1,012/1,012 query replay, `errors=[]`, and
146,246/146,246 source-line-map coverage with no missing or extra entries.
`unzip -t` passed for both ZIPs. Their SHA-256 values are
`ae99244fc148e1c6e9dc2f7d8185233dfbba7de50904296f2e143c1e83481204`
(control) and
`06ff171ad133418ee1445f68f33bfb71d8cd2fef8d14cef49dfeea97249de551`
(variant). The latest focused rerun was
`3 passed, 92 deselected`; the current workspace full regression is
`584 passed, 1 skipped`.

The route remains `PARTIAL=1010`, `UNRESOLVED=2`, without a strict
certificate, human semantic approval, or `promotion_allowed`. Decision:
retain this narrow route in the authorized best-effort candidate lane and
measure it against the official scorer later. This is one locally replayed
prediction change, not Answer Accuracy +1, Execution Accuracy +1, or an
official score delta. The next research action is the argmax/period-extreme
unresolved cohort: group its 20 rejected cases by failure family and select
one bounded contract for a new immutable A/B; do not relax the global gates.

## 17. Bounded overlay extension: strict argmax adapter

The strict argmax A/B in
`artifacts/runs/vifinqa_answer_optimization_20260830/argmax_strict_ab_v4/`
has now been materialized as an explicit overlay on top of the latest signed
lease candidate. The A/B variant has 43 typed plans, resolves 23 cohorts, and
independently replays 93/93 source cells across 23/23 cohorts. Its adapter
tier is deliberately preserved as `program_arg_extreme_period_v1`; the
materializer does not relabel it as the builder's
`source_first_period_extreme_v1` route.

The output is
`artifacts/runs/vifinqa_answer_optimization_20260830/integrated_route_overlay_signed_lease_argmax_v1/`
and ZIP
`artifacts/runs/vifinqa_answer_optimization_20260830/integrated_route_overlay_signed_lease_argmax_v1.zip`.
It inherits the 22 allow-listed replacements from the signed lease overlay
and adds exactly these 23 argmax IDs:

`Q813, Q832, Q841, Q850, Q860, Q874, Q876, Q890, Q897, Q904, Q906, Q910,
Q929, Q933, Q936, Q946, Q948, Q953, Q974, Q981, Q997, Q999, Q1000`.

The resulting manifest records `total_replacement_count=45`,
`new_numeric_arithmetic_invented=false`, `human_verified=false`, and
`promotion_allowed=false`. A direct comparison against the inherited base
found exactly 23 answer/tier changes and no unrelated row changes. The final
overlay passed validation with `records=1012`, `queries_replayed=1012`,
`errors=[]`, contains 1,012 evidence CSV members, and passed `unzip -t`.
The ZIP SHA-256 is
`39bd7c75a2d12233320b8cf7e1e704f8141e52e4a4bbf235bbbcb2a07ec20586`.

This is the strongest current local best-effort candidate by route coverage,
not a measured score improvement. It still has no matching gold/scorer,
strict certificate, human semantic approval, or Kaggle submission. The
official baseline remains Answer Accuracy `0.17` and Execution Accuracy
`0.17`; the 45 replacements must be judged by the authoritative scorer before
any score claim. The separate Q858 expense-magnitude sensitivity remains
separate and is not silently changed by this overlay.

## 18. Bounded argmax extension: tax-paid alias and cash-flow context

The next unresolved argmax family was narrowed to Q879, where the typed metric
hint kept the wrapper `giá trị` and the 2024/2025 structured records were
classified as generic schedules. The immutable A/B is recorded at
`artifacts/runs/vifinqa_answer_optimization_20260830/argmax_tax_paid_ab_v1/`
and the detailed report is
`docs/research/ARGMAX_TAX_PAID_SOURCE_FIRST_ABLATION_V1.md`.

The strict adapter accepts the generic schedule only when the exact row is
`Thuế thu nhập doanh nghiệp đã nộp` and the source title contains both
`Lưu chuyển tiền` and `hoạt động kinh doanh`. It also derives the exact row
alias from the accounting phrase, keeps one separate VNM scope across all
five years, requires one source multiplier and rejects ties. This is an
accounting-family contract, not a Q-ID value lookup and not a generic schedule
relaxation.

The current control disabled the argmax adapter and the variant enabled it on
the same complete 146,246-table asset. Both arms passed `valid=true`, replayed
1,012/1,012 questions and had `errors=[]`; the variant accepted 24 argmax
plans, consisting of the 23 previously validated strict argmax IDs plus Q879.
The route-local same-snapshot check with the new alias/context rules removed
rejected Q879 as `REJECTED_NO_COHERENT_ROW_FAMILY`; the patched route accepted
it as `program_arg_extreme_period_v1`.

Independent replay of the five exact VNM separate rows gives signed values:

`2019=-2,025,224,469,158; 2022=-1,903,065,886,321; 2023=-1,441,600,595,087;
2024=-1,997,458,922,345; 2025=-1,353,040,369,936.`

The unique signed maximum is 2025. Parentheses in the source indicate
negative cash-flow values; the route intentionally does not interpret
“highest” as absolute payment magnitude. That signed-versus-absolute choice
remains an explicit semantic review item.

The result was materialized as one explicit overlay replacement on top of the
45-row argmax/lease candidate:

`artifacts/runs/vifinqa_answer_optimization_20260830/integrated_route_overlay_signed_lease_argmax_taxpaid_v1/`

and

`artifacts/runs/vifinqa_answer_optimization_20260830/integrated_route_overlay_signed_lease_argmax_taxpaid_v1.zip`

The overlay contains 46 total replacements, passes 1,012/1,012 validation and
`unzip -t`, and has ZIP SHA-256
`63a43ad052871094d898ee6ee6feb1f0f8ff1da658dbd426b77b20d346a42f88`.
`new_numeric_arithmetic_invented=false`, `human_verified=false` and
`promotion_allowed=false` remain in the manifest. The current full workspace
regression after the materializer mapping is `584 passed, 1 skipped`.

Decision: retain Q879 in the authorized best-effort candidate lane and do not
claim an official score delta. The remaining argmax queue is now 19 rejected
plans: 14 no-coherent-row-family cases, 4 unsupported formula/composition
cases and 1 tie. The next experiment must target one family contract at a
time and preserve the same strict source, scope, unit, replay and promotion
gates.

## 19. Bounded argmax extension: related-party service revenue

The next family-specific probe targeted Q1008, whose typed metric asks for the
highest related-party service revenue year for SSH. The source row is the
disclosed aggregate `Doanh thu cung cấp dịch vụ`; its `bên liên quan` qualifier
is carried by the table source title. A narrow adapter rule now normalizes the
2020 `financial_note` record and the 2021--2023 `related_party_schedule`
records into one family only when the exact aggregate row and related-party
transaction context are both present. Generic notes or schedules do not pass
this rule.

The detailed report is
`docs/research/ARGMAX_RELATED_PARTY_REVENUE_SOURCE_FIRST_ABLATION_V1.md`.
The new full variant is
`artifacts/runs/vifinqa_answer_optimization_20260830/argmax_related_party_revenue_ab_v1/variant/`.
It was paired with the completed pinned control
`artifacts/runs/vifinqa_answer_optimization_20260830/argmax_related_party_revenue_ab_v2/control/`,
which has the same builder SHA `97e91fba…`, source lookup SHA `d68ed471…`,
full structured asset and source-line map; the canonical period route and the
other experimental routes were disabled in that control. Its ZIP SHA is
`a627760d3574bc9b23289b92dac30969a686f09f922dfbeab367d313dfc94c17`.

Both arms have `valid=true`, 1,012 records, 1,012 query replay,
`errors=[]`, and complete 146,246/146,246 source-line-map coverage. The
variant considers 43 typed argmax plans and accepts 25: the prior 23 strict
argmax IDs, Q879 from the preceding tax-paid extension, and the new Q1008.
The exact answer/tier diff against the pinned control is the 25-ID set recorded
in the detailed report; only Q1008 is the incremental change for this family.

Independent replay of Q1008 verifies four source UIDs, four exact row/column
coordinates, VND multiplier `1`, and the vector
`[11863882275, 61614783673, 62744631137, 88961131800]`. The unique signed
maximum is 2023. The source variant ZIP SHA is
`cf0dc1abfa04cea9bc3645c27530dab5273ff7d694721c5eefa914d695fae6fc`.

The change is materialized as one explicit replacement on top of the prior
46-row signed lease/argmax/tax-paid overlay:
`artifacts/runs/vifinqa_answer_optimization_20260830/integrated_route_overlay_signed_lease_argmax_taxpaid_relatedparty_v1.zip`.
The new manifest reports 47 total replacements, 1,012/1,012 replay,
`errors=[]`, 1,012 CSV members, `new_numeric_arithmetic_invented=false`,
`human_verified=false` and `promotion_allowed=false`. Its ZIP SHA is
`bc940f5615cc1cdd880a614e6c45e40180c0dfaee397a54971c36357152e5b27`.

Decision: retain this exact accounting-family rule in the authorized
best-effort candidate lane, but do not call it an official score gain. The
remaining argmax queue is 18 rejected plans: 13 no-coherent-row-family cases,
four unsupported formula/composition cases and one tie. The next action is
another one-family immutable A/B, with no global unit or table-kind relaxation.
The route-specific tests are green at 11 passed. After the concurrent
subsidiary-investment work settled, the full shared-worktree pytest suite is
green at 607 passed, 1 skipped.

## 20. Bounded multi-entity threshold extension: Q1002 interest expense

The next high-yield residual family was an explicit multi-entity threshold
question. The immutable A/B is recorded at
`artifacts/runs/vifinqa_answer_optimization_20260830/multi_entity_interest_threshold_ab_v3/`
and its route-specific analysis is
`docs/research/MULTI_ENTITY_INTEREST_THRESHOLD_SOURCE_FIRST_ABLATION_V1.md`.

The control disables the interest-threshold adapter and the variant enables
it against the same complete 146,246-table source asset. Both arms validate
1,012/1,012 rows with `errors=[]`; exactly one answer changes: Q1002 changes
from the generic `0` proposal to `2`.

The question names the parent companies AAA, NKG, DCM and DPM, year 2016,
and a threshold of more than 100 billion VND. Independent replay of the
four exact parent/separate tables finds interest expense values of
23.874478344, 141.639235578, 203.937110047 and 4.473655664 billion VND.
Only NKG and DCM pass the strict `> 100` predicate, so the count is 2. The
route requires explicit year, ticker, parent/separate scope, explicit unit,
and an income-statement or finance-note interest row; it does not generalize
to arbitrary threshold questions.

The result was materialized on top of the 47-row signed lease/argmax/
related-party candidate at
`artifacts/runs/vifinqa_answer_optimization_20260830/integrated_route_overlay_signed_lease_argmax_taxpaid_relatedparty_interest_q1002_v1/`
and
`artifacts/runs/vifinqa_answer_optimization_20260830/integrated_route_overlay_signed_lease_argmax_taxpaid_relatedparty_interest_q1002_v1.zip`.
It contains 48 unique replacements, validates 1,012/1,012 rows with
`errors=[]`, and passed ZIP integrity. ZIP SHA-256:
`2ad2cfd40e3f03307376209467cae2823ac53f6c8bfb45cfe56f8143a040e7bf`.

This is an authorized best-effort submission candidate only:
`human_verified=false`, `promotion_allowed=false`, and no local gold/scorer
or official leaderboard measurement is available. The replay count is a
structural/computational gate, not an accuracy score.

## 21. Selective hydration residual audit: Q827/Q927 provenance and Q994

The immutable selective-hydration A/B at
`artifacts/runs/vifinqa_answer_optimization_20260830/selective_route_hydration_ab_v2_immutable/`
produced 19 answer diffs on the complete 1,012-question population. The
diffs cluster into period-extreme (10), direct aggregation (6), selector (1),
threshold (1) and interest-threshold (1); this is evidence for family-level
research, not permission to promote all 19 rows.

Q827 and Q927 passed independent source-cell and arithmetic replay in the
direct-aggregation family. Adding their explicit route metadata to the
candidate changes provenance but not their numeric answers because the
inherited 48-row candidate already had the same values. The resulting
50-row provenance-enriched artifact is
`artifacts/runs/vifinqa_answer_optimization_20260830/integrated_route_overlay_signed_lease_argmax_taxpaid_relatedparty_interest_direct_v1.zip`
with SHA-256
`c25d870e9a0f59f71af71476fad1c69e84923ce1e161309d668e7bce00bae5da`.

Q994 is the next numeric candidate. Its exact five QNS consolidated segment
tables use the `Tổng cộng` column and the `Chi phí bán hàng` row for 2019--2023,
yielding 785.326185604, 654.113883340, 693.932847200, 868.297002640 and
805.491960579 billion VND. The unique maximum is 2022, answer
`868.29700264`. Although the structured record's local `unit_hint` is null,
each original extracted document independently declares the accounting
currency as VND and also has a `Đơn vị tính: VND` declaration. The header
pairs put the requested year in column 11 and the prior year in column 12 for
every table. This clears the source/unit/column audit. It was then
materialized as exactly one additional replacement on the 50-row
provenance-enriched candidate at
`artifacts/runs/vifinqa_answer_optimization_20260830/integrated_route_overlay_signed_lease_argmax_taxpaid_relatedparty_interest_direct_q994_v1/`
and
`artifacts/runs/vifinqa_answer_optimization_20260830/integrated_route_overlay_signed_lease_argmax_taxpaid_relatedparty_interest_direct_q994_v1.zip`.
The resulting candidate has 51 unique replacements, validates 1,012/1,012
rows with `errors=[]`, and passed ZIP integrity; ZIP SHA-256 is
`f4c4827931ae1a6d0fd1c88057cd602ab0d12ffdb4d679ebd718adc904a488fe`.

No score claim is made from these changes. The official baseline recorded in
this workspace remains Answer Accuracy `0.17` and Execution Accuracy `0.17`
until an authoritative scorer or leaderboard result is obtained.

## 22. Bounded direct-row extension: subsidiary investment

The next high-yield direct-lookup family targeted questions asking for
`đầu tư vào công ty con` in a parent-company separate report. The strict
adapter is implemented in `scripts/research/run_subsidiary_investment_variant_v1.py`.
It requires one ticker, one report year, explicit separate scope, the exact
subsidiary-investment row, current-period column, and a source-declared unit
or same-value unit anchor. It rejects generic investment rows, joint-venture
rows, conflicting duplicate values, missing units and unsupported scope.

The full-population A/B is in
`artifacts/runs/vifinqa_answer_optimization_20260830/subsidiary_investment_strict_ab_v1/`.
Control and variant share builder SHA `d1edacca…`, source lookup SHA
`72fd26a0…`, and the same 146,246-table asset/source-line-map inputs. Both
arms pass `valid=true`, replay 1,012/1,012 rows, have `errors=[]`, complete
source-line-map coverage and valid ZIPs.

Exactly four answer/tier rows change: Q76, Q77, Q238 and Q290. The source
replay independently verifies 4/4 UIDs, coordinates, ticker/year/scope,
units and Decimal outputs. The variant changes Q76 from `0` to `3126.89704`
(tỷ đồng), Q77 from `0` to `13976356` (triệu đồng), Q238 from `0` to
`1059688` (triệu đồng), and Q290 from the mis-scaled
`28075666712.31` to `28.07566671231` (trăm tỷ đồng). Q290 also has a
same-value VND unit anchor in a separate balance-sheet table.

The change is materialized as four explicit replacements on top of the
previous 47-row candidate:
`artifacts/runs/vifinqa_answer_optimization_20260830/integrated_route_overlay_signed_lease_argmax_taxpaid_relatedparty_subsidiary_v1.zip`.
The overlay contains 51 total replacements, 1,012/1,012 replay,
`errors=[]`, 1,012 CSV members, and ZIP SHA
`8602ac4d4a013bd355f896e44cf2105dbddd2537b9546af87dfd3035c15a418b`.
`new_numeric_arithmetic_invented=false`, `human_verified=false` and
`promotion_allowed=false` remain in the manifest.

Decision: retain the route in the authorized best-effort candidate lane, but
do not claim an official score gain. The detailed source/unit audit is in
`docs/research/SUBSIDIARY_INVESTMENT_SOURCE_FIRST_ABLATION_V1.md`.

## 22. Bounded subsidiary-investment extension: Q76/Q77/Q238/Q290

The strict subsidiary-investment A/B is recorded in
`docs/research/SUBSIDIARY_INVESTMENT_SOURCE_FIRST_ABLATION_V1.md` and at
`artifacts/runs/vifinqa_answer_optimization_20260830/subsidiary_investment_strict_ab_v1/`.
Both arms use the same 146,246-table source asset and replay 1,012/1,012
questions with `errors=[]`. The variant accepts four exact source rows and
rejects one conflicting duplicate case.

The four A/B diffs are Q76 `0 -> 3126.89704`, Q77 `0 -> 13976356`, Q238
`1059688 -> 1059688` (provenance-only), and Q290
`28075666712.31 -> 28.07566671231`. Independent source-file SHA, byte-slice,
coordinate and Decimal replay passes 4/4. The route requires explicit
parent/separate scope, one ticker/year, the exact subsidiary row, current
period column and declared unit; original-cost questions also require the
cost column.

The four rows were materialized on the prior 51-row candidate as
`artifacts/runs/vifinqa_answer_optimization_20260830/integrated_route_overlay_signed_lease_argmax_taxpaid_relatedparty_interest_direct_q994_subsidiary_v1.zip`.
The resulting candidate has 55 unique replacements, validates 1,012/1,012
with `errors=[]`, includes 1,012 CSV evidence members, passes `unzip -t`, and
has ZIP SHA-256
`c0c7c7124c0f07bc34994a48a990b031f90f924061d039b559fc1219dbe388fe`.

This remains `authorized_best_effort_submission_candidate` with
`human_verified=false` and `promotion_allowed=false`. It is not an official
score delta; no matching local gold/scorer is available.

## 23. Bounded argmax extension: OCB specific loan-loss provision expense

The next accounting-family A/B targeted Q928, where the exact row
`Trích lập dự phòng cụ thể cho vay khách hàng` appears in OCB separate reports
for 2017, 2018, 2019 and 2025. The 2017--2019 records are classified as
`debt_schedule`, while 2025 is `financial_note_detail`; a narrow contract now
normalizes only this row when its source context contains
`Chi phí dự phòng rủi ro tín dụng`. Generic detail/schedule records remain
fail-closed. The implementation and source audit are in
`docs/research/ARGMAX_PROVISION_EXPENSE_SOURCE_FIRST_ABLATION_V1.md`.

The immutable A/B is recorded at
`artifacts/runs/vifinqa_answer_optimization_20260830/argmax_provision_expense_ab_v1/`.
Both arms use builder SHA `d1edacca…`, source lookup SHA `72fd26a0…`, the
same 146,246-table asset and complete source-line map. Both validate 1,012 /
1,012 records, replay 1,012 / 1,012 queries with `errors=[]`, report
source-line-map `PASS`, and pass ZIP integrity. The strict variant sees 43
typed argmax plans and accepts 26; Q928 is the only incremental accounting
family change. The raw variant/control comparison has 26 expected argmax
cohort diffs plus collateral Q368/Q369 caused by wrapper-level proposal
selection; those two collateral rows are excluded from materialization.

Independent replay verifies Q928 values
`[112060694398, 633084224721, 822479834736, 2163777088772]` from four exact
OCB separate source UIDs, row 2 / column 1, VND context and a unique maximum
at 2025. The route was copied as exactly one allow-listed replacement onto the
existing 60-replacement candidate:
`artifacts/runs/vifinqa_answer_optimization_20260830/integrated_route_overlay_signed_lease_argmax_taxpaid_relatedparty_interest_direct_q994_subsidiary_conditional_provision_v1.zip`.
The new overlay has 61 total replacements, 1,012 / 1,012 replay,
`errors=[]`, 1,012 CSV members, and ZIP SHA
`565df40e5b8d62560e620dce466628697b8ecbd661a50dfc95f1e178149b6be6`.
An independent base/output comparison found exactly Q928 changed. This is
an authorized best-effort candidate only: `human_verified=false`,
`promotion_allowed=false`, and no official score delta is claimed.

## 24. Bounded argmax accounting aliases: Q829/Q900/Q971

The residual argmax pass tested narrow normalization/context contracts in the
full-population A/B at
`artifacts/runs/vifinqa_answer_optimization_20260830/argmax_accounting_alias_ab_v1/`.
The run's builder/source fingerprints differ from the earlier canonical
argmax snapshot, so its 29 raw diffs are not treated as one causal score
claim. The exact contract and independent replay are documented in
`docs/research/ARGMAX_ACCOUNTING_ALIAS_SOURCE_FIRST_ABLATION_V1.md`.

Both arms validate 1,012/1,012 records, replay 1,012/1,012 queries with
`errors=[]`, and pass ZIP integrity. Variant telemetry is 43 typed argmax
plans seen, 29 accepted, 9 cohort-rejected, 4 unsupported-contract and 1
unique-extreme tie. The accepted IDs include three new alias candidates:
Q829, Q900 and Q971; earlier cohort IDs are not counted again.

Independent replay accepts Q900 (MSN parent/separate, exact common-share row,
unique winner 2021) and Q971 (KHG parent/separate, exact brokerage-cost row in
short-term-payables context, unique winner 2023). Q829 is explicitly rejected:
the unscoped TTF `Lợi nhuận khác` question has both separate and consolidated
source values, so selecting separate is not semantically authorized. Q928 was
already established by section 23 and is not a new alias gain here.

Only Q900 and Q971 were materialized onto the 64-replacement candidate at
`artifacts/runs/vifinqa_answer_optimization_20260830/integrated_route_overlay_signed_lease_argmax_taxpaid_relatedparty_interest_direct_q994_subsidiary_conditional_provision_named_compensation_accounting_alias_v1/`.
The output has 66 unique replacements, validates and replays 1,012/1,012
with `errors=[]`, contains 1,012 CSV members, passes ZIP integrity, and has
ZIP SHA-256
`92127eb6c5de01aba31573cf44ce1be7d259ff1e6e3867bbea5b5ee666cccb02`.
The final manifest contains no Q829 entry. `new_numeric_arithmetic_invented=false`,
`human_verified=false`, and `promotion_allowed=false` remain in force.

Focused route tests pass (`18 passed`). This is an authorized best-effort
candidate, not a strict release or official score delta. The next residual
research is family-bounded audit of the nine no-coherent-row-family cases,
starting with explicit-parent Q822; no global table-kind relaxation is
authorized by this result.

## 25. Bounded argmax extension: SAB Crown payable Q921

The next residual `arg_extreme_period` audit targeted Q921, where the
question explicitly names SAB parent/separate scope and the related-party
supplier `Công ty Liên doanh TNHH Crown Sài Gòn`, but the legacy route had
selected an unrelated payable cell and returned `113224326586.0`. A narrow
source-bound alias now accepts only the exact Crown row in a source context
containing both `phải trả người bán` and `bên liên quan`; generic related-party
or supplier-payable rows remain closed. Full details are in
`docs/research/ARGMAX_CROWN_PAYABLE_SOURCE_FIRST_ABLATION_V1.md`.

The immutable full-population A/B is recorded at
`artifacts/runs/vifinqa_answer_optimization_20260830/argmax_crown_payable_ab_v1/`.
Control and variant each validate 1,012/1,012 records, replay 1,012/1,012
queries with `errors=[]`, cover all 146,246 source-line-map entries and pass
ZIP integrity. The fresh raw comparison has 31 answer diffs, but Q529 is an
unrelated wrapper-selection collateral and Q47 is provenance-only. The
incremental comparison against the prior accounting-alias candidate has
exactly one answer/full-record diff: Q921.

Independent replay verifies the exact separate SAB source vector
`[226245964160, 559509431031, 404695685526]` for 2019, 2021 and 2025 from
three `related_party_schedule` UIDs at row 3 / column 1, with source-map
coverage 3/3 and `failures=[]`. The unique maximum is 2021. This single row
was materialized onto the prior 59-replacement candidate as
`artifacts/runs/vifinqa_answer_optimization_20260830/integrated_route_overlay_signed_lease_argmax_taxpaid_relatedparty_interest_direct_q994_subsidiary_alias_q921_v1.zip`.
The output has 60 unique replacements, validates/replays 1,012/1,012 with
`errors=[]`, contains 1,012 evidence CSV members, passes `unzip -t`, and has
ZIP SHA-256
`92752a9e6cf1c4468529c3f11a8438bce814bdc3173e5a9ed40a24cba32997cf`.
Independent base/output comparison finds exactly Q921 changed:
`113224326586.0 -> 2021.0`.

The candidate remains
`authorized_best_effort_submission_candidate` with
`new_numeric_arithmetic_invented=false`, `human_verified=false` and
`promotion_allowed=false`. The post-patch regression is `652 passed, 1
skipped`; no official score delta is claimed. Next residual priority is Q989,
the explicit STB accrued-interest row family.

## 26. Bounded argmax extension: ASM other income Q822

The next available residual audit targeted Q822, an explicit ASM
parent/separate question for `Thu nhập khác` across 2016, 2021, 2022 and 2024.
The legacy answer was `30476603104.0`. A narrow source-first contract now
accepts only code 31 / `Thu nhập khác` in the same separate `income_statement`
family, with a bounded exact-source unit fallback for structured records whose
unit declaration is missing or OCR-corrupted. The fallback is gated to this
context and does not relax unrelated rows or table kinds. Full details are in
`docs/research/ARGMAX_OTHER_INCOME_SOURCE_FIRST_ABLATION_V1.md`.

The immutable same-snapshot A/B is recorded at
`artifacts/runs/vifinqa_answer_optimization_20260830/argmax_other_income_ab_v1/`.
Both arms use builder SHA `08b6551f…`, source lookup SHA `fed5d4a6…`, the same
146,246-table asset and complete source-line map. Both validate and replay
1,012/1,012 with `errors=[]` and pass ZIP integrity. The same-snapshot
comparison has 32 typed-argmax diffs and no non-argmax diffs. Variant telemetry
is 43 typed plans seen, 32 accepted, 6 cohort-rejected, 1 unique-extreme tie,
4 unsupported contracts, and 2 bounded raw-source unit fallbacks. These 32
variant diffs are an attribution boundary; only Q822 is independently replayed
and materialized by this section.

Independent replay checks the four exact source UIDs at lines 366, 408, 353
and 421, row 12 / column 3, with values
`[3188673987, 180044077757, 3734568921, 2401123931]`, same VND multiplier,
same separate scope, and a unique maximum at 2021. The replay artifact is
`artifacts/runs/vifinqa_answer_optimization_20260830/argmax_other_income_ab_v1/independent_replay_q822_v1.json`
and reports `status=PASS`, while `promotion_allowed=false`.

Exactly one allow-listed replacement was copied onto the prior 66-replacement
candidate. The new 67-replacement candidate is
`artifacts/runs/vifinqa_answer_optimization_20260830/integrated_route_overlay_signed_lease_argmax_taxpaid_relatedparty_interest_direct_q994_subsidiary_conditional_provision_named_compensation_accounting_alias_other_income_v1.zip`.
It has one new replacement, 66 inherited replacements, 1,012/1,012 replay,
`errors=[]`, 1,012 CSV members, passes `unzip -t`, and has SHA-256
`543fa06012bc3baccd79210b851945013d7735994051f63db873f5fda25c4539`. An
independent base/output comparison found exactly Q822 changed, and the final
route manifest contains no Q829 entry.

This remains an
`authorized_best_effort_submission_candidate`: `human_verified=false`,
`promotion_allowed=false`, `new_numeric_arithmetic_invented=false`, and no
official score delta is claimed. Focused route tests pass (`19 passed`) and
changed scripts compile. The Q989 accrued-interest family and other
family-bounded runs remain in progress; do not treat a running process as a
score gain until its artifact and gate audit is complete.

## 27. Bounded argmax extension: STB accrued interest Q989

The accrued-interest A/B completed for Q989, which asks for the highest
end-of-period customer-loan accrued interest for STB in 2017, 2022 and 2024.
The legacy answer was `130864302.0`. The source-first alias is restricted to
the exact `Các khoản lãi, phí phải thu` disclosure, consolidated STB scope,
the year-specific customer-loan-interest row, current-year column, and a
million-VND header. The structured classifier calls these tables
`debt_schedule`; that metadata is not used as semantic authorization. Full
details are in
`docs/research/ARGMAX_ACCRUED_INTEREST_SOURCE_FIRST_ABLATION_Q989_V1.md`.

The immutable full-population A/B is recorded at
`artifacts/runs/vifinqa_answer_optimization_20260830/argmax_accrued_interest_ab_v2/`.
Both arms use builder SHA `8588dc20…`, source lookup SHA `72fd26a0…`, the same
146,246-table asset and complete source-line map. Both validate/replay
1,012/1,012 with `errors=[]` and pass ZIP integrity. The variant sees 43 typed
argmax plans, accepts 32, rejects 6 cohort cases, sees 1 tie and 4 unsupported
contracts. This A/B is an attribution boundary; only Q989 is independently
replayed and materialized here.

Independent replay checks the exact UIDs at source lines `1554`, `1678` and
`2054`, row 1 / column 1, with raw values `[22399323, 3370271, 3390704]`
million VND and a unique maximum at 2017. The replay artifact is
`artifacts/runs/vifinqa_answer_optimization_20260830/argmax_accrued_interest_ab_v2/independent_replay_q989_v1.json`,
with `status=PASS` and `promotion_allowed=false`.

Exactly one allow-listed replacement was copied onto the prior 67-replacement
candidate. The new 68-replacement candidate is
`artifacts/runs/vifinqa_answer_optimization_20260830/integrated_route_overlay_signed_lease_argmax_taxpaid_relatedparty_interest_direct_q994_subsidiary_conditional_provision_named_compensation_accounting_alias_other_income_accrued_interest_q989_v1.zip`.
It has one new and 67 inherited replacements, validates/replays 1,012/1,012,
contains 1,012 CSV members, passes `unzip -t`, and has SHA-256
`cc507808a8b0262942c51f968fc4ff76dbf88d72360e86ac5607155821ca1a3a`. An
independent base/output comparison found exactly Q989 changed, and the route
manifest includes Q989 but no Q829.

This remains an
`authorized_best_effort_submission_candidate`: `human_verified=false`,
`promotion_allowed=false`, `new_numeric_arithmetic_invented=false`, and no
official score delta is claimed. The tax-payable family remains in progress;
the completed subsidiary selector is not promoted until its artifact and
answer-level diff are audited.

## 28. Answer-level selector shadow A/B: no output delta

The completed subsidiary answer-level selector shadow was audited against its
disabled control at
`submission_ready/vifinqa_answer_level_selector_control_20260831/` and
`submission_ready/vifinqa_answer_level_selector_optin_20260831_r4/`.
Both submissions validate/replay 1,012/1,012 with `errors=[]`. The opt-in
selector reports `selected=296`, `baseline_fallback=296` and `abstain=716`,
but an exact JSON-value comparison finds `0` full-record diffs and `0` answer
diffs against control. The subsidiary route itself accepted 4 of 5 family
candidates and rejected 1 conflicting duplicate, but it contributes no new
candidate delta in this shadow run.

The selector remains diagnostic only: `strict_answer_authorized=false`,
`release_authorized=false`, `training_eligible=false` and
`promotion_allowed=false`. Nothing from this run is added to the 68-row
candidate. This negative result closes the current selector experiment until a
newly isolated input or contract changes the control/variant output.

## 29. Bounded loan-provision extension: HBC Q324

The next direct-lookup family targeted Q324, where the legacy candidate chose
an unrelated HBC semantic cell and returned `-154.380696547`. The question
asks for the end-2024 provision for short-term loans of the HBC parent company
in billion VND. A narrow route now accepts only the exact titled schedule
`Chi tiết dự phòng các khoản cho vay ngắn hạn`, the current `31/12/2024VND`
column and a final total whose three detail rows pass a Decimal checksum. Full
details are in
`docs/research/LOAN_PROVISION_Q324_SOURCE_FIRST_ABLATION_V1.md`.

The paired full-population A/B is recorded at
`artifacts/runs/vifinqa_answer_optimization_20260830/loan_provision_ab_v1/`.
Control and variant use the same builder/source fingerprints, 146,246-table
asset, complete source-line map and full-population inputs. Both validate and
replay 1,012/1,012 with `errors=[]`, cover 146,246/146,246 source-line
entries and pass ZIP integrity. Variant family telemetry is 4 cases seen, 1
accepted and 3 conflicting duplicates rejected. The exact control-to-variant
comparison has one full-record and answer diff only: Q324, `0.0 ->
80.864684721`; no collateral diff is present.

Independent replay is
`artifacts/runs/vifinqa_answer_optimization_20260830/loan_provision_ab_v1/independent_replay_q324_v1.json`.
It checks the exact HBC separate UID
`4bee72b67b57d131a6edd3f4f700cc5407054894694b64c05bbb38db6f434d79` at source
line 1197, byte/character coordinates, VND header, rows
`75,075,867,681 + 1,429,181,347 + 4,359,635,693`, and total
`80,864,684,721`. The checksum passes and the converted answer is
`80.864684721` billion VND; `promotion_allowed=false` remains explicit.

Exactly one allow-listed replacement was copied onto the canonical
68-replacement Q989 candidate. The resulting 69-replacement candidate is
`artifacts/runs/vifinqa_answer_optimization_20260830/integrated_route_overlay_signed_lease_argmax_taxpaid_relatedparty_interest_direct_q994_subsidiary_conditional_provision_named_compensation_accounting_alias_other_income_accrued_interest_q989_loan_provision_q324_v1.zip`.
It has 68 inherited replacements, one new Q324 replacement, validates/replays
1,012/1,012 with `errors=[]`, contains 1,012 evidence CSV members, passes
`unzip -t`, and has ZIP SHA-256
`27d9a6e1fd87f4ccf10036f386a1963c2427448d55f3339cdbe8a7f7cdbf2369`.
Independent base/output comparison finds exactly Q324 changed:
`-154.380696547 -> 80.864684721`.

This remains an
`authorized_best_effort_submission_candidate`: `human_verified=false`,
`promotion_allowed=false`, `new_numeric_arithmetic_invented=false`, and no
official score delta is claimed. The loan-provision focused checks pass, the
full suite remains `685 passed, 1 skipped`, and `git diff --check` passes. The
three conflicting provision cases stay quarantined; the next research remains
family-based residual audit with independent replay required for each new
materialization.

## 30. Bounded argmax extension: KBC investment-property factory Q878

The next isolated argmax family targeted Q878, which asks which of 2015, 2017
and 2019 has the largest year-end carrying amount for KBC investment property
described as a factory. The 68-replacement candidate's heuristic answer was
`100000.0`; the bounded exact-row route returns `2019.0`. Full details are in
`docs/research/ARGMAX_INVESTMENT_PROPERTY_SOURCE_FIRST_ABLATION_Q878_V1.md`.

The independent replay checks the exact consolidated KBC UIDs at source lines
`1137`, `1024` and `1110`, with carrying-value rows `14`, `12` and `14`,
column 1, and raw VND values `20,415,184,100`, `134,884,233,798` and
`432,718,621,923`. It verifies the exact `Bất động sản đầu tư` context, the
factory asset row, the `Giá trị còn lại` hierarchy, same consolidated scope,
same financial-note/movement-schedule family, same VND multiplier, source
hashes, and a unique maximum at 2019. The replay artifact is
`artifacts/runs/vifinqa_answer_optimization_20260830/argmax_investment_property_ab_v1/independent_replay_q878_v1.json`;
it reports `status=PASS` and `promotion_allowed=false`.

The immutable A/B is under
`artifacts/runs/vifinqa_answer_optimization_20260830/argmax_investment_property_ab_v1/`.
Both arms use builder SHA `8badd7ab…`, source lookup SHA `72fd26a…`, the same
29,428-table filtered asset and 146,246-entry source-line map, and both
validate/replay 1,012/1,012 with `errors=[]` and pass ZIP integrity. The
variant reports 43 typed argmax plans seen, 16 accepted, 22 cohort-rejected,
one unique-extreme tie, three ratio contracts skipped, one multi-row
composition contract skipped, and the remaining strict rejection counts in
the standalone report. The control/variant comparison has 16 argmax-family
diffs including Q878; this is not attributed as 16 Q878 gains.

To preserve attribution, only Q878 was copied from the variant onto the
68-replacement candidate. The resulting 69-replacement candidate is
`artifacts/runs/vifinqa_answer_optimization_20260830/integrated_route_overlay_signed_lease_argmax_taxpaid_relatedparty_interest_direct_q994_subsidiary_conditional_provision_named_compensation_accounting_alias_other_income_accrued_interest_q989_investment_property_q878_v1.zip`.
It contains 68 inherited plus one Q878 replacement, validates/replays
1,012/1,012 with `errors=[]`, has 1,013 ZIP members / 1,012 CSV members, and
passes ZIP integrity. An independent base/output comparison finds exactly
Q878 changed: `100000.0 -> 2019.0`. ZIP SHA-256 is
`10473060121dc81ecd7298fce3bc447479aeda07c6641c69855be0535b903bbd`.

This remains an
`authorized_best_effort_submission_candidate`: `human_verified=false`,
`promotion_allowed=false`, `new_numeric_arithmetic_invented=false`, and no
official score delta is claimed. The next work remains family-based residual
audit of the tie/ratio/no-coherent-row classes, with independent replay
required before any further replacement.

## 31. Bounded supplier-payable extension: BSR Q338

The next direct-lookup family targeted Q338, where the 69-replacement
candidate selected an unrelated equity-adjustment row and returned
`31.00499616`. The question asks for the end-2018 payable balance to the named
supplier `Tổng Công ty Dầu Việt Nam - CTCP` for BSR, in thousand-billion VND.
The bounded route requires a local `Phải trả nhà cung cấp`/`Phải trả người
bán` section, the exact named counterparty row, the current VND column and
agreement across separate/consolidated duplicates. Full details are in
`docs/research/SUPPLIER_PAYABLE_Q338_SOURCE_FIRST_ABLATION_V1.md`.

The full-population variant is recorded at
`artifacts/runs/vifinqa_answer_optimization_20260830/supplier_payable_ab_v1/`.
It uses builder SHA `d1edacca…`, source lookup SHA `72fd26a…` and the complete
146,246-table asset. The frozen control and variant both validate/replay
1,012/1,012 with `errors=[]`; the variant ZIP has 1,013 members / 1,012 CSV
members and SHA-256
`500dd416a5c2f2a75160d1464120373c222cbadfa8f5b9ee3cc4eb10d0adc50d`. The
family sees one eligible case and accepts one; unsafe table kinds are
rejected. The control-to-variant comparison has exactly one answer diff,
Q338: `0.0` / `fallback_zero` -> `2.499485052166` /
`program_supplier_payable_v1`.

Independent replay is
`artifacts/runs/vifinqa_answer_optimization_20260830/supplier_payable_ab_v1/independent_replay_q338_v1.json`.
It checks both exact source UIDs: the separate table at line 1174 and the
consolidated table at line 1224. Both local supplier sections, exact
counterparty rows, current `31/12/2018 · VND` cells, source/table hashes,
coordinates and unit conversion agree on `2.499485052166`; the replay reports
`status=PASS` and `promotion_allowed=false`.

Only Q338 was copied onto the canonical 69-replacement candidate. The
resulting 70-replacement candidate is
`artifacts/runs/vifinqa_answer_optimization_20260830/integrated_route_overlay_signed_lease_argmax_taxpaid_relatedparty_interest_direct_q994_subsidiary_conditional_provision_named_compensation_accounting_alias_other_income_accrued_interest_q989_loan_provision_q324_supplier_payable_q338_v1.zip`.
It has 69 inherited plus one new replacement, validates/replays 1,012/1,012
with `errors=[]`, contains 1,013 ZIP members / 1,012 CSV members, passes
`unzip -t`, and has SHA-256
`b682cc7c9a60b738b2fd7441085636881071b257cea8f6925c11bf3f34837338`.
Independent base/output comparison finds exactly Q338 changed:
`31.00499616 -> 2.499485052166`.

This remains an
`authorized_best_effort_submission_candidate`: `human_verified=false`,
`promotion_allowed=false`, `new_numeric_arithmetic_invented=false`, and no
official score delta is claimed. The nearby Q226 tax-payable route was not
materialized because its answer already matches the canonical candidate and
there is no answer-level gain. The next work remains a family-based residual
audit with independent replay required before any further replacement.

## 32. Re-audited Crown payable Q921 rebased onto the active candidate

Q921 had an earlier 60-replacement artifact, but it was not present in the
active Q989/Q324/Q338 lineage. The three SAB source cells were independently
replayed again with
`scripts/research/replay_crown_payable_q921_v1.py`; the result is
`artifacts/runs/vifinqa_answer_optimization_20260830/argmax_crown_payable_ab_v1/independent_replay_q921_v1.json`.
The checker verifies exact separate-scope SAB tables for 2019, 2021 and 2025,
source/table hashes, coordinates, related-party payable context, the Crown
row, VND headers and the unique maximum `2021`; it reports `status=PASS` and
`promotion_allowed=false`.

Only Q921 was copied from the replayed Crown variant onto the active
71-replacement candidate. The resulting 72-replacement candidate is
`artifacts/runs/vifinqa_answer_optimization_20260830/integrated_candidate_q72_q921_after_q878_v1.zip`.
It has 71 inherited plus one new replacement, validates/replays 1,012/1,012
with `errors=[]`, contains 1,013 ZIP members / 1,012 CSV members, passes
`unzip -t`, and has SHA-256
`94b292fd79555c00f2d08f071bd4601bfb6d0d67cd698bc0a33356fb944c3c6c`.
Independent base/output comparison finds exactly Q921 changed:
`113224326586.0 -> 2021.0`.

The active artifact remains an
`authorized_best_effort_submission_candidate`: `human_verified=false`,
`promotion_allowed=false`, `new_numeric_arithmetic_invented=false`, and no
official score delta is claimed. The next residual audit remains family-based
and must independently replay any new proposed replacement.

## 33. Scope-consensus other-profit extension: TTF Q829

The next residual arg-extreme audit targeted Q829, which asks for the year
with the highest TTF `Lợi nhuận khác` across 2016, 2017, 2023 and 2025. The
previous active candidate returned `139807296911.0` from an unrelated cell.
The new family-level contract binds statement code 40, the normalized
`Lợi nhuận khác`/loss aliases, the current `Năm nay` column, one coherent row
family and one coherent scope per cohort.

Q829 had both complete TTF consolidated and separate cohorts. They have
different values, so the route does not combine them; it requires a unique
winner in each cohort and accepts the preferred scope only when all eligible
scope winners agree. The full-corpus run recorded two independent winners at
2025 and trace basis `winner_consensus_preferred_scope`.

The full variant is at
`artifacts/runs/vifinqa_answer_optimization_20260830/argmax_scope_consensus_ab_v1/variant/submission/`.
It used the 146,246-table asset (SHA-256
`617ae044499907cf302519b7ff6fb96fb86ab70ff08e6fdcb0e6a3f8df5251f7`),
validated/replayed 1,012/1,012 with `errors=[]`, accepted 33 period-extreme
cases and recorded 7 cross-scope-consensus cases. The independent Q829
checker is
`artifacts/runs/vifinqa_answer_optimization_20260830/argmax_scope_consensus_ab_v1/independent_replay_q829_v1.json`;
it checks 8 exact source records, reports `status=PASS`, and keeps
`promotion_allowed=false`.

Only Q829 was copied onto the 72-replacement active candidate. The resulting
73-replacement candidate is
`artifacts/runs/vifinqa_answer_optimization_20260830/integrated_candidate_q73_q829_after_q921_v1.zip`.
It changes exactly Q829 (`139807296911.0 -> 2025.0`), preserves the other
1,011 records at JSON-value level, validates/replays 1,012/1,012 with
`errors=[]`, has 1,013 ZIP members / 1,012 CSV members, passes `unzip -t`,
and has SHA-256
`ad7afa28f4c01b3cbfdab180f0bee1519c24fcc32c098525292124a3083c4968`.

This remains an
`authorized_best_effort_submission_candidate`: `human_verified=false`,
`promotion_allowed=false`, and no official Answer/Execution score delta is
claimed. Full details and the eight-record value matrix are in
`docs/research/ARGMAX_OTHER_PROFIT_Q829_SCOPE_CONSENSUS_V1.md`.

## 34. Context-bound total-liabilities extension: HND Q884

The next residual arg-extreme audit targeted Q884, which asks for the HND
year with the highest total liabilities across 2016, 2017, 2018, 2021 and
2022. The previous active candidate returned `-384536504472.0` from an
unrelated semantic-cell fallback. The generalized family contract binds a
balance-sheet table, row code `300`, the OCR-normalized
`NỘ PHẢI TRẢ (300 = 310 + 330)` row family, the current `31/12/{year} VND`
column, one coherent scope/multiplier cohort and a unique arg-max. It rejects
the cash-flow change row and prior-period columns. Full details are in
`docs/research/ARGMAX_TOTAL_LIABILITIES_Q884_SOURCE_FIRST_ABLATION_V1.md`.

The immutable full-population control is
`artifacts/runs/vifinqa_answer_optimization_20260830/argmax_scope_consensus_ab_v1/variant/submission/`;
the candidate is
`artifacts/runs/vifinqa_answer_optimization_20260830/argmax_total_liabilities_ab_v5/variant/submission/`.
Both use the 1,012-question population, the same 146,246-table structured
asset (SHA-256
`617ae044499907cf302519b7ff6fb96fb86ab70ff08e6fdcb0e6a3f8df5251f7`), the
same builder SHA `97e91fbac2000ce0cf9377f4d91b62c5116dc4fd27242008937bf8f02eabc60e`
and source lookup SHA
`d68ed471ce73db8fe8f0f9f263b0cfdcc3fb223ce80821980a8603ca61385528`.
Both validate/replay 1,012/1,012 with `errors=[]`, emit 952 predicted and 60
fallback questions, and retain local verification classes
`PARTIAL=950`, `UNRESOLVED=62`.

The full A/B has exactly one answer change and no non-target change: Q884
`-384536504472.0` / `semantic_cell_heuristic` -> `2016.0` /
`program_arg_extreme_period_v1`. The route diagnostic changes are accepted
arg-extreme `33 -> 34`, cohort-rejected `5 -> 4`, and semantic tier `573 ->
572`; these are not accuracy metrics. There is no independent gold or
official scorer, so `ANSWER_ACCURACY` and `EXECUTION_ACCURACY` are both
`NOT_MEASURED`.

The independent replay is
`artifacts/runs/vifinqa_answer_optimization_20260830/argmax_total_liabilities_ab_v1/independent_replay_q884_v1.json`.
It checks five exact HND balance-sheet UIDs and values, the code-300 row,
current VND columns, source/table hashes and coordinates, and reports
`status=PASS`, unique winner 2016, `promotion_allowed=false` and
`strict_certificate=false`.

Only that independently replayed source-UID closure was materialized onto the
73-replacement active candidate. The valid output is
`artifacts/runs/vifinqa_answer_optimization_20260830/integrated_candidate_q74_q884_after_q829_generic_v2.zip`;
it has SHA-256
`d7f576f15c865c51f23544dfa6441ee80cbc27bfc1282721d02761ed9ff3b7b3`, 74
total route overrides, 1,012 evidence CSVs, and `unzip -t` passes. Its
overlay report records
`replacement_selection_basis=independent_replay_source_uid_exact_set`,
`replacement_ids_are_explicit=false`, replay PASS, verified UID count 5,
source/final validation 1,012/1,012, `errors=[]`,
`human_verified=false`, `promotion_allowed=false` and
`new_numeric_arithmetic_invented=false`. Comparing it with the active base
changes exactly Q884 and preserves the other 1,011 records at JSON-value
level. The earlier `...q74_q884_after_q829_v1.zip` packaging attempt is kept
but superseded because its metadata used the legacy explicit-ID mode.

Decision: `KEEP` the generalized family candidate for further independent
scoring/holdout evaluation; authority remains `CANDIDATE_ONLY`. The next
research queue is a new family-level residual audit, with Q822 other-profit
inspection next and no Q884-specific exception.

## 35. Generic UID-closure rebase: three argmax ratio families

Q822 was rechecked before selecting the next queue and was found already
present in the active Q73/Q74 lineage (`2021.0`); it produced no new delta.
The next available high-yield candidate was the already full-population
validated argmax ratio-period family: KBC lease-land-cost share (Q826), HDB
deposit-interest share (Q877) and PVT transport-segment asset share (Q982).
The family adapter and its independent replay are documented in
`docs/research/ARGMAX_RATIO_PERIOD_SOURCE_FIRST_ABLATION_V2.md`.

The frozen ratio A/B used the same 1,012-question population, 146,246-table
asset (SHA-256
`617ae044499907cf302519b7ff6fb96fb86ab70ff08e6fdcb0e6a3f8df5251f7`), builder,
selector, source lookup and source-line-map inputs in both arms. It validated
and replayed 1,012/1,012 with `errors=[]`. The candidate changed exactly three
records and no non-target rows:

- Q826: `73339644528.0 -> 2016.0`;
- Q877: `16786000000.0 -> 2025.0`;
- Q982: `3651292326370.0 -> 2025.0`.

The independent replay is
`artifacts/runs/vifinqa_answer_optimization_20260830/argmax_ratio_period_ab_v2/independent_replay_ratio_families_v1.json`.
It reports `status=PASS`, 3 records and no failures. Its 16 unique source
UIDs form three exact closures with counts 8, 3 and 5. No official scorer or
gold evaluator is available, so both `ANSWER_ACCURACY` and
`EXECUTION_ACCURACY` remain `NOT_MEASURED` and no score gain is claimed.

The valid integrated rebase starts from the 74-replacement Q884 candidate and
is:
`artifacts/runs/vifinqa_answer_optimization_20260830/integrated_candidate_q77_ratio_after_q884_generic_v2.zip`.
Its SHA-256 is
`d5d45dea9eafd14f36904f06a2db0a91b93b18316a06f07da4021145840d2baa`.
It has 77 total route overrides, validates/replays 1,012/1,012 with
`errors=[]`, contains 1,013 ZIP members / 1,012 evidence CSVs, and passes
`unzip -t`. Comparing it to Q74 changes exactly Q826/Q877/Q982 and preserves
the other 1,009 records.

The materializer now selects the three source rows by
`independent_replay_source_uid_exact_set`, records
`replacement_ids_are_explicit=false`, 16 verified UIDs and 3 closure groups;
the replay IDs appear only as tracking metadata. It copies source numeric
values, invents no arithmetic, and records `human_verified=false` and
`promotion_allowed=false`. The full regression after this generic multi-
closure change is `716 passed, 1 skipped`; `git diff --check` passes.

Decision: `KEEP` the ratio family and Q77 generic v2 as a candidate for an
independent scorer/holdout run. Authority remains `CANDIDATE_ONLY`; the old
explicit-packaging ratio overlay is historical and is not the handoff. The
next queue is a new family-level residual audit, not a QID exception.

## 36. Independent replay rebase audit: BVH Q261 was already active

The financial-liability maturity family was independently replayed for Q261.
The reusable contract binds a financial-note liability maturity schedule, the
requested date header, consolidated scope, the `TỔNG CỘNG` row, the `Tổng
cộng` column, and the million-VND unit. The family A/B changes exactly one
record in the complete 1,012-question population: Q261
`16496708.0 -> 174052754.0`, with 1,011 unchanged, zero missing records,
zero errors, and zero non-target changes. Both answer and execution accuracy
are `NOT_MEASURED` because no independent gold or official scorer is available.

The independent replay
`artifacts/runs/vifinqa_answer_optimization_20260830/financial_liability_total_ab_v2/independent_replay_q261_v1.json`
has SHA-256
`4d71405c0c6f695b83b191e314393d953ec2627617761a23a216f8174aa07afc` and
reports `status=PASS`, exact UID/source/table/coordinate/header/section/row/
column/unit checks, `failures=[]`, `promotion_allowed=false`, and
`authority_status=CANDIDATE_ONLY`. The checker source hash is
`43d7eca991d10f998e9ccfcaccd009c05ea8b77d959ea93b60d35617188ffe14`.

An attempted generic rebase onto Q77 produced
`artifacts/runs/vifinqa_answer_optimization_20260830/integrated_candidate_q78_financial_liability_after_q77_generic_v1.zip`
with SHA-256
`1653d54edceda804844a4999cb985997a6276425b1123c020f3861b5a4a1129e`.
The materializer used
`replacement_ids_are_explicit=false` and
`independent_replay_source_uid_exact_set`, but Q261 was already present in
Q73, Q74, and Q77 with answer `174052754.0`. Therefore Q77 -> q78 is a
no-op: 0 changed and 1,012 unchanged. The q78 report is
`artifacts/runs/vifinqa_answer_optimization_20260830/integrated_candidate_q78_financial_liability_after_q77_generic_v1/build_report.json`
with SHA-256
`cdb7c68603dfbcbad8e50d149efeacfed79d9bbc84343396eff614e4ac49789f`.
It validates source/final 1,012/1,012 with `errors=[]`, contains 1,013 ZIP
members / 1,012 CSVs, and passes `unzip -t`.

Decision: `KEEP` the family replay evidence and the existing active Q261
candidate, but do not count q78 as a new score or answer improvement. The
active authority remains `CANDIDATE_ONLY`. The next queue is a residual family
not already inherited by Q77.

## 37. Tax-payable balance-sheet argmax: Q866 full-population candidate

The next residual family was SJG Q866: choose the largest current balance for
`Thuế và các khoản phải nộp Nhà nước` across 2018--2021. The reusable contract
requires a balance-sheet table, exact row code `313`, the current `Số cuối
năm` column, consolidated scope, one source multiplier, and explicit VND
document evidence. It does not use Q866 as a predictor or allow a retrieved
cell to substitute for the source row. The implementation and full handoff
are documented in
`docs/research/ARGMAX_TAX_BALANCE_SHEET_Q866_SOURCE_FIRST_ABLATION_V1.md`.

The full source candidate ran over all 1,012 questions and 146,246 tables,
validated/replayed 1,012/1,012 with `errors=[]`, and accepted 35 strict
period-extreme records. The isolated comparison uses the active q78
integrated candidate as the frozen control, because an older Q884 artifact
predated inherited Q77/Q78 routes and was not an isolated control. The
generic q78 -> q79 materialization changes exactly Q866:

```text
Q866: 337129394454.0 -> 2018.0
changed rows=1; unchanged rows=1011; non-target changes=0; missing=0; errors=0
```

The independent replay is
`artifacts/runs/vifinqa_answer_optimization_20260830/argmax_tax_balance_sheet_ab_v1/independent_replay_q866_v1.json`.
It reports `status=PASS`, four exact SJG source UIDs/cells, unique winner
2018, no failures, `answer_accuracy=NOT_MEASURED`,
`execution_accuracy=NOT_MEASURED`, and `authority_status=CANDIDATE_ONLY`.
The generic materialized candidate is
`artifacts/runs/vifinqa_answer_optimization_20260830/integrated_candidate_q79_tax_balance_after_q78_generic_v1.zip`
with SHA-256
`045beaacacc5205cd25cc2b59bd55fb9db575bb28dbc7f9a84f317606886e83a`.
It has 79 total route overrides, 1,013 ZIP members / 1,012 evidence CSVs,
source/final validation `1012/1012`, `errors=[]`,
`replacement_ids_are_explicit=false`, and exact source-cell closure
selection. `unzip -t` passes.

No independent gold or official scorer is available. Therefore both answer
and execution accuracy remain `NOT_MEASURED`; the one changed answer is a
source-supported candidate result, not a measured score increase. The q79
authority remains `CANDIDATE_ONLY`.

Decision: `KEEP` for independent scorer/holdout evaluation. Next queue is the
remaining residual family audit, starting with Q883/Q978 only after this q79
handoff is retained; no QID-specific patch is permitted.

## 38. Short-term construction-cost payable argmax: Q978 full-population candidate

The next residual family was NVL Q978: choose the largest short-term
construction cost payable across 2020, 2022 and 2025. The generalized
contract binds the exact `Chi phí xây dựng` row inside a `Chi phí phải trả`
context, supports both titled short-term notes and an explicit `a. Ngắn hạn`
schedule hierarchy, requires consolidated scope and one VND multiplier, and
selects the current-period column. It does not use Q978 as a predictor or
permit the old retrieved cash/asset cell to stand in for the metric. The full
handoff is documented in
`docs/research/ARGMAX_CONSTRUCTION_COST_PAYABLE_Q978_SOURCE_FIRST_ABLATION_V1.md`.

The full source candidate ran over all 1,012 questions and 146,246 tables,
validated/replayed 1,012/1,012 with `errors=[]`, and accepted 36 strict
period-extreme records. The isolated comparison uses the active q79
integrated candidate as the frozen control; older pre-q79 artifacts would mix
inherited route changes. The generic q79 -> q80 materialization changes
exactly Q978:

```text
Q978: 21644596460.0 -> 2025.0
changed rows=1; unchanged rows=1011; non-target changes=0; missing=0; errors=0
```

The independent replay is
`artifacts/runs/vifinqa_answer_optimization_20260830/argmax_construction_payable_ab_v1/independent_replay_q978_v1.json`.
It has SHA-256
`0aba10a4b36456224cdf54d65e69d2dbf9ce06e30955587495459d5f4ccfed56` and
reports `status=PASS`, three exact NVL consolidated source UIDs/cells, a
unique winner of 2025, no failures, `answer_accuracy=NOT_MEASURED`,
`execution_accuracy=NOT_MEASURED`, and
`authority_status=CANDIDATE_ONLY`. The generic materialized candidate is
`artifacts/runs/vifinqa_answer_optimization_20260830/integrated_candidate_q80_construction_payable_after_q79_generic_v1.zip`
with SHA-256
`222fd4457d3f6c57a15b5c9da767fc95e27e6377611ab52faf693bd2c08ad6fb`.
It has 80 total route overrides, 1,013 ZIP members / 1,012 evidence CSVs,
source/final validation `1012/1012`, `errors=[]`,
`replacement_ids_are_explicit=false`, and exact source-cell closure
selection. `unzip -tq` passes.

No independent gold or official scorer is available. Therefore both answer
and execution accuracy remain `NOT_MEASURED`; the one changed answer is a
source-supported candidate result, not a measured score increase. The q80
authority remains `CANDIDATE_ONLY`.

Decision: `KEEP` for independent scorer/holdout evaluation. The next queue is
the unresolved MBB trading-securities-debt residual family (Q883), with no
Q978-specific exception permitted.

## 39. Trading-debt-securities argmax: Q883 full-population candidate

The next residual family was MBB Q883: choose the largest debt-securities
balance held for trading across 2022, 2023 and 2025. The generalized contract
binds the exact `Chứng khoán nợ` row in note 8, `Chứng khoán kinh doanh`,
requires `financial_note`/note-detail context, current-period columns,
consolidated scope and the `triệu đồng` source unit, and rejects investment,
listing-status and income/provision contexts. It does not use Q883 as a
predictor or permit a retrieved `Chứng khoán kinh doanh` total to substitute
for the debt-securities row. The full handoff is documented in
`docs/research/ARGMAX_TRADING_DEBT_SECURITIES_Q883_SOURCE_FIRST_ABLATION_V1.md`.

The full source candidate ran over all 1,012 questions and 146,246 tables,
validated/replayed 1,012/1,012 with `errors=[]`, and accepted 37 strict
period-extreme records. The isolated comparison uses the active q80
integrated candidate as the frozen control; older artifacts would mix in
inherited route changes. The generic q80 -> q81 materialization changes
exactly Q883:

```text
Q883: 1220511000000.0 -> 2023.0
changed rows=1; unchanged rows=1011; non-target changes=0; missing=0; errors=0
```

The independent replay is
`artifacts/runs/vifinqa_answer_optimization_20260830/argmax_trading_debt_securities_ab_v1/independent_replay_q883_v1.json`.
It has SHA-256
`72145e7c80aaf6908f8fa40c8e10c351be4d733fd18a32d339a953d580d5519d` and
reports `status=PASS`, three exact MBB consolidated source UIDs/cells, a
unique winner of 2023, no failures, `answer_accuracy=NOT_MEASURED`,
`execution_accuracy=NOT_MEASURED`, and
`authority_status=CANDIDATE_ONLY`. The generic materialized candidate is
`artifacts/runs/vifinqa_answer_optimization_20260830/integrated_candidate_q81_trading_debt_after_q80_generic_v1.zip`
with SHA-256
`dd9e2369786bb454f6857f3205bac68539f757f5aa2f3de133814657a5dc2d84`.
It has 81 total route overrides, 1,013 ZIP members / 1,012 evidence CSVs,
source/final validation `1012/1012`, `errors=[]`,
`replacement_ids_are_explicit=false`, and exact source-cell closure
selection. `unzip -tq` passes.

No independent gold or official scorer is available. Therefore both answer
and execution accuracy remain `NOT_MEASURED`; the one changed answer is a
source-supported candidate result, not a measured score increase. The q81
authority remains `CANDIDATE_ONLY`.

Decision: `KEEP` for independent scorer/holdout evaluation. The next queue is
a new residual family audit, with no Q883-specific exception permitted.

## 40. Ratio-period extension rebased on q81: q82

The ratio-period family was extended onto the latest q81 integrated lineage.
The source candidate contains eight independently replayed family records, but
three (`Q826`, `Q877`, `Q982`) were already present in q81. Exact comparison of
q81 against the q82 materialized overlay therefore changes only five rows:
`Q849`, `Q885`, `Q968`, `Q980`, and `Q1004`. All five change answer and
prediction tier; `1,007` complete rows remain equal and non-target changes are
zero.

The independent replay reports `PASS` for `8/8` supported rows, `37` verified
source UIDs, `81` source cells, and `0` failures. The q82 overlay validates
`1,012/1,012` records/queries with `errors=[]`, contains `89` total route
overrides (`81` inherited plus `8` ratio-family closures), and its ZIP has
`1,013` members / `1,012` evidence CSVs with `unzip -tq` exit `0`. Replacement
selection is based on independent source-cell exact closure and is not an
explicit ID allowlist; tracking IDs are retained only for audit.

The full A/B handoff is documented in
`docs/research/ARGMAX_RATIO_PERIOD_REBASE_Q82_ABLATION_V1.md`. The q82 output
is
`artifacts/runs/vifinqa_answer_optimization_20260830/integrated_candidate_q82_ratio_extended_after_q81_v1.zip`
with SHA-256
`589bdadc0a1cb42e63357b52f0166fec83d6cf23154e5d390d90666d927967d8`.
There is still no independent gold/official scorer, so answer and execution
accuracy are `NOT_MEASURED`; q82 remains `CANDIDATE_ONLY`. The generic
completion gate exits `1` because its full-build lifecycle fields are absent
from the overlay report (`question_count`, `zip_path`, and
`best_candidate_ledger_count`), while overlay validation and ZIP integrity
pass. Next gate: repair/adapt overlay schema and score q81 vs q82 with the
same independent evaluator.
