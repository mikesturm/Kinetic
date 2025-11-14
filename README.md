# Kinetic
Files for my chat-based personal productivity system. 

### Data Map
                        ┌──────────────────────────────┐
                         │        USER MARKDOWN          │
                         │  S3.md / Projects.md /        │
                         │  /Projects/*.md / Cards/*.md  │
                         └──────────────┬───────────────┘
                                        │
                                        ▼
                    ┌──────────────────────────────────────┐
                    │          workflow.py Parsers          │
                    │  - S3 Parser                          │
                    │  - Projects.md Parser                 │
                    │  - Project File Parser                │
                    │  - Today Card Parser                  │
                    └──────────────────────┬───────────────┘
                                           │
                                           ▼
              ┌──────────────────────────────────────────────────┐
              │            LEDGER NORMALIZATION ENGINE           │
              │          (Kinetic-ID-Index.csv Writer)           │
              │--------------------------------------------------│
              │  Columns updated here:                           │
              │   • Object ID  ↔ (round-trip for identity)       │
              │   • Canonical Name → (Markdown → Ledger)         │
              │   • Type →                                        │
              │   • Parent Object ID →                            │
              │   • State →                                       │
              │   • Tags → (partial round-trip for S3 buckets)    │
              │   • File Location →                               │
              │   • Notes ↔ (full round-trip)                     │
              │   • People (future)                               │
              │   • Timestamps (future)                           │
              └──────────────────────┬────────────────────────────┘
                                     │
                                     ▼
                         ┌────────────────────────────┐
                         │     KINeTIC-ID-INDEX.csv   │
                         │        SINGLE TRUTH        │
                         └──────────────┬─────────────┘
                                        │
                                        ▼
                 ┌────────────────────────────────────────┐
                 │        compiler.py (View Builder)       │
                 │------------------------------------------│
                 │  Reads ledger to generate:               │
                 │    • S3 views                            │
                 │    • Project index                       │
                 │    • Goals & AoRs                        │
                 │    • Active tasks                        │
                 │    • Today Card snapshot                 │
                 │    • Ledger summary                      │
                 └────────────────────────┬────────────────┘
                                          │
                                          ▼
                  ┌────────────────────────────────────────────┐
                  │              /Views (Read-only)             │
                  │   Used by Kinetic GPT to prevent drift      │
                  │   and to load complete + non-degraded data  │
                  └────────────────────────────┬───────────────┘
                                               │
                                               ▼
                           ┌────────────────────────────────┐
                           │       KINETIC GPT RUNTIME      │
                           │ - Always reads ledger + views  │
                           │ - Never trusts Markdown        │
                           │ - Coach mode, Today logic      │
                           │ - Plans, reflects, prioritizes │
                           └────────────────────────────────┘


For detailed info, see "Kinetic Data Maps"


