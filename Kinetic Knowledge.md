# Kinetic Knowledge

---

### Markdown Parsing Logic — Fewest-Hashtags Rule

**Purpose:**  
To infer hierarchical structure, object type, and relational linkage from Markdown heading depth in project-related files, ensuring perfect bidirectional consistency between Markdown and the Kinetic Object Ledger (CSV).

**Rule Summary:**  
1. **Heading Detection:** Any line beginning with `##` or deeper is treated as a structural heading.  
2. **Minimum Depth:** The smallest number of `#` marks among headings that contain or precede tasks defines the root subproject level (`.1`, `.2`, etc.).  
3. **Nested Depth:** Each additional heading level adds one decimal place (`P3.1.1`, `P3.1.1.1`, etc.).  
4. **Sibling Sequencing:** Sibling headings at the same depth increment the final decimal (`P3.1`, `P3.2`, `P3.3`, etc.).  
5. **Task Inheritance:** Tasks inherit the Object ID of the most recent structural heading above them.  
6. **Parent Assignment:** Subprojects inherit the Object ID of their immediate shallower heading as their `Parent Object ID`.  
7. **Task State:** Tasks marked `[ ]` are “Active”; `[x]` are “Completed.”  

---

### Canonical S3 Buckets and Derived Views

**Purpose:**  
Define the permanent canonical IDs and display mappings for the Simplified Scheduled System (S3) buckets.  
These IDs provide stable reference points for time-horizon tags used throughout the Kinetic system.

**Canonical Buckets:**

| Canonical ID | Colloquial (Display) Name | Notes |
|---------------|---------------------------|--------|
| **S3-1** | This Week’s Big Three | Top weekly focus items — limited to 3 highest-impact tasks. |
| **S3-2** | Today | Tasks or micro-goals for the current day. |
| **S3-3** | Next Few Days | Near-term priorities, typically 2–5 days out. |
| **S3-4** | This Week | Broader current-week objectives. |
| **S3-5** | Next Week + After | Tasks or projects scheduled for future cycles. |

**Derived Views (Non-Canonical):**

| View | Logic |
|------|--------|
| **Unscheduled** | Any task or project *without* an assigned S3 tag. |
| **Active Projects** | Derived dynamically from the Project Ledger (Projects marked “Active”). |

**Interpretation:**  
S3 headings are *tags*, not objects. Their purpose is to project task and project data into temporal views.  
Tasks inherit tags based on placement under an S3 heading. When regenerating Markdown, tasks are grouped under S3 sections by tag ID.  
S3 bucket IDs are immutable; display names may change freely without breaking tag mappings.
