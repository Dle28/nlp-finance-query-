#!/usr/bin/env python3
from __future__ import annotations
import argparse
from pathlib import Path
from finance_query.route_completeness import build_route_completeness_overlay
def main() -> None:
 p=argparse.ArgumentParser(); p.add_argument("--questions",type=Path,required=True); p.add_argument("--routes",type=Path,required=True); p.add_argument("--routes-manifest",type=Path,required=True); p.add_argument("--output",type=Path,required=True); a=p.parse_args(); print(build_route_completeness_overlay(questions_path=a.questions,routes_path=a.routes,routes_manifest_path=a.routes_manifest,output=a.output)["manifest_path"])
if __name__=="__main__":main()
