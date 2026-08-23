#!/usr/bin/env python3
from __future__ import annotations
import argparse
from pathlib import Path
from finance_query.grounded_execution_v2 import run
def main():
 p=argparse.ArgumentParser();p.add_argument("--bindings",type=Path,required=True);p.add_argument("--bindings-manifest",type=Path,required=True);p.add_argument("--metric-registry",type=Path,required=True);p.add_argument("--output",type=Path,required=True);a=p.parse_args();print(run(bindings=a.bindings,bindings_manifest=a.bindings_manifest,metric_registry=a.metric_registry,output=a.output)["manifest_path"])
if __name__=="__main__":main()
