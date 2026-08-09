from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import PropertyMock, patch

import numpy as np

from finance_query.retrieval import DenseIndex


class _FakeEncoder:
    def __init__(self) -> None:
        self.calls: list[tuple[int, int]] = []

    def encode(self, texts, *, batch_size, **_kwargs):
        self.calls.append((len(texts), batch_size))
        return np.ones((len(texts), 3), dtype="float32")


class DenseIndexBatchingTests(unittest.TestCase):
    def test_outer_chunks_reduce_encode_calls_without_changing_micro_batch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            assets = root / "assets.jsonl"
            assets.write_text(
                "".join(
                    json.dumps(
                        {
                            "internal_table_uid": f"u{index}",
                            "search_text": f"table {index}",
                        }
                    )
                    + "\n"
                    for index in range(9)
                ),
                encoding="utf-8",
            )
            encoder = _FakeEncoder()
            dense = DenseIndex(
                index_path=root / "dense.index",
                uids_path=root / "uids.jsonl",
                model_name="fake",
                device="cpu",
            )
            with patch.object(DenseIndex, "model", new_callable=PropertyMock, return_value=encoder):
                self.assertEqual(dense.build(assets, batch_size=2, encode_chunk_size=4), 9)

            self.assertEqual(encoder.calls, [(4, 2), (4, 2), (1, 1)])

    def test_chunk_must_cover_at_least_one_micro_batch(self) -> None:
        dense = DenseIndex(Path("x.index"), Path("x.uids"), "fake", "cpu")
        with self.assertRaisesRegex(ValueError, "at least batch_size"):
            dense.build(Path("missing.jsonl"), batch_size=4, encode_chunk_size=3)


if __name__ == "__main__":
    unittest.main()
