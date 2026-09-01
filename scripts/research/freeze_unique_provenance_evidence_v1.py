#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "src") not in sys.path: sys.path.insert(0, str(REPO_ROOT / "src"))
from finance_query.research.unique_provenance_evidence import freeze_unique_provenance_split
def main() -> None:
    p=argparse.ArgumentParser(); p.add_argument("--hypothesis",type=Path,required=True); p.add_argument("--dataset",type=Path,required=True); p.add_argument("--official-splits",type=Path,required=True); p.add_argument("--output-dir",type=Path,required=True); a=p.parse_args()
    print(json.dumps(freeze_unique_provenance_split(hypothesis_path=a.hypothesis,dataset_path=a.dataset,official_splits_path=a.official_splits,output_dir=a.output_dir),ensure_ascii=False,indent=2,sort_keys=True))
if __name__ == "__main__": main()
