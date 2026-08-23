import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
TAXONOMY = ROOT / "configs" / "vietnamese_financial_taxonomy_v1.yaml"
REGISTRY = ROOT / "configs" / "financial_metric_registry_v1.yaml"
_SPEC = importlib.util.spec_from_file_location(
    "question_concept_route_builder",
    ROOT / "scripts" / "build_question_concept_routes.py",
)
assert _SPEC is not None and _SPEC.loader is not None
_BUILDER = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_BUILDER)
build_routes = _BUILDER.build_routes


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class BuildQuestionConceptRoutesTests(unittest.TestCase):
    def test_materialization_is_hash_bound_non_promotable_and_complete(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            questions = root / "questions.jsonl"
            output = root / "question_concept_routes_v1.jsonl"
            questions.write_text(
                "\n".join(
                    json.dumps(record, ensure_ascii=False)
                    for record in [
                        {
                            "id": 2,
                            "question": "Tổng tài sản ngắn hạn của HPG năm 2022 là bao nhiêu?",
                            "question_plan": {
                                "tickers": ["HPG"],
                                "years": [2022],
                                "scope": "consolidated",
                            },
                        },
                        {
                            "id": 1,
                            "question": "Hệ số thanh toán nhanh của HPG năm 2022 là bao nhiêu?",
                            "question_plan": {
                                "tickers": ["HPG"],
                                "years": [2022],
                                "scope": "consolidated",
                            },
                        },
                        {
                            "id": 3,
                            "question": "Số nhân viên của HPG năm 2022 là bao nhiêu?",
                            "question_plan": {
                                "tickers": ["HPG"],
                                "years": [2022],
                                "scope": "consolidated",
                            },
                        },
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            manifest = build_routes(
                questions=questions,
                taxonomy_path=TAXONOMY,
                metric_registry_path=REGISTRY,
                output=output,
            )
            manifest_path = output.with_suffix(".manifest.json")
            rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
            persisted_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual([row["question_id"] for row in rows], [1, 2, 3])
            self.assertEqual(manifest["question_count"], 3)
            self.assertEqual(manifest["question_id_count"], 3)
            self.assertEqual(manifest["output"]["sha256"], sha256(output))
            self.assertEqual(manifest["inputs"]["questions"]["sha256"], sha256(questions))
            self.assertEqual(persisted_manifest, manifest)
            self.assertEqual(manifest["route_status_counts"], {
                "abstain": 1,
                "concept_lookup_candidate": 1,
                "metric_candidate": 1,
            })
            self.assertEqual(manifest["metric_counts"], {"quick_ratio": 1})
            self.assertEqual(
                manifest["concept_counts"],
                {
                    "current_assets": 2,
                    "current_liabilities": 1,
                    "inventory": 1,
                },
            )
            self.assertFalse(manifest["source_contract"]["evidence_eligible"])
            self.assertFalse(manifest["source_contract"]["training_eligible"])
            self.assertFalse(manifest["source_contract"]["submission_eligible"])
            self.assertFalse(manifest["source_contract"]["promotion_allowed"])
            self.assertEqual(
                manifest["top_unmatched_normalized_phrases"],
                [{"normalized_phrase": "so nhan vien cua hpg nam 2022 la bao nhieu?", "count": 1}],
            )

    def test_duplicate_question_id_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            questions = root / "questions.jsonl"
            output = root / "question_concept_routes_v1.jsonl"
            row = {
                "id": 9,
                "question": "Hệ số thanh toán nhanh của HPG năm 2022 là bao nhiêu?",
                "question_plan": {
                    "tickers": ["HPG"],
                    "years": [2022],
                    "scope": "consolidated",
                },
            }
            questions.write_text(
                "\n".join(json.dumps(row, ensure_ascii=False) for _ in range(2)) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "duplicate ids"):
                build_routes(
                    questions=questions,
                    taxonomy_path=TAXONOMY,
                    metric_registry_path=REGISTRY,
                    output=output,
                )
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
