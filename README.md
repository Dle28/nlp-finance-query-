# ViFinQA — Retrieval, Evidence Grounding và Human-in-the-loop Review

Repository này xây dựng pipeline QA tài chính tiếng Việt trên ViFinQA theo hướng:

> **retrieve đúng bảng → trích đúng bằng chứng trong bảng → review có kiểm chứng → học từ một phần human label → mới tự động hóa phần còn lại.**

Mục tiêu hiện tại chưa phải sinh answer cuối bằng LLM. Mục tiêu trước mắt là tạo được **retrieval labels đáng tin cậy** để huấn luyện/evaluate retriever, reranker và các bước reasoning phía sau.

---

## 1. Kiến trúc hiện tại

```text
                    KAGGLE / GPU
                        │
                        ▼
                raw financial reports
                        │
                        ▼
                 table_assets.jsonl
                        │
              ┌─────────┴─────────┐
              ▼                   ▼
        lexical index          dense index
          SQLite FTS             FAISS
              └─────────┬─────────┘
                        ▼
                 hybrid retrieval
                        │
                        ▼
                Top-K table candidates
                        │
                        ▼
           V3 evidence projection / repair
              - effective_metric
              - adjacent-table recovery
              - period-aware value row
              - exact direct_evidence
                        │
                        ▼
             vifinqa_review_bundle_v3
                        │
                    DOWNLOAD
                        │
                        ▼
                      LOCAL
                        │
                        ▼
       raw-HTML table-structure sidecar V2
       - preserve empty cells / expand spans
       - canonical evidence context V3
       - table function/purpose + context trace
       - conservative inline-`td` header recovery
       - accounting-side and formula-operand gates
                        │
                        ▼
                  diagnostic gate
                        │
                        ▼
             source-aware multi-agent cross-review
                        │
          exact raw-row / raw-column / metric gate
                        │
       machine-silver accumulation (no human relabelling)
                        │
            ┌───────────┴───────────┐
            ▼                       ▼
     machine_calibrated         needs_human
            │                       │
            └───────────┬───────────┘
                        ▼
                final retrieval labels
```

Chi tiết kiến trúc nằm trong [`ARCHITECTURE.md`](ARCHITECTURE.md).
Bản giải thích lại theo ranh giới mô-đun, artifact contract, rủi ro và roadmap
cho người review kiến trúc nằm tại
[`docs/CURRENT_ARCHITECTURE_REVIEW.md`](docs/CURRENT_ARCHITECTURE_REVIEW.md).
ArtifactRegistry workspace-level dùng logical artifact name thay cho routing
theo filename version nằm tại
[`docs/ARTIFACT_REGISTRY.md`](docs/ARTIFACT_REGISTRY.md).
QueryProgram shadow-only cho câu hỏi nhiều bước và boundary không tự trả lời/
đổi nhãn nằm tại [`docs/QUERY_PROGRAM.md`](docs/QUERY_PROGRAM.md).
OCR Quality Profile V1 đo cell/layout có rủi ro từ V2/V3 mà không sửa OCR hay
đổi evidence/rank nằm tại [`docs/OCR_QUALITY_PROFILE.md`](docs/OCR_QUALITY_PROFILE.md).
Sửa grid review cục bộ và UI an toàn được mô tả tại
[`docs/TABLE_STRUCTURE_V2.md`](docs/TABLE_STRUCTURE_V2.md); bước này không
rebuild Kaggle, lexical index hay dense index.
Formula-aware review và contract EvidenceSet nằm tại
[`docs/FORMULA_EVIDENCE_SETS.md`](docs/FORMULA_EVIDENCE_SETS.md).
Tóm tắt thay đổi và kết quả validation nằm tại
[`docs/CONTEXT_FORMULA_REVIEW_IMPLEMENTATION.md`](docs/CONTEXT_FORMULA_REVIEW_IMPLEMENTATION.md).
Canary lọc→xếp hạng→truy xuất Q369 và phép tính grounded nằm tại
[`docs/Q369_GROUNDED_REVIEW.md`](docs/Q369_GROUNDED_REVIEW.md).
Pilot xếp lại candidate Top-K, provenance policy và evaluation boundary nằm tại
[`docs/PILOT_CANDIDATE_RERANKER.md`](docs/PILOT_CANDIDATE_RERANKER.md).
Luồng tự động khi không có human reviewer—raw-table canonicalization, agent
cross-check và machine-silver training gate—nằm tại
[`docs/AUTONOMOUS_RAW_REVIEW.md`](docs/AUTONOMOUS_RAW_REVIEW.md).

---

## 2. Data contract

Public ViFinQA hiện có:

- `1,012` câu hỏi;
- OCR financial reports;
- `code_stock.csv`;
- question records chủ yếu gồm `id` và `question`.

Public question file không cung cấp đầy đủ gold retrieval fields như:

```text
answer
relevant_docs
relevant_tables
evidence
pandas_query
```

Do đó repository này phải tự xây **verified retrieval labels** thay vì giả định public data đã có gold.

---

## 3. Các question family đang dùng

Đây là weak/research labels, không phải official gold labels.

| ID | Family | Count |
|---:|---|---:|
| `1–361` | direct lookup | 361 |
| `362–577` | conditional / analytical | 216 |
| `578–655` | temporal change | 78 |
| `656–732` | ratio / derived | 77 |
| `733–812` | cross-entity comparison | 80 |
| `813–1012` | multi-entity / multi-period aggregation | 200 |

---

# 4. Cài đặt local

```bash
cd ~/Documents/AI_guru

python -m venv .venv
source .venv/bin/activate

python -m pip install --upgrade pip
pip install -e .
```

Cài UI review local:

```bash
python -m pip install jupyterlab ipywidgets
```

Pull code mới:

```bash
git pull --ff-only origin main
```

Run tests:

```bash
python -m unittest discover -s tests -v
```

---

# 5. Kaggle: build/index/export

Kaggle chịu phần nặng:

```text
raw reports
→ table assets
→ lexical index
→ dense index
→ hybrid retrieval
→ V3 review bundle
```

Nếu session mới hoàn toàn và chưa có artifacts, dùng notebook/script build-from-scratch.

Nếu artifacts đã tồn tại, export V3 bằng:

```python
%cd /kaggle/working/AI_guru

%run kaggle/export_review_bundle_v3.py \
    --top-k 20 \
    --max-review-candidates 40 \
    --neighbor-radius 1 \
    --force
```

Output:

```text
/kaggle/working/vifinqa_review_bundle_v3.tar.gz
/kaggle/working/vifinqa_review_bundle_v3.tar.gz.sha256
/kaggle/working/vifinqa_review_handoff_v3.json
```

Tải cả 3 file về local.

Xem thêm:

- [`docs/KAGGLE_TO_LOCAL_REVIEW.md`](docs/KAGGLE_TO_LOCAL_REVIEW.md)
- [`docs/REVIEW_FIX_V3.md`](docs/REVIEW_FIX_V3.md)

---

# 6. Local: chuẩn bị bundle

Ví dụ:

```bash
mkdir -p ~/ViFinQA_review/run_002

tar -xzf ~/Downloads/vifinqa_review_bundle_v3.tar.gz \
    -C ~/ViFinQA_review/run_002
```

Thư mục sau extract phải có:

```text
manifest.json
review_items.jsonl
tables.jsonl
errors.jsonl
SHA256SUMS
```

Verify archive trước khi dùng:

```bash
cd ~/Downloads
sha256sum -c vifinqa_review_bundle_v3.tar.gz.sha256
```

### 6.1 Khôi phục cấu trúc bảng cho local review

Trước khi xác minh số liệu bằng widget, dựng **Bảng nguồn đã tái dựng (V2)**
từ raw HTML local:

```bash
python local/run_local_review_stage.py repair-tables \
    --bundle-dir ~/ViFinQA_review/run_002 \
    --repair-force
```

Lệnh tạo `tables_structured_v2.jsonl` và manifest ngay trong bundle local.
Archive V3, Kaggle corpus và dense/lexical index không bị sửa. Widget tự nhận
sidecar này; `--repair-force` chỉ thay sidecar cũ nếu đã có, không rebuild
index. Xem chi tiết tại [`docs/TABLE_STRUCTURE_V2.md`](docs/TABLE_STRUCTURE_V2.md).

Tên hiển thị trong UI để tránh nhầm version:

| File kỹ thuật | Tên đọc trong UI | Dùng để làm gì |
| --- | --- | --- |
| `tables_structured_v2.jsonl` | **Bảng nguồn đã tái dựng (V2)** | grid raw HTML, ô trống/span và provenance cell |
| `tables_evidence_context_v3.jsonl` | **Ngữ cảnh evidence chuẩn hóa (V3)** | header cha–con, kỳ, đơn vị, chức năng bảng |

V2 là bằng chứng gốc đã tái dựng; V3 chỉ là metadata suy dẫn từ V2. Không
phiên bản nào tự sửa số OCR hoặc thay evidence cell.

---

# 7. Diagnostic trước khi review

Diagnostic không tạo gold label.

```bash
cd ~/Documents/AI_guru

python local/run_local_review_stage.py diagnose \
    --bundle-archive ~/Downloads/vifinqa_review_bundle_v3.tar.gz \
    --diagnostic-output data/diagnostics/run_002 \
    --audit-size 12
```

Output:

```text
data/diagnostics/run_002/
├── diagnostic_summary.json
├── review_bundle_summary.csv
├── review_bundle_diagnostics.jsonl
└── manual_audit_queue.jsonl
```

Các lỗi chính:

```text
ADJACENT_CONTEXT_HIT
RETRIEVAL_RISK
EVIDENCE_RISK
PLANNER_RISK
AMBIGUOUS_TOPK
COMPLEX_FAMILY_REVIEW
```

Không coi `NO_CANDIDATE_IN_TOPK` là `NO_GOLD_IN_CORPUS`.

---

# 8. Machine review baseline

Sau khi diagnostic chấp nhận được:

```bash
cd ~/Documents/AI_guru

python local/run_local_review_stage.py baseline \
    --bundle-dir ~/ViFinQA_review/run_002 \
    --seed-size 12
```

Output:

```text
data/labels/machine_reviews_60.jsonl
data/labels/human_seed_queue_12.jsonl
```

Với bundle schema V3, runner tự dùng reviewer mới:

```text
scripts/auto_review_bundle_v31.py
```

Reviewer này có grounding guard để recovered adjacent table không được thắng chỉ vì context leak.

---

# 9. Collaborative review: Codex đề xuất, human xác nhận

`local/review_bundle_widget.py` dùng `ipywidgets`, vì vậy UI phải chạy trong **Jupyter Notebook/JupyterLab**.

`%run` là **IPython/Jupyter magic command**, không phải Bash command.

Do đó lệnh này sai nếu gõ trực tiếp trong terminal:

```bash
%run local/review_bundle_widget.py ...
```

Bash sẽ báo:

```text
bash: fg: %run: no such job
```

## 9.1 Terminal: chỉ launch Jupyter

```bash
cd ~/Documents/AI_guru
source .venv/bin/activate

python -m pip install jupyterlab ipywidgets
jupyter lab
```

Nếu không muốn JupyterLab:

```bash
jupyter notebook
```

## 9.2 Tạo ledger và human check queue

Codex review không được ghi thành `human_verified`. Mỗi recommendation phải giữ candidate UID, rank, exact source rows, completeness, confidence và reason codes.

```bash
python local/run_local_review_stage.py collaborate \
    --bundle-dir ~/ViFinQA_review/run_002 \
    --assistant-labels data/labels/codex_assisted_reviews.jsonl \
    --human-check-size 6
```

Output:

```text
data/labels/review_ledger_60.jsonl
data/labels/human_check_queue.jsonl
```

Ledger luôn giữ đủ 60 câu; queue chỉ chứa spot-check của vòng hiện tại.

## 9.3 Trong một Jupyter code cell: chạy widget

```python
%cd ~/Documents/AI_guru

%run local/review_bundle_widget.py \
    --bundle-dir ~/ViFinQA_review/run_002 \
    --machine-reviews data/labels/machine_reviews_60.jsonl \
    --assistant-reviews data/labels/codex_assisted_reviews.jsonl \
    --queue data/labels/human_check_queue.jsonl \
    --output data/labels/retriever_verified_60.jsonl
```

Bạn có thể dừng sau vài câu. Output được ghi atomically; Codex đọc các xác nhận/bất đồng, review lại phần còn lại và tạo queue vòng sau.

UI ưu tiên hiển thị:

```text
Question
Machine/Codex recommendation đã rút gọn
Chức năng bảng và phần kế toán
Chức năng sử dụng nhanh + tiêu đề mục nguồn gần nhất
Mức phù hợp hoặc cảnh báo mismatch
Preview tối đa 4 exact rows; full grid chỉ mở khi cần
Formula + operand coverage cho câu hệ số/derived
One-line summary grounded
```

Nút `Accept Codex` là thao tác xác nhận của human. Chỉ sau thao tác này record mới thành `human_verified`; recommendation Codex ban đầu vẫn được giữ trong ledger để audit.

Với evidence set `partial`, nút đổi thành `Confirm partial` và lưu `human_verified_partial`. Trạng thái này xác nhận các bảng đã chọn là relevant nhưng không biến toàn câu thành training gold.

Khi grid V2 đã xác minh, calibrator có thể dùng các bảng được chọn từ partial
review như **positive-only** candidate evidence; các candidate không được chọn
vẫn là unknown, tuyệt đối không bị tạo thành negative label. Partial vẫn không
được export vào final retrieval training labels.

Với câu ratio/temporal/derived, `Save EvidenceSet` lưu riêng từng operand với
candidate UID, rank, exact source rows và column labels. Multi-table evidence
được phép và thường là bắt buộc. Nếu công thức thuộc loại `ambiguous` hoặc
`review_required`, checkbox xác nhận công thức của human là gate độc lập;
không có xác nhận thì không nâng thành complete.

Nếu chưa có grid V2 đã xác minh từ raw HTML, quick numeric view và
`Accept machine` bị khóa; lựa chọn positive mới chỉ có thể lưu partial.

## 9.4 Autonomous raw-source review (không cần human reviewer)

Khi không có người review, không dùng `Accept Codex`, không nâng machine label
thành `human_verified`, và không train từ `machine_provisional`. Thay vào đó:

```bash
python local/run_local_review_stage.py preprocess \
    --bundle-dir ~/ViFinQA_review/run_002

python local/run_local_review_stage.py autonomous \
    --bundle-dir ~/ViFinQA_review/run_002
```

`preprocess` tạo `tables_evidence_context_v3.jsonl` cùng manifest riêng từ V2
raw-HTML grid. Context V3 giữ nguyên grid/numbers và khôi phục path tiêu đề
cha–con theo span provenance. Khi bảng **không có** header HTML, nó chỉ có thể
dùng tối đa ba hàng `td` đứng trước data làm header nếu từng cột số có text
nguồn, provenance hợp lệ và có cue kỳ/đơn vị/tham chiếu; heading nhóm không
được gán lại cho cột số. Nếu thiếu điều kiện, bảng bị quarantine thay vì đoán.
Sidecar cũng tách cell chỉ *trông* giống số khỏi cell parse được đúng một số;
OCR ghép nhiều nhóm số bị quarantine, không bị tách hay suy diễn.
`autonomous` cho retrieval/semantic/evidence/metadata/challenger/critic views
review chéo. Chỉ direct lookup qua toàn bộ source gate **và** có raw value-row
metric trùng exact planned metric mới là `machine_calibrated` silver. Ngoại lệ
duy nhất phải có audit source riêng: row match sau khi bỏ ticker/mốc thời gian/
`Số dư` ở query, hoặc EvidenceSet gồm **parent heading nguồn + row exact +
period cell exact** của cùng table. Phần còn lại vẫn là `machine_provisional`
hoặc `needs_human` và bị loại khỏi train.

`tables_evidence_context_v1.jsonl` và `v2` chỉ được giữ để audit lịch sử;
không bị ghi đè. Các stage mới mặc định dùng V3 và từ chối manifest không có
policy bind đúng một số raw-source cho mỗi cell.

Với cụm từ giống phép tính nhưng thực tế là một **dòng đã được báo cáo sẵn**
(`Tổng cộng tài sản`, `Tỷ lệ sở hữu`, `Lỗ chênh lệch tỷ giá`), không sửa
`review_items.jsonl` gốc. Tạo sidecar hash-bound rồi dùng nó khi autonomous
review:

```bash
python scripts/build_question_plan_overrides.py \
  --bundle-dir ~/ViFinQA_review/run_002 \
  --output ~/ViFinQA_review/run_002/question_plan_overrides_v1.jsonl

python local/run_local_review_stage.py autonomous \
  --bundle-dir ~/ViFinQA_review/run_002 \
  --question-plan-overrides ~/ViFinQA_review/run_002/question_plan_overrides_v1.jsonl
```

Chỉ dạng một chủ thể/một kỳ, không có so sánh/tổng hợp/range, mới được
replan. Override mang hash câu hỏi + plan gốc và được ghi lại trong review;
nó không thay đổi raw table, không giả mạo `human_verified` và vẫn phải qua
exact-row/exact-column gate.

Khi plan direct thiếu ticker, cùng script chỉ có thể thêm ticker nếu câu hỏi
chứa **đúng một** mã viết hoa, tách token, nằm trong metadata của chính bundle.
Không dùng fuzzy company-name match; không có hoặc có nhiều mã thì plan giữ
nguyên. Rule này chỉ thu hẹp tập bảng cần xét, không tự tạo evidence/label.

Với câu ratio/temporal có controlled formula, có thể tạo `EvidenceSet` exact
row riêng để biết operand nào đã đủ hoặc còn thiếu; lệnh này không tính answer
và không thay đổi label:

```bash
python scripts/build_formula_evidence_sets.py \
  --bundle-dir ~/ViFinQA_review/run_002 \
  --evidence-context ~/ViFinQA_review/run_002/tables_evidence_context_v3.jsonl \
  --discover-source-operands \
  --output ~/ViFinQA_review/run_002/formula_evidence_sets_context_v3_discovered.jsonl
```

Discovery này chỉ join UID với metadata immutable `tables.jsonl`: ticker trong
question plan phải được resolve, report year phải là năm operand hoặc năm báo
cáo kế tiếp. Sau đó operand vẫn phải qua exact raw-row/cell binding; discovery
không tạo answer hay label nào.

Direct lookup có một nhánh recall tương tự nhưng chặt hơn: khi plan chỉ có một
entity, năm rõ ràng và một nhãn metric literal khớp đúng một phrase của raw
row, exporter thêm bảng nguồn với `direct_metadata_support_v1`. Nó cũng không
vào UI candidate. Sau repair V2, `build_direct_evidence_sets.py` phải xác nhận
lại raw metric identity, source header và đúng period cell; phrase support chỉ
tăng recall, tuyệt đối không tự tạo machine-silver.

## Chuẩn hóa report segment

Để tách OCR/HTML boilerplate khỏi ngữ cảnh đọc nhanh, build sidecar từ bundle
đã có V2/V3 hợp lệ:

```bash
python scripts/build_report_segments.py \
  --bundle-dir ~/ViFinQA_review/run_full_metadata_support_v1 \
  --evidence-context ~/ViFinQA_review/run_full_metadata_support_v1/tables_evidence_context_v3.jsonl \
  --output ~/ViFinQA_review/run_full_metadata_support_v1/report_segments_v1.jsonl
```

Mỗi segment giữ UID, document, raw-context SHA, heading cùng trang, chức năng
bảng, section, kỳ và đơn vị đã quan sát. Heading của báo cáo bắt đầu đúng tại
tên báo cáo (không kéo tên công ty/boilerplate trước đó). Với note dài, UI chỉ
hiện prefix nguồn trước một câu diễn giải đã nhận diện; nếu không xác định
được ranh giới tiêu đề, UI hạ về dòng tóm tắt **chức năng · phần (nếu không
trùng) · kỳ · đơn vị**. Không serialise row, công thức hay số liệu. Nó bỏ table
của trang trước/HTML noise. Manifest ghi policy
`source_heading_metadata_only_no_numeric_inference_v2` để phân biệt lần chuẩn
hóa đọc nhanh, và có
`evidence_eligible=false`, `training_eligible=false`. Widget tự dùng sidecar
khi có mặt, hoặc có thể chỉ rõ `--report-segments ...`. Raw grid V2 vẫn là bằng
chứng duy nhất.

`auto_review_bundle_v4.py` cũng tự attach sidecar này khi nó có trong bundle
(hoặc nhận `--report-segments ...`). Chỉ `unit_labels` đã hash-bound mới có thể
giúp đọc đơn vị; title/descriptor không tham gia bind row, period, điểm semantic
hay quyết định `machine_calibrated`.

### Alias thực thể từ title zone nguồn

`report_entity_aliases_v1.jsonl` là một sidecar độc lập để xử lý câu công thức
có nhắc tên doanh nghiệp nhưng question plan chưa có ticker. Nó chỉ nhận tên
thực thể xuất hiện **ở đầu trang nguồn**, trước marker `Báo cáo`/`Thuyết minh`/
`Mẫu số`; tên xuất hiện trong thân bảng (khách hàng, nhà cung cấp, bên liên
quan) bị bỏ. Legal-form như `CTCP` và `Công ty Cổ phần` được chuẩn hoá về cùng
cách viết, nhưng tên riêng phải khớp nguyên chuỗi token và tất cả alias khớp
phải dẫn tới đúng một ticker. Scope không bao giờ được suy ra.

```bash
python scripts/build_report_entity_aliases.py \
  --bundle-dir ~/ViFinQA_review/run_full_metadata_support_v1 \
  --output ~/ViFinQA_review/run_full_metadata_support_v1/report_entity_aliases_v1.jsonl

python scripts/build_formula_evidence_sets.py \
  --bundle-dir ~/ViFinQA_review/run_full_metadata_support_v1 \
  --evidence-context ~/ViFinQA_review/run_full_metadata_support_v1/tables_evidence_context_v3.jsonl \
  --report-entity-aliases ~/ViFinQA_review/run_full_metadata_support_v1/report_entity_aliases_v1.jsonl \
  --discover-source-operands \
  --output ~/ViFinQA_review/run_full_metadata_support_v1/formula_evidence_sets_context_v3_entity_titles_v1.jsonl
```

Formula EvidenceSet V6 khóa SHA của sidecar alias và manifest của nó. Alias
chỉ mở candidate discovery; với câu hỏi có ticker viết hoa rõ ràng (ví dụ
`PC1`), V6 chỉ có thể bổ sung ticker khi token đó xuất hiện đúng một lần trong
metadata nguồn của bundle. Mỗi operand vẫn cần raw V2 row exact, canonical
header và một cell nguồn parse được. Với bảng cân đối có header `Số cuối năm`,
period chỉ được bind khi document metadata có đúng năm operand và có đúng một
cột numeric nguồn mang header đó. Alias/ticker/segment không phải evidence,
không tạo answer, không suy ra scope, không đổi `machine_provisional` thành
`machine_calibrated` và không được dùng để train.

## Direct EvidenceSet theo ngữ cảnh bảng

`build_direct_evidence_sets.py` giữ exact raw row là đường chính. Khi metric
trong query dính ticker/mốc cuối kỳ hoặc tiền tố `Số dư`, nó chỉ tạo biến thể
context-free nếu row nguồn khớp exact token sau khi bỏ đúng các thành phần đó;
`tổng`, `ngắn hạn`, `nguyên giá` và các modifier kế toán không bị bỏ.
Mốc thời gian phải là cụm đầy đủ (`cuối năm 2023`, `31 tháng 12 năm`, ...);
từ `năm` trong một metric thời hạn như `kỳ hạn dưới 1 năm` không bao giờ bị
coi là context để xoá.

Với table note có cấu trúc `heading cha → heading con → row`, fallback chỉ hợp
lệ khi parent heading và row đều xuất hiện nguyên chuỗi token trong câu hỏi,
parent có accounting token, V2 row/cell bind được và hash raw context trùng
segment. Ví dụ “Cho vay khách hàng → Theo ngành nghề → Thương mại”. Đây là
EvidenceSet nhiều phần, không phải tóm tắt hay suy diễn số.

Manifest của Direct EvidenceSet cũng khóa filename và SHA-256 của
`report_segments_v1.jsonl`; V4 từ chối chạy nếu sidecar hierarchy được tạo từ
một segment khác. Vì vậy tiêu đề dùng để giải thích cấu trúc không thể bị tráo
giữa các bundle hoặc lần chuẩn hoá.

Sau mỗi vòng, audit readiness thay vì train sớm:

```bash
python scripts/analyze_autonomous_reviews.py \
  --machine-reviews ~/ViFinQA_review/evaluation_metadata_support_v1/machine_reviews_1012_hierarchy_v3.jsonl \
  --baseline-machine-reviews ~/ViFinQA_review/evaluation_metadata_support_v1/machine_reviews_1012_metric_context_v2.jsonl \
  --output ~/ViFinQA_review/evaluation_metadata_support_v1/autonomous_readiness_hierarchy_v3.json \
  --min-machine-pairs 200
```

Audit từ chối mọi `machine_calibrated` thiếu UID, V2 validation, exact identity
hoặc `cell_bound`, và báo rõ số pair còn thiếu cho gate training.

Từ bundle export mới, với operand controlled đã có đủ `entity`, năm và
`allowed_table_functions`, exporter còn đưa **raw statement table** tương ứng
vào `tables.jsonl` qua metadata SQLite có kiểm soát. Các bảng này có
`bundle_inclusion.formula_metadata_support`, được gắn
`candidate_source=formula_metadata_support_v1` khi Formula EvidenceSet dùng
chúng, và **không** được thêm vào `review_items.candidates`; do đó UI review
không bị phình bởi các bảng phụ. Nó chỉ đọc asset/index đã hợp lệ, không rebuild
corpus, lexical/dense index, không sửa retrieval rank và không tạo answer,
review label hay training pair. Operand không có table-function allow-list bị
bỏ qua có chủ ý thay vì export toàn bộ note/OCR của doanh nghiệp.

Khi audit cho thấy statement tồn tại trong raw report nhưng bị thiếu hẳn khỏi
bundle, chạy source completion ở chế độ **shadow**:

```bash
python local/run_local_review_stage.py source-completion \
  --bundle-dir ~/ViFinQA_review/run_002
```

Luồng này tạo `formula_source_completion_audit_v1.json`, sau đó chỉ đưa các
raw table đã revalidate lại report SHA, table SHA, deterministic UID, header
V3, period cell và provenance vào `source_completion_*_v1` sidecar. Nó tạo
Formula EvidenceSet shadow riêng, không sửa `tables.jsonl`, corpus/index,
review status, answer, execution ledger hoặc training label. Một raw table
bổ sung vẫn phải qua common-scope/unique-binding gate; không được tự chọn
`consolidated` hay `separate`.

Nếu cần chạy audit bổ sung (ví dụ scope-gap) sau snapshot đầu, tạo một output
mới có kế thừa thay vì thay thế snapshot cũ:

```bash
python scripts/build_source_completion_sidecar.py \
  --bundle-dir ~/ViFinQA_review/run_002 \
  --source-audit ~/ViFinQA_review/run_002/formula_source_completion_scope_audit.json \
  --base-tables ~/ViFinQA_review/run_002/source_completion_tables_v1.jsonl \
  --base-contexts ~/ViFinQA_review/run_002/source_completion_context_v1.jsonl \
  --tables-output ~/ViFinQA_review/run_002/source_completion_combined_v1.jsonl \
  --contexts-output ~/ViFinQA_review/run_002/source_completion_combined_context_v1.jsonl
```

UID trùng chỉ được gộp nếu raw grid, source provenance và V3 context y hệt;
ngược lại command fail-closed. Base và output đều shadow-only.

Với công thức nhiều entity theo stage, `audit_formula_source_coverage.py` có
scope-gap probe để chỉ audit raw statement bị thiếu ở scope còn lại, dù operand
đã có match ở một scope. Nó chỉ là đọc/audit (`--include-scope-gap-operands`,
tuỳ chọn `--question-id`) và không tự chọn consolidated/separate hay tạo label.

`complete` chỉ có nghĩa tất cả operand của formula đã có exact raw row/cell và
không mơ hồ: một entity cần một binding duy nhất trong cùng scope; nhiều entity
cần một scope không rỗng chung cho mọi entity và một binding duy nhất cho từng
operand trong scope ấy. Không có/mơ hồ scope chung vẫn là `partial`. Câu có lọc
nhóm, xếp hạng hoặc công thức ambiguous vẫn giữ `partial` cho đến khi có
executor theo stage. Mỗi cell còn
phải parse thành đúng **một** số nguồn tin cậy; cell OCR ghép hai nhóm số bị
giữ `partial`, không thể tạo answer.

Execution ledger có allow-list tách biệt, rất hẹp. `percentage_change` đã
`defined`, confidence ≥0,95, đủ đúng hai operand `x_old`/`x_new`, cùng source
unit và qua lần revalidate thứ hai với raw V2 row/cell/header/provenance mới có
thể tạo execution record **eligible cho submission**. Một path shadow-only
khác xử lý riêng câu một entity liệt kê các năm và hỏi năm có `lưu chuyển tiền
thuần từ hoạt động kinh doanh` cao nhất: mỗi năm phải có exact raw V2 cell
cùng scope/unit, raw report đúng năm và cash-flow function; tie hoặc thiếu
binding fail-closed. Path này luôn ghi `submission_eligible=false`, nên
compiler từ chối nó; nó chỉ dùng để audit/đánh giá, không đổi review label hay
tạo machine-silver training pair. Ratio, lọc/xếp hạng và multi-stage vẫn
fail-closed.

Kiểm tra lại mọi operand đã lưu trước khi nghiên cứu executor tiếp theo:

```bash
python scripts/analyze_formula_evidence.py \
  --bundle-dir ~/ViFinQA_review/run_002 \
  --formula-evidence ~/ViFinQA_review/run_002/formula_evidence_sets_context_v3_discovered.jsonl \
  --output ~/ViFinQA_review/run_002/formula_evidence_sets_context_v3_discovered.audit.json
```

Semantic rerank V2 là sidecar audit riêng: model chỉ thấy question, exact V2
row đã đối chiếu với `evidence_window`, header/function/unit gọn; nó lưu
`source_input` + hash và audit render lại từ raw source. Điểm semantic không
tự nâng bất kỳ provenance nào; các gate exact-row/exact-column/unit/critic vẫn
bắt buộc.

Sau khi tích luỹ đủ 200 machine-silver pairs, chạy:

```bash
python local/run_local_review_stage.py autotrain \
    --bundle-dir ~/ViFinQA_review/run_002
```

Nếu chưa đủ, lệnh trả `deferred` và không ghi model. Không có bước nào rebuild
corpus, lexical index, dense embeddings hay FAISS. Chi tiết safety contract tại
[`docs/AUTONOMOUS_RAW_REVIEW.md`](docs/AUTONOMOUS_RAW_REVIEW.md).

Câu lọc/xếp hạng nhiều giai đoạn không thể bị hiểu nhầm thành một công thức đơn
lẻ theo keyword đầu tiên. Q369 có plan ba stage và EvidenceSet riêng theo
entity; các dạng chưa có controlled rule vẫn được gắn
`multi_stage_selection_unresolved`.

Positive labels đã tạo bằng UI cũ vẫn nằm nguyên trong audit file nhưng được
đưa lại vào review queue và bị loại khỏi calibration/training export cho đến
khi human xác minh lại trên grid V2.

Ở lần baseline/rerun kế tiếp, V3.1 reviewer cũng chỉ vote trên candidate có
`VALUE/ANCHOR` khớp đúng exact V2 row. Machine label không có gate này không
được final training export.

---

# 10. Calibration

Sau khi review đủ seed:

```bash
python local/run_local_review_stage.py calibrate \
    --bundle-dir ~/ViFinQA_review/run_002
```

Pipeline:

```text
human seed
→ candidate features
→ LogisticRegression calibrator
→ rerun source-aware reviewers
→ calibrated probabilities
```

Output:

```text
artifacts/review_calibrator.joblib
data/labels/machine_reviews_60_calibrated.jsonl
data/labels/needs_human_after_calibration.jsonl
```

Resolution queue phải bao gồm cả `needs_human`, `retrieval_failure`, `machine_provisional` chưa đủ điều kiện training và complex evidence set còn thiếu operand. Provisional vẫn được giữ trong ledger ngay cả khi không được export để train.

---

# 11. Final labels

```bash
python local/run_local_review_stage.py final \
    --bundle-dir ~/ViFinQA_review/run_002
```

Output:

```text
data/labels/review_ledger_60.jsonl
data/labels/retriever_labels_v2.jsonl
```

`review_ledger_60.jsonl` là audit/provenance đầy đủ. `retriever_labels_v2.jsonl` chỉ là training subset đã qua eligibility gate.

Provenance được giữ riêng:

```text
human_verified
machine_calibrated
machine_high_confidence
machine_provisional
needs_human
retrieval_failure
```

Machine label không được giả mạo thành human gold.

---

## 11.1 Pilot reranker sau final labels (shadow-only)

Sau khi có final labels, có thể chạy pilot nhỏ mà không rebuild corpus hay
dense/FAISS index:

```bash
python local/run_local_review_stage.py pilot \
    --bundle-dir ~/ViFinQA_review/run_002
```

Pilot chỉ re-rank những table đã nằm trong `review_items.jsonl` Top-K. Complete
`human_verified` labels tạo positive và negative; `machine_calibrated` chỉ là
positive pseudo-label có weight, không suy diễn candidate không được chọn là
negative. Output là artifact, metadata có grouped OOF MRR/recall@K và shadow
ranking để audit; script không tự động thay đổi retriever/index. Xem contract
đầy đủ tại [`docs/PILOT_CANDIDATE_RERANKER.md`](docs/PILOT_CANDIDATE_RERANKER.md).

---

# 12. V3 evidence projection

V3 không chỉ lấy một row có text gần query.

Nó cố tạo evidence dạng:

```text
TABLE: 10. Bất động sản đầu tư
||
COLUMNS: Nguyên giá | Hao mòn lũy kế | Giá trị còn lại
||
VALUE: Số cuối năm | 417.860.288.970 | 39.303.347.137 | 378.556.941.833
```

Các field chính:

```text
effective_metric
context_heading
table_topic
direct_evidence
anchor_row_index
value_row_index
period_intent
period_match
evidence_features
```

`direct_evidence` phải được tạo từ source table/context, không phải LLM tưởng tượng.

---

# 13. Source-aware multi-agent review

Hiện tại các reviewer là deterministic Python reviewers + optional learned calibrator, không phải nhiều ChatGPT độc lập.

Các view chính:

```text
lexical_agent
dense_agent
metadata_agent
evidence_agent
challenger_agent
grounding_agent
calibrator_agent  # chỉ có sau human calibration
verifier
```

Consensus chỉ mạnh khi candidate vừa có retrieval support vừa có grounded evidence.

Recovered adjacent candidate phải qua strict grounding guard trước khi được vote.

---

# 14. Repository structure hiện tại

```text
src/finance_query/
├── config.py
├── corpus.py
├── table_structure.py
├── questions.py
├── retrieval.py
├── binding.py
├── execution.py
├── pipeline.py
├── schemas.py
└── cli.py

kaggle/
├── export_review_bundle.py
├── export_review_bundle_v3.py
└── run_kaggle_retrieval_export.py

scripts/
├── build_review_bundle.py
├── build_review_bundle_v3.py
├── auto_review_bundle.py
├── auto_review_bundle_v3.py
├── auto_review_bundle_v31.py
├── repair_review_bundle_tables.py
├── train_review_calibrator.py
├── export_review_labels.py
├── train_question_router.py
├── train_dense_retriever.py
└── train_reranker.py

local/
├── diagnose_review_bundle.py
├── review_bundle_widget.py
└── run_local_review_stage.py

docs/
├── KAGGLE_TO_LOCAL_REVIEW.md
├── REVIEW_PIPELINE_V2.md
├── REVIEW_FIX_V3.md
├── LOCAL_DIAGNOSTIC.md
├── V3_BUNDLE_VALIDATION_AND_NEXT.md
├── TABLE_STRUCTURE_V2.md
├── FORMULA_EVIDENCE_SETS.md
├── CONTEXT_FORMULA_REVIEW_IMPLEMENTATION.md
└── Q369_GROUNDED_REVIEW.md
```

---

# 15. Current development priority

```text
1. reliable table retrieval
2. source-grounded evidence projection
3. verified retrieval labels
4. calibrate multi-agent reviewer
5. retriever/reranker evaluation
6. improve table/row/cell binding
7. operand-level reasoning
8. deterministic execution
9. final answer/submission generation
```

Không tối ưu answer generation trước khi retrieval/evidence labels đủ đáng tin.
