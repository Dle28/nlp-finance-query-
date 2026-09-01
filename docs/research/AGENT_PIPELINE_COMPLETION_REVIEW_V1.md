# AI_guru — Audit completion gate và research/model isolation (V1)

Ngày audit: 2026-08-30
Workspace: /home/dungle/Documents/AI_guru
Phạm vi: entrypoint/config/contract/receipt hiện hành của AI_guru/ViFinQA; kiểm tra theo family-level, không xử lý theo Question-ID.
Giới hạn thao tác: chỉ tạo file báo cáo này; không sửa code, config, artifact hay file nào khác.

## Kết luận điều hành

Tại snapshot audit này chưa có bằng chứng để gọi **full pipeline strict VERIFIED**, và chưa có completion gate phát hành độc lập đã đạt. Có ba trạng thái cần tách rõ:

1. **VERIFIED — technical artifact only:** một số lần chạy build-submission đã tạo đủ ZIP, 1.012 prediction records, 1.012 query replay và source-line-map coverage đầy đủ cho artifact đó. Đây là bằng chứng package/replay/coordinate kỹ thuật; nó **không** phải bằng chứng semantic authorization, strict release hay accuracy.
2. **PARTIAL — canonical migration:** resolver đã tạo 1.012 RESOLVED_CANDIDATE, nhưng independent E2E observer/grounded authorization hiện cho 1.012 ABSTAIN; strict_ready_count=0, release_authorized=false. Đây là trạng thái migration có receipt, chưa phải completion.
3. **VERIFIED blocker — canonical config smoke test:** run-e2e không đi vào replay vì config khai báo một best_candidate_predictions path không tồn tại. Đây là blocker tái hiện được, không phải suy đoán.

Không được suy ra accuracy: data/ViFinQA/questions/questions.jsonl chỉ có id và question trong snapshot được kiểm tra; không thấy trường gold/label và không có official scorer/gold receipt trong các artifact dùng cho kết luận này. Các con số dưới đây là coverage, trạng thái contract, replay và blocker telemetry; chúng không phải accuracy.

Worktree hiện dirty. git log -1 --oneline là 6be0c82 Merge pull request #1 from Dle28/codex/synthetic-finance-curriculum-v1, nhưng các artifact cũ có fingerprint trỏ vào /tmp hoặc được tạo ở run trước. Vì vậy mọi kết luận “đã chạy” dưới đây được gắn với đúng path/hash/manifest của artifact, không nâng thành “source hiện tại reproducible” nếu chưa rerun trên output directory bất biến mới.

## 1. Quy ước trạng thái và tiêu chuẩn audit

- **VERIFIED:** có file/command/manifest cụ thể, có thể đối chiếu lại trong snapshot; chỉ áp dụng đúng mệnh đề nhỏ được kiểm tra, ví dụ “ZIP hợp lệ” hoặc “worker đã được gọi đủ dòng”. Không dùng nhãn này để nói câu trả lời đã đúng nếu chưa có certificate semantic tương ứng.
- **PARTIAL:** có một phần output hoặc contract đã chạy, nhưng thiếu một gate, thiếu lineage, còn blocker, hoặc artifact không đủ chứng minh full population.
- **PROPOSAL:** hành động/command cần thực hiện để nâng gate; chưa được chạy trong audit này và không được xem là kết quả.
- **BLOCKER:** điều kiện làm completion/strict/release không đạt hoặc khiến pipeline có thể nhận nhầm proposal/research/model thành authority.

### Nguyên tắc chống trùng nghiên cứu

Audit này không tạo patch theo từng câu hỏi và không chọn “câu nào cần agent khác tìm tiếp”. Các phát hiện được gom vào các family contract:

- source identity và source-line coordinate;
- entity/role/period/reporting scope/metric/unit;
- formula/operand/AST/Decimal replay;
- counterfactual/alternative rejection;
- population coverage, memory và artifact lineage;
- research/model isolation;
- delivery/release và điều kiện nâng VERIFIED.

Những artifact research_only hoặc review độc lập chỉ được dùng như telemetry/gợi ý family; chúng không được dùng để kết luận accuracy, answer truth, gold hoặc release.

## 2. Entry point và config hiện hành

### 2.1 Public CLI

src/finance_query/cli.py:17-22 công bố đúng bốn command:

| Entry point | Vai trò theo source | Tình trạng audit |
| --- | --- | --- |
| run-e2e | independent deterministic/grounded verification từ config explicit | Có entrypoint; smoke test hiện bị chặn bởi missing input trong config |
| build-submission | proposal/coverage/legacy compatibility producer và ZIP best-effort | Có artifact kỹ thuật hoàn tất; không cấp strict authority |
| run-submission-flow | Proposal → Resolve → E2E → Compile → feedback | Có flow; implementation vẫn qua legacy adapter và release gate cuối chưa mở |
| evaluate-blocked | chuẩn bị/chạy feedback cho các row bị block | Có packet; model feedback không authorizing |

src/finance_query/cli.py:39-79 tạo parser; các option proposal được nạp động qua src/finance_query/e2e/submission_pipeline.py:1-37, còn run-e2e chỉ nhận --config và --output-dir. Điều này làm cho help của build-submission/run-submission-flow không phải là toàn bộ contract của E2E core; khi audit cần kiểm tra cả adapter và config thực tế.

Các help command đã được chạy thành công bằng source checkout:

~~~bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src \
  .venv/bin/python -m finance_query.cli build-submission --help
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src \
  .venv/bin/python -m finance_query.cli run-submission-flow --help
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src \
  .venv/bin/python -m finance_query.cli run-e2e --help
~~~

Help/source hiện cho phép các input có rủi ro isolation hoặc memory:

- --structured-table-filter {candidate_uids,all}; source default là candidate_uids trong scripts/e2e/build_competition_submission_v1.py:11360-11367;
- --source-line-map, --require-source-line-map, --allow-local-ordinal-fallback;
- optional learned candidate model, dense index, fine-tuned model;
- --research-candidate, --route-overlay, --model-answer-candidate, --direct-evidence-replay, --disable-research-fusion;
- unified flow có --flow-release-policy {best_effort,strict} và optional feedback model.

### 2.2 Canonical product contract

docs/PIPELINE.md:1-21 xác định một đường duy nhất:

~~~text
ProposalAST → Deterministic Resolver → ResolvedPrediction
→ independent E2E verification → Submission Compiler → package
→ blocked feedback / next-version experiment
~~~

docs/PIPELINE.md:23-41 và docs/PIPELINE.md:43-48 tách rõ proposal, resolver, E2E, compiler, delivery; migration hiện vẫn ghi duplicate_execution_legacy=true và canonical_decimal_reexecution=false. docs/PIPELINE.md:91-104 quy định VERIFIED phải đồng thời có exact source binding, semantic fields, formula/operand compatibility, allow-listed AST, hash, Decimal replay, complete certificate và không còn blocker. Retrieval rank, model confidence, candidate validity, cell hiển thị đúng ô, pandas/Decimal chạy được, ready label hoặc tên file chỉ là diagnostic.

docs/PIPELINE.md:211-254 ghi run-submission-flow là canonical command, nhưng đồng thời xác nhận build-submission cũ đang là compatibility producer và release_authorized=false cho tới release gate độc lập. Đây là contract đúng về mặt tài liệu nhưng hiện chưa được khép bằng một completion/release command có receipt đạt.

## 3. Completion gate hiện tại: bằng chứng và kết quả

### 3.1 Blocker B0 — canonical config khai báo input không tồn tại

**Trạng thái: VERIFIED blocker, tái hiện trực tiếp.**

configs/e2e/deterministic_replay_v1_locked.yaml:1-17 khai báo các input main/route/V2/V3/metric registry và:

~~~text
best_candidate_predictions:
  ../../submissions/vifinqa_primary_integrated_20260828_validity_v4/
  best_surviving_candidates_v1.jsonl
~~~

Path resolve tại workspace là:

~~~text
/home/dungle/Documents/AI_guru/submissions/
  vifinqa_primary_integrated_20260828_validity_v4/
  best_surviving_candidates_v1.jsonl
~~~

File này không tồn tại. Lệnh kiểm tra đã chạy:

~~~bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src \
  .venv/bin/python -m finance_query.cli run-e2e \
  --config configs/e2e/deterministic_replay_v1_locked.yaml \
  --output-dir /tmp/ai_guru_audit_config_probe_20260830
~~~

Kết quả: exit code 1, FileNotFoundError: Grounded E2E input is missing: best_candidate_predictions=.../best_surviving_candidates_v1.jsonl. Không tạo output directory vì src/finance_query/e2e/pipeline.py:206-213 kiểm tra file trước; src/finance_query/e2e/pipeline.py:112-124 đưa mọi reference_paths khác None, trong đó có candidate ledger, vào _check_files.

Hệ quả: lệnh documented ở docs/PIPELINE.md:122-126 chưa phải smoke test xanh trên snapshot này. Cần hoặc materialize đúng candidate ledger với manifest/lineage tương ứng, hoặc dùng một config explicit không khai báo optional ledger khi muốn kiểm tra source-only. Không được tạo file thay thế bằng tên tương tự hoặc chọn “latest” vì sẽ phá source closure.

### 3.2 Bằng chứng builder technical completion — không phải authorization

**Trạng thái: VERIFIED technical artifact; PARTIAL semantic/release.**

Artifact lịch sử submission_ready/submission_full_corpus_20260830.build_report.json:1-14,15-26,28-34,55-82,83-127,253-273 ghi:

- question_count=1012;
- validation valid, records=1012, queries_replayed=1012, errors=[];
- ZIP được tạo; kiểm tra unzip -t không có lỗi;
- structured full asset có 146246 tables;
- source coordinate map có 146246 entries, fallback=0, missing coverage 0, status PASS;
- predicted=1012, fallback 0;
- nhưng verification certificate/config là null, promotion/verification counts là PARTIAL=1010, UNRESOLVED=2;
- research fusion bật; model answer candidate có 2 record được chấp nhận sau current-table replay.

Một artifact local tương tự, artifacts/runs/vifinqa_answer_optimization_20260830/local_source_first_exact_only_v16_nodense/build_report.json:1-22,24-46,88-104,130-175, cũng ghi 1.012 validation/replay, no errors, coordinate map exact, nhưng PARTIAL=1010, UNRESOLVED=2, certificate/config null, research enabled và 2 model answers.

Các kiểm tra read-only đã xác nhận nội dung không chỉ dựa trên report:

~~~bash
unzip -t \
  artifacts/runs/vifinqa_answer_optimization_20260830/local_source_first_exact_only_v16_nodense.zip
~~~

Và kiểm tra JSON/ZIP cho thấy submission_records=1012, ID chạy từ 1 đến 1012, queries_replayed=1012, errors=[], source-line-map coverage đầy đủ, không có local fallback. Đây là **completion của builder technical validation**, không phải completion gate của product path; compiler/E2E authority vẫn phải đứng sau nó.

Caveat lineage: report local có implementation fingerprint trỏ tới /tmp/vifinqa-final-v16..., còn worktree hiện dirty. Artifact này cần được coi là historical evidence cho đúng run, không phải proof rằng source hiện tại có thể tái tạo y nguyên.

### 3.3 Blocker B1 — canonical migration chưa đạt E2E authorization và release

**Trạng thái: VERIFIED blocker từ canonical manifests.**

Artifact artifacts/runs/vifinqa_primary_canonical_migration_20260829_r1/resolver/resolver_run.manifest.json:1-15,382-391 có:

- question_count=1012;
- RESOLVED_CANDIDATE=1012;
- operand BOUND_CANDIDATE=1497, UNRESOLVED=4;
- authority resolved_prediction_is_authoritative=false;
- e2e_required=true;
- duplicate_execution_legacy=true;
- canonical_decimal_reexecution=false;
- next refactor vẫn là chuyển hydrate/formula/Decimal vào resolver canonical.

artifacts/runs/vifinqa_primary_canonical_migration_20260829_r1/e2e_observer/e2e_observer.manifest.json:1-11 ghi tất cả 1.012 row là PARTIAL. Grounded authorization manifest và authorization_readiness_v1.json trong cùng run ghi:

- answer_certificate_status_counts: ABSTAIN=1012;
- answer_count=0;
- machine_semantic_binding_count=0;
- evidence_binding_count=884, đều bị block;
- binding packet: binding_ready=55, binding_blocked=497, route_incomplete=460;
- execution: execution_replay_ready=55, binding_conflict=8, route_incomplete=949;
- authorization_status=blocked;
- release_authorized=false và submission_compilation_allowed=false.

Các family blocker receipts gồm, trong số 11.086 blocker receipt: PERIOD_YEAR_MISMATCH, UNIT_CONVERSION_UNVERIFIED, VARIABLE_MISMATCH, FORMULA_COMPATIBILITY_CONSTRAINT_VIOLATION, MULTI_STAGE_EXECUTION_GRAPH_NOT_MATERIALIZED, NO_EXECUTABLE_STAGE, EXECUTION_NOT_PASSED, ANSWER_DECIMAL_INVALID, BINDING_FIELD_NOT_PASS, các hash lineage invalid và counterfactual chưa được reject/kiểm tra. Đây là evidence contract-level; không chuyển thành kết luận câu nào đúng/sai.

Compiler artifacts/runs/vifinqa_primary_canonical_migration_20260829_r1/compiled_submission/submission_compile.manifest.json:1-45 ghi:

~~~text
delivery_policy=best_effort
delivery_status_counts.BEST_EFFORT_CANDIDATE=1012
strict_ready_count=0
release_authorized=false
~~~

Source src/finance_query/pipeline/submission_compiler.py:90-116,119-243 phù hợp với receipt: chỉ row có resolution_status=RESOLVED_CANDIDATE và e2e_status=VERIFIED mới là strict-ready; compiler vẫn đặt release false và yêu cầu release gate độc lập. Do đó việc có submission.zip hoặc đủ 1.012 row không thỏa completion.

### 3.4 Blocker B2 — flow canonical vẫn chạy compatibility producer legacy

**Trạng thái: VERIFIED architecture/implementation blocker; một phần là migration TODO đã được tài liệu hóa.**

src/finance_query/pipeline/submission_flow.py:139-221 cho thấy run_submission_flow:

- gọi legacy builder qua adapter;
- dùng output sibling .proposal_compatibility;
- truyền verification_config=None và verification_certificate=None cho builder compatibility producer (:153-164);
- sau đó mới gọi resolver, E2E observer nếu có verification config, rồi compiler (:167-182);
- trả về flow_authority.release_authorized=False (:184-220).

Điều này giữ được migration boundary nhưng tạo hai rủi ro completion:

1. builder có thể tạo prediction/replay trước, nhưng không thể chứng minh đó là canonical resolved answer;
2. nếu config E2E hỏng hoặc không truyền, flow vẫn có proposal/compatibility output nhưng không có E2E receipt đạt.

src/finance_query/pipeline/resolver.py:382-391 và src/finance_query/pipeline/e2e_observer.py:324-337 đã ghi authority boundary đúng: resolver không authoritative; observer không tự sinh answer; certificate khớp và release gate độc lập là bắt buộc. Blocker thực tế là chưa có run mới nào chứng minh backend canonical đã thay legacy execution và đạt full-population strict gate.

### 3.5 Blocker B3 — “full corpus” và memory bound chưa cùng lúc được chứng minh

**Trạng thái: VERIFIED risk/partial completion.**

Các asset được kiểm tra:

- artifacts/research/document_corpus_round2_assets_v1_20260829_r1/full_table_assets_v1.jsonl: 1,015,012,903 bytes, khoảng 968 MiB, 146,246 tables;
- artifacts/research/document_corpus_round2_assets_v1_20260829_r1/source_line_map_full_v1.json: source-line map full, SHA được ghi trong manifest;
- artifacts/kaggle_runs/notebook5554bd790_v10_20260811/vifinqa_review_bundle/tables_structured_v2.jsonl: khoảng 257 MB;
- review packet hiện có 1.012 questions, 40,313 candidate records và 29,428 candidate UIDs.

Với --structured-table-filter candidate_uids, tỷ lệ UIDs đang được hydrate là 29,428/146,246, xấp xỉ 20.1% số table. Đây là memory-bounded hơn nhưng không chứng minh closure của full corpus: một target nằm ngoài frozen candidate UID universe có thể không được source-first hydrated.

Với --structured-table-filter all, source scripts/e2e/build_competition_submission_v1.py:9970-10020 materialize toàn bộ tables vào dictionary rồi lập index. Đây là path có memory pressure trực tiếp. Hai thư mục hiện có:

- artifacts/runs/vifinqa_answer_optimization_20260830/local_source_first_semantic_contract_v1: chỉ có evidence partial tới khoảng q0120, không có build_report.json;
- artifacts/runs/vifinqa_answer_optimization_20260830/local_source_first_semantic_contract_v1_v2: chỉ có evidence partial quanh q0310, không có build_report.json.

Trong phiên làm việc trước, process full-corpus source-first đã biến mất khi RSS quan sát được khoảng 3.5 GB; vì không có completion receipt nên phải phân loại là **PARTIAL/observed resource-bound failure**, không tự gắn nhãn OOM có nguyên nhân đã chứng minh.

Completion đang bị kẹt giữa hai điều kiện:

- candidate-only: giữ memory dễ hơn nhưng cần chứng minh candidate universe là complete source closure;
- all-corpus: có khả năng closure rộng hơn nhưng cần streaming/sharding hoặc một memory budget receipt; hiện chưa có implementation/receipt chứng minh bound.

### 3.6 Blocker B4 — source-line coordinate đúng ở artifact cũ, nhưng là dependency tách khỏi E2E config

**Trạng thái: VERIFIED cho historical builder artifact; PARTIAL ở interface full-flow.**

scripts/e2e/README_VI.md:21-40 quy định local_ordinal chỉ là coordinate nội bộ; competition relevant_tables cần document_id|<1-based-table-start-line>. Runtime production phải truyền --source-line-map và --require-source-line-map; local ordinal fallback chỉ được phép ở diagnostic mode.

build_competition_submission_v1.py:9747-9821 load/validate map; parser flags ở :11371-11395; production gate từ chối thiếu map hoặc vừa require map vừa bật ordinal fallback. Historical full-corpus report submission_ready/submission_full_corpus_20260830.build_report.json:55-71 chứng minh map 146,246 entries, missing 0, fallback 0, status PASS. Đây là pass kỹ thuật đáng tin cậy cho run đó.

Tuy nhiên configs/e2e/deterministic_replay_v1_locked.yaml:1-17 không tự khai báo competition source-line map/relevant-table projection. E2E core có thể replay exact V2 cell nhưng không tự chứng minh output ZIP có coordinate map đúng cho delivery. Vì vậy pipeline dependency phải nêu rõ:

~~~text
source closure + V2/V3 hashes
  → source_line_map_full_v1.json
  → builder coordinate validation
  → resolver/E2E provenance binding
  → compiler/release ledger
~~~

Nếu bất kỳ stage nào dùng map khác hash hoặc map chỉ phủ candidate subset nhưng claim full corpus, không được nâng VERIFIED.

## 4. Research/model isolation — điểm có thể tiêu thụ output của agent khác

### 4.1 Default auto-discovery trong builder

**Trạng thái: VERIFIED isolation risk; guard hiện có là PARTIAL nhưng có tác dụng.**

build_competition_submission_v1.py:73-87 đặt default sidecar:

- artifacts/research/agent4_candidate_union_v1_20260827_r2/period_column_candidate_packets_v1.jsonl;
- artifacts/research/multicol_semantic_diagnostic_v1_20260827_r2/candidate_packets_v1.jsonl;
- research route overlay;
- artifacts/kaggle_runs/notebook5554bd790_v10_20260811/vifinqa_qwen_review_results/qwen_staged_execution_ledger_v1.jsonl;
- candidate validity model artifacts/review_calibrator.joblib.

build_competition_submission_v1.py:10086-10102 auto-load research khi không có explicit path và chưa disable; :10122-10134 auto-load route overlay/model answer candidate tương tự. Command canonical trong docs/PIPELINE.md:211-218 không truyền --disable-research-fusion, nên operator chỉ chạy command documented có thể vô tình tiêu thụ sidecar của agent/research khác.

Đây là rủi ro **tiêu thụ proposal**, không phải bằng chứng raw numeric leak vào authority. Các guard hiện có:

- docs/PIPELINE.md:52-69 nói builder chỉ proposal/coverage và mọi value phải hydrate/replay từ V2;
- docs/PIPELINE.md:271-277 cấm feedback model cấp numeric answer/certificate/training label/release;
- historical report submission_ready/submission_full_corpus_20260830.build_report.json:83-127 ghi research values raw không được dùng trực tiếp, candidate additions được replay ở current table, candidate validity model ranking_only=true, may_authorize_answer=false;
- report đồng thời ghi research làm thay đổi 77 questions và model answer candidate được nhận 2 row. Tức là sidecar có ảnh hưởng thực tế lên proposal/selection dù không có authority.

Vì vậy isolation hiện không fail-closed ở ingestion: không require operator opt-in để load sidecar. Nó fail-closed tốt hơn ở authority, nhưng vẫn có thể làm thay đổi candidate set, route và coverage của run mà operator tưởng là source-only.

### 4.2 Research artifact có contract riêng, không được promoted

artifacts/research/document_corpus_round2_assets_v1_20260829_r1/manifest.json khai báo research_only=true, submission_eligible=false, production_index_replacement_allowed=false.
configs/e2e/explicit_ticker_candidate_replay_agent4_r9.yaml:1-17 cũng nêu research-only, semantic decisions empty và không answer/evidence/release/training/submission authority.

Các contract này là đúng hướng. Blocker nằm ở chỗ builder có default path tới research sidecar; boundary chỉ an toàn khi run explicit disable hoặc khi manifest/role được kiểm tra trước ingestion. Không được coi “research_only trong manifest” là đủ nếu consumer vẫn tự nạp artifact rồi dùng nó để đổi proposal.

### 4.3 Independent Q&A review không phải gold/accuracy

artifacts/research/independent_qna_review_v1_20260830_r4/README_VI.md:1-14 và summary.json:1-35 mô tả 172 registry-clean subset, 146,246 lines scanned, 6,610 candidate UIDs hydrated; family counts gồm SEMANTIC_ABSTAIN=36, SEMANTIC_CANDIDATE_CHANGED=104, SEMANTIC_CANDIDATE_RETAINED=32, selected guard pass 136, strict verification count 0. Artifact ghi rõ không có gold và không dùng raw research value.

Đây chỉ là evidence để ưu tiên semantic family/contract. Không được dùng 136, 104, 32 để nói accuracy hay “agent khác đã xác nhận answer”.

### 4.4 Cách kiểm tra isolation sạch

**PROPOSAL — chưa chạy trong audit:** một run dùng cho audit baseline cần explicit opt-out:

~~~bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src \
  .venv/bin/python -m finance_query.cli run-submission-flow \
  --output artifacts/runs/<new_immutable_run> \
  --structured-table-filter candidate_uids \
  --source-line-map \
    artifacts/research/document_corpus_round2_assets_v1_20260829_r1/source_line_map_full_v1.json \
  --require-source-line-map \
  --disable-research-fusion \
  --disable-candidate-validity \
  --flow-release-policy strict
~~~

Không coi lệnh trên là completed: trước khi chạy phải sửa/chuẩn bị **bằng config/input hợp lệ** cho B0; command ở đây chỉ mô tả isolation test. Không thêm --dense-index, --fine-tuned-model, --model-answer-candidate, --direct-evidence-replay vào baseline. Nếu sau đó chạy một lane có research/model, phải tạo output directory khác và so sánh manifest/sidecar influence; không overwrite baseline.

## 5. Family-level contract audit

### F1 — Source identity, lineage và source-line coordinate

**Contract cần đạt:** mỗi answer candidate phải trỏ tới document/table/row/column hiện hành, source hash, table hash, raw-cell hash và coordinate map hash; không dùng filename rank, “latest”, local ordinal hoặc sidecar làm proof.

**Evidence hiện tại:**

- docs/PIPELINE.md:27-35,143-148 yêu cầu source closure/hash và tách navigation khỏi authority;
- scripts/e2e/README_VI.md:21-40 yêu cầu 1-based table-start line trong competition output;
- historical builder artifact có map PASS, fallback 0;
- canonical E2E hiện có BINDING_LINEAGE_DOCUMENT_SHA256_INVALID, BINDING_LINEAGE_RAW_CELL_SHA256_INVALID, BINDING_LINEAGE_TABLE_SHA256_INVALID và 385 coordinate/lineage-related blocker receipts.

**Judgement:** historical coordinate serialization **VERIFIED technical**; full-flow lineage **PARTIAL/BLOCKED**. Nâng được khi mọi row trong full population có matching hash-bound binding và map được kiểm độc lập, không chỉ map coverage count.

### F2 — Entity, role, reporting scope, period/year, metric và unit

**Contract cần đạt:** semantic fields phải pass hoặc chứng minh NOT_APPLICABLE; không điền từ route score, registry label, filename, model hoặc reviewer metadata.

**Evidence hiện tại:** src/finance_query/e2e/core/grounded_authorization.py:1-9 mô tả fail-closed adapter; docs/PIPELINE.md:91-104 nêu same rule. Canonical readiness có PERIOD_YEAR_MISMATCH, VARIABLE_MISMATCH, COUNTERFACTUAL_DIMENSION_INVALID, UNIT_CONVERSION_UNVERIFIED; machine semantic binding count là 0.

**Judgement:** family contract **BLOCKED**, không được dùng candidate validity/research rank để nâng. SEMANTIC_CANDIDATE_CHANGED trong independent review chỉ là queue signal.

### F3 — Metric row/column và exact numeric token

**Contract cần đạt:** exact V2 cell phải phù hợp với metric/row/column được hỏi; raw token, scale và unit conversion phải giữ lineage; Decimal parse/replay không tự prove semantic match.

**Evidence hiện tại:** docs/ARTIFACTS.md:21-35 phân biệt V2/V3 replay và Answer Certificate; canonical E2E có ANSWER_DECIMAL_INVALID, EXECUTION_NOT_PASSED, UNIT_CONVERSION_UNVERIFIED và NO_EXECUTABLE_STAGE.

**Judgement:** technical replay của legacy builder **VERIFIED** cho câu query đã replay; semantic numeric authorization **PARTIAL/BLOCKED**. Không có gold nên không nói token đã đúng đáp án benchmark.

### F4 — Formula, operand graph, AST và Decimal execution

**Contract cần đạt:** formula/operation phải allow-listed; operand graph đầy đủ; AST hash khớp; formula compatibility receipt không vi phạm; Decimal replay deterministic; không re-execute mù lần hai trong compiler.

**Evidence hiện tại:** docs/PIPELINE.md:33-35,91-99; src/finance_query/pipeline/resolver.py:382-391 vẫn ghi canonical_decimal_reexecution=false; canonical blocker categories có FORMULA_COMPATIBILITY_CONSTRAINT_VIOLATION=1612, FORMULA_COMPATIBILITY_RULE=164, MULTI_STAGE_EXECUTION_GRAPH_NOT_MATERIALIZED=132, NO_EXECUTABLE_STAGE=489.

**Judgement:** **BLOCKED** ở canonical authorization; legacy query replay chỉ là candidate/technical evidence.

### F5 — Counterfactual và alternative rejection

**Contract cần đạt:** alternative/counterfactual dimension phải được kiểm tra và reject khi không phù hợp; không để một route candidate duy nhất biến thành proof.

**Evidence hiện tại:** canonical readiness có COUNTERFACTUAL_NOT_REJECTED, COUNTERFACTUAL_UNCHECKED và COUNTERFACTUAL_DIMENSION_INVALID; answer_count=0.

**Judgement:** **BLOCKED**. Đây là family repair chung, không phân công sửa từng Question-ID.

### F6 — Population/coverage/memory

**Contract cần đạt:** cùng một frozen question manifest phải xuất hiện đủ một lần trong resolver, E2E receipts, certificate, compiler ledger, feedback packet và release ledger; input closure và memory budget phải được chứng minh.

**Evidence hiện tại:** builder historical có 1.012/1.012 technical records/replay; canonical resolver có 1.012 nhưng E2E 1.012 ABSTAIN; full asset 146,246 tables; candidate UID default chỉ khoảng 20.1%; partial local runs thiếu completion report.

**Judgement:** population count có evidence tốt, nhưng strict closure/release **BLOCKED** và memory bound **PARTIAL**. Count đủ không đồng nghĩa evidence đủ.

### F7 — Research/model/feedback isolation

**Contract cần đạt:** proposal sidecars được gắn role/hash, chỉ navigation hoặc candidate input; mọi value phải hydrate current source; model không authorise answer, certificate, gold, training, promotion hay release; feedback chỉ nuôi version sau.

**Evidence hiện tại:** docs/PIPELINE.md:67-69,271-313; builder defaults tại :73-87,10086-10134; reports ghi research enabled, 77 question changes, 2 model answer candidates; src/finance_query/pipeline/feedback/blocked.py:336-354,417-556 đặt feedback non-authorizing.

**Judgement:** authority guard **VERIFIED theo source contract**, ingestion opt-in/isolation **PARTIAL** vì auto-discovery. Đây là blocker reproducibility/audit purity, dù chưa có evidence raw research value được promote thành answer.

### F8 — Delivery/release gate

**Contract cần đạt:** strict-ready toàn population, complete certificates, independent audit/release ledger, no blocker, explicit release_authorized=true; ZIP chỉ là serialization sau gate.

**Evidence hiện tại:** compiler manifest có BEST_EFFORT_CANDIDATE=1012, strict_ready_count=0, release_authorized=false; docs/PIPELINE.md:252-254 nói ngay cả strict vẫn chờ release gate; docs/ARTIFACTS.md:21-35 nói ZIP không phải release.

**Judgement:** **VERIFIED blocker**. Chưa có artifact hợp lệ để tuyên bố full pipeline completion.

## 6. Feedback/model worker status

### 6.1 Prepare-only lane

artifacts/runs/vifinqa_primary_canonical_migration_20260829_r1/blocked_feedback_prepare/blocked_feedback_run.manifest.json:1-98 ghi:

- status=PREPARED_MODEL_FEEDBACK_NOT_RUN;
- blocked_count=1012, feedback records 1012;
- model_evaluated_count=0;
- MODEL_NOT_RUN=1012;
- promotion_allowed=false, release_authorized=false, training_eligible=false.

**Judgement: PARTIAL.** Packet preparation complete nhưng model lane chưa chạy; feedback không được xem là completion của pipeline answer.

### 6.2 Remote model worker

artifacts/runs/vifinqa_blocked_feedback_gpu_v1_20260829_v3/vifinqa_blocked_feedback_qwen_v1/model_run_manifest.json:1-39 ghi:

- real_model_invoked=true;
- đủ row count, errors=[];
- status=COMPLETED_MODEL_FEEDBACK_NON_AUTHORIZING;
- nhưng MODEL_OUTPUT_INVALID=1012, valid_feedback_count=0, model_evaluated_count=0;
- 1.012 row cần human review và mọi authority flag vẫn false.

**Judgement:** worker invocation/completion telemetry **VERIFIED** theo manifest; feedback usable/improvement **PARTIAL**. docs/PIPELINE.md:324-337 cũng quy định real model, pinned revision, full row count và errors=[] mới là worker completion; output invalid vẫn là telemetry/ABSTAIN, không phải gold/training/answer.

## 7. Dependency graph và điểm gãy

~~~text
questions.jsonl
  → immutable request/question manifest
  → review bundle + candidate universe
  → legacy builder compatibility proposal
      ├─ optional research/model/dense sidecars (rủi ro auto-discovery)
      ├─ structured tables + source-line-map (memory/coordinate dependency)
      └─ proposal ledger + ZIP (technical only)
  → deterministic resolver
  → independent E2E config
      ├─ V2/V3/metric registry hashes
      └─ optional best_candidate_predictions (B0: path hiện missing)
  → E2E observer / Answer Certificate
  → submission compiler
  → independent release ledger/gate (chưa đạt/chưa có receipt xanh)
  → blocked feedback (non-authorizing, version sau)
~~~

Các dependency bắt buộc phải được coi là độc lập:

| Dependency | Consumer | Nếu thiếu/sai | Trạng thái |
| --- | --- | --- | --- |
| frozen 1.012 question IDs/order | mọi stage | coverage không chứng minh full population | Có count; cần hash-bound rerun |
| V2 structured tables | builder/E2E | không hydrate/replay exact cell | Có nhiều path; canonical run còn blockers |
| V3 context + manifest | E2E semantic/provenance | thiếu context/hash | Đã khai báo trong config, nhưng certificate blocked |
| metric registry | typed formula/unit | unit/metric ambiguity | Đã khai báo; semantic/unit blockers còn |
| candidate ledger | canonical run-e2e | config smoke fail trước replay | **Thiếu tại declared path** |
| source-line map | competition builder/delivery | local ordinal hoặc coordinate không portable | Historical artifact PASS; current full-flow dependency vẫn cần map gate |
| research/model sidecars | proposal only | dễ tiêu thụ agent output; thay đổi candidate | Auto-discovery đang bật nếu không disable |
| independent certificate | compiler/release | proposal chỉ BEST_EFFORT/PARTIAL | canonical count 0 strict-ready |
| gold/official scorer | accuracy statement | không được kết luận accuracy | Không thấy trong input snapshot |

## 8. Lệnh kiểm tra và cách đọc kết quả

Các lệnh dưới đây là read-only hoặc command kiểm tra; những lệnh có <...> là **PROPOSAL**, chưa chạy trong audit và không tạo artifact ở đây.

### 8.1 Smoke test canonical config

~~~bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src \
  .venv/bin/python -m finance_query.cli run-e2e \
  --config configs/e2e/deterministic_replay_v1_locked.yaml \
  --output-dir /tmp/ai_guru_audit_config_probe_20260830
~~~

Expected hiện tại: fail trước replay với missing best_candidate_predictions. Sau khi input/config được sửa, completion tối thiểu phải có output manifest, input hashes, question count, certificate/ABSTAIN counts và không có missing input.

### 8.2 Audit canonical migration receipts

~~~bash
jq '{question_count, e2e_status_counts, authority}' \
  artifacts/runs/vifinqa_primary_canonical_migration_20260829_r1/e2e_observer/e2e_observer.manifest.json

jq '{authorization_status, question_count, answer_count, abstain_count,
    binding_status_counts, blocker_receipt_count, blocker_category_counts,
    release_authorized, submission_compilation_allowed}' \
  artifacts/runs/vifinqa_primary_canonical_migration_20260829_r1/e2e_observer/grounded_e2e/authorization_readiness_v1.json

jq '{delivery_policy, delivery_status_counts, strict_ready_count,
    release_authorized}' \
  artifacts/runs/vifinqa_primary_canonical_migration_20260829_r1/compiled_submission/submission_compile.manifest.json
~~~

Nếu answer_count=0, authorization_status=blocked, strict_ready_count=0 hoặc release_authorized=false, không gọi full strict completion.

### 8.3 Audit technical ZIP, không nâng authority

~~~bash
unzip -t \
  artifacts/runs/vifinqa_answer_optimization_20260830/
  local_source_first_exact_only_v16_nodense.zip

jq '{question_count, validation, verification, structured_table_asset,
    coordinate_validation, research, model_answer_candidates}' \
  artifacts/runs/vifinqa_answer_optimization_20260830/local_source_first_exact_only_v16_nodense/build_report.json
~~~

Cần đọc riêng verification.class_counts và certificate/config cùng với validation; valid=true/errors=[] chỉ là replay/package gate.

### 8.4 Kiểm tra isolation trước run mới

~~~bash
rg -n --glob '*.py' \
  'DEFAULT_RESEARCH|DEFAULT_ROUTE_OVERLAY|DEFAULT_MODEL_ANSWER|disable-research-fusion|disable-candidate-validity' \
  scripts/e2e/build_competition_submission_v1.py

rg -n --glob '*.md' --glob '*.yaml' \
  'research_only|submission_eligible|release_authorized|may_authorize_answer' \
  docs configs artifacts/research
~~~

Baseline source-only phải ghi explicit --disable-research-fusion --disable-candidate-validity và không truyền model/dense/fine-tuned/direct replay. Một lane research/model sau đó phải chạy ở output directory và lineage khác.

### 8.5 Kiểm tra worker feedback

~~~bash
jq '{status, blocked_count, feedback_record_count, model_evaluated_count,
    valid_feedback_count, model_status_counts, authority_boundary}' \
  artifacts/runs/vifinqa_primary_canonical_migration_20260829_r1/blocked_feedback_prepare/blocked_feedback_run.manifest.json

jq '{real_model_invoked, model_status_counts, valid_feedback_count,
    model_evaluated_count, errors, status, require_human_review_count}' \
  artifacts/runs/vifinqa_blocked_feedback_gpu_v1_20260829_v3/vifinqa_blocked_feedback_qwen_v1/model_run_manifest.json
~~~

real_model_invoked=true không biến feedback thành answer; MODEL_OUTPUT_INVALID không thể là improvement evidence usable nếu chưa qua contract/human/source verification.

### 8.6 Proposal memory-bounded/full-closure test

**PROPOSAL — chưa chạy:** cần tách hai lane và ghi memory peak/row checkpoint vào manifest:

~~~text
Lane A: candidate_uids + exact source-line-map
  → chứng minh candidate universe closure bằng manifest/hash, hoặc ghi rõ chỉ là coverage proposal.

Lane B: full corpus
  → streaming/sharded UID hydration, không materialize toàn bộ JSONL vào một dictionary;
  → ghi peak RSS, shard count, input SHA, used UID count, missing UID count, completion receipt.
~~~

Không được gọi Lane A là full-corpus chỉ vì question_count=1012; không được gọi Lane B completed chỉ vì process đã bắt đầu hoặc có partial evidence files.

## 9. Điều kiện nâng từ PARTIAL/UNRESOLVED lên VERIFIED

Một run mới chỉ được nâng strict VERIFIED khi tất cả điều kiện sau cùng đúng trên cùng immutable input closure:

1. **Input closure:** config không còn reference thiếu; mọi V2/V3/registry/candidate/source-map path tồn tại, SHA khớp manifest; không dùng symlink/latest/filename substitution ngoài closure.
2. **Population closure:** frozen 1.012 IDs/order xuất hiện đúng một lần ở proposal, resolver, E2E receipt/certificate, compiler ledger, feedback (nếu chạy) và release ledger; missing/duplicate/extra đều fail-closed.
3. **Coordinate closure:** mỗi used table có document_id|table-start-line hợp lệ, source-line-map hash-bound, no local ordinal fallback; nếu claim full corpus thì phải chứng minh map/full asset coverage, không chỉ candidate subset.
4. **Semantic family pass:** entity, role, reporting scope, year/period, flow-vs-stock, metric, row/column, unit/scale và revision/period transition đều pass hoặc explicit contract NOT_APPLICABLE; không lấy route/model/research/reviewer label làm proof.
5. **Exact evidence:** exact document/table/row/column và raw numeric token được bind vào current source; document/table/cell hash pass; V3/context chỉ bổ sung provenance/semantic context theo contract.
6. **Operation pass:** formula/operand graph complete, AST allow-list/hash pass, formula compatibility pass, counterfactual/alternative checks complete, Decimal replay deterministic và không còn EXECUTION_NOT_PASSED/NO_EXECUTABLE_STAGE.
7. **Independent certificate:** mọi row có matching canonical Answer Certificate; không có blocker receipt; top-level answer_authorized chỉ true khi certificate pass; candidate prediction vẫn tách khỏi answer authorization.
8. **Isolation receipt:** baseline source-only hoặc research lane explicit được ghi trong manifest; sidecar path/hash/role/accepted influence được kiểm; raw research/model output không trở thành evidence, gold, training label, promotion hoặc release decision.
9. **Release gate:** compiler/independent auditor xác nhận strict_ready_count=1012, không PARTIAL/UNRESOLVED/REJECTED ở strict population, release_authorized=true, artifact package hash-bound và release ledger có đầy đủ audit evidence.
10. **Reproducibility:** run được thực hiện từ source/config snapshot đã xác định, không phụ thuộc /tmp implementation path không còn truy cập được; output directory mới, không overwrite; peak memory và completion receipt đủ để operator lặp lại.

Gold/official scorer là điều kiện riêng để nói **accuracy**. Ngay cả khi 10 điều kiện trên đạt source-grounded authorization, nếu gold vẫn thiếu thì chỉ được nói “verified theo source/contract”, không nói accuracy, leaderboard correctness hay improvement percentage.

## 10. Queue sửa chữa theo family, không trùng nghiên cứu theo câu

Đây là thứ tự ưu tiên để một agent/operator triển khai sau audit. Các mục này là **PROPOSAL**, không phải thay đổi đã thực hiện trong file này.

### P0 — Khôi phục một canonical smoke test deterministic

- Sửa ownership/input manifest của best_candidate_predictions hoặc dùng config source-only explicit không khai báo path thiếu.
- Không chọn artifact khác chỉ vì tên gần nhất; phải có SHA, question manifest và provenance.
- Chạy lại run-e2e ở output directory mới; lưu exit code/manifest.

### P1 — Đóng release gate thật sự

- Duy trì builder ZIP như proposal technical output.
- Dùng resolver → E2E observer → compiler → independent release ledger làm authority path.
- Không coi BEST_EFFORT_CANDIDATE, PARTIAL, UNRESOLVED, REJECTED, ABSTAIN hoặc submission.zip là completion.
- Chỉ tuyên bố completion khi manifest ghi strict population và release_authorized=true.

### P2 — Làm isolation fail-closed ở ingestion

- Baseline operator phải opt-in từng research/model input; default không tự nạp sidecar.
- Mỗi sidecar cần role/hash/producer/status; consumer chỉ được dùng navigation/candidate proposal.
- Giữ current-table rehydration/replay; nếu thiếu exact source thì ABSTAIN.
- Model feedback chỉ tạo family hypothesis/next experiment; human/source-verified label mới có thể vào training/promotion.

### P3 — Giải memory/closure bằng streaming hoặc sharding

- Không nạp full 1.015 GB JSONL vào một dictionary khi chưa có bound.
- Nếu dùng candidate UID filter, thêm closure receipt chứng minh phạm vi candidate và ghi đây là candidate coverage, không gọi full-corpus.
- Nếu cần full-corpus, hydrate theo shard/UID, ghi peak RSS, shard completion, missing/duplicate UIDs và source map hash.

### P4 — Sửa các family semantic/operation từ blocker aggregate

- Ưu tiên period/year/scope/entity/metric/unit;
- sau đó formula/operand/AST/counterfactual;
- rồi lineage/hash/coordinate;
- mỗi thay đổi phải kiểm tra held-out family set hoặc independent audit set, không patch theo QID và không dùng review packet làm gold.

## 11. Kết luận cuối

- **Đã verify:** public CLI tồn tại; builder historical có technical ZIP 1.012 records/replay và source-line-map pass; canonical resolver/observer/compiler/feedback đều có manifest và authority boundary rõ; remote feedback worker có invocation telemetry đủ nhưng output invalid.
- **Blocker đã verify:** canonical E2E config thiếu declared candidate ledger; canonical migration 1.012 row đều ABSTAIN, strict_ready_count=0, release false; flow còn legacy compatibility producer; research/model sidecar có auto-discovery; full-corpus memory/closure chưa được chứng minh đồng thời; semantic/operation/counterfactual/lineage family còn blocker.
- **Partial:** technical package không đồng nghĩa source-grounded answer; prepare-only feedback không phải model completion; model completion không phải answer/gold; candidate UID coverage không phải full-corpus closure; independent research review không phải accuracy.
- **Proposal:** sửa input closure, chạy baseline isolation sạch, tạo memory-bound receipt, hoàn thiện canonical resolver/E2E/release ledger và chỉ nâng VERIFIED khi checklist mục 9 đạt toàn population.
- **Accuracy:** không đưa ra kết luận accuracy vì snapshot thiếu gold/official scorer. Không có claim leaderboard, correctness rate hoặc improvement rate trong audit này.

## File đã tạo

- [docs/research/AGENT_PIPELINE_COMPLETION_REVIEW_V1.md](/home/dungle/Documents/AI_guru/docs/research/AGENT_PIPELINE_COMPLETION_REVIEW_V1.md)

Không tạo hoặc sửa file nào khác.
