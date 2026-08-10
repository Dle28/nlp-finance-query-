#!/usr/bin/env python3
"""ViFinQA review bundle v3: recover adjacent tables and ground evidence rows."""
from __future__ import annotations
import argparse, hashlib, json, os, re, shutil, sqlite3, subprocess, tarfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from bs4 import BeautifulSoup
from finance_query.binding import row_label
from finance_query.config import ProjectPaths
from finance_query.financial_metrics import fold_text, infer_formula_spec
from finance_query.pipeline import ViFinQARetrievalPipeline, load_config

TOK = re.compile(r"[A-Za-zÀ-ỹ0-9%]+", re.UNICODE)
NUM = re.compile(r"^[\s()\-+\d.,%/]+$")
STOP = {"cua","của","la","là","bao","nhieu","nhiêu","vao","vào","tai","tại","trong","cho","theo","den","đến","ngay","ngày","thang","tháng","nam","năm","cuoi","cuối","dau","đầu","va","và","mot","một","cac","các","duoc","được","co","có","dong","đồng","trieu","triệu","ty","tỷ","phan","phần","tram","trăm","công","cong","ctcp"}
ENTITY = re.compile(r"\s+của\s+(?:công\s+ty\s+mẹ|tổng\s+công\s+ty|ctcp|công\s+ty\s+cổ\s+phần|ngân\s+hàng|tập\s+đoàn)\b", re.I)
END = re.compile(r"cuối\s+năm|31\s*[/.-]\s*12|31\s+tháng\s+12|đến\s+ngày\s+31|tại\s+ngày\s+31", re.I)
START = re.compile(r"đầu\s+năm|(?:^|\D)0?1\s*[/.-]\s*0?1(?:\D|$)", re.I)

FORMULA_BUNDLE_SUPPORT_POLICY = "resolved_operand_entity_year_or_following_statement_function_v1"
FORMULA_BUNDLE_SUPPORT_CANDIDATE_SOURCE = "formula_metadata_support_v1"
DIRECT_BUNDLE_SUPPORT_POLICY = "resolved_direct_entity_year_exact_row_phrase_v1"
DIRECT_BUNDLE_SUPPORT_CANDIDATE_SOURCE = "direct_metadata_support_v1"


def args():
    p=argparse.ArgumentParser(); p.add_argument("--questions",type=Path,default=Path("data/labels/annotation_questions_60.jsonl")); p.add_argument("--config",type=Path,default=Path("configs/annotation_baseline.yaml")); p.add_argument("--output-dir",type=Path,required=True); p.add_argument("--repo-root",type=Path,default=Path.cwd()); p.add_argument("--top-k",type=int,default=20); p.add_argument("--max-review-candidates",type=int,default=40); p.add_argument("--max-formula-support-tables",type=int,default=128,help="Maximum metadata-selected statement tables added per question for formula evidence; they are not review UI candidates."); p.add_argument("--max-direct-support-tables",type=int,default=24,help="Maximum exact-row-phrase source tables added per direct lookup; they are not review UI candidates."); p.add_argument("--neighbor-radius",type=int,default=1); p.add_argument("--context-recovery-threshold",type=float,default=.45); p.add_argument("--min-asset-count",type=int,default=100000); p.add_argument("--no-dense",action="store_true"); p.add_argument("--force",action="store_true"); p.add_argument("--allow-errors",action="store_true"); return p.parse_args()

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

def source_revision(root):
    """Return an auditable source revision before the costly bundle loop.

    A normal clone provides ``.git``.  Kaggle's private source snapshot does
    not include Git metadata, so its bootstrap must explicitly supply the
    immutable revision through ``VIFINQA_SOURCE_REVISION``.  Refuse an
    unversioned snapshot early rather than failing after generating a full
    bundle.
    """
    snapshot_revision=os.environ.get("VIFINQA_SOURCE_REVISION", "").strip()
    if snapshot_revision:
        return snapshot_revision
    if not (root/".git").is_dir():
        raise RuntimeError(
            "Source has no .git directory. Set VIFINQA_SOURCE_REVISION to an immutable source revision."
        )
    return subprocess.check_output(["git","-C",str(root),"rev-parse","HEAD"],text=True).strip()

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


def _support_requests(formula):
    """Return only explicitly scoped statement requests from a formula template.

    A metadata support table is deliberately narrower than retrieval.  We add
    it only if the controlled formula names an entity, an operand year, and an
    allowed statement function.  Generic formula slots without a function
    constraint stay with normal retrieval; exporting every note for a ticker
    would create noise and would not establish evidence.
    """
    requests=[]
    for operand in formula.get("operands") or []:
        entity=str(operand.get("entity") or "").strip()
        functions={str(value) for value in operand.get("allowed_table_functions") or [] if str(value)}
        years=[]
        for value in operand.get("years") or []:
            try: years.append(int(value))
            except (TypeError,ValueError): pass
        if not entity or not years or not functions: continue
        requests.append({"entity":entity,"years":set(years),"functions":functions,"operand_id":str(operand.get("operand_id") or "")})
    return requests


def _formula_support_asset_rows(store, formula, plan):
    """Read a bounded metadata pool from the existing lexical artifact only.

    This is an ordinary SQLite metadata lookup.  It does not alter the corpus,
    lexical index, dense index, raw grids or retrieval scores.
    """
    requests=_support_requests(formula)
    if not requests: return [],requests
    tickers=sorted({request["entity"] for request in requests})
    report_years=sorted({year+delta for request in requests for year in request["years"] for delta in (0,1)})
    conditions=[f"ticker IN ({','.join('?' for _ in tickers)})",f"report_year IN ({','.join('?' for _ in report_years)})"]
    parameters=[*tickers,*report_years]
    expected_scope=str(plan.get("scope") or "")
    if expected_scope:
        conditions.append("scope = ?"); parameters.append(expected_scope)
    sql=("SELECT * FROM assets WHERE "+" AND ".join(conditions)+" ORDER BY ticker, scope, report_year, document_id, local_ordinal, uid")
    with store.connect() as connection:
        return [dict(row) for row in connection.execute(sql,parameters).fetchall()],requests


def formula_support_assets(formula, plan, assets, *, max_tables):
    """Select formula source tables from metadata with no text/OCR inference.

    The output retains every matching operand id per UID.  A table may support
    several slots, but its inclusion never makes it a positive review label or
    a candidate in the compact reviewer UI.
    """
    if max_tables < 1: raise ValueError("max_formula_support_tables must be positive")
    requests=_support_requests(formula)
    expected_scope=str(plan.get("scope") or "")
    selected={}
    for asset in assets:
        ticker=str(asset.get("ticker") or "")
        scope=str(asset.get("scope") or "")
        try: report_year=int(asset.get("report_year"))
        except (TypeError,ValueError): continue
        if expected_scope and scope!=expected_scope: continue
        kind=str((jfield(asset,"table_function_json",{}) or {}).get("kind") or "")
        operand_ids=[]
        for request in requests:
            if ticker!=request["entity"] or kind not in request["functions"]: continue
            if report_year not in request["years"] and report_year-1 not in request["years"]: continue
            operand_ids.append(request["operand_id"])
        if operand_ids:
            selected[str(asset["uid"])]={"asset":asset,"operand_ids":sorted(set(operand_ids))}
    ordered=sorted(selected.values(),key=lambda value:(str(value["asset"].get("ticker") or ""),str(value["asset"].get("scope") or ""),int(value["asset"].get("report_year") or 0),str(value["asset"].get("document_id") or ""),int(value["asset"].get("local_ordinal") or 0),str(value["asset"].get("uid") or "")))
    return ordered[:max_tables],len(ordered)


def add_formula_support_table(cache, support, *, question_id, formula_id):
    """Add immutable raw table content with auditable, UI-invisible provenance."""
    asset=support["asset"]; uid=str(asset["uid"])
    already_present=uid in cache
    source=cache.setdefault(uid,table(asset))
    inclusion=source.setdefault("bundle_inclusion",{})
    formula_support=inclusion.setdefault("formula_metadata_support",{"policy":FORMULA_BUNDLE_SUPPORT_POLICY,"question_ids":[],"formula_ids":[],"operand_ids":[]})
    for key,values in (("question_ids",[int(question_id)]),("formula_ids",[str(formula_id)]),("operand_ids",support["operand_ids"])):
        formula_support[key]=sorted(set(formula_support.get(key) or [])|set(values))
    return uid,not already_present


def _direct_metric_tokens(value):
    """Return source comparison tokens without using fuzzy semantic similarity."""
    return [token for token in fold_text(str(value or "")).split() if not token.isdecimal()]


def _direct_phrase_matches_metric(metric_tokens, row):
    """Locate a bounded exact metric phrase in a source row label.

    This is only a recall filter for a UI-invisible table inclusion.  The later
    V2 direct-evidence builder still requires an exact raw row identity and a
    canonical period-bound numeric cell before a machine-silver label exists.
    """
    if len(metric_tokens)<2: return False
    label_tokens=_direct_metric_tokens(row_label([str(value) for value in row]))
    return label_tokens==metric_tokens


def _direct_support_asset_rows(store, plan):
    if str(plan.get("family") or "")!="direct_lookup": return []
    tickers=sorted({str(value) for value in plan.get("tickers") or [] if str(value)})
    years=[]
    for value in plan.get("years") or []:
        try: years.append(int(value))
        except (TypeError,ValueError): pass
    if len(tickers)!=1 or not years: return []
    conditions=["ticker = ?",f"report_year IN ({','.join('?' for _ in sorted(set(years)))})"]
    parameters=[tickers[0],*sorted(set(years))]
    expected_scope=str(plan.get("scope") or "")
    if expected_scope:
        conditions.append("scope = ?"); parameters.append(expected_scope)
    sql=("SELECT * FROM assets WHERE "+" AND ".join(conditions)+" ORDER BY ticker, scope, report_year, document_id, local_ordinal, uid")
    with store.connect() as connection:
        return [dict(row) for row in connection.execute(sql,parameters).fetchall()]


def direct_support_assets(plan, metric, assets, *, max_tables):
    """Select source tables containing the literal planned direct metric phrase.

    No score, generated summary, OCR repair or answer is inferred here.  The
    raw grid is merely made available to the post-export V2 verifier, which
    decides separately whether its row and period cell are trustworthy.
    """
    if max_tables<1: raise ValueError("max_direct_support_tables must be positive")
    metric_tokens=_direct_metric_tokens(metric)
    selected=[]
    for asset in assets:
        matched_rows=[index for index,row in enumerate(rows(asset)) if _direct_phrase_matches_metric(metric_tokens,row)]
        if matched_rows:
            selected.append({"asset":asset,"matching_row_indices":matched_rows})
    selected.sort(key=lambda value:(str(value["asset"].get("scope") or ""),int(value["asset"].get("report_year") or 0),str(value["asset"].get("document_id") or ""),int(value["asset"].get("local_ordinal") or 0),str(value["asset"].get("uid") or "")))
    return selected[:max_tables],len(selected)


def add_direct_support_table(cache, support, *, question_id, metric):
    """Record a direct-lookup raw source table without adding a UI candidate."""
    asset=support["asset"]; uid=str(asset["uid"]); already_present=uid in cache
    source=cache.setdefault(uid,table(asset))
    inclusion=source.setdefault("bundle_inclusion",{})
    direct_support=inclusion.setdefault("direct_metadata_support",{"policy":DIRECT_BUNDLE_SUPPORT_POLICY,"question_ids":[],"effective_metrics":[],"matching_row_indices":[]})
    direct_support["question_ids"]=sorted(set(direct_support.get("question_ids") or [])|{int(question_id)})
    direct_support["effective_metrics"]=sorted(set(direct_support.get("effective_metrics") or [])|{str(metric)})
    direct_support["matching_row_indices"]=sorted(set(direct_support.get("matching_row_indices") or [])|set(support["matching_row_indices"]))
    return uid,not already_present

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
    commit=source_revision(root)
    if out.exists() and any(out.iterdir()):
        if not a.force:raise RuntimeError(f"Output not empty: {out}")
        shutil.rmtree(out)
    out.mkdir(parents=True,exist_ok=True); h=health(root,a.min_asset_count,not a.no_dense); print(json.dumps(h,indent=2));
    if not h["valid"]:raise RuntimeError("Artifact integrity gate failed")
    if a.max_formula_support_tables < 1 or a.max_direct_support_tables < 1:
        raise ValueError("formula/direct metadata support limits must be positive")
    pipe=ViFinQARetrievalPipeline(ProjectPaths.from_repository(root),load_config(cp),use_dense=not a.no_dense); items=[]; cache={}; errs=[]; rec=0; formula_support_tables=0; formula_support_requested=0; formula_support_truncated_questions=0; direct_support_tables=0; direct_support_requested=0; direct_support_truncated_questions=0
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
            formula=infer_formula_spec(q)
            support_added=0; support_available=0
            if formula is not None:
                pool,requests=_formula_support_asset_rows(pipe.store,formula,plan)
                if requests:
                    supports,support_available=formula_support_assets(formula,plan,pool,max_tables=a.max_formula_support_tables)
                    formula_support_requested+=support_available
                    if support_available>len(supports): formula_support_truncated_questions+=1
                    for support in supports:
                        _,was_added=add_formula_support_table(cache,support,question_id=qid,formula_id=str(formula.get("formula_id") or ""))
                        # A normal retrieval table can also be an exact formula
                        # source, but it is not a new immutable bundle table.
                        if was_added: support_added+=1
                    formula_support_tables+=support_added
            direct_added=0; direct_available=0
            direct_pool=_direct_support_asset_rows(pipe.store,plan)
            if direct_pool:
                direct_supports,direct_available=direct_support_assets(plan,metric,direct_pool,max_tables=a.max_direct_support_tables)
                direct_support_requested+=direct_available
                if direct_available>len(direct_supports): direct_support_truncated_questions+=1
                for support in direct_supports:
                    _,was_added=add_direct_support_table(cache,support,question_id=qid,metric=metric)
                    if was_added: direct_added+=1
                direct_support_tables+=direct_added
            items.append({"id":qid,"question":q,"weak_family":fam,"question_plan":plan,"effective_metric":metric,"retrieval_candidate_count":len(got),"candidate_count":len(cs),"recovered_adjacent_count":sum(c["candidate_source"]!="retrieved" for c in cs),"formula_metadata_support_table_count":support_added,"formula_metadata_support_available":support_available,"direct_metadata_support_table_count":direct_added,"direct_metadata_support_available":direct_available,"candidates":cs}); print(f"[{pos}] Q{qid}: {len(got)} -> {len(cs)} | formula {support_added}/{support_available} | direct {direct_added}/{direct_available}")
        except Exception as e:
            errs.append({"id":qid,"question":q,"error":repr(e)}); print("ERROR",qid,e)
            if not a.allow_errors:break
    rp,tp,ep=out/"review_items.jsonl",out/"tables.jsonl",out/"errors.jsonl"; writej(rp,items); writej(tp,cache.values()); writej(ep,errs)
    man={"schema_version":3,"created_at_utc":datetime.now(timezone.utc).isoformat(),"git_commit":commit,"config_path":str(cp),"config_sha256":sha(cp),"questions_path":str(qp),"questions_sha256":sha(qp),"question_count":len(loadj(qp)),"review_item_count":len(items),"unique_table_count":len(cache),"retrieval_top_k":a.top_k,"max_review_candidates":a.max_review_candidates,"neighbor_radius":a.neighbor_radius,"recovered_adjacent_candidates":rec,"formula_metadata_support":{"enabled":True,"policy":FORMULA_BUNDLE_SUPPORT_POLICY,"candidate_source":FORMULA_BUNDLE_SUPPORT_CANDIDATE_SOURCE,"max_tables_per_question":a.max_formula_support_tables,"new_unique_table_count":formula_support_tables,"matched_table_count_before_per_question_cap":formula_support_requested,"truncated_question_count":formula_support_truncated_questions,"ui_candidate_effect":"not_added_to_review_candidates","answer_eligible":False,"training_eligible":False},"direct_metadata_support":{"enabled":True,"policy":DIRECT_BUNDLE_SUPPORT_POLICY,"candidate_source":DIRECT_BUNDLE_SUPPORT_CANDIDATE_SOURCE,"max_tables_per_question":a.max_direct_support_tables,"new_unique_table_count":direct_support_tables,"matched_table_count_before_per_question_cap":direct_support_requested,"truncated_question_count":direct_support_truncated_questions,"ui_candidate_effect":"not_added_to_review_candidates","answer_eligible":False,"training_eligible":False},"use_dense":not a.no_dense,"artifact_health":h,"error_count":len(errs)}; mp=out/"manifest.json"; mp.write_text(json.dumps(man,ensure_ascii=False,indent=2),encoding="utf-8"); (out/"SHA256SUMS").write_text("".join(f"{sha(out/n)}  {n}\n" for n in ["manifest.json","review_items.jsonl","tables.jsonl","errors.jsonl"]),encoding="utf-8")
    ar=out.parent/f"{out.name}.tar.gz"; ar.unlink(missing_ok=True)
    with tarfile.open(ar,"w:gz") as t:
        for n in ["manifest.json","review_items.jsonl","tables.jsonl","errors.jsonl","SHA256SUMS"]:t.add(out/n,arcname=n)
    sp=ar.with_suffix(ar.suffix+".sha256"); sp.write_text(f"{sha(ar)}  {ar.name}\n",encoding="utf-8"); print("Archive:",ar,"Recovered:",rec)
    if errs and not a.allow_errors:raise RuntimeError(f"Export errors: {len(errs)}")
if __name__=="__main__":main()
