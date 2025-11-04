#!/usr/bin/env python3
"""
Middleware/SafeCommit.py
--------------------------------------------------------
Wrapper for GitHub file updates that enforces CommitGuard.
--------------------------------------------------------
"""

import os
import subprocess
import sys
import base64
import requests
import json

# Import integrity check
subprocess.run(["python", "Middleware/CommitGuard.py", sys.argv[1]], check=True)

# If we reach here, the file passed validation
path = sys.argv[1]
message = sys.argv[2] if len(sys.argv) > 2 else "auto-commit (SafeCommit)"
branch = "main"

# Read file contents
with open(path, "r", encoding="utf-8") as f:
    content = f.read()
encoded = base64.b64encode(content.encode("utf-8")).decode("utf-8")

# Fetch current file SHA
url = f"https://api.github.com/repos/mikesturm/Kinetic/contents/{path}?ref={branch}"
sha = requests.get(url).json()["sha"]

payload = {
    "message": message,
    "content": encoded,
    "sha": sha,
    "branch": branch
}

headers = {
    "Authorization": f"token {os.getenv('GITHUB_TOKEN')}",
    "Accept": "application/vnd.github+json"
}

resp = requests.put(url, headers=headers, data=json.dumps(payload))
if resp.status_code >= 300:
    print(resp.json())
    sys.exit(f"❌ Commit failed for {path}")
else:
    print(f"✅ Safe commit successful for {path}")