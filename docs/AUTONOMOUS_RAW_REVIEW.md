# Autonomous raw-source review and machine-silver training

This is the continuation path when there is no human reviewer in the loop.
It does **not** rename machine output as human gold.  The system may produce
only `machine_calibrated` silver labels, retain `machine_provisional` and
`needs_human` as quarantined records, and train only from the former.

## Architecture

```text
immutable review bundle
        │
        ├── raw HTML → V2 lossless grid
        │       - empty cells preserved
        │       - rowspan / colspan provenance preserved
        │       - raw source UID / hash checked
        │
        └── V1 canonical evidence context
                - parent + child header paths reconstructed from span provenance
                - period and unit labels per source column
                - row roles and numeric-column profiles
                - review_ready / needs_processing / blocked gate
        │
        ▼
autonomous V4 review
  retrieval view · semantic view · evidence-row view · metadata view
  source-quality view · challenger · critic
        │
        ├── machine_calibrated → machine silver only
        ├── machine_provisional → audit only, never training
        └── needs_human       → quarantine only, never training
        │
        ▼
minimum-size + provenance gate
        │
        ▼
dense model fine-tuning (new model only; no FAISS/index rebuild)
```

## Why a new preprocessing layer is required

V2 is intentionally lossless: a `colspan` parent value appears in its anchor
cell and the covered cells remain blank.  This prevents an OCR table from
shifting columns, but a naïve display can consequently show a child header
without its parent period.  V1 canonical context follows the V2 span
provenance to form a *derived header path* such as:

```text
Số cuối năm · Giá trị VND
Số đầu năm  · Dự phòng
```

It does not fill missing OCR text, correct a number, or move a source cell.
If an expected header/data relationship cannot be reconstructed, the table is
quarantined instead of being interpreted by an agent.

Canonical headers also form a prefix: if V2's structural heuristic marks a
numeric row *after the first observed data row* as a header, that later marker
is excluded from the derived header path and recorded as
`nonleading_header_rows_excluded_from_canonical_path`. This prevents values
from being concatenated into a period label. The raw V2 cells/markers remain
unchanged; tables whose remaining prefix cannot label numeric data become
`needs_processing` rather than being guessed.

## Independent autonomous gates

A selected candidate must pass all of these before it can be machine silver:

1. Its projected evidence equals an exact raw-HTML V2 row at the same UID.
2. Its canonical table context is `review_ready`; tables with missing headers,
   rows or provenance are not eligible.
3. The evidence row is classified as a data row, not a header/narrative row.
4. For an opening/closing-period **or explicitly named-year** question, one
   and only one numeric column can be bound through a raw source header to that
   period/year. A report metadata year alone never binds a comparison column.
   ``Năm nay`` is allowed only when the candidate report year exactly equals
   the question year and the raw header says so.
5. Semantic grounding, metadata and multiple candidate-selection views agree;
   the critic must not find a near-tied alternative.

The views are deliberately not treated as independent ground truth.  They are
separate failure detectors over raw-source facts.  Therefore their agreement
creates `machine_calibrated` **silver**, never `human_verified` gold.

## Commands

```bash
# Derive/refresh canonical context from the existing local V2 sidecar.
python local/run_local_review_stage.py preprocess \
  --bundle-dir ~/ViFinQA_review/run_002

# Let V4 agents review the bundle and export only source-gated machine silver.
python local/run_local_review_stage.py autonomous \
  --bundle-dir ~/ViFinQA_review/run_002

# Fine-tune only after the minimum amount of V4 silver exists.
python local/run_local_review_stage.py autotrain \
  --bundle-dir ~/ViFinQA_review/run_002 \
  --autonomous-min-pairs 200
```

`autotrain` exits with a `deferred` report if fewer than 200 valid pairs exist;
it does not create a model in that case. When it trains, it writes a new
encoder model only. It does not modify the existing corpus, lexical index,
dense embeddings or FAISS index.

### Correcting a high-precision plan without rewriting the bundle

Some Vietnamese row labels contain operation-like words even though the report
already discloses one value: for example ``Tổng cộng tài sản``, ``Tỷ lệ sở
hữu`` or ``Lỗ chênh lệch tỷ giá``. The planner only treats narrow,
single-subject/single-period forms as a direct lookup. Group, comparison,
range and arithmetic wording stays composed.

For an existing immutable bundle, create a separate, hash-bound sidecar rather
than editing `review_items.jsonl`:

```bash
python scripts/build_question_plan_overrides.py \
  --bundle-dir ~/ViFinQA_review/run_002 \
  --output ~/ViFinQA_review/run_002/question_plan_overrides_v1.jsonl

python local/run_local_review_stage.py autonomous \
  --bundle-dir ~/ViFinQA_review/run_002 \
  --question-plan-overrides ~/ViFinQA_review/run_002/question_plan_overrides_v1.jsonl
```

Each override includes the exact question hash and original-plan hash, a narrow
reason code, and an effective one-operand `lookup` plan. V4 records both the
effective plan and its provenance; the execution ledger refuses an override
that does not match the original bundle. The sidecar changes neither source
tables nor any `human_verified`/machine provenance state.

### Formula EvidenceSets are coverage, not answers

For controlled ratio/temporal formula templates, create a separate exact-row
coverage sidecar:

```bash
python scripts/build_formula_evidence_sets.py \
  --bundle-dir ~/ViFinQA_review/run_002 \
  --output ~/ViFinQA_review/run_002/formula_evidence_sets_v1.jsonl
```

An operand is recorded only when its metric has an exact V2 data row and a
unique raw canonical year/period cell. The collector rejects unmatched ticker
or scope metadata and requires one coherent entity/scope combination plus one
unambiguous binding per required operand. `complete` therefore means that the
controlled input EvidenceSet is source-complete; it still cannot execute when
the formula is ambiguous, needs a stage selection, or the question family is a
group/comparison/conditional program. The collector never changes a review
status or produces an answer.

## Current run on `run_002`

The context sidecar covered all 1,211 bundle tables without source errors:

```text
review_ready:      969
needs_processing:  242
blocked:             0
span-header recoveries: 899 cells across 299 tables
safe continuation-header recoveries: 0/81 candidates
```

On the 60-question bundle, conservative V4 produced 2
`machine_calibrated`, 16 `machine_provisional` and 42 `needs_human`. That is
the expected outcome for a clean-first gate: it shows the current 60-question
sample is far too small to train safely. It must not be weakened simply to
create more labels.

The 242 quarantined tables are primarily pages with no data row or numeric data
without a source header. The repository has extracted OCR text but no original
PDF/HTML layout files, so a missing heading cannot be reconstructed safely from
the present source. The continuation recovery considered 81 adjacent cases and
accepted none under the exact same-document/adjacent-ordinal/width/function/
numeric-layout rule. This is intentional evidence, not a fallback failure.

## Scaling without human review

The next autonomous batch should retrieve the remaining ViFinQA questions from
the existing corpus/index, then run the same `preprocess → autonomous` steps.
No dense-index rebuild is required. Only after enough labels pass the same
raw-source protocol should `autotrain` run. Model evaluation remains
machine-silver evaluation—not a claim of human/official accuracy—until an
external gold source becomes available.
