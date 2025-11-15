#!/usr/bin/env python
import csv, os, re, datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEDGER_PATH = os.path.join(ROOT, "ledger", "ledger.csv")
S3_PATH = os.path.join(ROOT, "surfaces", "S3.md")

BUCKET_HEADINGS = {
    "today": "today",
    "up next": "up_next",
    "next few days": "next_few_days",
    "this week": "this_week",
    "next week": "next_week",
    "after": "after",
}

def load_ledger():
    rows=[]
    if not os.path.exists(LEDGER_PATH): return rows
    with open(LEDGER_PATH,newline="",encoding="utf-8") as f:
        r=csv.DictReader(f)
        for row in r: rows.append(row)
    return rows

def write_ledger(rows):
    if not rows: return
    fields=rows[0].keys()
    with open(LEDGER_PATH,"w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=fields); w.writeheader()
        for r in rows: w.writerow(r)

def normalize_heading(t):
    t=t.strip().lower()
    for k in BUCKET_HEADINGS:
        if k in t: return BUCKET_HEADINGS[k]
    return None

def extract_id_and_text(line):
    m=re.search(r"\(([APTG CNR]\d+)\)",line)
    if m:
        oid=m.group(1).strip()
        text=re.sub(r"\([APTG CNR]\d+\)","",line).strip("- []x")
        return oid,text.strip()
    text=re.sub(r"^- \[[ xX]\]\s*","",line).strip()
    return None,text

def schedule():
    if not os.path.exists(S3_PATH): return
    rows=load_ledger()
    if not rows: return
    id_index={r["id"]:r for r in rows}
    with open(S3_PATH,encoding="utf-8") as f: lines=f.readlines()
    now=datetime.datetime.utcnow().isoformat()
    current_bucket=None
    for line in lines:
        s=line.strip()
        if s.startswith("## "):
            current_bucket=normalize_heading(s.lstrip("#").strip())
            continue
        if s.startswith("- [") and current_bucket is not None:
            checked=s.startswith("- [x]") or s.startswith("- [X]")
            oid,text=extract_id_and_text(s)
            target=None
            if oid and oid in id_index:
                target=id_index[oid]
            else:
                c=[r for r in rows if r.get("text","").strip()==text and r.get("status","")!="deleted"]
                if len(c)==1: target=c[0]
            if not target: continue
            if checked:
                target["status"]="complete"
                target["bucket"]=""
            else:
                target["status"]=target.get("status") or "open"
                target["bucket"]=current_bucket or ""
            target["updated_at"]=now
    write_ledger(rows)
    print("S3 scheduling sync complete.")

if __name__=="__main__":
    schedule()
