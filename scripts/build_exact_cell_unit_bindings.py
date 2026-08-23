#!/usr/bin/env python3
from __future__ import annotations
import argparse
from pathlib import Path
from finance_query.exact_cell_bindings import build_exact_cell_unit_bindings

def main() -> None:
    p = argparse.ArgumentParser(description="Build non-promotable exact cell/unit binding candidates.")
    p.add_argument("--period-packets", type=Path, required=True); p.add_argument("--period-manifest", type=Path, required=True)
    p.add_argument("--metric-registry", type=Path, required=True); p.add_argument("--structured-tables", type=Path, required=True); p.add_argument("--evidence-context", type=Path, required=True)
    p.add_argument("--approved-repairs", type=Path); p.add_argument("--repairs-manifest", type=Path); p.add_argument("--output-dir", type=Path, required=True)
    a = p.parse_args(); print(build_exact_cell_unit_bindings(period_packets_path=a.period_packets, period_manifest_path=a.period_manifest, metric_registry_path=a.metric_registry, structured_tables_path=a.structured_tables, evidence_context_path=a.evidence_context, approved_repairs_path=a.approved_repairs, repairs_manifest_path=a.repairs_manifest, output_dir=a.output_dir)["manifest_path"])

if __name__ == "__main__": main()
