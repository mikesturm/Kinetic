# Kinetic Knowledge Specification

### Companion to the Kinetic Constitutional Charter

---

## Purpose

This document defines how Kinetic interprets, validates, and applies the principles of the Charter in practice.  
It provides both the logic and the ethos of Kinetic’s operations.

---

## Core Definitions

**Object** — Any unit of tracked intent: Task, Project, Goal, or Area of Responsibility.  
**Ledger** — Any Markdown document within the system that houses Objects.  
**State** — The current file in which an Object resides.  
**Canonical Name** — The immutable name fixed at Object creation.  
**Colloquial Name** — The mutable, visible name used in daily work.  
**Checksum** — A cryptographic hash verifying the Canonical Name’s integrity.  
**Drift** — Divergence between Canonical and Colloquial semantics over time.  

---

## The Three Laws of Kinetic Cognition

1. **Truth** — No deletion or falsification. Every Object remains discoverable and historically valid.  
2. **Continuity** — All Objects maintain lineage, from creation to closure.  
3. **Comprehensibility** — Every state and rule is transparent to human eyes.

---

## Verification Workflow

**Upon Repository Fetch:**

1. Download and verify SHA/Base64 of all tracked files.  
2. Parse Objects, validate parent-child hierarchy.  
3. Compute checksums for Canonical Names; compare to previous logs.  
4. Audit for orphaned entries (missing ID or Canonical).  
5. Conduct drift analysis (Canonical ↔ Colloquial similarity).  
6. Log findings in `/Kinetic-Diagnostics.md`.  

---

## Divergence Audit Logic

1. Compute text similarity using cosine metric or equivalent.  
2. If similarity < 0.75:  
   - Flag the Object for review.  
   - Prompt user: “Issue new Object ID?”  
3. If accepted, create new ID and carry over metadata; archive the prior ID as “superseded.”  

---

## Colloquial Naming Generation

**Pattern:** `{Verb-Noun-Descriptor(1-3)}`  
Example: `Call-Dave-Autocar-Margin`  
If duplicate found:  

- In `Deleted.md` → prompt resurrection.  
- In active Ledger → append incremental suffix.  

---

## Integrity Safeguards

- Canonical Names sealed and validated via checksum.  
- No write operations allowed without verified SHA.  
- No Object ID may exist without a corresponding Canonical Name.  
- No Object may exist in multiple files concurrently.  
- Subtasks must reference valid parents.  

---

## Daily & Reflection Routines

**Morning Routine:**

- Run verification and divergence audit.  
- Identify new or orphaned tasks.  
- Generate TodayCard recommendations.  

**Evening Routine:**

- Compile daily change summary.  
- Record completions, return open tasks.  
- Update `/Kinetic-ID-Index.md`.  
- Verify Ledger integrity post-sync.  

---

## Integrity Reports

Kinetic maintains:

1. `/Kinetic-ID-Index.md` — master list of all IDs and states.  
2. `/Kinetic-Diagnostics.md` — audit log of issues, drift, and anomalies.  
3. `/Deleted.md` — permanent ledger of retired Objects.  

---

## Philosophy of Use

Kinetic is not automation — it is **augmented cognition**.  
Its purpose is to enable trust in one’s own recorded history.  
It embodies four guiding virtues:

| Virtue         | Description                                                    |
| -------------- | -------------------------------------------------------------- |
| **Honesty**    | Every change is explicit, never silent.                        |
| **Continuity** | All actions form an unbroken chain of provenance.              |
| **Legibility** | Human understanding precedes machine optimization.             |
| **Reflection** | The end of each day is an act of meaning, not just data entry. |

---

## Endnote

Kinetic Knowledge acts as both manual and conscience for the system.  
It ensures that every implementation remains faithful to the Charter — a harmony of logic, language, and memory.

> *“The record is not what was done; it is what remains true.”*

---

### Markdown Parsing Logic — Fewest-Hashtags Rule

**Purpose:**  
To infer hierarchical structure, object type, and relational linkage from Markdown heading depth in project-related files, ensuring perfect bidirectional consistency between Markdown and the Kinetic Object Ledger (CSV).

**Overview:**  
Heading depth (the number of `#` symbols) determines the hierarchical level of a Project and its subprojects. The parser interprets relative heading depth among headings that contain or precede tasks to infer Object IDs and parent–child relationships automatically.

**Rule Summary:**  
1. **Heading Detection:** Any line beginning with `##` or deeper is treated as a structural heading.  
2. **Minimum Depth:** The smallest number of `#` marks among headings that contain or precede tasks defines the root subproject level (`.1`, `.2`, etc.).  
3. **Nested Depth:** Each additional heading level adds one decimal place (`P3.1.1`, `P3.1.1.1`, etc.).  
4. **Sibling Sequencing:** Sibling headings at the same depth increment the final decimal (`P3.1`, `P3.2`, `P3.3`, etc.).  
5. **Task Inheritance:** Tasks inherit the Object ID of the most recent structural heading above them.  
6. **Parent Assignment:** Subprojects inherit the Object ID of their immediate shallower heading as their `Parent Object ID`.  
7. **Task State:** Tasks marked `[ ]` are “Active”; `[x]` are “Completed.”  

**Example:**

```markdown
## Phase 1 – Design
### UX Wireframes
- [x] Finalize layout
- [ ] Approve colors

## Phase 2 – Build
### Backend
- [ ] Implement endpoints
- [x] Set up database
```

**Produces:**

| Object ID | Type | Colloquial Name | Parent Object ID |
|------------|------|------------------|------------------|
| P3 | Project | Attachment A Rollout | — |
| P3.1 | Project | Phase 1 – Design | P3 |
| P3.1.1 | Project | UX Wireframes | P3.1 |
| T41 | Task | Finalize layout | P3.1.1 |
| T42 | Task | Approve colors | P3.1.1 |
| P3.2 | Project | Phase 2 – Build | P3 |
| P3.2.1 | Project | Backend | P3.2 |
| T43 | Task | Implement endpoints | P3.2.1 |
| T44 | Task | Set up database | P3.2.1 |

**Notes:**  
- Headings without tasks or descendant tasks are ignored.  
- Tasks may appear directly under the file-level project (no heading); these inherit the file’s project ID.  
- Tag and person detection follows standard Markdown inline syntax.  
- This rule applies recursively to any heading depth supported by the Markdown file.
