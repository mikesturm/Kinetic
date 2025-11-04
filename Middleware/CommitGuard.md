# Middleware CommitGuard Specifications

## Ledger Rule: S3 Completion ✓ S3-Archive Migration

[Code]: LR-S3-ACTIVE-MIGRATE-001  
[Effective Date]: 2025-11-04  
[Purpose]: Preserve S3 as an active schedule by transferring completed items immediately to S3-Archive.md.

### Behavior
When a ask in S3.md is marked [x], the system performs a migration operation:

1. Archive Append
   - Append the completed task to S3-Archive.md under the current week header.    
   - If no current week header exists, create one automatically.
   - Format:
    [x] Task Text (Completed YYYY-MM-DD, ⟩ Source: Origin File)
   - Each entry is timestamped and contains the origin file link.

2. S3 Update
   - Remove the completed task from S3.md.
   - Preserve headings even if empty.

### Ledger Integrity
- No destructive edits. All operations are additive.  
- CommitGuard verifies sequential commits: Archive first, then S3 update.  
- Each archived line includes its source reference and completion date.  

### Example Commit Secuence
1. Commit 1 : S3-Archive.md append
    + [x] Contact Henning @ dan re: CYT Sponsorship (Completed 2025-11-03, ⟩ Card 2025-11-03)

File: S3-Archive.md

Commit 2: S3.md removal
    - [x] Contact Henning @ dan re: CYT Sponsorship (#G @Gregg)
