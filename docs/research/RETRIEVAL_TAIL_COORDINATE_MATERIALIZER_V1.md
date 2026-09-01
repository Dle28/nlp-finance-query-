# Retrieval tail coordinate materializer v1

Ngày: 2026-08-31  
Lane: `CANDIDATE_ONLY`  
Mục tiêu: chuyển navigation rows ở rank 11--20 thành coordinate hints để
kiểm tra downstream, không cấp answer authority

## Hypothesis/family

Nếu lexical top-20 tìm thêm source-table candidates ngoài top-10, một resolver
theo period/header có thể biến phần tail thành exact-coordinate hints để
downstream pipeline tự hydrate, semantic-check và Decimal-replay. Rule này áp
dụng cho mọi eligible route/operand/year; `question_id` chỉ là tracking key.

## Contract

Materializer đọc candidate table/row metadata và full structured asset. Nó có
thể dùng numeric cells nội bộ để kiểm tra một cột được chọn là numeric, nhưng
không ghi numeric value, row label, evidence window hay answer ra output. Mọi
hint chỉ là navigation candidate:

```text
navigation tail row
  -> current period/header resolver
  -> (question, operand, table UID, row index, column index)
  -> current builder hydration
  -> source/semantic/replay/policy gates
```

`numeric_values_emitted=false`, `may_authorize_answer=false`,
`submission_eligible=false`, `promotion_allowed=false`.

## Inputs and fingerprints

- builder/resolver:
  `scripts/e2e/build_competition_submission_v1.py`, SHA-256
  `df1fe0f7486c43261f693cf927fa8a1398261be76b0e792d4a734e3460cd361d`;
- questions:
  `artifacts/kaggle_upload/dungle2810_vifinqa_answer_optimized_v2_20260830/runtime/questions.jsonl`;
- candidate artifact:
  `artifacts/research/full_corpus_candidate_retrieval_top20_v1_20260831_r1/`;
- candidate manifest SHA-256:
  `ae596f381925112185e521ffb8da845a3175ba7229015b408468203f1f74789b`;
- full structured asset:
  `artifacts/research/document_corpus_round2_assets_v1_20260829_r1/full_table_assets_v1.jsonl`;
- structured asset SHA-256:
  `617ae044499907cf302519b7ff6fb96fb86ab70ff08e6fdcb0e6a3f8df5251f7`;
- candidate table SHA-256:
  `e60afa8da78bf12ac28f7a46135dfdc5deebe45b852120cdce5e0489f077491d`;
- row-review queue SHA-256:
  `c450f5568719400b616805a927dacaeaf60dbbabdc38f97e776004a9cb0d77a5`.

## Full-population materialization result

- candidate tail table rows: `10.630`;
- tail route groups: `1.064`;
- tail unique table UIDs: `9.900`;
- row-review packets available: `25.111`;
- candidate keys without a row packet: `253`;
- selected UIDs found in structured asset: `9.660/9.660`;
- coordinate hints emitted: `22.577`;
- eligible questions with at least one hint: `599/599`;
- duplicate coordinates rejected: `111`;
- year-column unresolved: `2.423`;
- UID missing from structured asset: `0`.

The 2.423 unresolved columns and 253 missing row packets are fail-closed
rejections. They are not converted into coordinates or answers. The materializer
therefore demonstrates a usable navigation-to-coordinate bridge but not
correctness.

## Required downstream A/B

The next experiment must run the same current builder twice on the complete
1.012-question population:

- control: current builder plus the frozen existing research inputs, without
  tail hints;
- candidate: exactly the same inputs plus
  `coordinate_hints_v1.jsonl`;
- same full asset, source line map, replay, candidate-validity policy and
  route flags;
- separate output directories and implementation fingerprint;
- exact source hydration/replay and ZIP checks for both arms;
- answer/execution score only after an independent scorer or frozen gold set.

The candidate must not replace q82 wholesale, and the hint count, replay count,
or changed answer count cannot be presented as an accuracy delta.

## Mandatory research handoff

```text
Hypothesis/family: generic lexical retrieval tail rank 11--20 converted into current-resolver coordinate hints
Control fingerprint: top-10 navigation artifact plus current builder; downstream answer A/B NOT_EXECUTED_YET
Candidate fingerprint: tail coordinate materializer v1; library=560ea6720709756fdc347e1f6173a14e6b3a63c7b24007eac3a6298156f0e109; hints=e3e897470ca8e62efc0e051cbcaded335f7aa5d13878f30537a03f1554aff7a1
Population and split: complete 1,012-question population; 599 eligible typed routes; full 146,246-table asset
Scorer/gold: NOT_AVAILABLE
ANSWER_ACCURACY: control=NOT_MEASURED candidate=NOT_MEASURED delta=NOT_MEASURED
EXECUTION_ACCURACY: control=NOT_MEASURED candidate=NOT_MEASURED delta=NOT_MEASURED
Improved / regressed / unchanged / unresolved: coordinate coverage only; 22,577 hints emitted; 2,423 columns unresolved; 253 row packets absent; answer counts NOT_MEASURED
Family-level results: not yet scored downstream; all 599 eligible questions received at least one candidate hint
Diagnostic-only metrics: 9,660/9,660 selected UIDs hydrated; no numeric values emitted; 111 duplicate coordinates rejected
Artifact paths and hashes: see Artifact paths and hashes below
Decision: INVESTIGATE_FURTHER
Authority status: CANDIDATE_ONLY
```

## Artifacts and validation

- implementation library:
  `src/finance_query/research/retrieval_tail_coordinate_materializer.py`,
  SHA-256 `560ea6720709756fdc347e1f6173a14e6b3a63c7b24007eac3a6298156f0e109`;
- CLI:
  `scripts/research/materialize_retrieval_tail_coordinates_v1.py`, SHA-256
  `12b59a856bc85162827e937ab0307fb35dd67a1b3d4dd3325459c568c183d31d`;
- output directory:
  `artifacts/research/retrieval_tail_coordinate_materializer_v1_20260831_q82_top20_r1/`;
- output manifest SHA-256:
  `27b22d8e6f33447a1b791e159788fbd54f880341d93450c495b47b41ea6241fe`;
- summary SHA-256:
  `64145f87c565a7b8661ed2e55f85da198eeadd904736206a34226be325f8ed01`;
- coordinate hints SHA-256:
  `e3e897470ca8e62efc0e051cbcaded335f7aa5d13878f30537a03f1554aff7a1`;
- materializer validation: `PASS`;
- answer accuracy: `NOT_MEASURED`;
- execution accuracy: `NOT_MEASURED`.

## Authority status

`CANDIDATE_ONLY`; not `VERIFIED`, `PROMOTION_READY`, or
`RELEASE_AUTHORIZED`.
