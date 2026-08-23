#!/usr/bin/env python3
from __future__ import annotations
import argparse
from pathlib import Path
from finance_query.exact_cell_bindings_v2 import build
def main():
 p=argparse.ArgumentParser();
 for n in ("period-packets","period-manifest","route-overlay","route-overlay-manifest","structured-tables","metric-registry","output"):p.add_argument("--"+n,type=Path,required=True)
 p.add_argument("--approved-repairs",type=Path);p.add_argument("--repairs-manifest",type=Path);a=p.parse_args();print(build(**{k.replace("_","-").replace("-","_"):v for k,v in vars(a).items()})["manifest_path"])
if __name__=="__main__":main()
