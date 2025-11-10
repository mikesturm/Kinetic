#!/usr/bin/env python3
"""Compile normalized Kinetic ledger views."""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
from collections import Counter, OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Dict, Iterable, List, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
LEDGER_PATH = REPO_ROOT / "Kinetic-ID-Index.csv"
S3_BUCKETS_PATH = REPO_ROOT / "S3-Buckets.csv"
CARDS_DIR = REPO_ROOT / "Cards"
VIEWS_DIR = REPO_ROOT / "Views"

S3_PATTERN = re.compile(r"^S3-\\d+$")
TODAYCARD_PATTERN = re.compile(r"^\\d{4}-\\d{2}-\\d{2}-TodayCard\\.md$")


@dataclass
class FileArtifact:
    """Metadata captured for generated artifacts."""

    name: str
    rows: int
    bytes_size: int
    sha1: str


def load_ledger(path: Path = LEDGER_PATH) -> tuple[List[Dict[str, str]], List[str], str]:
    """Load the canonical ledger CSV records, header, and raw text."""

    text = path.read_text(encoding="utf-8")
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fieldnames = reader.fieldnames or []
        records: List[Dict[str, str]] = []
        for row in reader:
            normalized = {key: (value.strip() if isinstance(value, str) else "") for key, value in row.items()}
            records.append(normalized)
    return records, fieldnames, text


def derive_s3_column(tags_values: Iterable[str]) -> List[str]:
    """Derive S3 codes for each tag string."""

    def extract(token_string: str) -> str:
        if not token_string:
            return ""
        for token in (piece.strip() for piece in token_string.split(";")):
            if S3_PATTERN.match(token):
                return token
        return ""

    return [extract(tags or "") for tags in tags_values]


def _s3_sort_key(value: str) -> tuple[int, str]:
    if not value:
        return (10_000, "")
    match = re.match(r"^S3-(\d+)$", value)
    if match:
        return (int(match.group(1)), value)
    return (9_999, value)


def write_csv(path: Path, rows: Sequence[Dict[str, str]], fieldnames: Sequence[str], header_comment: Optional[str] = None) -> FileArtifact:
    """Write CSV rows with deterministic ordering and metadata."""

    path.parent.mkdir(parents=True, exist_ok=True)

    with NamedTemporaryFile("w", newline="", encoding="utf-8", dir=path.parent, delete=False) as tmp:
        temp_path = Path(tmp.name)
        if header_comment:
            comment = header_comment.rstrip("\n") + "\n"
            tmp.write(comment)
        writer = csv.DictWriter(tmp, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})

    os.replace(temp_path, path)

    data_bytes = path.read_bytes()
    sha1 = hashlib.sha1(data_bytes).hexdigest()
    return FileArtifact(name=path.name, rows=len(rows), bytes_size=len(data_bytes), sha1=sha1)


def write_json(path: Path, data: Any) -> FileArtifact:
    """Write JSON data atomically and return artifact metadata."""

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))

    with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as tmp:
        temp_path = Path(tmp.name)
        tmp.write(payload)

    os.replace(temp_path, path)

    data_bytes = path.read_bytes()
    sha1 = hashlib.sha1(data_bytes).hexdigest()
    rows = 1 if isinstance(data, dict) else len(data)
    return FileArtifact(name=path.name, rows=rows, bytes_size=len(data_bytes), sha1=sha1)


def summarize_ledger(records: List[Dict[str, str]], source_sha: str) -> Dict[str, Any]:
    """Build the ledger summary payload."""

    counts_by_type = Counter(record.get("Type", "") for record in records)
    counts_by_type = OrderedDict(sorted(counts_by_type.items()))

    status_counts = Counter(record.get("Current State", "") for record in records)
    status_counts = OrderedDict(sorted(status_counts.items()))

    task_records = [record for record in records if record.get("Type") == "Task"]
    s3_values = derive_s3_column(record.get("Tags", "") for record in task_records)
    for record, s3_value in zip(task_records, s3_values):
        record["S3"] = s3_value

    s3_counts = Counter(record.get("S3", "") or "Unassigned" for record in task_records)

    def sort_s3_items(items: Iterable[tuple[str, int]]) -> List[tuple[str, int]]:
        return sorted(items, key=lambda item: _s3_sort_key("" if item[0] == "Unassigned" else item[0]))

    s3_counts_ordered = OrderedDict(sort_s3_items(s3_counts.items()))

    orphans = sum(1 for record in task_records if not (record.get("Parent Object ID", "").strip()))

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_sha": source_sha,
        "counts_by_type": counts_by_type,
        "status_counts": status_counts,
        "s3_distribution": s3_counts_ordered,
        "orphans_count": orphans,
    }


def load_s3_definitions(path: Path = S3_BUCKETS_PATH) -> List[Dict[str, Any]]:
    """Parse the S3 bucket definitions into normalized dictionaries."""

    records: List[Dict[str, Any]] = []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            bucket = (row.get("Canonical ID") or "").strip()
            name = (row.get("Display Name") or "").strip()
            description = (row.get("Notes") or "").strip()
            priority_match = re.match(r"^S3-(\d+)$", bucket)
            priority = int(priority_match.group(1)) if priority_match else 9999
            records.append({
                "bucket": bucket,
                "name": name,
                "description": description,
                "priority": priority,
            })
    records.sort(key=lambda item: item["priority"])
    return records


def find_latest_todaycard(cards_dir: Path = CARDS_DIR) -> Optional[Path]:
    """Locate the most recent TodayCard by lexicographic order."""

    if not cards_dir.exists():
        return None

    candidates = [path for path in cards_dir.glob("*TodayCard.md") if TODAYCARD_PATTERN.match(path.name)]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.name)


def copy_todaycard(destination: Path, source: Optional[Path]) -> FileArtifact:
    """Copy the latest TodayCard content or create an empty file if missing."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    content = source.read_bytes() if source and source.exists() else b""

    with NamedTemporaryFile("wb", dir=destination.parent, delete=False) as tmp:
        temp_path = Path(tmp.name)
        tmp.write(content)

    os.replace(temp_path, destination)

    sha1 = hashlib.sha1(content).hexdigest()
    rows = content.count(b"\n") + (1 if content else 0)
    return FileArtifact(name=destination.name, rows=rows, bytes_size=len(content), sha1=sha1)


def prepare_tasks(records: List[Dict[str, str]]) -> List[Dict[str, str]]:
    tasks = [dict(record) for record in records if record.get("Type") == "Task"]
    s3_values = derive_s3_column(task.get("Tags", "") for task in tasks)
    for task, s3_value in zip(tasks, s3_values):
        task["S3"] = s3_value
    return tasks


def compile_views() -> None:
    records, _, ledger_text = load_ledger()
    ledger_sha = hashlib.sha1(ledger_text.encode("utf-8")).hexdigest()

    VIEWS_DIR.mkdir(parents=True, exist_ok=True)

    artifacts: List[FileArtifact] = []

    summary_payload = summarize_ledger(records, ledger_sha)
    summary_artifact = write_json(VIEWS_DIR / "Ledger_Summary.json", summary_payload)
    artifacts.append(summary_artifact)

    tasks = prepare_tasks(records)
    active_tasks = [task for task in tasks if task.get("Current State") != "Complete"]
    active_tasks_sorted = sorted(
        active_tasks,
        key=lambda task: (_s3_sort_key(task.get("S3", "")), task.get("Colloquial Name", "")),
    )
    active_tasks_rows = [
        {
            "Object ID": task.get("Object ID", ""),
            "Colloquial Name": task.get("Colloquial Name", ""),
            "Current State": task.get("Current State", ""),
            "S3": task.get("S3", ""),
            "People": task.get("People", ""),
            "Parent Object ID": task.get("Parent Object ID", ""),
            "Tags": task.get("Tags", ""),
            "Notes": task.get("Notes", ""),
        }
        for task in active_tasks_sorted
    ]

    tasks_active_artifact = write_csv(
        VIEWS_DIR / "Tasks_Active.csv",
        active_tasks_rows,
        [
            "Object ID",
            "Colloquial Name",
            "Current State",
            "S3",
            "People",
            "Parent Object ID",
            "Tags",
            "Notes",
        ],
    )
    artifacts.append(tasks_active_artifact)

    s3_counts = Counter(row.get("S3", "") or "Unassigned" for row in active_tasks_rows)
    comment_lines = [
        f"# {('Unassigned' if bucket == 'Unassigned' else bucket)}: {count} tasks"
        for bucket, count in sorted(
            s3_counts.items(),
            key=lambda item: _s3_sort_key("" if item[0] == "Unassigned" else item[0]),
        )
    ]
    header_comment = "\n".join(comment_lines) if comment_lines else "# No active tasks"

    tasks_by_s3_artifact = write_csv(
        VIEWS_DIR / "Tasks_By_S3.csv",
        active_tasks_rows,
        [
            "Object ID",
            "Colloquial Name",
            "Current State",
            "S3",
            "People",
            "Parent Object ID",
            "Tags",
            "Notes",
        ],
        header_comment=header_comment,
    )
    artifacts.append(tasks_by_s3_artifact)

    projects_open_rows = [
        {
            "Object ID": record.get("Object ID", ""),
            "Colloquial Name": record.get("Colloquial Name", ""),
            "Current State": record.get("Current State", ""),
            "People": record.get("People", ""),
            "Parent Object ID": record.get("Parent Object ID", ""),
            "Tags": record.get("Tags", ""),
            "Notes": record.get("Notes", ""),
        }
        for record in records
        if record.get("Type") == "Project" and record.get("Current State") != "Complete"
    ]
    projects_open_rows.sort(key=lambda row: row.get("Colloquial Name", ""))

    projects_artifact = write_csv(
        VIEWS_DIR / "Projects_Open.csv",
        projects_open_rows,
        [
            "Object ID",
            "Colloquial Name",
            "Current State",
            "People",
            "Parent Object ID",
            "Tags",
            "Notes",
        ],
    )
    artifacts.append(projects_artifact)

    goals_aors_rows = [
        {
            "Object ID": record.get("Object ID", ""),
            "Type": record.get("Type", ""),
            "Colloquial Name": record.get("Colloquial Name", ""),
            "Current State": record.get("Current State", ""),
            "People": record.get("People", ""),
            "Notes": record.get("Notes", ""),
        }
        for record in records
        if record.get("Type") in {"Goal", "AOR"}
    ]
    goals_aors_rows.sort(key=lambda row: (row.get("Type", ""), row.get("Colloquial Name", "")))

    goals_artifact = write_csv(
        VIEWS_DIR / "Goals_And_AoRs.csv",
        goals_aors_rows,
        [
            "Object ID",
            "Type",
            "Colloquial Name",
            "Current State",
            "People",
            "Notes",
        ],
    )
    artifacts.append(goals_artifact)

    s3_definitions = load_s3_definitions()
    s3_artifact = write_json(VIEWS_DIR / "S3_Definitions.json", s3_definitions)
    artifacts.append(s3_artifact)

    latest_card = find_latest_todaycard()
    todaycard_artifact = copy_todaycard(VIEWS_DIR / "TodayCard_Latest.md", latest_card)
    artifacts.append(todaycard_artifact)

    for artifact in artifacts:
        print(f"{artifact.name}: rows={artifact.rows} bytes={artifact.bytes_size} sha1={artifact.sha1}")

    summary = {
        artifact.name: {
            "rows": artifact.rows,
            "size_kb": round(artifact.bytes_size / 1024, 3),
        }
        for artifact in artifacts
    }
    print(json.dumps(summary, ensure_ascii=False))


def main() -> None:
    compile_views()


if __name__ == "__main__":
    main()
