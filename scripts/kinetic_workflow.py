"""Utilities for synchronizing Kinetic cards, S3 buckets, and the ID index.

This script keeps the Markdown planning files in sync with the canonical
``Kinetic-ID-Index.csv`` ledger.  It performs three high-level operations:

1. Capture manual updates from ``S3.md`` bucket sections and reflect them in the
   ledger (including creating new task objects when needed).
2. Analyse the latest daily card and ensure objects referenced there carry the
   ``#Today`` tag, while completed card items mark their corresponding ledger
   entries as complete.
3. Regenerate the managed portions of ``S3.md`` so the document always mirrors
   the ledger for bucketed tasks.

Run ``python scripts/kinetic_workflow.py --run`` from the repository root to
execute all reconciliation steps.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from difflib import get_close_matches
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
KII_PATH = REPO_ROOT / "Kinetic-ID-Index.csv"
S3_PATH = REPO_ROOT / "S3.md"
S3_BUCKETS_PATH = REPO_ROOT / "S3-Buckets.csv"
CARDS_PATH = REPO_ROOT / "Cards"
DELETED_PATH = REPO_ROOT / "Deleted.csv"


DEFAULT_DELETED_FIELDNAMES = [
    "Object ID",
    "Canonical Name",
    "Date of Deletion",
    "Origin File",
    "Reason",
    "Notes",
]


DELETED_IDS_CACHE: Optional[Set[str]] = None


CARD_DATE_PATTERN = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
CHECKBOX_PATTERN = re.compile(r"\[[xX ]\]")
OBJECT_ID_PATTERN = re.compile(r"\b([A-Z]\d+(?:\.\d+)*)\b")
OBJECT_ID_SUFFIX_PATTERN = re.compile(r"\[\s*Object ID\s*:\s*([A-Z]\d+(?:\.\d+)*)\s*\]")
TASK_LINE_PATTERN = re.compile(
    r"^(?P<indent>\s*)(?P<bullet>[-*+]|\d+\.)\s+(?P<checkbox>\[[xX ]\])\s+(?P<rest>.*)$"
)
PROJECT_HEADING_PATTERN = re.compile(r"Project:\s*(.+)$", re.IGNORECASE)
PROJECT_CHILD_ID_PATTERN = re.compile(r"^P\d+\.")
SNAPSHOT_START_MARKER = "<!-- SNAPSHOT START -->"
SNAPSHOT_END_MARKER = "<!-- SNAPSHOT END -->"
SNAPSHOT_BLOCK_PATTERN = re.compile(
    rf"{re.escape(SNAPSHOT_START_MARKER)}.*?{re.escape(SNAPSHOT_END_MARKER)}",
    re.DOTALL,
)


@dataclass
class LedgerRow:
    object_id: str
    type: str
    checksum: str
    canonical_text: str
    colloquial_name: str
    current_state: str
    file_location: str
    tags: List[str] = field(default_factory=list)
    people: str = ""
    parent_object_id: str = ""
    child_object_ids: str = ""
    notes: str = ""

    @classmethod
    def from_dict(cls, data: Dict[str, str]) -> "LedgerRow":
        tags = split_tags(data.get("Tags", ""))
        return cls(
            object_id=data.get("Object ID", "").strip(),
            type=data.get("Type", "").strip(),
            checksum=data.get("Canonical Name (Checksum)", "").strip(),
            canonical_text=data.get("Canonical Name (Text)", "").strip(),
            colloquial_name=data.get("Colloquial Name", "").strip(),
            current_state=data.get("Current State", "").strip(),
            file_location=data.get("File Location", "").strip(),
            tags=tags,
            people=data.get("People", ""),
            parent_object_id=data.get("Parent Object ID", ""),
            child_object_ids=data.get("Child Object IDs", ""),
            notes=data.get("Notes", ""),
        )

    def to_dict(self) -> Dict[str, str]:
        return {
            "Object ID": self.object_id,
            "Type": self.type,
            "Canonical Name (Checksum)": self.checksum,
            "Canonical Name (Text)": self.canonical_text,
            "Colloquial Name": self.colloquial_name,
            "Current State": self.current_state,
            "File Location": self.file_location,
            "Tags": join_tags(self.tags),
            "People": self.people,
            "Parent Object ID": self.parent_object_id,
            "Child Object IDs": self.child_object_ids,
            "Notes": self.notes,
        }


@dataclass
class Bucket:
    canonical_id: str
    display_name: str
    notes: str = ""


@dataclass
class Section:
    heading: Optional[str]
    level: Optional[int]
    lines: List[str] = field(default_factory=list)


@dataclass
class ParsedTask:
    line_index: int
    indent: int
    checkbox: str
    completed: bool
    text: str
    object_id: Optional[str]
    notes: List[str] = field(default_factory=list)
    parent: Optional["ParsedTask"] = None
    resolved_object_id: Optional[str] = None
    heading: Optional["HeadingNode"] = None


@dataclass(eq=False)
class HeadingNode:
    title: str
    depth: int
    line_index: int
    parent: Optional["HeadingNode"] = None
    children: List["HeadingNode"] = field(default_factory=list)
    tasks: List[ParsedTask] = field(default_factory=list)
    resolved_object_id: Optional[str] = None
    has_task_descendants: bool = False


@dataclass
class ProjectStructure:
    headings: List[HeadingNode]
    tasks: List[ParsedTask]
    root_tasks: List[ParsedTask]


def split_tags(raw: str) -> List[str]:
    if not raw:
        return []
    parts = re.split(r"\s*[;,]\s*", raw.strip())
    normalized: List[str] = []
    for part in parts:
        if not part:
            continue
        match = re.match(r"^S-(\d+)$", part)
        if match:
            normalized.append(f"S3-{match.group(1)}")
        else:
            normalized.append(part)
    return normalized


def join_tags(tags: Sequence[str]) -> str:
    if not tags:
        return ""
    return "; ".join(dict.fromkeys(tag for tag in tags if tag))


def split_list(raw: str) -> List[str]:
    if not raw:
        return []
    return [part.strip() for part in raw.split(";") if part.strip()]


def join_list(items: Sequence[str]) -> str:
    if not items:
        return ""
    return "; ".join(dict.fromkeys(item for item in items if item))


def escape_snapshot_value(value: str) -> str:
    if not value:
        return ""
    return value.replace("|", "\\|").replace("\n", "<br>")


def unique_preserve(items: Iterable[str]) -> List[str]:
    seen: set = set()
    ordered: List[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            ordered.append(item)
    return ordered


def normalize_heading(text: Optional[str]) -> str:
    if text is None:
        return ""
    return text.replace("’", "'").strip()


def sanitize_colloquial(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^[-*+]\s*", "", text)
    text = re.sub(r"^\d+\.\s*", "", text)
    text = re.sub(r"^\[[xX ]\]\s*", "", text)
    text = text.strip()
    text = OBJECT_ID_SUFFIX_PATTERN.sub("", text)
    text = re.sub(r"\(Object ID:\s*[A-Z]\d+\)\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"<small>.*?</small>", "", text, flags=re.IGNORECASE)
    text = text.strip("*_ ")
    text = text.replace("**", "")
    text = text.replace("*", "")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def normalize_for_match(text: str) -> str:
    cleaned = sanitize_colloquial(text)
    cleaned = cleaned.lower()
    cleaned = re.sub(r"[^a-z0-9]+", "", cleaned)
    return cleaned


def canonicalize_text(text: str) -> str:
    cleaned = sanitize_colloquial(text)
    cleaned = re.sub(r"[^A-Za-z0-9]", "", cleaned)
    return cleaned


def checksum(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def read_csv(path: Path) -> Tuple[List[str], List[Dict[str, str]]]:
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
        return reader.fieldnames or [], rows


def write_csv(path: Path, fieldnames: Sequence[str], rows: Sequence[Dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def default_deleted_preamble() -> List[str]:
    today = datetime.today().date().isoformat()
    return [
        "# Deleted Objects Ledger",
        f"#  Initialized: {today}",
        "# Purpose: Permanent ledger of retired or obsolete objects.",
        "# No rows are ever removed. Resurrection is annotated in the Notes column.",
        "",
    ]


def load_deleted_ledger() -> Tuple[List[str], List[str], List[Dict[str, str]]]:
    if not DELETED_PATH.exists():
        preamble = default_deleted_preamble()
        return preamble, list(DEFAULT_DELETED_FIELDNAMES), []

    with DELETED_PATH.open(encoding="utf-8", newline="") as fh:
        lines = [line.rstrip("\n") for line in fh]

    header_index: Optional[int] = None
    for idx, line in enumerate(lines):
        if line.startswith("Object ID"):
            header_index = idx
            break

    if header_index is None:
        preamble = lines if lines else default_deleted_preamble()
        return preamble, list(DEFAULT_DELETED_FIELDNAMES), []

    preamble = lines[:header_index]
    data_lines = lines[header_index:]
    reader = csv.DictReader(data_lines)
    fieldnames = reader.fieldnames or list(DEFAULT_DELETED_FIELDNAMES)
    rows = [row for row in reader]
    return preamble, list(fieldnames), rows


def write_deleted_ledger(
    preamble: Sequence[str], fieldnames: Sequence[str], rows: Sequence[Dict[str, str]]
) -> None:
    if not preamble:
        preamble = default_deleted_preamble()

    with DELETED_PATH.open("w", newline="", encoding="utf-8") as fh:
        for line in preamble:
            fh.write(f"{line}\n")
        if preamble and preamble[-1]:
            fh.write("\n")

        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def load_deleted_ids(refresh: bool = False) -> Set[str]:
    global DELETED_IDS_CACHE
    if DELETED_IDS_CACHE is None or refresh:
        _, _, rows = load_deleted_ledger()
        DELETED_IDS_CACHE = {
            row.get("Object ID", "").strip()
            for row in rows
            if row.get("Object ID", "").strip()
        }
    return set(DELETED_IDS_CACHE)


def append_deleted_record(row: LedgerRow, reason: str) -> None:
    if not row.object_id:
        return

    preamble, fieldnames, existing_rows = load_deleted_ledger()
    existing_ids = {entry.get("Object ID", "").strip() for entry in existing_rows}
    if row.object_id in existing_ids:
        return

    canonical_name = row.canonical_text or row.colloquial_name or row.object_id
    record = {
        "Object ID": row.object_id,
        "Canonical Name": canonical_name,
        "Date of Deletion": datetime.today().date().isoformat(),
        "Origin File": row.file_location or "",
        "Reason": reason,
        "Notes": row.notes or "",
    }

    updated_rows = list(existing_rows)
    updated_rows.append(record)
    write_deleted_ledger(preamble, fieldnames, updated_rows)
    load_deleted_ids(refresh=True)


def load_ledger() -> Tuple[List[str], List[LedgerRow]]:
    fieldnames, raw_rows = read_csv(KII_PATH)
    ledger_rows = [LedgerRow.from_dict(r) for r in raw_rows]
    return fieldnames, ledger_rows


def save_ledger(fieldnames: Sequence[str], rows: Sequence[LedgerRow]) -> None:
    write_csv(KII_PATH, fieldnames, [row.to_dict() for row in rows])


def load_buckets() -> List[Bucket]:
    _, raw_rows = read_csv(S3_BUCKETS_PATH)
    buckets = [Bucket(r["Canonical ID"].strip(), r["Display Name"].strip(), r.get("Notes", "")) for r in raw_rows]
    return buckets


def parse_s3_sections() -> List[Section]:
    sections: List[Section] = []
    current = Section(heading=None, level=None, lines=[])

    with S3_PATH.open(encoding="utf-8", errors="replace") as fh:
        for raw_line in fh:
            line = raw_line.rstrip("\n")
            match = re.match(r"^(#+)\s+(.*)", line)
            if match:
                sections.append(current)
                level = len(match.group(1))
                heading = match.group(2).strip()
                current = Section(heading=heading, level=level, lines=[])
            else:
                current.lines.append(line)

    sections.append(current)
    return sections


def write_s3_sections(sections: Sequence[Section]) -> None:
    lines: List[str] = []
    for section in sections:
        if section.heading is not None and section.level is not None:
            lines.append(f"{'#' * section.level} {section.heading}")
        lines.extend(section.lines)
    content = "\n".join(lines).rstrip("\n") + "\n"
    S3_PATH.write_text(content, encoding="utf-8")


def parse_markdown_tasks_with_notes(lines: Sequence[str]) -> List[ParsedTask]:
    tasks: List[ParsedTask] = []
    stack: List[ParsedTask] = []

    for idx, line in enumerate(lines):
        match = TASK_LINE_PATTERN.match(line)
        if match:
            indent = len(match.group("indent").replace("\t", "    "))
            checkbox = match.group("checkbox")
            completed = checkbox.lower() == "[x]"
            rest = match.group("rest").rstrip()
            obj_match = OBJECT_ID_SUFFIX_PATTERN.search(rest)
            object_id = obj_match.group(1) if obj_match else None
            text = sanitize_colloquial(rest)
            if text.strip().lower() == "_no tracked items_":
                continue

            task = ParsedTask(
                line_index=idx,
                indent=indent,
                checkbox=checkbox,
                completed=completed,
                text=text,
                object_id=object_id,
            )

            while stack and stack[-1].indent >= indent:
                stack.pop()
            if stack:
                task.parent = stack[-1]

            tasks.append(task)
            stack.append(task)
        else:
            stripped = line.strip()
            if not stripped:
                continue
            indent = len(line[: len(line) - len(line.lstrip(" \t"))].replace("\t", "    "))
            while stack and stack[-1].indent >= indent:
                stack.pop()
            if stack:
                note_text = sanitize_colloquial(stripped)
                if note_text:
                    stack[-1].notes.append(note_text)

    return tasks


def parse_project_structure(lines: Sequence[str]) -> ProjectStructure:
    headings: List[HeadingNode] = []
    heading_stack: List[HeadingNode] = []
    tasks: List[ParsedTask] = []
    root_tasks: List[ParsedTask] = []
    task_stack: List[ParsedTask] = []

    for idx, line in enumerate(lines):
        heading_match = re.match(r"^(#+)\s+(.*)$", line)
        if heading_match and len(heading_match.group(1)) >= 2:
            depth = len(heading_match.group(1))
            title = sanitize_colloquial(heading_match.group(2))
            while heading_stack and heading_stack[-1].depth >= depth:
                heading_stack.pop()
            parent = heading_stack[-1] if heading_stack else None
            node = HeadingNode(title=title, depth=depth, line_index=idx, parent=parent)
            if parent:
                parent.children.append(node)
            headings.append(node)
            heading_stack.append(node)
            task_stack.clear()
            continue

        match = TASK_LINE_PATTERN.match(line)
        if match:
            indent = len(match.group("indent").replace("\t", "    "))
            checkbox = match.group("checkbox")
            completed = checkbox.lower() == "[x]"
            rest = match.group("rest").rstrip()
            obj_match = OBJECT_ID_SUFFIX_PATTERN.search(rest)
            object_id = obj_match.group(1) if obj_match else None
            text = sanitize_colloquial(rest)
            if text.strip().lower() == "_no tracked items_":
                task_stack.clear()
                continue

            while task_stack and task_stack[-1].indent >= indent:
                task_stack.pop()

            task = ParsedTask(
                line_index=idx,
                indent=indent,
                checkbox=checkbox,
                completed=completed,
                text=text,
                object_id=object_id,
            )

            if task_stack:
                task.parent = task_stack[-1]

            if heading_stack:
                task.heading = heading_stack[-1]
                heading_stack[-1].tasks.append(task)
            else:
                root_tasks.append(task)

            tasks.append(task)
            task_stack.append(task)
        else:
            stripped = line.strip()
            if not stripped:
                continue
            indent = len(line[: len(line) - len(line.lstrip(" \t"))].replace("\t", "    "))
            while task_stack and task_stack[-1].indent >= indent:
                task_stack.pop()
            if task_stack:
                note_text = sanitize_colloquial(stripped)
                if note_text:
                    task_stack[-1].notes.append(note_text)

    relevant_roots: List[HeadingNode] = []

    def mark_descendants(node: HeadingNode) -> bool:
        has_tasks = bool(node.tasks)
        for child in node.children:
            if mark_descendants(child):
                has_tasks = True
        node.has_task_descendants = has_tasks
        return has_tasks

    for heading in headings:
        if heading.parent is None:
            if mark_descendants(heading):
                relevant_roots.append(heading)

    return ProjectStructure(headings=headings, tasks=tasks, root_tasks=root_tasks)


def build_colloquial_index(rows: Sequence[LedgerRow]) -> Dict[str, List[str]]:
    index: Dict[str, List[str]] = defaultdict(list)
    for row in rows:
        if row.colloquial_name:
            key = normalize_for_match(row.colloquial_name)
            if row.object_id not in index[key]:
                index[key].append(row.object_id)
        if row.canonical_text:
            key = row.canonical_text.lower()
            if row.object_id not in index[key]:
                index[key].append(row.object_id)
    return index


def update_text_index(index: Dict[str, List[str]], row: LedgerRow) -> None:
    if row.colloquial_name:
        key = normalize_for_match(row.colloquial_name)
        if row.object_id not in index[key]:
            index[key].append(row.object_id)
    if row.canonical_text:
        key = row.canonical_text.lower()
        if row.object_id not in index[key]:
            index[key].append(row.object_id)


def next_task_id(rows: Sequence[LedgerRow]) -> str:
    prefix = "T"
    highest = 0
    for row in rows:
        if row.object_id.startswith(prefix):
            suffix = row.object_id[len(prefix) :].split(".")[0]
            if suffix.isdigit():
                highest = max(highest, int(suffix))
    for deleted_id in load_deleted_ids():
        if deleted_id.startswith(prefix):
            suffix = deleted_id[len(prefix) :].split(".")[0]
            if suffix.isdigit():
                highest = max(highest, int(suffix))
    return f"{prefix}{highest + 1}"


def next_project_id(rows: Sequence[LedgerRow]) -> str:
    prefix = "P"
    highest = 0
    for row in rows:
        if row.object_id.startswith(prefix):
            suffix = row.object_id[len(prefix) :]
            if suffix.isdigit():
                highest = max(highest, int(suffix))
    for deleted_id in load_deleted_ids():
        if deleted_id.startswith(prefix):
            suffix = deleted_id[len(prefix) :]
            if suffix.isdigit():
                highest = max(highest, int(suffix))
    return f"{prefix}{highest + 1}"


def next_child_id(parent_id: str, id_to_row: Dict[str, LedgerRow]) -> str:
    used_ids = set(id_to_row.keys()) | load_deleted_ids()
    counter = 1
    while True:
        candidate = f"{parent_id}.{counter}"
        if candidate not in used_ids:
            return candidate
        counter += 1


def ensure_child_reference(parent: LedgerRow, child_id: str) -> None:
    children = split_list(parent.child_object_ids)
    if child_id not in children:
        children.append(child_id)
        parent.child_object_ids = join_list(children)


def rename_object(
    rows: List[LedgerRow], id_to_row: Dict[str, LedgerRow], old_id: str, new_id: str
) -> None:
    if old_id == new_id:
        return
    if new_id in id_to_row:
        raise ValueError(f"Cannot rename {old_id} to {new_id}; destination already exists")
    row = id_to_row.pop(old_id, None)
    if not row:
        return
    for other in rows:
        if other.parent_object_id == old_id:
            other.parent_object_id = new_id
        if other.child_object_ids:
            children = split_list(other.child_object_ids)
            if old_id in children:
                updated = [new_id if child == old_id else child for child in children]
                other.child_object_ids = join_list(updated)
    row.object_id = new_id
    id_to_row[new_id] = row


def remove_row(
    rows: List[LedgerRow],
    id_to_row: Dict[str, LedgerRow],
    object_id: str,
    reason: str = "Removed during synchronization",
) -> None:
    row = id_to_row.get(object_id)
    if not row:
        return

    append_deleted_record(row, reason)

    if row in rows:
        rows.remove(row)
    id_to_row.pop(object_id, None)

    for other in rows:
        if other.parent_object_id == object_id:
            other.parent_object_id = ""
        if other.child_object_ids:
            children = [child for child in split_list(other.child_object_ids) if child != object_id]
            if len(children) != len(split_list(other.child_object_ids)):
                other.child_object_ids = join_list(children)


def apply_tag_with_inheritance(
    row: LedgerRow,
    tag: str,
    known_bucket_ids: Iterable[str],
    id_to_row: Dict[str, LedgerRow],
) -> None:
    existing = [existing_tag for existing_tag in row.tags if existing_tag not in known_bucket_ids]
    had_tag = tag in row.tags
    if tag not in existing:
        existing.append(tag)
    row.tags = existing
    if not had_tag:
        for child_id, child_row in ((cid, id_to_row.get(cid)) for cid in split_list(row.child_object_ids)):
            if child_row and tag not in child_row.tags:
                child_row.tags.append(tag)


def ensure_today_tags(rows: Sequence[LedgerRow], latest_card_ids: Iterable[str]) -> None:
    latest_set = set(latest_card_ids)
    for row in rows:
        tags = list(row.tags)
        if latest_set and row.object_id in latest_set:
            if "#Today" not in tags:
                tags.append("#Today")
        else:
            if "#Today" in tags:
                tags = [tag for tag in tags if tag != "#Today"]
        row.tags = tags


def mark_completed_from_cards(rows: Dict[str, LedgerRow], completed_ids: Iterable[str]) -> None:
    for object_id in completed_ids:
        row = rows.get(object_id)
        if row:
            row.current_state = "Complete"


def update_today_card_snapshot(
    card_path: Path, rows: Sequence[LedgerRow], latest_ids: Sequence[str]
) -> None:
    unique_ids = unique_preserve(latest_ids)
    id_lookup = {row.object_id: row for row in rows if row.object_id}
    snapshot_rows = [id_lookup[obj_id] for obj_id in unique_ids if obj_id in id_lookup]

    try:
        content = card_path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return

    if not snapshot_rows:
        if SNAPSHOT_BLOCK_PATTERN.search(content):
            new_content = SNAPSHOT_BLOCK_PATTERN.sub("", content)
            new_content = re.sub(r"\n{3,}", "\n\n", new_content)
            if new_content and not new_content.endswith("\n"):
                new_content += "\n"
            if new_content != content:
                card_path.write_text(new_content, encoding="utf-8")
        return

    table_lines = [
        "| Object ID | Canonical Name | Colloquial Name | State | Tags |",
        "| --- | --- | --- | --- | --- |",
    ]

    for row in snapshot_rows:
        tags = join_tags(row.tags)
        table_lines.append(
            "| "
            + " | ".join(
                [
                    escape_snapshot_value(row.object_id),
                    escape_snapshot_value(row.canonical_text or ""),
                    escape_snapshot_value(row.colloquial_name or ""),
                    escape_snapshot_value(row.current_state or ""),
                    escape_snapshot_value(tags),
                ]
            )
            + " |"
        )

    block_lines = [
        SNAPSHOT_START_MARKER,
        "### Object Snapshot",
        "",
        *table_lines,
        SNAPSHOT_END_MARKER,
    ]
    block_text = "\n".join(block_lines)

    if SNAPSHOT_BLOCK_PATTERN.search(content):
        new_content = SNAPSHOT_BLOCK_PATTERN.sub(block_text, content)
    else:
        new_content = content
        if not new_content.endswith("\n"):
            new_content += "\n"
        if not new_content.endswith("\n\n"):
            new_content += "\n"
        new_content += block_text + "\n"

    new_content = re.sub(r"\n{3,}", "\n\n", new_content)
    if new_content and not new_content.endswith("\n"):
        new_content += "\n"

    if new_content != content:
        card_path.write_text(new_content, encoding="utf-8")


def parse_cards(existing_ids: Iterable[str]) -> Tuple[List[str], List[str], Optional[Path]]:
    object_ids = set(existing_ids)
    card_paths = sorted(CARDS_PATH.glob("*.md"))
    latest_ids: List[str] = []
    completed_ids: List[str] = []

    latest_card_path: Optional[Path] = None
    latest_date: Optional[datetime] = None

    for path in card_paths:
        match = CARD_DATE_PATTERN.search(path.name)
        if not match:
            continue
        date = datetime.strptime(match.group(0), "%Y-%m-%d")
        if latest_date is None or date > latest_date:
            latest_date = date
            latest_card_path = path

        with path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                ids_in_line = [token for token in OBJECT_ID_PATTERN.findall(line) if token in object_ids]
                if ids_in_line and CHECKBOX_PATTERN.search(line) and "[x]" in line.lower():
                    completed_ids.extend(ids_in_line)

    if latest_card_path:
        with latest_card_path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                latest_ids.extend([token for token in OBJECT_ID_PATTERN.findall(line) if token in object_ids])

    return latest_ids, completed_ids, latest_card_path


def set_bucket_tag(row: LedgerRow, bucket_id: str, known_bucket_ids: Iterable[str]) -> None:
    existing = [tag for tag in row.tags if tag not in known_bucket_ids]
    if bucket_id not in existing:
        existing.append(bucket_id)
    row.tags = existing


def process_s3_sections(rows: List[LedgerRow], buckets: Sequence[Bucket], sections: List[Section]) -> None:
    id_to_row: Dict[str, LedgerRow] = {row.object_id: row for row in rows if row.object_id}
    bucket_lookup = {normalize_heading(bucket.display_name): bucket for bucket in buckets}
    bucket_ids = {bucket.canonical_id for bucket in buckets}
    text_index = build_colloquial_index(rows)
    next_top_level_value = int(next_task_id(rows)[1:])
    next_project_value = int(next_project_id(rows)[1:])
    project_name_index: Dict[str, List[str]] = defaultdict(list)

    def allocate_top_level_id() -> str:
        nonlocal next_top_level_value
        new_id = f"T{next_top_level_value}"
        next_top_level_value += 1
        return new_id

    def allocate_project_id() -> str:
        nonlocal next_project_value
        new_id = f"P{next_project_value}"
        next_project_value += 1
        return new_id

    def allocate_child_for(parent_identifier: str) -> str:
        parent_row = id_to_row.get(parent_identifier)
        if parent_row and parent_row.object_id.startswith("P"):
            return allocate_top_level_id()
        return next_child_id(parent_identifier, id_to_row)

    def register_project_name(row: LedgerRow) -> None:
        key = normalize_for_match(row.colloquial_name or row.canonical_text or row.object_id)
        for existing_key in list(project_name_index.keys()):
            if row.object_id in project_name_index[existing_key] and existing_key != key:
                project_name_index[existing_key] = [
                    cid for cid in project_name_index[existing_key] if cid != row.object_id
                ]
                if not project_name_index[existing_key]:
                    del project_name_index[existing_key]
        if row.object_id not in project_name_index[key]:
            project_name_index[key].append(row.object_id)

    for existing_row in rows:
        if existing_row.type.lower() == "project":
            register_project_name(existing_row)

    for section in sections:
        key = normalize_heading(section.heading)

        if key in bucket_lookup:
            bucket = bucket_lookup[key]
            tasks = parse_markdown_tasks_with_notes(section.lines)

            for task in tasks:
                parent_id = task.parent.resolved_object_id if task.parent else None
                object_id = task.object_id
                if object_id and object_id not in id_to_row:
                    object_id = None

                if not object_id and task.parent and parent_id:
                    object_id = allocate_child_for(parent_id)

                if not object_id:
                    candidates = text_index.get(normalize_for_match(task.text), [])
                    if len(candidates) == 1:
                        object_id = candidates[0]

                if not object_id:
                    if task.parent and parent_id:
                        object_id = allocate_child_for(parent_id)
                    else:
                        object_id = allocate_top_level_id()

                if object_id in id_to_row:
                    row = id_to_row[object_id]
                else:
                    canonical_text = canonicalize_text(task.text)
                    row = LedgerRow(
                        object_id=object_id,
                        type="Task",
                        checksum=checksum(canonical_text),
                        canonical_text=canonical_text,
                        colloquial_name=task.text,
                        current_state="Complete" if task.completed else "Active",
                        file_location="S3.md",
                        tags=[],
                    )
                    rows.append(row)
                    id_to_row[object_id] = row

                task.resolved_object_id = object_id

                row.type = row.type or "Task"
                row.colloquial_name = task.text
                if task.completed:
                    row.current_state = "Complete"
                elif row.current_state.lower() == "complete":
                    row.current_state = "Active"
                elif not row.current_state:
                    row.current_state = "Active"

                if task.notes:
                    row.notes = "\n".join(task.notes)

                if parent_id:
                    row.parent_object_id = parent_id
                    parent_row = id_to_row.get(parent_id)
                    if parent_row:
                        ensure_child_reference(parent_row, row.object_id)
                        if parent_row.file_location:
                            row.file_location = parent_row.file_location
                elif not task.parent:
                    row.parent_object_id = row.parent_object_id if row.parent_object_id and row.parent_object_id.startswith("P") else ""

                row.file_location = row.file_location or "S3.md"

                if task.completed:
                    row.tags = [
                        tag
                        for tag in row.tags
                        if not (
                            tag.startswith("S3-")
                            and tag[3:].isdigit()
                            and int(tag[3:]) > 1
                        )
                    ]
                else:
                    apply_tag_with_inheritance(row, bucket.canonical_id, bucket_ids, id_to_row)

                update_text_index(text_index, row)

            section.lines = []
        elif key.lower() == "active projects":
            tasks = parse_markdown_tasks_with_notes(section.lines)
            seen_projects: set = set()
            for task in tasks:
                object_id = task.object_id
                if object_id and object_id not in id_to_row:
                    object_id = None

                update_name = True
                project_label = task.text
                file_match = re.search(r"Project-[A-Za-z0-9-]+\.md", task.text)
                if file_match:
                    stripped = project_label.replace(file_match.group(0), "").strip()
                    if stripped:
                        project_label = " ".join(stripped.split())
                normalized_label = normalize_for_match(project_label)

                if not object_id:
                    direct_candidates = project_name_index.get(normalized_label, [])
                    if len(direct_candidates) == 1:
                        object_id = direct_candidates[0]
                if not object_id:
                    candidates = text_index.get(normalized_label, [])
                    candidates = [
                        cid
                        for cid in candidates
                        if cid in id_to_row and id_to_row[cid].object_id.startswith("P")
                    ]
                    if len(candidates) == 1:
                        object_id = candidates[0]
                if not object_id and project_name_index:
                    close_keys = get_close_matches(
                        normalized_label,
                        list(project_name_index.keys()),
                        n=1,
                        cutoff=0.85,
                    )
                    if close_keys:
                        close_candidates = project_name_index.get(close_keys[0], [])
                        if len(close_candidates) == 1:
                            object_id = close_candidates[0]

                if not object_id:
                    task_candidates = [
                        cid
                        for cid in text_index.get(normalized_label, [])
                        if cid in id_to_row and id_to_row[cid].type.lower() == "task"
                    ]
                    parent_candidates = {
                        id_to_row[cid].parent_object_id
                        for cid in task_candidates
                        if id_to_row[cid].parent_object_id in id_to_row
                        and id_to_row[id_to_row[cid].parent_object_id].object_id.startswith("P")
                    }
                    if len(parent_candidates) == 1:
                        object_id = parent_candidates.pop()
                        update_name = False

                if not object_id:
                    object_id = allocate_project_id()
                    canonical_text = canonicalize_text(project_label)
                    new_row = LedgerRow(
                        object_id=object_id,
                        type="Project",
                        checksum=checksum(canonical_text),
                        canonical_text=canonical_text,
                        colloquial_name=project_label,
                        current_state="Active" if not task.completed else "Complete",
                        file_location="S3.md",
                        tags=["Active Projects"],
                    )
                    rows.append(new_row)
                    id_to_row[object_id] = new_row
                    row = new_row
                else:
                    row = id_to_row[object_id]
                    if not row.type:
                        row.type = "Project"

                task.resolved_object_id = object_id

                if object_id in seen_projects:
                    update_name = False
                else:
                    seen_projects.add(object_id)

                row.type = "Project"
                if update_name:
                    row.colloquial_name = project_label
                    if not row.canonical_text:
                        row.canonical_text = canonicalize_text(project_label)
                    if row.canonical_text and not row.checksum:
                        row.checksum = checksum(row.canonical_text)
                if task.completed:
                    row.current_state = "Complete"
                elif not row.current_state or row.current_state.lower() == "complete":
                    row.current_state = "Active"

                if update_name and file_match:
                    potential = f"Projects/{file_match.group(0)}"
                    if (REPO_ROOT / potential).exists():
                        row.file_location = potential
                    else:
                        row.file_location = row.file_location or potential
                else:
                    row.file_location = row.file_location or "S3.md"

                if "Active Projects" not in row.tags:
                    row.tags.append("Active Projects")

                if update_name and task.notes:
                    row.notes = "\n".join(task.notes)

                register_project_name(row)
                update_text_index(text_index, row)

            section.lines = []

    rows[:] = [
        row
        for row in rows
        if row.colloquial_name.strip().strip("()").strip().lower() != "no tracked items"
    ]


def prune_invalid_project_children(rows: List[LedgerRow]) -> None:
    invalid_ids = {
        row.object_id
        for row in rows
        if PROJECT_CHILD_ID_PATTERN.match(row.object_id)
    }
    seen_projects: Dict[str, str] = {}
    seen_locations: Dict[str, str] = {}
    duplicate_projects: set = set()

    for row in rows:
        if row.type.lower() == "project" and row.canonical_text:
            key = row.canonical_text
            if key in seen_projects:
                duplicate_projects.add(row.object_id)
            else:
                seen_projects[key] = row.object_id
            if row.file_location:
                location_key = row.file_location.lower()
                if location_key in seen_locations:
                    duplicate_projects.add(row.object_id)
                else:
                    seen_locations[location_key] = row.object_id

    removable_ids = invalid_ids | duplicate_projects

    if removable_ids:
        for row in rows:
            if row.child_object_ids:
                children = [child for child in split_list(row.child_object_ids) if child not in removable_ids]
                if len(children) != len(split_list(row.child_object_ids)):
                    row.child_object_ids = join_list(children)

        rows[:] = [
            row
            for row in rows
            if row.object_id not in removable_ids and row.parent_object_id not in removable_ids
        ]

    valid_ids = {row.object_id for row in rows}
    rows[:] = [
        row
        for row in rows
        if not row.parent_object_id or row.parent_object_id in valid_ids
    ]

    seen_task_keys: Dict[Tuple[str, str], str] = {}
    duplicate_tasks: set = set()
    for row in rows:
        if row.type.lower() == "task" and row.canonical_text:
            key = (row.parent_object_id, row.canonical_text)
            if key in seen_task_keys:
                duplicate_tasks.add(row.object_id)
            else:
                seen_task_keys[key] = row.object_id

    if duplicate_tasks:
        rows[:] = [row for row in rows if row.object_id not in duplicate_tasks]

    valid_ids = {row.object_id for row in rows}
    for row in rows:
        if row.child_object_ids:
            children = [child for child in split_list(row.child_object_ids) if child in valid_ids]
            if len(children) != len(split_list(row.child_object_ids)):
                row.child_object_ids = join_list(children)


def process_project_files(rows: List[LedgerRow]) -> None:
    projects_dir = REPO_ROOT / "Projects"
    if not projects_dir.exists():
        return

    id_to_row: Dict[str, LedgerRow] = {row.object_id: row for row in rows if row.object_id}
    text_index = build_colloquial_index(rows)
    next_project_value = int(next_project_id(rows)[1:])
    next_task_value = int(next_task_id(rows)[1:])

    def allocate_project_id() -> str:
        nonlocal next_project_value
        new_id = f"P{next_project_value}"
        next_project_value += 1
        return new_id

    def allocate_task_id() -> str:
        nonlocal next_task_value
        new_id = f"T{next_task_value}"
        next_task_value += 1
        return new_id

    for path in sorted(projects_dir.rglob("*.md")):
        rel_path = path.relative_to(REPO_ROOT)
        with path.open(encoding="utf-8", errors="replace") as fh:
            lines = [line.rstrip("\n") for line in fh]

        project_name: Optional[str] = None
        for line in lines:
            match = PROJECT_HEADING_PATTERN.search(line)
            if match:
                project_name = sanitize_colloquial(match.group(1))
                break

        if not project_name:
            continue

        canonical = canonicalize_text(project_name)
        project_row: Optional[LedgerRow] = None
        for row in rows:
            if row.object_id.startswith("P") and row.canonical_text == canonical:
                project_row = row
                break

        if project_row is None:
            object_id = allocate_project_id()
            project_row = LedgerRow(
                object_id=object_id,
                type="Project",
                checksum=checksum(canonical),
                canonical_text=canonical,
                colloquial_name=project_name,
                current_state="Active",
                file_location=str(rel_path),
                tags=[],
            )
            rows.append(project_row)
            id_to_row[object_id] = project_row
        else:
            project_row.type = project_row.type or "Project"

        project_row.colloquial_name = project_name
        if not project_row.canonical_text:
            project_row.canonical_text = canonical
        if project_row.canonical_text and not project_row.checksum:
            project_row.checksum = checksum(project_row.canonical_text)
        project_row.file_location = str(rel_path)
        if not project_row.current_state:
            project_row.current_state = "Active"

        update_text_index(text_index, project_row)

        structure = parse_project_structure(lines)

        heading_id_map: Dict[HeadingNode, str] = {}
        heading_child_counts: Dict[str, int] = defaultdict(int)
        child_map: Dict[str, List[str]] = defaultdict(list)
        seen_ids: set = {project_row.object_id}

        def heading_key_from_row(row: LedgerRow) -> str:
            base = row.canonical_text or normalize_for_match(row.colloquial_name or row.object_id)
            return base

        def heading_key_from_title(title: str) -> str:
            canonical_title = canonicalize_text(title)
            if canonical_title:
                return canonical_title
            return normalize_for_match(title)

        existing_heading_rows: Dict[str, LedgerRow] = {}
        for row in rows:
            if (
                row.file_location == str(rel_path)
                and row.object_id != project_row.object_id
                and row.object_id.startswith(project_row.object_id)
                and row.type.lower() == "project"
            ):
                existing_heading_rows[heading_key_from_row(row)] = row

        def nearest_relevant_parent(node: HeadingNode) -> Optional[HeadingNode]:
            parent = node.parent
            while parent and not parent.has_task_descendants:
                parent = parent.parent
            return parent

        for heading in structure.headings:
            if not heading.has_task_descendants:
                continue

            parent_heading = nearest_relevant_parent(heading)
            parent_id = (
                project_row.object_id
                if parent_heading is None
                else heading_id_map[parent_heading]
            )

            counter_key = parent_id
            index = heading_child_counts[counter_key] + 1
            candidate_id = f"{parent_id}.{index}"
            heading_key = heading_key_from_title(heading.title or candidate_id)

            existing_row = existing_heading_rows.get(heading_key)

            if existing_row:
                candidate_id = existing_row.object_id
                if candidate_id.startswith(f"{parent_id}."):
                    suffix = candidate_id[len(parent_id) + 1 :]
                    if suffix.isdigit():
                        heading_child_counts[counter_key] = max(
                            heading_child_counts[counter_key], int(suffix)
                        )
                else:
                    heading_child_counts[counter_key] = max(heading_child_counts[counter_key], index)
            else:
                while candidate_id in id_to_row or candidate_id in load_deleted_ids():
                    index += 1
                    candidate_id = f"{parent_id}.{index}"
                heading_child_counts[counter_key] = max(heading_child_counts[counter_key], index)
                canonical_text = canonicalize_text(heading.title or candidate_id)
                new_row = LedgerRow(
                    object_id=candidate_id,
                    type="Project",
                    checksum=checksum(canonical_text),
                    canonical_text=canonical_text,
                    colloquial_name=heading.title or candidate_id,
                    current_state=project_row.current_state or "Active",
                    file_location=str(rel_path),
                    tags=list(project_row.tags),
                )
                rows.append(new_row)
                id_to_row[new_row.object_id] = new_row
                existing_row = new_row

            canonical_text = canonicalize_text(heading.title or existing_row.colloquial_name or candidate_id)
            existing_row.type = existing_row.type or "Project"
            if heading.title:
                existing_row.colloquial_name = heading.title
            elif not existing_row.colloquial_name:
                existing_row.colloquial_name = candidate_id
            if not existing_row.canonical_text:
                existing_row.canonical_text = canonical_text
            if existing_row.canonical_text and not existing_row.checksum:
                existing_row.checksum = checksum(existing_row.canonical_text)
            existing_row.file_location = str(rel_path)
            if not existing_row.current_state:
                existing_row.current_state = project_row.current_state or "Active"
            existing_row.parent_object_id = parent_id

            heading.resolved_object_id = existing_row.object_id
            heading_id_map[heading] = existing_row.object_id
            seen_ids.add(existing_row.object_id)
            child_map[parent_id].append(existing_row.object_id)
            existing_heading_rows[heading_key] = existing_row

            update_text_index(text_index, existing_row)

        for task in structure.tasks:
            parent_id: Optional[str] = None
            if task.parent and task.parent.resolved_object_id:
                parent_id = task.parent.resolved_object_id
            elif task.heading and task.heading.resolved_object_id:
                parent_id = task.heading.resolved_object_id
            else:
                parent_id = project_row.object_id

            object_id = task.object_id
            if object_id and PROJECT_CHILD_ID_PATTERN.match(object_id):
                object_id = None
            if object_id and object_id not in id_to_row:
                object_id = None

            if not object_id:
                normalized = normalize_for_match(task.text)
                candidates = [
                    cid
                    for cid in text_index.get(normalized, [])
                    if cid in id_to_row
                    and id_to_row[cid].parent_object_id == parent_id
                    and not PROJECT_CHILD_ID_PATTERN.match(cid)
                ]
                if len(candidates) == 1:
                    object_id = candidates[0]

            if not object_id:
                parent_row = id_to_row.get(parent_id)
                if parent_row and parent_row.object_id.startswith("P"):
                    object_id = allocate_task_id()
                else:
                    object_id = next_child_id(parent_id, id_to_row)

            if object_id in id_to_row:
                row = id_to_row[object_id]
            else:
                canonical_text = canonicalize_text(task.text)
                parent_tags = list(id_to_row[parent_id].tags) if parent_id in id_to_row else []
                row = LedgerRow(
                    object_id=object_id,
                    type="Task",
                    checksum=checksum(canonical_text),
                    canonical_text=canonical_text,
                    colloquial_name=task.text,
                    current_state="Complete" if task.completed else "Active",
                    file_location=str(rel_path),
                    tags=parent_tags,
                )
                rows.append(row)
                id_to_row[object_id] = row

            task.resolved_object_id = object_id
            seen_ids.add(object_id)

            row.type = row.type or "Task"
            row.colloquial_name = task.text
            if not row.canonical_text:
                row.canonical_text = canonicalize_text(task.text)
            if row.canonical_text and not row.checksum:
                row.checksum = checksum(row.canonical_text)
            if task.completed:
                row.current_state = "Complete"
            elif row.current_state.lower() == "complete" or not row.current_state:
                row.current_state = "Active"

            row.parent_object_id = parent_id
            child_map[parent_id].append(row.object_id)

            parent_row = id_to_row.get(parent_id)
            if parent_row and parent_row.file_location:
                row.file_location = parent_row.file_location
            else:
                row.file_location = str(rel_path)

            if task.notes:
                row.notes = "\n".join(task.notes)

            update_text_index(text_index, row)

        for parent_id, children in child_map.items():
            parent_row = id_to_row.get(parent_id)
            if parent_row:
                parent_row.child_object_ids = join_list(unique_preserve(children))

        for row in list(rows):
            if row.file_location == str(rel_path) and row.object_id not in seen_ids:
                reason = f"Removed from {rel_path.name} during synchronization"
                remove_row(rows, id_to_row, row.object_id, reason)

def rebuild_s3_sections(rows: Sequence[LedgerRow], buckets: Sequence[Bucket], sections: List[Section]) -> None:
    bucket_lookup = {normalize_heading(bucket.display_name): bucket for bucket in buckets}
    id_to_row: Dict[str, LedgerRow] = {row.object_id: row for row in rows if row.object_id}

    children_map: Dict[str, List[LedgerRow]] = defaultdict(list)
    for row in rows:
        if row.parent_object_id and row.parent_object_id in id_to_row:
            children_map[row.parent_object_id].append(row)

    for child_list in children_map.values():
        child_list.sort(key=lambda r: r.object_id)

    bucket_tasks: Dict[str, List[LedgerRow]] = {
        bucket.canonical_id: [
            row
            for row in rows
            if bucket.canonical_id in row.tags and not row.object_id.startswith("P")
        ]
        for bucket in buckets
    }

    def render_task(
        row: LedgerRow,
        lines: List[str],
        indent: int,
        tagged_ids: set,
    ) -> None:
        checkbox = "[x]" if row.current_state.lower() == "complete" else "[ ]"
        description = sanitize_colloquial(row.colloquial_name or row.canonical_text or "")
        if not description:
            description = row.object_id
        parent_note = ""
        if row.parent_object_id and row.parent_object_id in id_to_row:
            parent_row = id_to_row[row.parent_object_id]
            if parent_row.object_id.startswith("P"):
                parent_note = f" <small>{parent_row.canonical_text}</small>"
        prefix = "    " * indent
        lines.append(f"{prefix}- {checkbox} {description}{parent_note} [Object ID: {row.object_id}]")
        if row.notes:
            for note in row.notes.splitlines():
                note_text = note.strip()
                if note_text:
                    lines.append(f"{prefix}    {note_text}")
        for child in children_map.get(row.object_id, []):
            if child.object_id in tagged_ids:
                render_task(child, lines, indent + 1, tagged_ids)

    for section in sections:
        key = normalize_heading(section.heading)
        if key in bucket_lookup:
            bucket = bucket_lookup[key]
            tagged_ids = {row.object_id for row in bucket_tasks[bucket.canonical_id]}
            top_level = [
                row
                for row in bucket_tasks[bucket.canonical_id]
                if not row.parent_object_id or row.parent_object_id not in tagged_ids
            ]
            top_level.sort(key=lambda r: r.object_id)
            lines: List[str] = [""]
            if not top_level:
                lines.append("- [ ] _(No tracked items)_")
            else:
                for row in top_level:
                    render_task(row, lines, 0, tagged_ids)
            section.lines = lines
        elif key.lower() == "active projects":
            active_projects = [
                row
                for row in rows
                if row.object_id.startswith("P")
                and row.current_state.lower() not in {"complete", "archived"}
            ]
            active_projects.sort(key=lambda r: r.object_id)
            if active_projects:
                lines = [""]
                for project in active_projects:
                    checkbox = "[x]" if project.current_state.lower() == "complete" else "[ ]"
                    description = sanitize_colloquial(
                        project.colloquial_name or project.canonical_text or ""
                    )
                    if not description:
                        description = project.object_id
                    lines.append(f"* {checkbox} {description} [Object ID: {project.object_id}]")
            else:
                lines = ["", "* [ ] _(No active projects)_"]
            section.lines = lines


def ensure_today_and_completion(rows: List[LedgerRow]) -> None:
    id_set = [row.object_id for row in rows]
    latest_ids, completed_ids, latest_card_path = parse_cards(id_set)
    ensure_today_tags(rows, latest_ids)
    id_to_row = {row.object_id: row for row in rows}
    mark_completed_from_cards(id_to_row, completed_ids)
    if latest_card_path:
        update_today_card_snapshot(latest_card_path, rows, latest_ids)


def run_workflow() -> None:
    fieldnames, ledger_rows = load_ledger()
    buckets = load_buckets()
    sections = parse_s3_sections()

    prune_invalid_project_children(ledger_rows)
    process_s3_sections(ledger_rows, buckets, sections)
    process_project_files(ledger_rows)
    prune_invalid_project_children(ledger_rows)
    ensure_today_and_completion(ledger_rows)
    rebuild_s3_sections(ledger_rows, buckets, sections)

    save_ledger(fieldnames, ledger_rows)
    write_s3_sections(sections)


def main(argv: Optional[Sequence[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Synchronize Kinetic planning documents with the ID index.")
    parser.add_argument("--run", action="store_true", help="Execute the reconciliation workflow.")
    args = parser.parse_args(argv)

    if args.run:
        run_workflow()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
