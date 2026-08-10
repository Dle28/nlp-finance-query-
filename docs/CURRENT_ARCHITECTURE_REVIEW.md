# ViFinQA — kiến trúc hiện tại, nhìn từ góc độ architecture review

> Trạng thái: current design review. Tài liệu này mô tả hệ đang chạy, ranh giới
> module và các quyết định cần ưu tiên. Nó không thay raw report, evidence,
> bundle hay provenance nhãn.

## 1. Kết luận ngắn

ViFinQA không phải một chatbot RAG trả lời trực tiếp từ bảng gần nhất. Nó là
hệ thống **retrieval có kiểm chứng nguồn**:

```text
câu hỏi
→ lập kế hoạch truy vấn
→ tìm bảng ứng viên
→ dựng lại cấu trúc bảng và ngữ cảnh có provenance
→ bind exact row + exact column + exact cell
→ reviewer/calibrator quyết định mức tin cậy
→ chỉ executor allow-list mới được tính kết quả
```

Mục tiêu trước mắt là tạo retrieval/evidence labels đáng tin cậy. Vì vậy một
candidate ở rank 1, một score dense cao, hoặc một tóm tắt đọc hay **không** là
bằng chứng và không được tự tạo answer hay `machine_calibrated`.

Nguyên tắc cô đọng của kiến trúc là:

```text
ML finds.
Rules verify.
Provenance decides.
Executor calculates.
```

## 2. Nguyên tắc bất biến

| Nguyên tắc | Ý nghĩa vận hành |
| --- | --- |
| Raw report là nguồn sự thật | Không sửa số OCR hoặc thay raw cell bằng text suy diễn. |
| UID và hash là identity | Mọi sidecar phải bind `internal_table_uid`, table/report SHA và manifest input. |
| Retrieval khác grounding | Retrieval chỉ tìm; grounding phải chứng minh metric, kỳ, row, column và cell. |
| Metadata khác evidence | Tiêu đề, function, segment, entity alias chỉ điều hướng; không bind số. |
| Fail closed | Thiếu entity/scope/kỳ/cột duy nhất hoặc số OCR không tin cậy thì giữ partial/blocked. |
| Provenance không bị nâng cấp ngầm | `human_verified`, `machine_calibrated`, `machine_provisional`, `needs_human` là bốn trạng thái riêng. |

## 3. Bản đồ module

```mermaid
flowchart LR
    RAW[Raw OCR report + HTML table] --> A[1. Corpus / TableAsset]
    Q[Question] --> P[2. Planner / Question IR]
    A --> R[3. Retrieval: FTS + dense + RRF]
    P --> R
    R --> B[Immutable review bundle]

    B --> V2[4a. Bảng nguồn đã tái dựng (V2)]
    V2 --> V3[4b. Ngữ cảnh evidence chuẩn hóa (V3)]
    V3 --> N[4c. Report segment + entity sidecars]

    P --> E[5. Evidence compiler]
    V2 --> E
    V3 --> E
    N --> E
    E --> D[Direct EvidenceSet / Formula EvidenceSet]

    D --> G[6. Review, critic, calibrator, ledger]
    G --> L{Provenance}
    L --> HV[human_verified]
    L --> MC[machine_calibrated]
    L --> MP[machine_provisional]
    L --> NH[needs_human]

    HV --> T[7. Label export / training gate]
    MC --> T
    D --> X[8. Narrow executor / submission compiler]
```

### Module 1 — Corpus và TableAsset

**Code:** `corpus.py`, `schemas.py`, `build_*bundle*.py`.

Input là OCR text có bảng HTML-like. Extractor tạo `TableAsset` với UID ổn
định, ticker, năm, scope, page/ordinal, byte/character offsets, source/table
SHA, `context_before`, headers, rows và `search_text`.

Đây là biên giới ingestion. Không được tạo UID mới khi review, và không dùng
phần text OCR đã sửa để ghi đè raw grid. Kaggle xây corpus/index nặng; local
chỉ đọc bundle đã export.

### Module 2 — Planner và Question IR

**Code:** `questions.py`, `router_model.py`, `plan_overrides.py`.

Planner rule-based (hoặc router model khi đã cấu hình) tạo `QuestionPlan`:

```text
family · ticker · year · scope · metric hint · unit · operand · operation AST
```

`effective_metric` làm sạch ticker, tên công ty hoặc mốc kỳ khỏi phrase dùng để
tìm metric, nhưng original question/plan luôn được giữ để audit. Override chỉ
được áp dụng theo hash của question và có reason code; không viết lại
`review_items.jsonl`.

**Giới hạn hiện tại:** IR đủ cho direct lookup và một số template công thức,
nhưng chưa là một DSL hoàn chỉnh cho filter → median → rank → lookup đa stage.
Những câu ngoài allow-list được định danh `multi_stage_selection_unresolved`,
đây là hành vi đúng và an toàn. Đây là bottleneck thực sự của các câu nhiều
entity/kỳ, quan trọng hơn việc tăng độ phức tạp của retriever.

### Module 3 — Retrieval

**Code:** `retrieval.py`, `pipeline.py`, Kaggle build scripts.

| Thành phần | Vai trò |
| --- | --- |
| SQLite FTS | Bắt exact/near-exact metric wording. |
| FAISS dense index | Bắt biến thể ngữ nghĩa; baseline dùng multilingual E5. |
| Hybrid RRF | Hợp nhất rank lexical và dense thành candidate Top-K. |
| V3 repair | Recovery adjacent-table có provenance, không giả là kết quả truy hồi gốc. |

Output là **candidate table list**, không phải answer. Bundle đóng băng Top-K,
manifest và hash để local review reproducible. Dense/FAISS không được rebuild
khi chỉ sửa UI, table structure hoặc metadata sidecar.

### Module 4 — Chuẩn hóa nguồn cục bộ

Module này gồm bốn lớp có vai trò khác nhau.

| Lớp | File chính | Đầu ra | Được phép / không được phép |
| --- | --- | --- | --- |
| **Bảng nguồn đã tái dựng (V2)** | `table_structure.py` | rectangular raw-HTML grid, blank cell, span, cell provenance | Sửa cấu trúc hiển thị; không sửa OCR/số. |
| **Ngữ cảnh evidence chuẩn hóa (V3)** | `evidence_context.py` | canonical header path, row profile, period/unit, quality | Suy dẫn từ V2; không là evidence số. |
| Report segment | `report_segments.py` | reader heading, parent heading, descriptor ngắn | Điều hướng UI; không bind metric/kỳ/cell. |
| Report entity alias | `report_entities.py` | alias title-zone → ticker duy nhất | Chỉ discovery; không suy scope hay làm evidence. |

V2 là lớp bằng chứng cấu trúc: ô trống, `rowspan` và `colspan` được giữ đúng
vị trí. V3 chỉ nhận header/number mà provenance chứng minh được. Một cell OCR
có hai nhóm số không bị “sửa” thành một số; nó bị quarantine.

Report segment hiện dùng policy
`source_heading_metadata_only_no_numeric_inference_v2`: nó làm context đọc
nhanh hơn, nhưng title mơ hồ hạ về `chức năng · phần · kỳ · đơn vị`. Đây không
phải OCR correction engine.

### Module 5 — Evidence compiler

**Code:** `binding.py`, `formula_evidence.py`,
`build_direct_evidence_sets.py`, `build_formula_evidence_sets.py`.

Evidence compiler là lõi phân biệt ViFinQA với RAG thông thường.

**Direct EvidenceSet** chỉ hợp lệ khi nhận diện được exact raw row, raw header
và value cell/kỳ. Hai recovery contract hẹp có provenance: bỏ một số context
query không phải metric, hoặc `parent source heading + exact child row`. Cả hai
vẫn phải tái kiểm V2/canonical header.

**Formula EvidenceSet** tách câu thành operand slots. Mỗi slot cần entity,
metric, year, allowed table function và exact row/cell. Complete chỉ có khi mọi
operand có binding duy nhất, cùng scope khi contract yêu cầu, định nghĩa formula
không ambiguous và gate công thức đã thỏa. Một bảng đúng cho một operand không
làm toàn câu complete.

**Source completion** (`source_completion.py`) chỉ là audit/shadow khi raw
report có table cần thiết nhưng immutable bundle không có. Candidate completion
phải revalidate report SHA, table SHA, UID, grid và V3 context; luôn
`answer_eligible=false`, `training_eligible=false` cho đến khi được đưa qua
một quy trình materialize có kiểm soát.

### Module 6 — Review, critic, calibration và ledger

**Code:** `auto_review_bundle_v4.py`, `review_bundle_widget.py`,
`build_review_ledger.py`, `build_execution_ledger.py`,
`train_review_calibrator.py`.

“Multi-agent” ở đây là các reviewer module độc lập (lexical, dense, metadata,
evidence, challenger, grounding, verifier), không phải nhiều LLM tự thuyết
phục lẫn nhau. Candidate cần qua exact V2 row/column check trước consensus.

Calibrator là logistic-regression trên candidate features và chỉ chạy sau khi
có seed review phù hợp. Nó xếp hạng độ tin cậy, không được override grounding
verifier. Semantic cross-encoder cũng là audit signal, không thay evidence.

Ledger giữ toàn bộ outcome; final training export chỉ nhận provenance đủ điều
kiện. Điều này tránh biến `machine_provisional` hoặc `needs_human` thành
negative/positive giả.

### Module 7 — Training và evaluation

**Code:** `export_review_labels.py`, `train_dense_retriever.py`,
`train_pilot_candidate_reranker.py`, `analyze_autonomous_reviews.py`.

Training input phải tách:

```text
human_verified       → usable, weight cao
machine_calibrated   → usable sau gate, weight thấp hơn
machine_provisional  → audit only
needs_human          → quarantine only
```

Candidate reranker hiện chỉ shadow-rank trong immutable Top-K. Dense fine-tune
chỉ được mở khi đủ số machine-silver grounded theo threshold; model mới được
đào tạo song song, không ghi đè FAISS/dense index đang dùng.

### Biên giới của các model ML

Hệ nên giữ bốn component ML độc lập, với vai trò bị giới hạn:

```text
Question → Planner/Router → Dense E5 → candidate Top-K → Candidate reranker
                                                          ↓
                                                deterministic evidence binder
                                                          ↓
                                            Logistic confidence calibrator
```

- Multilingual E5 tiếp tục là dense baseline.
- Cross-encoder/reranker chỉ ước lượng `P(relevant | question, candidate)` để
  sắp candidate Top-K; không bao giờ tuyên bố bảng là evidence.
- Logistic regression phù hợp low-data, dễ kiểm tra feature và calibration.
- Confidence cao không override exact binding fail. Fine-tune E5 chỉ dùng
  positive đã grounded và hard-negative có supervision hợp lệ; top-1 retrieval
  không được tự coi là positive.

### Module 8 — Execution và submission

**Code:** `execution.py`, `submission.py`, `build_execution_ledger.py`.

Parser số dùng `Decimal`, từ chối cell có nhiều nhóm số và unit conversion có
allow-list. Executor không đọc candidate “có vẻ đúng”; nó chỉ đọc binding đã
revalidate. Hiện execution allow-list hẹp (ví dụ percentage change; một số
shadow executor), còn ratio/ranking/filter/multi-stage chủ yếu evidence-only.

Compiler submission tách binding table/AST/execution record và validate lại
trước khi đóng gói. Đây là boundary đúng: retrieval/review không được tự trở
thành answer engine.

## 4. Hợp đồng artifact và provenance

```text
raw report SHA + raw table SHA
        ↓
TableAsset.internal_table_uid
        ↓
immutable bundle manifest
        ↓
V2 structure manifest
        ↓
V3 context manifest
        ↓
segment / direct / formula sidecar manifests
        ↓
review ledger / execution ledger / training export
```

Một consumer phải từ chối sidecar nếu bundle/V2/V3/segment/override hash không
khớp. Phiên bản có nghĩa riêng, không được lẫn:

| Tên hiển thị | Artifact | Vai trò |
| --- | --- | --- |
| **Bảng nguồn đã tái dựng (V2)** | `tables_structured_v2.jsonl` | raw grid + cell provenance |
| **Ngữ cảnh evidence chuẩn hóa (V3)** | `tables_evidence_context_v3.jsonl` | header/kỳ/đơn vị/quality |
| Report segment schema V1, policy V2 | `report_segments_v1.jsonl` | metadata đọc nhanh |
| Formula EvidenceSet V6 | `formula_evidence_sets_*.jsonl` | discovery/binding operands |

Số phiên bản không phải mức độ “tốt hơn” chung; nó chỉ chỉ schema/policy của
từng artifact family.

## 5. Đánh giá kiến trúc

### Điểm mạnh

1. **Phân tách retrieval và truth đúng đắn.** Điều này giảm rủi ro trả lời từ
   context leakage hoặc dense similarity.
2. **V2/V3 là ranh giới kỹ thuật tốt cho OCR table.** Cấu trúc bảng và nội dung
   OCR được tách, nên không che giấu lỗi số.
3. **Fail-closed và provenance rõ.** Đây là điều kiện cần để tự huấn luyện mà
   không tự khuếch đại lỗi.
4. **Shadow sidecar an toàn.** Source completion/semantic rerank không âm thầm
   mutate corpus, rank hoặc training data.
5. **Execution bị giới hạn.** Quyết định này đúng hơn việc cố chạy mọi formula
   từ OCR chưa sạch.

### Rủi ro và technical debt

| Ưu tiên | Nhận xét reviewer | Hậu quả | Khuyến nghị |
| --- | --- | --- | --- |
| P0 | Version/file name tích lũy (`v3`, `v31`, `v4`, `ticker_v4`, …) nằm trong nhiều script. | Dễ chọn sai artifact hoặc sai hash dependency. | Tạo `ArtifactRegistry` + typed manifest + dependency DAG; stage nhận logical artifact, không hard-code filename. |
| P0 | JSON dict tự do xuyên module; contract phân tán trong validator. | Lỗi schema phát hiện muộn, khó refactor. | Định nghĩa model typed cho TableAsset, V2, V3, EvidenceBinding, ReviewDecision và manifest; validate ở mỗi boundary. |
| P0.5 | Planner đa stage hiện theo template riêng. | Coverage thấp cho câu filter/rank/ratio phối hợp. | Tạo constrained `QueryProgram` IR: source operands → filter → aggregate → rank → lookup; executor chỉ bật từng operator sau test. |
| P1 | Table function/section có broad/generic classification. | Note schedule có thể bị hiểu như primary statement hoặc ngược lại. | Tách `document_role`, `statement_family`, `note_hierarchy`, `table_layout`; audit precision theo lớp thay vì một label. |
| P1 | Chuẩn hóa hiện xử lý layout/context, không đánh giá chất lượng OCR nội dung. | Không thể tự “làm sạch số” một cách đáng tin. | Thêm `OCRQualityProfile`: malformed cell, multi-number, header confidence, row alignment; dùng để quarantine/retrieval weight, không tự sửa số. |
| P2 | Source completion quét raw report theo nhu cầu. | Chậm khi scale hoặc audit nhiều formula. | Build raw-source catalog read-only theo `(ticker, year, scope, function, normalized row label)` và vẫn revalidate raw hash ở lần dùng. |
| P2 | Evaluation chủ yếu pipeline/gate level. | Khó đo bộ phận nào gây sai nhiều nhất. | Dashboard theo family × statement function × evidence state × provenance; giữ canary regression set. |

ArtifactRegistry V1 đã có bootstrap/validator ở cấp workspace. Nó register
`raw_table`, `structured_table`, `evidence_context`, `direct_evidence`,
`formula_evidence`… bằng logical name, schema, SHA và dependency SHA. Đây là
migration additive: manifest nguồn của từng sidecar vẫn là contract chính cho
đến khi consumer được chuyển từng bước sang logical resolution. Chi tiết tại
[`ARTIFACT_REGISTRY.md`](ARTIFACT_REGISTRY.md).

P0.5 đã có implementation shadow đầu tiên: `QueryProgram` chỉ compile template
Quick Ratio → ΔGPM → Interest Coverage và chỉ evaluate operand map đã grounded
do caller đưa vào. Trên bundle hiện tại, Q369 compile được nhưng bị
`shadow_blocked` vì Formula EvidenceSet chưa complete/coherent; không answer,
không label promotion. Contract và lệnh chạy tại [`QUERY_PROGRAM.md`](QUERY_PROGRAM.md).

## 6. Kiến trúc đích nên đi theo

```text
immutable raw layer
  └─ TableAsset
      └─ structural layer (V2)
          └─ evidence-context layer (V3)
              ├─ SemanticCatalog (derived metadata only)
              ├─ RetrievalView
              ├─ EvidenceView → raw provenance
              └─ UIView

QuestionPlan / QueryProgram
  └─ candidate retrieval
      └─ exact bindings
          └─ review decision + provenance
              ├─ training export
              └─ allow-listed execution
```

Điểm then chốt là **một nguồn canonical cho structure/context**, nhưng nhiều
view theo nhiệm vụ. `SemanticCatalog` là metadata suy dẫn song song với
EvidenceView, không phải layer truth đứng giữa V3 và evidence: document role,
statement family hay note hierarchy có thể classification sai. Evidence binder
luôn phải đi trực tiếp từ V3 về raw provenance. Không đưa text segment đã làm
đẹp trở lại `search_text` hay dùng làm evidence; UI cũng không tự suy luận
semantic khác Evidence compiler.

Ở mức vận hành, tám module chi tiết có thể được thu gọn thành sáu khối:

```text
Question → QueryProgram Planner
Raw report → TableAsset → Retrieval (FTS + E5) → Top-K
Raw report → V2 Structure → V3 Context → Grounding (exact row/column/cell)
Grounding → Review / Calibration → Training | Execution (Decimal AST)
```

## 7. Roadmap khuyến nghị

1. **P0 — registry và typed contracts:** thống nhất logical names, dependency
   manifests, provenance enum và validator chung. Đây là bước giảm rủi ro vận
   hành lớn nhất, không cần GPU/rebuild dense.
2. **P0.5 — QueryProgram:** xây IR có giới hạn cho filter → aggregate → rank →
   lookup; bắt đầu một family có fixtures raw exact và shadow executor. Không
   tổng quát hoá bằng prompt hay đưa LLM-generated pandas/SQL vào pipeline.
3. **P1 — table semantics + OCR quality:** xây catalog structural trước,
   đo precision/coverage trên canary; không tự sửa content OCR.
4. **P2 — evaluator/dashboard:** đo retrieval, grounding, formula completeness
   và provenance riêng; quyết định training từ metric, không từ confidence đơn.
5. **P3 — training:** chỉ sau khi có đủ label grounded và holdout; train model
   song song, compare, rồi mới cân nhắc thay retrieval index.

## 8. Quyết định reviewer

Tôi **không khuyến nghị** GraphRAG, multi-agent LLM debate, LLM-generated
SQL/Pandas, end-to-end QA model, fine-tune large LM, OCR correction bằng LLM
hoặc rebuild FAISS liên tục ở thời điểm này. Những thứ đó làm hệ phức tạp hơn
nhưng không xử lý bottleneck contract/IR/grounding. Ưu tiên đúng là registry,
QueryProgram, source structure, semantic metadata và evaluation. Khi evidence
coverage/provenance tốt lên, retriever/reranker mới có supervision không nhiễu
để học.
