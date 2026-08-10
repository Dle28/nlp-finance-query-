# ArtifactRegistry V1

## Mục đích

ArtifactRegistry là index hash-bound ở **cấp workspace**. Nó thay routing theo
chuỗi filename phiên bản bằng logical names:

```text
raw_table
structured_table
evidence_context
report_segment
direct_evidence
formula_evidence
review_decision
```

Nó không thay manifest của V2/V3/Direct/Formula EvidenceSet và không thay đổi
nội dung artifact. Manifest sidecar hiện hữu vẫn là contract chuyên biệt của
producer/consumer; registry chỉ ghi dependency graph thống nhất để orchestration
không phải suy đoán `*_v4` là gì.

Mỗi record có:

```json
{
  "logical_name": "structured_table",
  "artifact_type": "structured_table",
  "schema_version": 2,
  "relative_path": "run_x/tables_structured_v2.jsonl",
  "sha256": "...",
  "dependencies": {"raw_table": "..."}
}
```

Registry từ chối path thoát workspace, SHA thay đổi, dependency thiếu/sai hash,
logical name/type không hợp lệ và dependency cycle.

## Tạo registry

Registry không suy luận business meaning từ filename. Caller đăng ký một lần
với logical name/type rõ ràng; version nằm trong manifest/schema field, không
trong routing code.

```bash
python scripts/build_artifact_registry.py \
  --workspace-root ~/ViFinQA_review \
  --artifact raw_table:table_asset:run_full_metadata_support_v1/tables.jsonl \
  --artifact structured_table:structured_table:run_full_metadata_support_v1/tables_structured_v2.jsonl \
  --artifact evidence_context:evidence_context:run_full_metadata_support_v1/tables_evidence_context_v3.jsonl \
  --schema-version raw_table=3 \
  --schema-version structured_table=2 \
  --schema-version evidence_context=3 \
  --depends-on structured_table=raw_table \
  --depends-on evidence_context=raw_table,structured_table
```

Parent artifacts phải được khai báo trước child. Output mặc định là:

```text
~/ViFinQA_review/artifact_registry_v1.json
```

## Xác thực trước một stage

```bash
python scripts/build_artifact_registry.py \
  --workspace-root ~/ViFinQA_review \
  --validate-only
```

Validation chỉ đọc/hash artifact; không build Kaggle corpus, SQLite FTS, FAISS
hay thay review/training label.

## Lộ trình migration

V1 là additive để không phá manifest cũ. Consumer được migrate theo thứ tự:

1. read-only diagnostics và local orchestration;
2. builders Direct/Formula EvidenceSet;
3. reviewer/ledger;
4. training/submission runners.

Trong thời gian migration, consumer phải validate cả registry và manifest
chuyên biệt. Registry không được dùng để promote `machine_calibrated`, bind
evidence hoặc thay provenance.
