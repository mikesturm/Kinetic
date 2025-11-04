#!/usr/bin/env python3
import sys
import os
import hashlib
import difflib

# Simple terminal colors
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
BLUE = "\033[94m"
RESET = "\033[0m"

def file_hash(path):
    """Return SHA256 hash of a file."""
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()

def main():
    if len(sys.argv) < 2:
        print(f"{RED}✖ ERROR:{RESET} No file specified for integrity check.")
        sys.exit(1)

    file_path = sys.argv[1]

    if not os.path.exists(file_path):
        print(f"{RED}✖ ERROR:{RESET} File not found: {file_path}")
        sys.exit(1)

    print(f"{BLUE}🔍 Validating ledger integrity for {file_path}...{RESET}")

    with open(file_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    if not lines:
        print(f"{RED}⚠ HALT:{RESET} File is empty — possible truncation.")
        sys.exit(1)

    # Detect any risky line deletions
    prev_file_path = f"{file_path}.bak"

    if os.path.exists(prev_file_path):
        with open(prev_file_path, "r", encoding="utf-8") as pf:
            prev_lines = pf.readlines()

        diff = list(difflib.unified_diff(prev_lines, lines))
        removed = [l for l in diff if l.startswith('-') and not l.startswith('---')]
        added = [l for l in diff if l.startswith('+') and not l.startswith('+++')]

        if not added and not removed:
            print(f"{BLUE}ℹ INFO:{RESET} No content change detected for {file_path}. Skipping commit.")
            sys.exit(0)

        if len(removed) > 0 and len(added) < len(removed):
            print(f"{RED}🛑 HALT:{RESET} Potential destructive edit detected — line count decreased.")
            print(f"Removed lines: {len(removed)} | Added: {len(added)}")
            sys.exit(1)

    # If we made it here, the edit is additive or safe.
    print(f"{GREEN}✅ Integrity check passed:{RESET} safe append or modification detected.")
    new_hash = file_hash(file_path)
    with open(prev_file_path, "w", encoding="utf-8") as pf:
        pf.writelines(lines)
    print(f"{BLUE}🔒 Snapshot updated:{RESET} {prev_file_path}")
    print(f"{GREEN}🟩 CommitGuard validation complete.{RESET}")

if __name__ == "__main__":
    main()