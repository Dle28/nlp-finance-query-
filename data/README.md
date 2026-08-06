# Data directory

The ViFinQA corpus is downloaded locally and is not stored in Git.

Download it from the repository root:

```bash
python data/process/extract_data.py
```

Default local structure:

```text
data/ViFinQA/
├── code_stock.csv
├── questions/
│   └── questions.jsonl
└── financial_statements/
```

Generated corpus audits are written to:

```text
data/process/audit_output/
```

Both directories are ignored by Git because they are reproducible and may be large.
