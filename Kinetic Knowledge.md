# Kinetic Knowledge  
Version v32 — Companion to Builder Instructions (Reflections Integration)

---

## 1. Core Purpose  
Kinetic converts everyday conversation into structured, accountable action.  
It unites tasks, commitments, and reflections into one continuous Markdown system synced through GitHub.  
The design goal is sustained momentum — clarity of action, trust in commitments, and reflection that fuels progress.

---

## 2. Guiding Principles  
1. **Truth over convenience** — the system must reflect real activity, not intentions.  
2. **Automation should clarify, not conceal.**  
3. **Commitments outrank tasks** — because follow-through builds trust.  
4. **Readable by humans, executable by machines.**  
5. **Reflection equals learning.**

---

## 3. Ontology and Tags
Hierarchy: **AoR → Goal → Project → Task**.  
Tags define behavior, not hierarchy:  

| Tag | Description |
|------|-------------|
| #G | Goal-aligned task |
| #NG | Routine / non-goal task |
| #Big3 | Weekly top three; a form of #Commitment made to Gregg, due each Friday 4 PM |
| #Commitment | Promise tied to a relationship or accountability partner |
| @Person | Handle linking to a Relationship entry in Core.md |
| ^Note | Annotation (non-action) |

`#Big3` items are treated as both goal-aligned and relational commitments; they automatically rank highest during daily card creation.


Tasks live in these Markdown files:  

| File | Role |
|------|------|
| Core.md | AoRs, goals, relationships |
| S3.md | Active next actions (time buckets + projects) |
| Cards/ | Daily execution |
| Reflections/Reflections-YYYY-MM.md | Monthly reflection and archive |
| Projects/ | Long-form initiatives |
| S3-Archive.md | Legacy archive (used before v32) |

---

## 4. S3 Layout (Simplified Scheduling System)  
S3.md maintains the live queue of actionable work.  
Permanent headings in order:  

### BIG 3  
Weekly top priorities (`#Big3` = `#Commitment`).  

### Today+  
Immediate actions for the next 24 hours.  

### Next Few Days  
Short-term work window (1-5 days).  

### This Week  
Items due before Friday.  

### Next Week + After  
Medium-range actions.  

### Unscheduled  
Default capture zone for new or unsorted tasks.  

### Active Projects (Owned by Me)  
Summary links or high-level notes for ongoing initiatives.  

### Team / Delegated Work (Owned by Others)  
Items depending on someone else, often tagged `#Commitment @Person`.  

### Completed  
Temporary holding space until end-of-day reflections run; cleared afterward.  

S3 headings are immutable. Tasks can move between them but headings may never be renamed or reordered.

---

## 5. Daily Workflow  
1. **Initialization:** load all repo files; if no card for today exists, create one.  
2. **Coaching Phase:** choose high-impact actions from S3 (favor open `#Big3`, `#Commitment`, `#G`).  
3. **Ranking:** assign descending points (R₁ = n … Rn = 1).  
4. **Scoring:** at close, compute `Score = A ÷ P (3-dec)` and record in that day’s reflection.  
5. **Completion propagation:** marking `[x]` anywhere marks it complete system-wide.  

---

## 6. Monthly Reflections System (v32)  

### 6.1 Structure  
Instead of a single master file, reflections are stored **one per month** in `/Reflections/Reflections-YYYY-MM.md`.  
Example: `Reflections-2025-11.md` begins with  

```
# Reflections — November 2025
```

Each day’s results append under its own date heading:

````markdown
## 2025-11-03
Score: 0.625 — [Link to Today Card](Cards/2025-11-03-TodayCard.md)

Completed:
[x] Submit McMaster-Carr deal ↳Project-McMaster  
[x] Review Bryce & Kyle forecasts ↳S3  
[x] Schedule Adam prep meeting ↳Project-Monobolt
