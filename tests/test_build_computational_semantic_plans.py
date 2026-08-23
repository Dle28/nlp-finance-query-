import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "computational_semantic_builder",
    ROOT / "scripts" / "build_computational_semantic_plans_v1.py",
)
assert SPEC is not None and SPEC.loader is not None
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_builder_is_complete_hash_bound_and_non_promotable() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        questions = root / "questions.jsonl"
        catalog = root / "catalog.jsonl"
        output = root / "computational_semantic_plans_v1.jsonl"
        records = [
            {
                "id": 2,
                "question": "Tổng tài sản của SSH cuối năm 2025 là bao nhiêu?",
                "question_plan": {"tickers": ["SSH"], "years": [2025], "scope": "consolidated", "operation_ast": {"op": "lookup", "args": ["x0"]}},
            },
            {
                "id": 1,
                "question": "Hệ số thanh toán hiện hành của HPG năm 2022 là bao nhiêu?",
                "question_plan": {"tickers": ["HPG"], "years": [2022], "scope": "consolidated", "operation_ast": {"op": "divide", "args": ["x0", "x1"]}},
            },
        ]
        questions.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in records) + "\n", encoding="utf-8")
        catalog.write_text("", encoding="utf-8")
        manifest = BUILDER.build_plans(
            questions=questions,
            taxonomy_path=ROOT / "configs" / "vietnamese_financial_taxonomy_v1.yaml",
            metric_registry_path=ROOT / "configs" / "financial_metric_registry_v1.yaml",
            table_catalog=catalog,
            output=output,
        )
        rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
        persisted = json.loads(output.with_suffix(".manifest.json").read_text(encoding="utf-8"))
        assert [row["question_id"] for row in rows] == [1, 2]
        assert manifest["question_count"] == 2
        assert manifest["output"]["sha256"] == sha256(output)
        assert manifest["inputs"]["questions"]["sha256"] == sha256(questions)
        assert persisted == manifest
        assert manifest["answer_eligible"] is False
        assert manifest["source_contract"]["may_select_value_cell"] is False


def test_builder_rejects_duplicate_ids_before_writing() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        questions = root / "questions.jsonl"
        catalog = root / "catalog.jsonl"
        output = root / "output.jsonl"
        row = {"id": 3, "question": "Tổng tài sản?", "question_plan": {}}
        questions.write_text("\n".join(json.dumps(row) for _ in range(2)) + "\n", encoding="utf-8")
        catalog.write_text("", encoding="utf-8")
        try:
            BUILDER.build_plans(
                questions=questions,
                taxonomy_path=ROOT / "configs" / "vietnamese_financial_taxonomy_v1.yaml",
                metric_registry_path=ROOT / "configs" / "financial_metric_registry_v1.yaml",
                table_catalog=catalog,
                output=output,
            )
        except ValueError as exc:
            assert "duplicate ids" in str(exc)
        else:
            raise AssertionError("duplicate question IDs must fail closed")
        assert not output.exists()

