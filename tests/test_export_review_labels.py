from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location(
    "export_review_labels", ROOT / "scripts" / "export_review_labels.py"
)
mod = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(mod)


class ExportReviewLabelTests(unittest.TestCase):
    def test_id_preview_is_bounded_but_keeps_the_full_count(self) -> None:
        self.assertEqual(mod.id_preview([]), "0")
        self.assertEqual(mod.id_preview([4, 8]), "2 [4, 8]")
        self.assertEqual(
            mod.id_preview(list(range(15))),
            "15 [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, …]",
        )


if __name__ == "__main__":
    unittest.main()
