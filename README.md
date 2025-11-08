# Kinetic
Files for my chat-based personal productivity system. More details...


## Automation

Run the reconciliation workflow whenever cards or the S3 planning board change
to keep `S3.md` and `Kinetic-ID-Index.csv` in sync:

```bash
python scripts/kinetic_workflow.py --run
```

The script will:

1. Import manual edits from the S3 bucket sections (creating new task objects
   when needed).
2. Add `#Today` tags for objects referenced on the most recent card and mark
   completed card items in the ledger.
3. Regenerate the managed sections in `S3.md` directly from the canonical
   ledger entries.


