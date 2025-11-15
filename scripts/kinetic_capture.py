#!/usr/bin/env python
import csv, os, datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEDGER_PATH = os.path.join(ROOT, "ledger", "ledger.csv")
INBOX_PATH = os.path.join(ROOT, "surfaces", "Inbox.md")
ARCHIVE_PATH = os.path.join(ROOT, "archive", "Inbox-Archive.md")

ID_PREFIX = {
    "aor": "A",
    "project": "P",
    "goal": "G",
    "task": "T",
    "commitment": "C",
    "note": "N",
    "person": "R",
}

def load_ledger():
    rows = []
    if not os.path.exists(LEDGER_PATH):
        return rows
    with open(LEDGER_PATH, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append(r)
    return rows

def write_ledger(rows):
    if not rows:
        with open(LEDGER_PATH,"w",newline="",encoding="utf-8") as f:
            w=csv.writer(f); w.writerow(["id","type","text","status","bucket","parent_id","goal_ids","aor_id","people","notes","created_at","updated_at","target_date","due_date"])
        return
    fieldnames = rows[0].keys()
    with open(LEDGER_PATH,"w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=fieldnames); w.writeheader()
        for r in rows: w.writerow(r)

def next_id(rows,obj_type):
    prefix = ID_PREFIX.get(obj_type,"X")
    max_num = 0
    for r in rows:
        rid = r.get("id","")
        if rid.startswith(prefix):
            try:
                num=int(rid[len(prefix):])
                max_num=max(max_num,num)
            except: pass
    return f"{prefix}{max_num+1}"

def infer_type(line):
    t=line.strip()
    low=t.lower()
    if low.startswith("p:"): return "project", t[2:].strip()
    if low.startswith("g:"): return "goal", t[2:].strip()
    if low.startswith("c:"): return "commitment", t[2:].strip()
    if low.startswith("n:"): return "note", t[2:].strip()
    if low.startswith("a:"): return "aor", t[2:].strip()
    return "task", t

def capture():
    if not os.path.exists(INBOX_PATH): return
    with open(INBOX_PATH,encoding="utf-8") as f: lines=f.readlines()
    header, body = [], []
    in_header=True
    for line in lines:
        if in_header and (line.startswith("#") or not line.strip()):
            header.append(line)
        else:
            in_header=False
            body.append(line)
    entries=[]
    for line in body:
        s=line.strip()
        if not s or s.startswith("#"): continue
        obj_type,text=infer_type(s)
        entries.append((obj_type,text,s))
    if not entries: return
    rows=load_ledger()
    now=datetime.datetime.utcnow().isoformat()
    for obj_type,text,raw in entries:
        new_id=next_id(rows,obj_type)
        rows.append({
            "id":new_id,
            "type":obj_type,
            "text":text,
            "status":"open",
            "bucket":"",
            "parent_id":"",
            "goal_ids":"",
            "aor_id":"",
            "people":"",
            "notes":"",
            "created_at":now,
            "updated_at":now,
            "target_date":"",
            "due_date":""
        })
        print(f"Captured {obj_type} -> {new_id}: {text}")
    write_ledger(rows)
    os.makedirs(os.path.dirname(ARCHIVE_PATH),exist_ok=True)
    with open(ARCHIVE_PATH,"a",encoding="utf-8") as f:
        f.write(f"\n## Capture at {now}\n")
        for _,_,raw in entries:
            f.write(raw+"\n")
    with open(INBOX_PATH,"w",encoding="utf-8") as f:
        for h in header: f.write(h)
        f.write("\n")

if __name__=="__main__":
    capture()
