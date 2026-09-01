#!/usr/bin/env python3
"""Build non-authorizing machine exact-cell proposals for one E2E repair batch."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from finance_query.research.machine_exact_cell_proposals import build_machine_exact_cell_proposals


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--triage", type=Path, required=True)
    parser.add_argument("--period-packets", type=Path, required=True)
    parser.add_argument("--table-diagnostics", type=Path, required=True)
    parser.add_argument("--row-candidates", type=Path, required=True)
    parser.add_argument("--structured-tables", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--minimum-row-jaccard", type=float, default=0.5)
    parser.add_argument("--minimum-row-margin", type=float, default=0.2)
    args = parser.parse_args()
    result = build_machine_exact_cell_proposals(
        triage_path=args.triage,
        period_packets_path=args.period_packets,
        table_diagnostics_path=args.table_diagnostics,
        row_candidates_path=args.row_candidates,
        structured_tables_path=args.structured_tables,
        output_dir=args.output_dir,
        minimum_row_jaccard=args.minimum_row_jaccard,
        minimum_row_margin=args.minimum_row_margin,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
