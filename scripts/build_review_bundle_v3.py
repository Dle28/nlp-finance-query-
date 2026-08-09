#!/usr/bin/env python3
"""ViFinQA review bundle v3: recover adjacent tables and ground evidence rows."""
from __future__ import annotations
import argparse, hashlib, json, os, re, shutil, sqlite3, subprocess, tarfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from bs4 import BeautifulSoup
from finance_query.config import ProjectPaths
from finance_query.pipeline import ViFinQARetrievalPipeline, load_config

TOK = re.compile(r"[A-Za-zÀ-ỹ0-9%]+", re.UNICODE)
NUM = re.compile(r"^[\s()\-+\d.,%/]+$")
STOP = {"cua","của","la","là","bao","nhieu","nhiêu","vao","vào","tai","tại","trong","cho","theo","den","đến","ngay","ngày","thang","tháng","nam","năm","cuoi","cuối","dau","đầu","va","và","mot","một","cac","các","duoc","được","co","có","dong","đồng","trieu","triệu","ty","tỷ","phan","phần","tram","trăm","công","cong","ctcp"}
ENTITY = re.compile(r"\s+của\s+(?:công\s+ty\s+mẹ|tổng\s+công\s+ty|ctcp|công\s+ty\s+cổ\s+phần|ngân\s+hàng|tập\s+đoàn)\b", re.I)
END = re.compile(r"cuối\s+năm|31\s*[/.-]\s*12|31\s+tháng\s+12|đến\s+ngày\s+31|tại\s+ngày\s+31", re.I)
START = re.compile(r"đầu\s+năm|(?:^|\D)0?1\s*[/.-]\s*0?1(?:\D|$)", re.I)

def args():
    p=argparse.ArgumentParser(); p.add_argument("--questions",type=Path,default=Path("data/labels/annotation_questions_60.jsonl")); p.add_argument("--config",type=Path,default=Path("configs/annotation_baseline.yaml")); p.add_argument("--output-dir",type=Path,required=True); p.add_argument("--repo-root",type=Path,default=Path.cwd()); p.add_argument("--top-k",type=int,default=20); p.add_argument("--max-review-candidates",type=int,default=40); p.add_argument("--neighbor-radius",type=int,default=1); p.add_argument("--context-recovery-threshold",type=float,default=.45); p.add_argument("--min-asset-count",type=int,default=100000); p.add_argument("--no-dense",action="store_true"); p.add_argument("--force",action="store_true"); p.add_argument("--allow-errors",action="store_true"); return p.parse_args()

def tokens(s): return {x.casefold() for x in TOK.findall(str(s)) if len(x)>=2 and x.casefold() not in STOP}
def ov(s,t): return len(tokens(s)&t)/len(t) if t else 0.0
def rowtext(r): return re.sub(r"\s+"," "," | ".join(str(x).strip() for x in r if str(x).strip())).strip()[:700]
def nnum(r): return sum(bool(x and NUM.fullmatch(x) and any(c.isdigit() for c in x)) for x in map(lambda z:str(z).strip(),r))
def loadj(p): return [json.loads(x) for x in p.open(encoding="utf-8-sig") if x.strip()]
def writej(p,rows):
    p.parent.mkdir(parents=True,exist_ok=True); q=p.with_suffix(p.suffix+".tmp")
    with q.open("w",encoding="utf-8") as f:
        for x in rows: f.write(json.dumps(x,ensure_ascii=False)+"\n")
        f.flush(); os.fsync(f.fileno())
    q.replace(p)
def sha(p):
    h=hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda:f.read(8*1024*1024),b""): h.update(b)
    return h.hexdigest()
def count(p): return sum(1 for x in p.open(encoding="utf-8") if x.strip()) if p.is_file() else 0

def plan_metric(plan):
    op=plan.get("operands") or []; return str((op[0] if op else {}).get("metric") or "").strip()
def effective_metric(q,plan,fam=None):
    family=str(plan.get("family") or fam or ""); raw=plan_metric(plan)
    if family=="direct_lookup":
        m=ENTITY.search(q)
        if m and m.start()>3:
            x=q[:m.start()].strip(" ?.,:;-")
            if len(tokens(x))>=2:return x
    x=re.sub(r"\b(?:19|20)\d{2}\b|\b\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?\b"," ",raw)
    for t in plan.get("tickers") or []: x=re.sub(rf"\b{re.escape(str(t))}\b"," ",x,flags=re.I)
    return re.sub(r"\s+"," ",x).strip(" ?.,:;-") or raw or q

def period(q): return "end" if END.search(q) else "start" if START.search(q) else "unspecified"
def pmatch(label,intent):
    s=label.casefold()
    if intent=="end" and any(x in s for x in ["số cuối năm","số dư cuối năm","cuối năm","31/12","31-12","31.12","cuối kỳ"]): return 1.0
    if intent=="start" and any(x in s for x in ["số đầu năm","số dư đầu năm","đầu năm","1/1","01/01","01-01","đầu kỳ"]): return 1.0
    return 0.0

def heading(ctx):
    if not ctx:return ""
    tail=ctx.rsplit("</table>",1)[-1]
    # The source title normally begins immediately after the newest page
    # marker.  Keeping its beginning avoids left-truncated names such as
    # "y Cổ phần ..." while still bounding bundle size.
    page_tail=re.split(r"=====\s*PAGE\s*\d+\s*=====",tail,flags=re.I)[-1]
    s=BeautifulSoup(page_tail,"lxml").get_text(" ",strip=True)
    s=re.sub(r"\s+"," ",s).strip()
    return s[:420]

def projection(rows,ctx,q,plan,metric):
    mt,qt=tokens(metric),tokens(q); intent=period(q); ch=heading(ctx); chm=ov(ch,mt); info=[]
    for i,r in enumerate(rows):
        text=rowtext(r); m,qq=ov(text,mt),ov(text,qt); nn=nnum(r); info.append({"index":i,"row":r,"metric_overlap":m,"question_overlap":qq,"numeric":nn>0,"numeric_cells":nn,"anchor_score":.76*m+.16*qq+.08})
    anchor=max(info,key=lambda x:x["anchor_score"],default=None); ai=anchor["index"] if anchor else None
    col=None
    for x in info[:4]:
        txt=rowtext(x["row"]).casefold()
        if any(k in txt for k in ["số cuối năm","số đầu năm","năm nay","năm trước","nguyên giá","giá trị còn lại","31/12"]): col=x; break
    total=None
    for x in info:
        lab=str(x["row"][0]).strip().casefold() if x["row"] else ""
        if x["numeric"] and lab in {"tổng cộng","cộng","tổng","total"}: total=x
    value=None
    if chm>=.45 and total is not None: value=total
    elif anchor:
        vals=[]
        for x in info:
            if not x["numeric"]:continue
            d=abs(x["index"]-ai)
            if d>10:continue
            ps=pmatch(str(x["row"][0]) if x["row"] else "",intent); vs=.45*ps+.25/(1+d)+.15*min(1,x["numeric_cells"]/3)+.10*x["metric_overlap"]+.05*x["question_overlap"]; vals.append((vs,x))
        if vals:value=max(vals,key=lambda z:z[0])[1]
        if value is None and anchor["numeric"]:value=anchor
    vi=value["index"] if value else None; topic=ch if ch and chm>=.2 else rowtext(anchor["row"]) if anchor else ch or "Không xác định được chủ đề"
    if not anchor and not ch:return {"effective_metric":metric,"context_heading":ch,"table_topic":topic,"one_line_summary":f"Bảng: {topic}.","direct_evidence":"Không có structured row","anchor_row_index":None,"best_row_index":None,"value_row_index":None,"evidence_window":[],"period_intent":intent,"period_match":0.0,"evidence_features":{"metric_overlap":0.0,"question_overlap":0.0,"numeric":False,"row_score":0.0}}
    am=anchor["metric_overlap"] if anchor else 0.0; aq=anchor["question_overlap"] if anchor else 0.0; ms=max(am,chm); qs=max(aq,value["question_overlap"] if value else 0.0); numeric=bool(value and value["numeric"]); ps=pmatch(str(value["row"][0]) if value and value["row"] else "",intent) if value else 0.0; support=min(1,.58*ms+.16*qs+(.16 if numeric else 0)+.10*ps)
    inds={x for x in [ai,vi,col["index"] if col else None] if x is not None}; lo=max(0,min(inds)-2) if inds else 0; hi=min(len(rows),max(inds)+3) if inds else min(len(rows),5); win=[{"index":i,"row":rows[i]} for i in range(lo,hi)]
    parts=[]
    if ch and chm>=.2: parts.append(f"TABLE: {ch}")
    if col and (value is None or col["index"]!=vi): parts.append(f"COLUMNS: {rowtext(col['row'])}")
    if value: parts.append(f"VALUE: {rowtext(value['row'])}")
    elif anchor: parts.append(f"ANCHOR: {rowtext(anchor['row'])}")
    direct=" || ".join(parts); best=vi if vi is not None else ai
    return {"effective_metric":metric,"context_heading":ch,"table_topic":topic[:260],"one_line_summary":f"Bảng: {topic[:260]}. Bằng chứng trực tiếp: {direct}"[:1100],"direct_evidence":direct,"anchor_row_index":ai,"best_row_index":best,"value_row_index":vi,"evidence_window":win,"period_intent":intent,"period_match":ps,"evidence_features":{"metric_overlap":ms,"question_overlap":qs,"numeric":numeric,"row_score":support,"period_match":ps,"anchor_row_index":ai,"value_row_index":vi,"context_heading_overlap":chm}}

def rows(asset): return json.loads(asset.get("rows_json") or "[]")
def jfield(asset,key,default):
    value=asset.get(key,default)
    if isinstance(value,str):
        try:return json.loads(value)
        except json.JSONDecodeError:return default
    return value if value is not None else default
def rowall(asset): return " ".join(rowtext(r) for r in rows(asset))
def previous(store,a,radius):
    o=a.get("local_ordinal"); d=a.get("document_id")
    if not d or o is None:return []
    with store.connect() as c: rs=c.execute("SELECT * FROM assets WHERE document_id=? AND local_ordinal BETWEEN ? AND ? ORDER BY local_ordinal DESC",(d,max(1,int(o)-radius),int(o)-1)).fetchall()
    return [dict(x) for x in rs]
def meta(plan,c):
    ts=[str(x).casefold() for x in plan.get("tickers") or []]; doc=str(c.get("document_id") or "").casefold(); tm=not ts or any(t in doc or t==str(c.get("ticker") or "").casefold() for t in ts); sc=plan.get("scope"); sm=not sc or c.get("scope")==sc; ys={int(y) for y in plan.get("years") or [] if str(y).isdigit()}; ym=not ys or c.get("report_year") in ys; return {"ticker_match":tm,"scope_match":sm,"year_match":ym,"metadata_score":.4*tm+.3*sm+.3*ym}
def desc_retr(c,r): return {**c,"original_retrieval_rank":r,"candidate_source":"retrieved","parent_retrieval_rank":r}
def desc_adj(a,r): return {"internal_table_uid":a["uid"],"document_id":a.get("document_id"),"ticker":a.get("ticker"),"report_year":a.get("report_year"),"scope":a.get("scope"),"lexical_rank":None,"dense_rank":None,"fused_score":0.0,"reranker_score":None,"preview":str(a.get("search_text") or "")[:500],"original_retrieval_rank":None,"candidate_source":"adjacent_previous_due_context","parent_retrieval_rank":r}
def record(d,a,q,plan,metric):
    pr=projection(rows(a),str(a.get("context_before") or ""),q,plan,metric); m=meta(plan,d); prior=1/int(d["original_retrieval_rank"]) if d.get("original_retrieval_rank") else .5/int(d.get("parent_retrieval_rank") or 999); score=min(1,.62*pr["evidence_features"]["row_score"]+.23*m["metadata_score"]+.11*min(1,prior)+(.04 if d.get("candidate_source")!="retrieved" else 0))
    return {"rank":None,"internal_table_uid":d["internal_table_uid"],"document_id":d.get("document_id"),"ticker":d.get("ticker"),"report_year":d.get("report_year"),"scope":d.get("scope"),"local_ordinal":a.get("local_ordinal"),"page_no":a.get("page_no"),"lexical_rank":d.get("lexical_rank"),"dense_rank":d.get("dense_rank"),"fused_score":float(d.get("fused_score") or 0),"reranker_score":d.get("reranker_score"),"original_retrieval_rank":d.get("original_retrieval_rank"),"candidate_source":d.get("candidate_source"),"parent_retrieval_rank":d.get("parent_retrieval_rank"),"review_score":score,"preview":d.get("preview"),**m,**pr}
def table(a): return {"internal_table_uid":a["uid"],"document_id":a.get("document_id"),"ticker":a.get("ticker"),"report_year":a.get("report_year"),"scope":a.get("scope"),"local_ordinal":a.get("local_ordinal"),"page_no":a.get("page_no"),"unit_hint":a.get("unit_hint"),"context_before":a.get("context_before") or "","headers":json.loads(a.get("headers_json") or "[]"),"rows":rows(a),"structure_version":int(a.get("structure_version") or 1),"context_schema_version":int(a.get("context_schema_version") or 1),"column_labels":json.loads(a.get("headers_json") or "[]"),"header_row_indices":jfield(a,"header_row_indices_json",[]),"table_function":jfield(a,"table_function_json",{}),"table_section":jfield(a,"table_section_json",{}),"table_purpose":jfield(a,"table_purpose_json",{}),"context_trace":jfield(a,"context_trace_json",{}),"structure_quality":jfield(a,"structure_quality_json",{})}

def health(root,minn,dense):
    ar=root/"artifacts"; ap=ar/"table_assets.jsonl"; db=ar/"lexical_index.sqlite3"; up=ar/"dense_uids.jsonl"; ip=ar/"dense.index"; na=count(ap); nu=count(up); nl=0
    if db.is_file():
        c=sqlite3.connect(db); nl=int(c.execute("SELECT COUNT(*) FROM assets").fetchone()[0]); c.close()
    nd=0
    if dense and ip.is_file(): import faiss; nd=int(faiss.read_index(str(ip)).ntotal)
    q13=False
    if ap.is_file():
        for line in ap.open(encoding="utf-8"):
            if line.strip():
                x=json.loads(line)
                if x.get("ticker")=="SAB" and x.get("report_year")==2016 and x.get("scope")=="separate" and x.get("local_ordinal")==5:q13=True;break
    vb=na>=minn and na==nl and q13; vd=(nu==na and nd==na) if dense else True; return {"asset_count":na,"lexical_count":nl,"dense_uid_count":nu,"dense_ntotal":nd,"q13_oracle":q13,"valid_base":vb,"valid_dense":vd,"valid":vb and vd}

def main():
    a=args(); root=a.repo_root.resolve(); qp=a.questions if a.questions.is_absolute() else root/a.questions; cp=a.config if a.config.is_absolute() else root/a.config; out=a.output_dir.resolve()
    if out.exists() and any(out.iterdir()):
        if not a.force:raise RuntimeError(f"Output not empty: {out}")
        shutil.rmtree(out)
    out.mkdir(parents=True,exist_ok=True); h=health(root,a.min_asset_count,not a.no_dense); print(json.dumps(h,indent=2));
    if not h["valid"]:raise RuntimeError("Artifact integrity gate failed")
    pipe=ViFinQARetrievalPipeline(ProjectPaths.from_repository(root),load_config(cp),use_dense=not a.no_dense); items=[]; cache={}; errs=[]; rec=0
    for pos,it in enumerate(loadj(qp),1):
        qid=int(it["id"]); q=str(it["question"]); fam=it.get("weak_family")
        try:
            res=pipe.retrieve(q,qid); plan=res["question_plan"]; metric=effective_metric(q,plan,fam); mt=tokens(metric); got=list(res.get("retrieved_tables") or [])[:a.top_k]; ds={}; aset={}
            for r,c in enumerate(got,1):
                uid=str(c["internal_table_uid"]); aa=pipe.store.get_asset(uid)
                if aa is None:raise RuntimeError(f"UID not found: {uid}")
                ds[uid]=desc_retr(c,r); aset[uid]=aa; ro=ov(rowall(aa),mt); co=ov(str(aa.get("context_before") or ""),mt)
                if a.neighbor_radius>0 and co>=a.context_recovery_threshold and co>=ro+.20:
                    for n in previous(pipe.store,aa,a.neighbor_radius):
                        nu=str(n["uid"]); no=ov(rowall(n),mt)
                        if nu not in ds and no>=max(.28,ro+.15): ds[nu]=desc_adj(n,r); aset[nu]=n; rec+=1
            cs=[record(d,aset[u],q,plan,metric) for u,d in ds.items()]; cs.sort(key=lambda x:(x["review_score"],x["evidence_features"]["metric_overlap"],-(x.get("original_retrieval_rank") or 10**9)),reverse=True); cs=cs[:a.max_review_candidates]
            for r,c in enumerate(cs,1): c["rank"]=r; u=c["internal_table_uid"]; cache.setdefault(u,table(aset[u]))
            items.append({"id":qid,"question":q,"weak_family":fam,"question_plan":plan,"effective_metric":metric,"retrieval_candidate_count":len(got),"candidate_count":len(cs),"recovered_adjacent_count":sum(c["candidate_source"]!="retrieved" for c in cs),"candidates":cs}); print(f"[{pos}] Q{qid}: {len(got)} -> {len(cs)}")
        except Exception as e:
            errs.append({"id":qid,"question":q,"error":repr(e)}); print("ERROR",qid,e)
            if not a.allow_errors:break
    rp,tp,ep=out/"review_items.jsonl",out/"tables.jsonl",out/"errors.jsonl"; writej(rp,items); writej(tp,cache.values()); writej(ep,errs)
    commit=subprocess.check_output(["git","-C",str(root),"rev-parse","HEAD"],text=True).strip(); man={"schema_version":3,"created_at_utc":datetime.now(timezone.utc).isoformat(),"git_commit":commit,"config_path":str(cp),"config_sha256":sha(cp),"questions_path":str(qp),"questions_sha256":sha(qp),"question_count":len(loadj(qp)),"review_item_count":len(items),"unique_table_count":len(cache),"retrieval_top_k":a.top_k,"max_review_candidates":a.max_review_candidates,"neighbor_radius":a.neighbor_radius,"recovered_adjacent_candidates":rec,"use_dense":not a.no_dense,"artifact_health":h,"error_count":len(errs)}; mp=out/"manifest.json"; mp.write_text(json.dumps(man,ensure_ascii=False,indent=2),encoding="utf-8"); (out/"SHA256SUMS").write_text("".join(f"{sha(out/n)}  {n}\n" for n in ["manifest.json","review_items.jsonl","tables.jsonl","errors.jsonl"]),encoding="utf-8")
    ar=out.parent/f"{out.name}.tar.gz"; ar.unlink(missing_ok=True)
    with tarfile.open(ar,"w:gz") as t:
        for n in ["manifest.json","review_items.jsonl","tables.jsonl","errors.jsonl","SHA256SUMS"]:t.add(out/n,arcname=n)
    sp=ar.with_suffix(ar.suffix+".sha256"); sp.write_text(f"{sha(ar)}  {ar.name}\n",encoding="utf-8"); print("Archive:",ar,"Recovered:",rec)
    if errs and not a.allow_errors:raise RuntimeError(f"Export errors: {len(errs)}")
if __name__=="__main__":main()
