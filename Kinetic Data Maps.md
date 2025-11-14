## Data Lineage Diagram
` 
┌───────────────────────── LEDGER COLUMN DATA FLOW ─────────────────────────┐

 Object ID ─────────────────────────────┐
   • Created by workflow.py                           │
   • S3.md / Projects.md / Project files           |
   • Today Cards                                             │
   ROUND-TRIP: Yes (Markdown ↔ Ledger)  ▼

 Canonical Name ─────────────── Markdown → workflow.py → Ledger
   • Never pushed back to Markdown
   ONE-WAY: Markdown → Ledger

 Type (Task/Project/AoR/Goal) ─ Markdown → workflow.py → Ledger
   ONE-WAY

 Parent Object ID ───────────── Project Markdown → workflow.py → Ledger
   ONE-WAY

 Current State (Open/Complete) ─ Markdown → workflow.py → Ledger
   • S3 checkboxes
   • Project file checkboxes
   • Today cards
   ONE-WAY

 Tags ───────────────────────── Markdown → Ledger
   • Manual tags (#Big3, #Today)
   • S3 tags (S3-X) are ROUND-TRIP
   PARTIAL ROUND-TRIP:
      - S3 bucket tags ↔
      - All others Markdown → Ledger (no pushback)

 File Location ─────────────── Markdown source → Ledger
   ONE-WAY

 Notes ───────────────────────────┐
   Markdown (/Projects/*.md)      │
   Markdown (Projects.md)         │
   workflow.py merges ↑ ↓         │
   Ledger stores                  ▼
   ROUND-TRIP: FULL (bidirectional)

 People ─────────────── future @mention parser
   Currently NONE

 Created At / Last Modified ─── future
   Not implemented

└──────────────────────────────────────────────────────────────────────────┘`

## Round-Trip Matrix Grid
Legend:
  →  one-way, Markdown → Ledger
  ←  one-way, Ledger → Markdown
  ↔  full round-trip
  ∅  no sync

# Kinetic Round-Trip Sync Matrix

| Ledger Column         | S3.md | Projects.md | /Projects/*.md | Today Cards | Views/ | Notes                                           |
| --------------------- | :---: | :---------: | :------------: | :---------: | :----: | ----------------------------------------------- |
| **Object ID**         |   ↔   |      ↔      |       ↔        |      ↔      |   →    | round-trip identity                             |
| **Canonical Name**    |   →   |      →      |       →        |      →      |   →    | ledger does not overwrite markdown              |
| **Type**              |   →   |      →      |       →        |      →      |   →    | task/project inferred from markdown             |
| **Parent Object ID**  |   ∅   |      ∅      |       →        |      ∅      |   →    | parent derived only from project file hierarchy |
| **Current State**     |   →   |      →      |       →        |      →      |   →    | checkbox-driven                                 |
| **Tags (manual)**     |   →   |      →      |       →        |      →      |   →    | manual tags only flow into ledger               |
| **Tags (S3 buckets)** |   ↔   |      →      |       →        |      →      |   →    | bucket tags round-trip with S3.md               |
| **File Location**     |   →   |      →      |       →        |      →      |   →    | ledger only logs origin path                    |
| **Notes**             |   ↔   |      ↔      |       ↔        |      ∅      |   →    | full bidirectional sync for project notes       |
| **People**            |   ∅   |      ∅      |       ∅        |      ∅      |   →    | future @mention support                         |
| **Created At**        |   ∅   |      ∅      |       ∅        |      ∅      |   ∅    | not implemented yet                             |
| **Last Modified At**  |   ∅   |      ∅      |       ∅        |      ∅      |   ∅    | not implemented yet                             |



## End-to-End Pipeline
 Markdown Sources
 (S3.md, Projects.md, /Projects/*.md, Cards/*.md)
        │
        ▼
 workflow.py
 ┌───────────────────────────────────────────────────────────┐
 │ Object ID  ↔  • Assign, maintain                                                        │
 │ Canonical Name → • Extract from task/project text                          │
 │ Type          → • Deduce (Task/Project)                                                │
 │ Parent ID     → • Derived from markdown hierarchy                         │
 │ State         → • Checkbox evaluation                                                 │
 │ Tags          → • Manual tags + Today + partial S3↔                          │
 │ File Location → • Set to originating file                                            │
 │ Notes         ↔ • Merge with Projects.md + project files                    │
 └───────────────────────────────────────────────────────────┘
        │
        ▼
 Kinetic-ID-Index.csv  (SSOT)
        │
        ▼
 compiler.py
   • Reads all columns
   • Emits structured, stable datasets
        │
        ▼
 /Views/  (input for GPT runtime)
