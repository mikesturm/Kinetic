#!/usr/bin/env python
import csv, os, re, datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEDGER_PATH = os.path.join(ROOT, "ledger", "ledger.csv")
VIEWS_DIR = os.path.join(ROOT, "views")

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

def next_id(rows,obj_type):
    prefix=ID_PREFIX.get(obj_type,"X")
    max_num=0
    for r in rows:
        rid=r.get("id","")
        if rid.startswith(prefix):
            try:
                num=int(rid[len(prefix):])
                max_num=max(max_num,num)
            except: pass
    return f"{prefix}{max_num+1}"

def parse_projects_view(rows):
    path=os.path.join(VIEWS_DIR,"Projects.md")
    if not os.path.exists(path): return rows
    with open(path,encoding="utf-8") as f: lines=f.readlines()
    rows_by_id={r["id"]:r for r in rows}
    now=datetime.datetime.utcnow().isoformat()
    current_project=None
    for line in lines:
        s=line.strip()
        if s.startswith("## "):
            m=re.match(r"^##\s+(.+?)\s+\(([APTG CNR]\d+)\)\s*$",s)
            if m:
                name=m.group(1).strip()
                pid=m.group(2).strip()
                current_project=pid
                if pid in rows_by_id:
                    pr=rows_by_id[pid]; pr["text"]=name; pr["updated_at"]=now
                else:
                    rows.append({
                        "id":pid,"type":"project","text":name,"status":"open","bucket":"","parent_id":"","goal_ids":"","aor_id":"","people":"","notes":"","created_at":now,"updated_at":now,"target_date":"","due_date":""
                    })
                    rows_by_id[pid]=rows[-1]
            continue
        if s.startswith("- [") and current_project:
            checked=s.startswith("- [x]") or s.startswith("- [X]")
            m=re.match(r"^- \[[ xX]\]\s+(.+?)(\(([APTG CNR]\d+)\))?\s*$",s)
            if not m: continue
            text=m.group(1).strip(); tid=m.group(3).strip() if m.group(3) else None
            if tid and tid in rows_by_id:
                tr=rows_by_id[tid]
                tr["text"]=text; tr["parent_id"]=current_project
                tr["status"]="complete" if checked else (tr.get("status") or "open")
                tr["updated_at"]=now
            else:
                new_id=next_id(rows,"task")
                rows.append({
                    "id":new_id,"type":"task","text":text,"status":"complete" if checked else "open","bucket":"","parent_id":current_project,"goal_ids":"","aor_id":"","people":"","notes":"","created_at":now,"updated_at":now,"target_date":"","due_date":""
                })
                rows_by_id[new_id]=rows[-1]
    return rows

def generate_projects_view(rows):
    projects=[r for r in rows if r.get("type")=="project"]
    tasks=[r for r in rows if r.get("type")=="task"]
    tasks_by_project={}
    for t in tasks:
        pid=t.get("parent_id","")
        if not pid: continue
        tasks_by_project.setdefault(pid,[]).append(t)
    lines=["# Projects",""]
    for p in sorted(projects,key=lambda r:r.get("text","").lower()):
        pid=p["id"]
        lines.append(f"## {p['text']} ({pid})")
        proj_tasks=tasks_by_project.get(pid,[])
        if not proj_tasks:
            lines.append("- (no tasks yet)")
        else:
            for t in sorted(proj_tasks,key=lambda r:r.get("text","").lower()):
                checked="x" if t.get("status")=="complete" else " "
                lines.append(f"- [{checked}] {t['text']} ({t['id']})")
        lines.append("")
    with open(os.path.join(VIEWS_DIR,"Projects.md"),"w",encoding="utf-8") as f:
        f.write("\n".join(lines))

def generate_goals_view(rows):
    goals=[r for r in rows if r.get("type")=="goal"]
    projects=[r for r in rows if r.get("type")=="project"]
    lines=["# Goals",""]
    for g in sorted(goals,key=lambda r:r.get("text","").lower()):
        gid=g["id"]
        lines.append(f"## {g['text']} ({gid})")
        g_projects=[p for p in projects if gid in (p.get("goal_ids","") or "")]
        lines.append("Projects:")
        if g_projects:
            for p in g_projects:
                lines.append(f"- {p['text']} ({p['id']})")
        else:
            lines.append("- (none yet)")
        lines.append("")
    with open(os.path.join(VIEWS_DIR,"Goals.md"),"w",encoding="utf-8") as f:
        f.write("\n".join(lines))

def generate_aors_view(rows):
    aors=[r for r in rows if r.get("type")=="aor"]
    projects=[r for r in rows if r.get("type")=="project"]
    lines=["# AORs",""]
    for a in sorted(aors,key=lambda r:r.get("text","").lower()):
        aid=a["id"]
        lines.append(f"## {a['text']} ({aid})")
        a_projects=[p for p in projects if p.get("aor_id","")==aid]
        lines.append("Projects:")
        if a_projects:
            for p in a_projects:
                lines.append(f"- {p['text']} ({p['id']})")
        else:
            lines.append("- (none yet)")
        lines.append("")
    with open(os.path.join(VIEWS_DIR,"AORs.md"),"w",encoding="utf-8") as f:
        f.write("\n".join(lines))

def generate_people_view(rows):
    people_map={}
    for r in rows:
        people=(r.get("people","") or "").strip()
        if not people: continue
        for p in [x.strip() for x in people.split(",") if x.strip()]:
            people_map.setdefault(p,[]).append(r)
    lines=["# People",""]
    for person in sorted(people_map.keys()):
        lines.append(f"## {person}")
        for obj in people_map[person]:
            lines.append(f"- {obj['type']}: {obj['text']} ({obj['id']})")
        lines.append("")
    with open(os.path.join(VIEWS_DIR,"People.md"),"w",encoding="utf-8") as f:
        f.write("\n".join(lines))

def generate_today_snapshot_view(rows):
    today=[r for r in rows if r.get("bucket")=="today" and r.get("status")!="deleted"]
    completed=[r for r in rows if r.get("status")=="complete"]
    lines=["# Today Snapshot",""]
    lines.append("## Today Tasks")
    if today:
        for t in today:
            lines.append(f"- [ ] {t['text']} ({t['id']})")
    else:
        lines.append("- (none)")
    lines.append("")
    lines.append("## Completed (all)")
    if completed:
        for t in completed:
            lines.append(f"- [x] {t['text']} ({t['id']})")
    else:
        lines.append("- (none)")
    with open(os.path.join(VIEWS_DIR,"Today_Snapshot.md"),"w",encoding="utf-8") as f:
        f.write("\n".join(lines))

def main():
    rows=load_ledger()
    if not rows:
        print("Ledger empty; nothing to sync."); return
    rows=parse_projects_view(rows)
    generate_projects_view(rows)
    generate_goals_view(rows)
    generate_aors_view(rows)
    generate_people_view(rows)
    generate_today_snapshot_view(rows)
    write_ledger(rows)
    print("Views synced.")

if __name__=="__main__":
    main()
