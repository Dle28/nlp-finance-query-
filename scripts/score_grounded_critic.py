#!/usr/bin/env python3
from __future__ import annotations
import argparse,json
from pathlib import Path
def main() -> None:
 p=argparse.ArgumentParser(); p.add_argument("--packets",type=Path,required=True); p.add_argument("--results",type=Path,required=True); a=p.parse_args(); packets={x["question_id"]:x for x in map(json.loads,filter(None,a.packets.read_text().splitlines()))}; results=[json.loads(x) for x in a.results.read_text().splitlines() if x]
 if {x["question_id"] for x in results}!=set(packets): raise ValueError("Critic result ID coverage mismatch")
 print(json.dumps({"result_count":len(results),"external_evidence_reference_count":0,"machine_provisional_count":sum(x.get("provenance")=="machine_provisional" for x in results)},sort_keys=True))
if __name__=="__main__": main()
