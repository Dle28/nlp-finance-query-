import json
import tempfile
import unittest
from pathlib import Path

from finance_query.retrieval import AssetStore


class RetrievalRoutingTests(unittest.TestCase):
    def test_lexical_allow_list_never_leaks_an_outside_table(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            assets = root / "tables.jsonl"
            rows = [
                {
                    "internal_table_uid": "allowed",
                    "document_id": "HPG_2022",
                    "ticker": "HPG",
                    "report_year": 2022,
                    "scope": "consolidated",
                    "headers": [],
                    "rows": [],
                    "search_text": "Tài sản ngắn hạn HPG 2022",
                },
                {
                    "internal_table_uid": "outside",
                    "document_id": "OTHER_2022",
                    "ticker": "OTHER",
                    "report_year": 2022,
                    "scope": "consolidated",
                    "headers": [],
                    "rows": [],
                    "search_text": "Tài sản ngắn hạn có độ khớp cao hơn HPG 2022",
                },
            ]
            assets.write_text(
                "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
                encoding="utf-8",
            )
            store = AssetStore(root / "assets.sqlite")
            store.build(assets)
            self.assertEqual(
                [uid for uid, _score in store.search_lexical(
                    "Tài sản ngắn hạn", top_k=5, allowed_uids=["allowed"]
                )],
                ["allowed"],
            )
            self.assertEqual(
                store.search_lexical("Tài sản ngắn hạn", top_k=5, allowed_uids=[]),
                [],
            )


if __name__ == "__main__":
    unittest.main()
