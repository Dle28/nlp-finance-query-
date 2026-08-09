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
        └── V3 canonical evidence context
                - parent + child header paths reconstructed from span provenance
                - limited inline-`td` header recovery when raw source proves it
                - period and unit labels per source column
                - row roles and reliable-numeric profiles
                - exactly-one-number binding policy per source cell
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
without its parent period.  V2 canonical context follows the V2 span
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

V2 also distinguishes a cell that merely *looks numeric* in OCR syntax from a
cell that parses as exactly one reliable number under the execution parser.
The former remains visible in the lossless grid, but a concatenation such as
`(72…)(27…)` is recorded in `unreliable_numeric_columns` and cannot be bound
as an answer or formula operand. A row with no remaining reliable number is
`data_with_unreliable_numeric`, not a usable data row; a sibling valid cell in
the same row remains available. No OCR text is split, replaced, or inferred.

V3 adds one narrow recovery for OCR exports whose header was emitted as leading
`td` rows rather than HTML `th`: it inspects at most three contiguous rows
before the first data row, accepts them only when every observed numeric column
has source text with valid provenance and a period/unit/reference cue, then
records the exact source cells used. It never uses a bare number as a header
and deliberately excludes section/group headings without those cues. Thus it
does not shift data, invent missing header text, or turn a table with an
unproven header into a review-ready table.

The old `tables_evidence_context_v1.jsonl` and `v2` remain immutable for
historical sidecar audits. New preprocessing writes
`tables_evidence_context_v3.jsonl` and
`table_evidence_context_v3.manifest.json`; every new review, formula
EvidenceSet and execution ledger must name or default to that V3 sidecar. The
convenience runner includes `_context_v3` in its generated review, silver label
and execution-ledger filenames for the same reason.

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
5. The raw value-row metric has the exact significant-token identity of the
   planned metric. Structural row codes, standalone accounting-note references
   and the standard acronym `TNDN` may be normalized only as recorded audit
   transforms; words such as `tổng`, `số dư` or `nguyên giá` are never dropped.
6. Semantic grounding, metadata and multiple candidate-selection views agree;
   the critic must not find a near-tied alternative.

The views are deliberately not treated as independent ground truth.  They are
separate failure detectors over raw-source facts.  Therefore their agreement
creates `machine_calibrated` **silver**, never `human_verified` gold.

Source-quality and metadata are hard gates, not meaningful selectors when all
eligible candidates have the same score. V4 records this selector tie instead
of treating list order as evidence. A direct candidate may use the separate
`strict_raw_metric_identity_tiebreak` policy only when semantic, evidence and
challenger selectors all choose it, the critic accepts it, both hard selectors
are tied, the raw V2 value-row label has the exact same significant token
sequence as the planned metric, and its semantic/evidence/source/metadata
scores meet the 0.90/0.85/1.00 thresholds. A standalone accounting-note
reference such as `VI.06` in a separate source cell is excluded from that
identity comparison and is retained in the audit record; no ordinary label
word is removed. This rejects a near label such as
`Thặng dư vốn cổ phần` for a question asking `Vốn cổ phần`; it never changes a
human label and it never applies to formula or multi-stage questions.

The critic may reject a direct candidate solely because another raw V2 row is
within its score margin. The separate `strict_equivalent_critic_answer` policy
can resolve that case only when the selected row has exact metric identity and
every near-tied alternative is independently cell-bound, parses to the same
number, and declares the same source unit. All alternative row/cell/value/unit
facts are retained in the review record. A different value, unknown unit,
different unit, or another unverified near tie remains `machine_provisional`.
The execution ledger repeats this check from V2: it verifies the full recorded
near-tie set, canonical headers, data-row/numeric-cell role, cell provenance,
parsed number and source unit before it can emit a grounded answer. Therefore
a review record cannot bypass the critic merely by carrying the policy name.

## Raw-V2 direct source discovery

Top-K retrieval can omit a table even when the local V2 corpus contains an
exact raw row for a direct lookup. Before V4 review, the autonomous runner now
builds `direct_evidence_sets_context_v3_discovered.jsonl`. It filters only by
the effective plan's ticker/year/scope, requires a `review_ready` canonical
context, an exact significant metric-token sequence in one raw row, no explicit
opposite `đầu`/`cuối` row endpoint, and exactly one reliable raw-header-bound
numeric cell. A table with more than one qualifying row is recorded as an
ambiguity and contributes no candidate.

This is a source-recall sidecar, not a label and not an answer. Its manifest
binds the bundle tables, V2 structures, canonical context and plan-override
file. V4 revalidates the projected row after merging it into candidates; all
usual critic, unit and execution-ledger contracts still apply. The sidecar can
therefore surface a missed competing raw row and demote an unsafe prior silver
label rather than only increasing label count.

When discovery replaces evidence for an already retrieved table, it also
replaces the display summary with compact immutable metadata
(`ticker | year | scope`) and the exact raw V2 row. It must never retain that
table's former projected Top-K preview: the UID can be the same while its old
preview names a different row. This display field is audit context only and is
not used to select or execute an answer.

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
  --evidence-context ~/ViFinQA_review/run_002/tables_evidence_context_v3.jsonl \
  --discover-source-operands \
  --output ~/ViFinQA_review/run_002/formula_evidence_sets_context_v3_discovered.jsonl
```

An operand is recorded only when its metric has an exact V2 data row and a
unique raw canonical year/period cell. The collector rejects unmatched ticker
or scope metadata and requires one coherent entity/scope combination plus one
unambiguous binding per required operand. `complete` therefore means that the
controlled input EvidenceSet is source-complete; it still cannot execute when
the formula is ambiguous, needs a stage selection, or the question family is a
group/comparison/conditional program. The collector never changes a review
status or produces an answer. V2 also parses each bound source cell with the
execution parser: an OCR cell containing multiple numeric groups (for example
two parenthesized values concatenated into one cell) is rejected, not treated
as a formula operand.

With `--discover-source-operands`, the collector additionally joins immutable
`tables.jsonl` metadata by UID. It requires a resolved question-plan ticker and
only considers the operand year or the immediately following comparative
report, plus the resolved scope when one exists. The discovered table still
needs the same exact V2 row, canonical header and one-number cell binding. It
is evidence discovery only, never answer or label generation.

Audit the generated sidecar before considering executor work:

```bash
python scripts/analyze_formula_evidence.py \
  --bundle-dir ~/ViFinQA_review/run_002 \
  --formula-evidence ~/ViFinQA_review/run_002/formula_evidence_sets_context_v3_discovered.jsonl \
  --output ~/ViFinQA_review/run_002/formula_evidence_sets_context_v3_discovered.audit.json
```

The audit verifies each stored operand row/cell/header against V2 again and
reports coverage bottlenecks. It does not calculate an answer.

The execution ledger has a separate, intentionally tiny allow-list. With the
audited sidecar passed as `--formula-evidence`, it can execute only a complete,
defined `percentage_change` with controlled confidence ≥0.95, exactly
`x_old`/`x_new`, identical resolved units and a second raw-V2 cell/header/
provenance/parse revalidation. It records the original V4 consensus status in
`formula_provenance` and sets `review_status_promoted=false`; it neither edits
the autonomous review nor writes a silver training label. All ratios,
comparison, aggregation, screening and multi-stage programs remain
`not_executable`.

### Semantic rerank V2 is diagnostic, exact-row and reproducible

The semantic cross-encoder is a separate audit sidecar, never a label
promoter. A candidate is scored only if its declared
`value_row_index`/`best_row_index`/`anchor_row_index` is present in the stored
candidate window **and** that window row equals the V2 raw row exactly. The
bounded input puts the question and cell-indexed V2 row before compact source
headers/function/unit context, so the model window cannot replace the row with
an OCR title or a projected evidence string.

The V2 sidecar stores the actual `source_input` alongside its SHA-256. Its
audit regenerates that input from the immutable V2 table and canonical context;
a mismatch fails the audit. Semantic score or margin can later be used as a
veto/demotion signal only after the raw-row, header, unit and critic gates; it
cannot promote `machine_provisional` or `needs_human` by itself.

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

## Full-bundle V2 checkpoint

For the extracted `run_full_001` bundle, context V2 built 29,428 contexts with
zero build errors. It placed 4,036 tables in `needs_processing` and retained
25,392 as `review_ready`; 22,373 OCR cells were visible but excluded from
numeric binding because they did not parse as exactly one reliable number.

The latest autonomous V4 run, including raw-V2 direct source discovery,
produced 66 `machine_calibrated`, 355 `machine_provisional`, and 591
`needs_human` records. Discovery inspected 358 effective direct-lookup plans,
emitted 242 exact source candidates, and excluded 12 same-table multi-row
ambiguities rather than choosing by order. It demoted Q22 when a second exact
raw row exposed a materially different value, promoted Q249 only after binding
`Vốn cổ phần | 411 | 22 | 759.680.800.000 | 684.118.840.000` to the raw
`31/12/2016 VND` cell, and left Q286 provisional because its source candidates
disagree. The direct exact-cell
ledger made 61 direct-lookups `grounded`; FormulaSet discovery joined 33,069
metadata-bound candidates and audited 391 exact operand bindings. Under the
separate formula allow-list, one defined, complete `percentage_change` also
passed a second exact V2 revalidation, for 62 grounded execution records in
total; the other 950 remain explicitly `not_executable`. Five direct records
remain blocked because their raw source does not declare a monetary unit; five
others now bind because the resolver preserves explicit `Triệu đồng` source
headers.

One of the four initial additional direct bindings is Q14: the raw V2 row is `Chi phí
quản lý doanh nghiệp | VI.06 | 144.071.806.197`, where `VI.06` is recorded as
an ignored standalone note reference. Its source header is `Năm nay` in ASM's
2025 separate report and the declared VND unit is converted to
`144071.806197` million VND in the execution ledger. The other three were
prior exact raw-label tie-break records. Five more direct bindings use the
equivalent-critic policy: every near-tied V2 alternative carries the same
parsed VND value, and the ledger independently revalidates those alternatives
before conversion. All 66 labels remain
machine-silver only; `autotrain` correctly defers because the protocol requires
at least 200 provenance-validated pairs. All ratios, comparisons, filters and
multi-stage formulas remain fail-closed.
