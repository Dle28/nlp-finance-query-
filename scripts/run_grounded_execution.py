#!/usr/bin/env python3
from __future__ import annotations
import argparse
from pathlib import Path
from finance_query.grounded_execution import run_grounded_execution

def main() -> None:
    p=argparse.ArgumentParser(); p.add_argument("--bindings",type=Path,required=True); p.add_argument("--bindings-manifest",type=Path,required=True); p.add_argument("--metric-registry",type=Path,required=True); p.add_argument("--output-dir",type=Path,required=True); a=p.parse_args()
    print(run_grounded_execution(bindings_path=a.bindings, bindings_manifest_path=a.bindings_manifest, metric_registry_path=a.metric_registry, output_dir=a.output_dir)["manifest_path"])
if __name__ == "__main__": main()
