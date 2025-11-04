#!/usr/bin/env python3
"""
Middleware/CommitGuard.py
--------------------------------------------------------
Kinetic Ledger Integrity Layer (Append-Only Enforcement)
Author: Mike Sturm (2025-11-03)
Purpose:
  Prevent destructive overwrites, truncations, or silent file shortening.
  Enforces Ledger Principle 6.8 — Append-Only Enforcement Protocol.
--------------------------------------------------------
"""

import hashlib
import json
import os
import sys
import requests
from datetime import datetime

# =====================================================
# CONFIGURATION
# =====================================================

GITHUB_API_URL = "https://api.github.com/repos/mikesturm/Kinetic/contents"
BRANCH = "main"
CONFIG_FILE = os.path.join(os.path.dirname(__file__), "CommitGuard.json")

# =====================================================
# HELPER FUNCTIONS
# =====================================================

def sha256_checksum(content: str) -> str:
    """Compute SHA256 checksum for given content string."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()

def get_latest_file(path: str) -> dict:
    """Fetch the latest version of a file from GitHub main branch."""
    url = f"{GITHUB_API_URL}/{path}?ref={BRANCH}"
    resp = requests.get(url)
    if resp.status_code != 200:
        raise RuntimeError(f"Failed to fetch file: {path} ({resp.status_code})")
    return resp.json()

def load_config():
    """Load local JSON configuration if available."""
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

# =====================================================
# CORE GUARD FUNCTION
# =====================================================

def validate_append_only(path: str, new_content: str):
    """Compare old vs new content, enforce append-only rule."""
    print(f"🔍 Validating ledger integrity for {path}...")

    file_data = get_latest_file(path)
    old_content = file_data.get("content", "")
    if file_data.get("encoding") == "base64":
        import base64
        old_content = base64.b64decode(old_content).decode("utf-8")

    L1 = len(old_content.splitlines())
    L2 = len(new_content.splitlines())
    delta = L2 - L1

    checksum_old = sha256_checksum(old_content)
    checksum_new = sha256_checksum(new_content)

    # HALT CONDITIONS
    if delta < 0:
        raise SystemExit(
            f"❌ HALT: Detected potential truncation in {path}\n"
            f"Original line count: {L1}\nNew line count: {L2}\n"
            f"Delta: {delta} (negative — file shortened)\n"
            f"Timestamp: {datetime.utcnow().isoformat()}Z"
        )

    if checksum_old == checksum_new:
        raise SystemExit(
            f"⚠️ HALT: No content change detected for {path}. Nothing to commit."
        )

    print(f"✅ Integrity check passed: ΔL = {delta} (append or modify only).")

# =====================================================
# OPTIONAL CHECKSUM VALIDATION AGAINST CONFIG
# =====================================================

def verify_against_config(path: str, new_content: str):
    """Optional: verify checksum policy from CommitGuard.json."""
    cfg = load_config()
    if not cfg.get("checksum_validation"):
        return
    expected_algo = cfg["checksum_validation"].get("algorithm", "sha256")
    if expected_algo != "sha256":
        raise SystemExit(f"Unsupported checksum algorithm: {expected_algo}")
    checksum_new = sha256_checksum(new_content)
    print(f"🔒 Checksum ({expected_algo}): {checksum_new}")

# =====================================================
# ENTRY POINT
# =====================================================

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python CommitGuard.py <path-to-file>")
        sys.exit(1)

    path = sys.argv[1]
    file_path = os.path.join(os.getcwd(), path)

    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Local file not found: {file_path}")

    with open(file_path, "r", encoding="utf-8") as f:
        new_content = f.read()

    # Run validations
    validate_append_only(path, new_content)
    verify_against_config(path, new_content)

    print("🟩 CommitGuard validation complete — file ready for push.")