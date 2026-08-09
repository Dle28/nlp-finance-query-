# Context + formula review implementation note

Ngày kiểm tra: 2026-08-08.

## Đã triển khai

### Checkpoint context V3 (autonomous path hiện hành)

- Context V3 vẫn dùng immutable raw-HTML grid V2, nhưng thêm recovery rất hẹp
  cho header bị OCR xuất thành hàng `td`: chỉ tối đa ba hàng trước data, từng
  cột số phải có source text/provenance và cue kỳ, đơn vị hoặc tham chiếu.
  Heading nhóm và ô số trần bị loại. Vì vậy V3 không dịch cột, không điền OCR
  thiếu và không suy diễn content bảng.
- Machine-silver phải có `raw_metric_identity.exact=true` ngoài exact row/cell
  và period binding. Chuẩn hóa chỉ cho mã dòng cấu trúc, reference thuyết minh
  độc lập và `TNDN`; các nhãn gần nghĩa vẫn `machine_provisional`.
- Artifact mới mang suffix `_context_v3` và protocol
  `raw_v2_canonical_context_v3`. V1/V2 tiếp tục được giữ cho audit lịch sử,
  không được đưa vào autonomous training.

- Dựng grid V2 từ raw HTML, giữ ô trống, mở rộng `rowspan`/`colspan` và giữ
  cell provenance.
- Thêm context schema V2: exact source title, nearest numbered topic, period,
  unit, table function, accounting section và table purpose.
- Tách độ chắc chắn của function thành `semantic`, `broad` và `generic` để
  không biến fallback thành kết luận nghiệp vụ.
- Thêm `specificity=structural`: nhận diện báo cáo chính bằng tập dòng chuẩn
  thay vì lệ thuộc hoàn toàn vào tiêu đề OCR. Với bảng lưu chuyển tiền tệ,
  function luôn thắng các từ giao dịch như “tài sản cố định”, nên không còn bị
  gắn nhầm section `asset`.
- Thu gọn UI: candidate nằm trong accordion; quick view tối đa bốn dòng liên
  quan; full grid, title dài và trace phân loại mặc định đóng.
- Ẩn numeric quick view và khóa machine acceptance khi grid chưa được xác minh
  từ raw HTML, candidate sai accounting side hoặc không map vào formula operand.
- Thêm controlled formula rules và multi-table `EvidenceSet`; mỗi operand giữ
  candidate UID/rank, exact row cells và column labels.
- Câu lọc/xếp hạng nhiều giai đoạn được route thành unresolved thay vì bị rút
  sai thành keyword công thức đầu tiên.
- Q369 có stage plan riêng theo entity: 12 operand Quick Ratio, 16 operand GPM
  và 8 operand Interest Coverage. UI gom 36 chi tiết vào phần thu gọn và chỉ
  hiện ba stage card ở góc nhìn nhanh.
- Binder khóa từng operand theo ticker, kỳ và loại báo cáo đáng tin. EBIT không
  được bind giả vào dòng lợi nhuận trước thuế; UI biểu diễn phép dẫn xuất
  `EBIT = LNTT + |chi phí lãi vay|` với hai exact-row references.
- Fallback `context heading + value row` chỉ chạy khi metric xuất hiện nguyên
  cụm trong nearest topic/heading. Các token rời rạc như “chi tiết”, “lãi suất”,
  “cho vay” không còn được ghép sai thành “chi phí lãi vay”.
- Review ledger đối chiếu lại formula coverage với sidecar V2 và dừng khi row
  hoặc column labels khác source.
- V3.1 machine reviewer bind lại `VALUE/ANCHOR` vào exact `best_row_index` của
  sidecar V2; machine label thiếu validation này không được final export.
- Widget, machine reviewer và ledger đều kiểm tra sidecar checksum, bundle
  `tables.jsonl` checksum, row count và `error_count=0` trước khi sử dụng.
- Giữ nguyên provenance: Codex/machine không tự thành `human_verified`; partial
  evidence không được export thành training gold.

## Validation trên bundle V3

Kiểm tra được chạy trên một thư mục tạm extract từ
`vifinqa_review_bundle_v3.tar.gz`; bundle/index gốc không bị sửa.

```text
bundle tables repaired: 1211/1211
source/UID errors:       0
semantic functions:     660
broad functions:        345
structural functions:    32
generic functions:      174
formula-aware questions: 19/60
candidate coverage complete before human formula confirmation: 12/19
specific staged plan:    1/60 (Q369)
multi-stage unresolved:  4/60
exact machine rows:      1227/1229 candidates
exact-row mismatches:    2 (fail closed)
unit tests:              59 passed
```

Sau canary, cùng parser đã được áp dụng cho bundle review local thật tại
`/home/dungle/ViFinQA_review/run_002`: 1.211/1.211 tables, 0 errors, sidecar
SHA-256 `eb89b633309c496b84584f2841e7bba4a3807167a160279a9074d77aaf777b0c`.
Widget load đủ 1.211 cấu trúc và ledger smoke-test giữ đủ 60 records. Archive
V3, label files và các index không bị ghi lại.

Bốn positive labels được tạo bằng UI cũ vẫn được giữ nguyên để audit nhưng
được gắn `needs_review_refresh`; chúng không còn được tính là training-eligible
cho đến khi người review lưu lại trên grid V2. Seed widget vì vậy hiển thị
progress hiện hành là 0/12 thay vì coi label legacy là đã hoàn tất.

“Candidate coverage complete” chỉ nói bundle hiện có đủ V2 row candidates cho
formula slots. Nó chưa phải `human_verified` và chưa phải answer correctness.

Canary Q960 đã được kiểm tra lại: ranks 1–5 đều là
`cash_flow_statement/cash_flow`, không còn biểu tượng loại sai. Các exact rows
cho 2017, 2019, 2020, 2021 và 2023 giữ nguyên; giá trị lớn nhất trong năm được
hỏi là 2023.

Q369 cố ý vẫn partial trong retrieval UI: coverage lần lượt là 0/12, 1/16 và
2/8 theo ba stage vì các primary-statement tables cần thiết không nằm trong
Top-20. Answer audit riêng từ raw source được ghi tại
[`Q369_GROUNDED_REVIEW.md`](Q369_GROUNDED_REVIEW.md); audit đó không nâng
provenance retrieval thành complete.

Codex-assisted review Q369 đã được cập nhật thành round 2, supersede round 1,
đề xuất ranks 5 và 8 với exact V2 rows nhưng vẫn mang `needs_human/partial`.
Ledger 60 câu đã dựng lại; label human cũ của Q369 được giữ nguyên để audit,
gắn `needs_review_refresh=true`, `training_eligible=false` và đưa vào
`human_check_queue.jsonl`.

Sau khi hoàn tất 12 seed review, calibrator dùng 6 partial review đã V2-verify
theo chế độ **positive-only**: exact selected candidates là positive, mọi
candidate không được chọn vẫn là unknown chứ không bị tạo negative label.
Partial không đi vào final retrieval training export. Rerun calibrator dùng 12
question groups, 128 candidate samples (26 positive, 102 negative), grouped
ROC-AUC 0,8303; xuất 4 `machine_calibrated`, 53 `machine_provisional` và 3
`needs_human` (Q781, Q791, Q862). Grounding guard loại 28 adjacent candidates;
1.227/1.229 candidate còn lại có exact V2 row validation.

Sau final review, ledger giữ đủ 60 question: 6 `human_verified`, 6
`human_verified_partial`, 1 `verified_no_candidate`, 3 `machine_calibrated`
và 44 `machine_provisional`. Final training export có 9 labels: 6 human
verified (gồm Q781 sau human confirmation) và 3 machine calibrated. Partial,
no-candidate và provisional vẫn ở ledger để audit nhưng không đi vào tập train.

## Pilot candidate reranker — 2026-08-09

Đã thêm và chạy `scripts/train_pilot_candidate_reranker.py` qua:

```bash
python local/run_local_review_stage.py pilot \
    --bundle-dir ~/ViFinQA_review/run_002
```

Pilot chỉ học re-rank candidate đã có trong `review_items.jsonl` Top-K; không
train embedding, không retrieve thêm bảng và không ghi corpus/index/bundle hay
labels. Negative sample chỉ đến từ 6 question `human_verified` có V2 complete.
Ba `machine_calibrated` được giữ provenance và chỉ thêm positive pseudo-label
có weight `0.8`; candidate không chọn của chúng vẫn là `unknown`.

```text
training questions:                   9 (6 human, 3 machine pseudo)
candidate rows used for fitting:     124
positive candidates:                  23 (20 human, 3 machine)
human-confirmed negatives:           101
evaluation:                           5-fold GroupKFold by question ID
human-only baseline / OOF MRR:       0.9167 / 0.9167
human-only recall@3:                 0.7825 / 0.6159
human-only recall@5:                 0.8968 / 0.7857
all-label baseline / OOF MRR:        0.9444 / 0.8889
promotion:                            hold
```

Vì chỉ có 6 human groups và OOF recall@3/@5 giảm, artifact không được tích hợp
vào retriever/UI. Output audit-only:

```text
artifacts/pilot_candidate_reranker.joblib
artifacts/pilot_candidate_reranker.joblib.json
artifacts/pilot_candidate_reranker.joblib.shadow.jsonl
```

Metadata giữ input hashes, policy provenance, fold split và metric. Chi tiết
contract nằm tại [`PILOT_CANDIDATE_RERANKER.md`](PILOT_CANDIDATE_RERANKER.md).

## Autonomous raw-source processing — 2026-08-09

Theo hướng không còn human reviewer, đã bổ sung một luồng độc lập với review
widget cũ:

```text
V2 raw-HTML grid
→ canonical header/period/unit sidecar V1
→ quality quarantine
→ V4 retrieval/semantic/evidence/metadata/source/challenger/critic consensus
→ machine-silver export
→ min-pair gate trước dense fine-tune
```

`src/finance_query/evidence_context.py` không sửa OCR hay số. Nó chỉ phục hồi
header cha bị `colspan` che phủ khi V2 `cell_provenance` trỏ đúng source anchor,
ghi path tiêu đề/kỳ/đơn vị theo cột và profile data/header row. V1 sidecar trên
1.211 bảng có 969 `review_ready`, 242 `needs_processing`, 0 `blocked`; phục hồi
899 span-header cells trên 299 bảng. Recovery cho bảng tiếp trang xét 81
candidate nhưng 0 case đồng thời đúng document, adjacent ordinal, width,
function và numeric layout, nên không case nào bị đoán header.

`scripts/auto_review_bundle_v4.py` yêu cầu exact V2 value row và source quality
`review_ready`; câu có đầu/cuối kỳ còn phải bind được một cột raw header duy
nhất. Trên bundle 60 câu, V4 xuất 2 `machine_calibrated` silver, 16
`machine_provisional`, 42 `needs_human`, quarantine 245 candidate. Hai silver
Q13/Q115 giữ `machine_self_review.protocol=raw_v2_canonical_context_v1` trong
artifact lịch sử 60 câu và không phải `human_verified`. Protocol V1 chỉ còn để
audit; pipeline mới yêu cầu `raw_v2_canonical_context_v2` cùng sidecar context
V2 có policy numeric-safe.

`autotrain` kiểm provenance đó, lấy passage trực tiếp từ immutable
`bundle/tables.jsonl` (không dùng lexical DB có UID namespace khác) và chỉ
train dense encoder mới khi có tối thiểu 200 pairs. Run hiện tại trả:

```text
deferred: need 200 machine_silver pairs; only 2 passed provenance validation
```

Do đó dense/FAISS index cũ, corpus, raw rows và labels cũ đều không bị sửa.
Chi tiết luồng và giới hạn nguồn tại
[`AUTONOMOUS_RAW_REVIEW.md`](AUTONOMOUS_RAW_REVIEW.md).

## Không thực hiện

- Không rebuild Kaggle corpus, lexical index, dense embeddings hoặc FAISS.
- Không sửa OCR text/số.
- Không để widget tự suy diễn answer ngoài exact source rows. Q369 chỉ có một
  answer audit tách biệt, dùng exact rows và công thức đã khai báo.
- Không ghi đè baseline `machine_reviews_60.jsonl`; guard mới sẽ được dùng ở
  lần rerun baseline/calibrated tiếp theo.

## Cách áp dụng vào bundle review thật

```bash
python local/run_local_review_stage.py repair-tables \
    --bundle-dir ~/ViFinQA_review/run_002 \
    --repair-force
```

Sau đó chạy widget trong Jupyter như hướng dẫn ở README. `--repair-force` chỉ
thay sidecar local nếu sidecar cũ tồn tại.

## Bước kiến trúc kế tiếp

Ưu tiên tiếp theo là exact-cell/period binding, unit resolver và source
completion có provenance cho bảng đúng nhưng nằm ngoài Top-K. Q369 đã có stage
planner cụ thể; bốn câu còn mang `multi_stage_selection_unresolved` vẫn phải
giữ `needs_human`/partial cho đến khi có rule tương ứng.
