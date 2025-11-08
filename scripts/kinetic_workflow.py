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
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
KII_PATH = REPO_ROOT / "Kinetic-ID-Index.csv"
S3_PATH = REPO_ROOT / "S3.md"
S3_BUCKETS_PATH = REPO_ROOT / "S3-Buckets.csv"
CARDS_PATH = REPO_ROOT / "Cards"


CARD_DATE_PATTERN = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
CHECKBOX_PATTERN = re.compile(r"\[[xX ]\]")
OBJECT_ID_PATTERN = re.compile(r"\b([A-Z]\d+)\b")
OBJECT_ID_SUFFIX_PATTERN = re.compile(r"\[\s*Object ID\s*:\s*([A-Z]\d+)\s*\]")


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
class S3Item:
    text: str
    checkbox: str
    object_id: Optional[str]
    completed: bool


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


def extract_s3_items(lines: Sequence[str]) -> List[S3Item]:
    items: List[S3Item] = []
    for line in lines:
        if "[" not in line:
            continue
        checkbox_match = CHECKBOX_PATTERN.search(line)
        if not checkbox_match:
            continue
        checkbox = checkbox_match.group(0)
        completed = checkbox.lower() == "[x]"
        obj_match = OBJECT_ID_SUFFIX_PATTERN.search(line)
        object_id = obj_match.group(1) if obj_match else None
        text = sanitize_colloquial(line)
        if not text:
            continue
        normalized = text.strip().strip("()").strip().lower()
        if normalized == "no tracked items":
            continue
        items.append(S3Item(text=text, checkbox=checkbox, object_id=object_id, completed=completed))
    return items


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


def next_task_id(rows: Sequence[LedgerRow]) -> str:
    prefix = "T"
    highest = 0
    for row in rows:
        if row.object_id.startswith(prefix):
            try:
                highest = max(highest, int(row.object_id[len(prefix):]))
            except ValueError:
                continue
    return f"{prefix}{highest + 1}"


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


def parse_cards(existing_ids: Iterable[str]) -> Tuple[List[str], List[str]]:
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

    return latest_ids, completed_ids


def set_bucket_tag(row: LedgerRow, bucket_id: str, known_bucket_ids: Iterable[str]) -> None:
    existing = [tag for tag in row.tags if tag not in known_bucket_ids]
    if bucket_id not in existing:
        existing.append(bucket_id)
    row.tags = existing


def process_s3_sections(rows: List[LedgerRow], buckets: Sequence[Bucket], sections: List[Section]) -> None:
    id_to_row: Dict[str, LedgerRow] = {row.object_id: row for row in rows}
    bucket_lookup = {normalize_heading(bucket.display_name): bucket for bucket in buckets}
    bucket_ids = {bucket.canonical_id for bucket in buckets}
    text_index = build_colloquial_index(rows)
    next_id = None

    for section in sections:
        key = normalize_heading(section.heading)
        if key not in bucket_lookup:
            continue
        bucket = bucket_lookup[key]
        items = extract_s3_items(section.lines)

        for item in items:
            object_id = item.object_id
            if object_id and object_id not in id_to_row:
                object_id = None

            if not object_id:
                candidates = text_index.get(normalize_for_match(item.text), [])
                if len(candidates) == 1:
                    object_id = candidates[0]

            if object_id:
                row = id_to_row[object_id]
                row.colloquial_name = sanitize_colloquial(item.text)
                norm = normalize_for_match(row.colloquial_name)
                if row.object_id not in text_index[norm]:
                    text_index[norm].append(row.object_id)
                if item.completed:
                    row.current_state = "Complete"
                elif row.current_state == "Complete":
                    # Keep manually completed rows marked complete; do not auto-reset.
                    pass
                if not row.file_location:
                    row.file_location = "S3.md"
                set_bucket_tag(row, bucket.canonical_id, bucket_ids)
            else:
                if next_id is None:
                    next_id = next_task_id(rows)
                else:
                    next_number = int(next_id[1:]) + 1
                    next_id = f"T{next_number}"
                object_id = next_id
                text = sanitize_colloquial(item.text)
                canonical_text = canonicalize_text(text)
                checksum_value = checksum(canonical_text)
                new_row = LedgerRow(
                    object_id=object_id,
                    type="Task",
                    checksum=checksum_value,
                    canonical_text=canonical_text,
                    colloquial_name=text,
                    current_state="Complete" if item.completed else "Active",
                    file_location="S3.md",
                    tags=[bucket.canonical_id],
                )
                rows.append(new_row)
                id_to_row[object_id] = new_row
                text_index[normalize_for_match(text)].append(object_id)

        # Rebuild the section lines based on the ledger rows for deterministic output later
        section.lines = []

    # After processing manual edits we will rebuild sections from ledger data elsewhere.
    rows[:] = [
        row
        for row in rows
        if row.colloquial_name.strip().strip("()").strip().lower() != "no tracked items"
    ]


def rebuild_s3_sections(rows: Sequence[LedgerRow], buckets: Sequence[Bucket], sections: List[Section]) -> None:
    bucket_lookup = {normalize_heading(bucket.display_name): bucket for bucket in buckets}
    bucket_ids = {bucket.canonical_id: bucket.display_name for bucket in buckets}

    # Prepare tasks by bucket
    tasks_by_bucket: Dict[str, List[LedgerRow]] = {bucket.canonical_id: [] for bucket in buckets}
    for row in rows:
        for tag in row.tags:
            if tag in bucket_ids:
                tasks_by_bucket[tag].append(row)

    # Sort tasks within each bucket by object id for stable output
    for task_list in tasks_by_bucket.values():
        task_list.sort(key=lambda r: r.object_id)

    for section in sections:
        key = normalize_heading(section.heading)
        if key not in bucket_lookup:
            continue
        bucket = bucket_lookup[key]
        task_lines: List[str] = [""] if tasks_by_bucket[bucket.canonical_id] else ["", "- [ ] _(No tracked items)_"]

        if tasks_by_bucket[bucket.canonical_id]:
            task_lines = [""]
            for row in tasks_by_bucket[bucket.canonical_id]:
                checkbox = "[x]" if row.current_state.lower() == "complete" else "[ ]"
                description = sanitize_colloquial(row.colloquial_name or row.canonical_text or "")
                if not description:
                    description = row.object_id
                task_lines.append(f"- {checkbox} {description} [Object ID: {row.object_id}]")

        section.lines = task_lines


def ensure_today_and_completion(rows: List[LedgerRow]) -> None:
    id_set = [row.object_id for row in rows]
    latest_ids, completed_ids = parse_cards(id_set)
    ensure_today_tags(rows, latest_ids)
    id_to_row = {row.object_id: row for row in rows}
    mark_completed_from_cards(id_to_row, completed_ids)


def run_workflow() -> None:
    fieldnames, ledger_rows = load_ledger()
    buckets = load_buckets()
    sections = parse_s3_sections()

    process_s3_sections(ledger_rows, buckets, sections)
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
