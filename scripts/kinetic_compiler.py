#!/usr/bin/env python3
"""Kinetic compiler rebuilt from scratch.

This script synchronizes Markdown-defined objects with the Kinetic ID ledger.
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
from typing import Dict, Iterable, List, Optional, Set, Tuple

REPO_ROOT = Path(__file__).resolve().parent
if not (REPO_ROOT / "Kinetic-ID-Index.csv").exists():
    REPO_ROOT = REPO_ROOT.parent
LEDGER_PATH = REPO_ROOT / "Kinetic-ID-Index.csv"
MARKDOWN_DIRECTORIES = [
    REPO_ROOT,
    REPO_ROOT / "Projects",
    REPO_ROOT / "Cards",
    REPO_ROOT / "Core",
]
FIELDNAMES = [
    "Object ID",
    "Type",
    "Canonical Name (Checksum)",
    "Canonical Name (Text)",
    "Colloquial Name",
    "Current State",
    "File Location",
    "Tags",
    "People",
    "Parent Object ID",
    "Child Object IDs",
    "Notes",
    "Created At",
    "Last Modified At",
]
ID_PATTERN = re.compile(r"([A-Za-z]+)(\d+(?:\.\d+)?)")
OBJECT_ID_PATTERN = re.compile(r"objectId:\s*([A-Za-z]+\d+(?:\.\d+)?)", re.IGNORECASE)
INLINE_ID_PATTERN = re.compile(r"\{([^}]+)\}")
HASHTAG_PATTERN = re.compile(r"#([A-Za-z0-9\-]+)")
PERSON_PATTERN = re.compile(r"@([A-Za-z][A-Za-z0-9_-]*)")
CHECKBOX_PATTERN = re.compile(r"\[( |x|X)\]")


def canonicalize_text(text: str) -> str:
    return "".join(ch for ch in text if ch.isalnum())


def checksum(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def object_id_key(object_id: str) -> Tuple[str, Tuple[int, ...]]:
    match = ID_PATTERN.match(object_id or "")
    if not match:
        return (object_id or "", (0,))
    prefix, number = match.groups()
    parts = tuple(int(part) for part in number.split(".")) if number else (0,)
    return (prefix, parts)


def dedupe_latest(rows: Iterable[LedgerRow]) -> List[LedgerRow]:
    latest: Dict[Tuple[str, str], LedgerRow] = {}
    for row in rows:
        data = row.data
        type_ = data.get("Type", "")
        canonical = data.get("Canonical Name (Text)", "") or canonicalize_text(
            data.get("Colloquial Name", "")
        )
        key = (type_, canonical)
        existing = latest.get(key)
        if existing is None or object_id_key(data.get("Object ID", "")) > object_id_key(
            existing.data.get("Object ID", "")
        ):
            latest[key] = row
    return sorted(latest.values(), key=lambda row: object_id_key(row.data.get("Object ID", "")))


def split_semicolon(value: str) -> List[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(";") if part.strip()]


@dataclass
class LedgerRow:
    data: Dict[str, str]

    def update(self, **kwargs: str) -> None:
        for key, value in kwargs.items():
            if value is None:
                continue
            self.data[key] = value


class Ledger:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.rows: List[LedgerRow] = []
        self.by_id: Dict[str, LedgerRow] = {}
        self.index: Dict[Tuple[str, str, str], str] = {}
        self.name_index: Dict[Tuple[str, str, str], str] = {}
        self.name_index_reverse: Dict[str, Set[Tuple[str, str, str]]] = defaultdict(set)
        self.id_counters: Dict[str, int] = defaultdict(int)
        self._load()

    def _load(self) -> None:
        with self.path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            for raw_row in reader:
                row = LedgerRow(raw_row)
                self.rows.append(row)
                object_id = raw_row.get("Object ID", "").strip()
                if object_id:
                    self.by_id[object_id] = row
                    match = ID_PATTERN.match(object_id)
                    if match:
                        prefix, number = match.groups()
                        if "." not in number:
                            self.id_counters[prefix] = max(self.id_counters[prefix], int(number))
                key = (
                    raw_row.get("Type", ""),
                    raw_row.get("File Location", ""),
                    raw_row.get("Canonical Name (Text)", ""),
                )
                if key[0] and key[1] and key[2] and key not in self.index:
                    self.index[key] = object_id
                self.register_name(row)

    def next_id(self, prefix: str) -> str:
        self.id_counters[prefix] += 1
        return f"{prefix}{self.id_counters[prefix]}"

    def get_or_create(
        self,
        object_id: str,
        *,
        type_: str,
        file_location: str,
        canonical_text: str,
        colloquial_name: str = "",
    ) -> LedgerRow:
        if object_id:
            row = self.by_id.get(object_id)
            if row is None:
                row = self._create_row(object_id)
            return row
        key = (type_, file_location, canonical_text)
        existing_id = self.index.get(key)
        if existing_id:
            return self.by_id[existing_id]
        duplicate_key = self._name_key(type_, canonical_text, colloquial_name)
        if duplicate_key:
            existing_duplicate = self.name_index.get(duplicate_key)
            if existing_duplicate:
                print(f"[Deduped] Found existing {existing_duplicate}")
                self.index[key] = existing_duplicate
                return self.by_id[existing_duplicate]
        new_id = self.next_id(prefix_for_type(type_))
        row = self._create_row(new_id)
        self.index[key] = new_id
        return row

    def _create_row(self, object_id: str) -> LedgerRow:
        data = {field: "" for field in FIELDNAMES}
        data["Object ID"] = object_id
        row = LedgerRow(data)
        self.rows.append(row)
        self.by_id[object_id] = row
        return row

    def _name_key(
        self, type_: str, canonical_text: str, colloquial_name: str
    ) -> Optional[Tuple[str, str, str]]:
        type_key = (type_ or "").strip().lower()
        canonical_key = (canonical_text or "").strip().lower()
        colloquial_key = (colloquial_name or "").strip().lower()
        if not colloquial_key:
            return None
        if not canonical_key:
            canonical_key = canonicalize_text(colloquial_name)
        if not type_key or not canonical_key:
            return None
        return (type_key, canonical_key, colloquial_key)

    def register_name(self, row: LedgerRow) -> None:
        object_id = (row.data.get("Object ID") or "").strip()
        if not object_id:
            return
        old_keys = list(self.name_index_reverse.get(object_id, set()))
        for key in old_keys:
            existing = self.name_index.get(key)
            if existing == object_id:
                self.name_index.pop(key, None)
        self.name_index_reverse[object_id] = set()
        key = self._name_key(
            row.data.get("Type", ""),
            row.data.get("Canonical Name (Text)", ""),
            row.data.get("Colloquial Name", ""),
        )
        if key:
            self.name_index[key] = object_id
            self.name_index_reverse[object_id].add(key)

    def write(self) -> None:
        # Sort rows by Object ID to maintain stability
        self.rows.sort(key=lambda row: row.data.get("Object ID", ""))
        with self.path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
            writer.writeheader()
            for row in self.rows:
                writer.writerow({field: row.data.get(field, "") for field in FIELDNAMES})


def prefix_for_type(type_: str) -> str:
    mapping = {
        "AOR": "A",
        "Goal": "G",
        "Relationship": "R",
        "Project": "P",
        "Task": "T",
        "Card": "C",
    }
    return mapping.get(type_, type_[:1].upper())


@dataclass
class ParsedObject:
    type: str
    file_location: str
    colloquial_name: str
    current_state: str = ""
    notes: str = ""
    tags: Set[str] = field(default_factory=set)
    people: Set[str] = field(default_factory=set)
    parent_id: str = ""
    object_id: str = ""
    canonical_text: str = ""

    def finalize(self) -> None:
        if not self.canonical_text:
            self.canonical_text = canonicalize_text(self.colloquial_name)


class Compiler:
    def __init__(self, ledger: Ledger) -> None:
        self.ledger = ledger
        self.objects: Dict[str, ParsedObject] = {}
        self.child_links: Dict[str, Set[str]] = defaultdict(set)
        self.projects_index: Dict[str, Dict[str, str]] = {}
        self.summary_counts = defaultdict(int)
        self.counted_objects: Set[str] = set()

    def run(self, *, dry_run: bool = False) -> None:
        self._parse_projects_index()
        self._parse_core()
        self._parse_markdown_files()
        self._apply_updates()
        if dry_run:
            print(f"[dry-run] Would update {self.ledger.path.name}")
        else:
            self.ledger.write()
        self._print_summary()

    def _parse_projects_index(self) -> None:
        index_path = REPO_ROOT / "Projects.md"
        if not index_path.exists():
            return
        text = index_path.read_text(encoding="utf-8")
        if "<details>" in text:
            self.projects_index = self._parse_legacy_projects_index(text)
        else:
            self.projects_index = self._parse_structured_projects_index(text)

    def _parse_legacy_projects_index(self, text: str) -> Dict[str, Dict[str, str]]:
        depth = 0
        current_id: Optional[str] = None
        body_lines: List[str] = []
        file_path_map: Dict[str, str] = {}
        notes_by_id: Dict[str, Dict[str, str]] = {}
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if "<details>" in line:
                depth += line.count("<details>")
                continue
            if "</details>" in line:
                depth -= line.count("</details>")
                if depth == 0 and current_id:
                    notes = "\n".join(body_lines).strip()
                    notes_by_id[current_id] = {
                        "notes": notes,
                        "file": file_path_map.get(current_id, ""),
                    }
                    body_lines = []
                    current_id = None
                continue
            if depth == 1 and line.startswith("<summary>") and "{" in line and "}" in line:
                summary_text = re.sub(r"<\\/?summary>", "", line).strip()
                match = re.search(r"\{([A-Za-z]+\d+(?:\.\d+)?)\}", summary_text)
                if not match:
                    continue
                current_id = match.group(1)
                file_path_map.setdefault(current_id, "")
                body_lines = []
                continue
            if depth != 1 or current_id is None:
                continue
            if line.startswith("<details>") or line.startswith("</details>"):
                continue
            clean = re.sub(r"\*\*(.*?)\*\*", r"\1", line).strip()
            if not clean:
                continue
            if clean.lower().startswith("file "):
                _, _, file_value = clean.partition("file ")
                file_path_map[current_id] = file_value.strip()
                continue
            body_lines.append(clean)
        # Merge any file mapping into notes entries
        for object_id, payload in list(notes_by_id.items()):
            file_value = file_path_map.get(object_id, "")
            if file_value:
                payload["file"] = file_value
            notes_by_id[object_id] = payload
        return notes_by_id

    def _parse_structured_projects_index(self, text: str) -> Dict[str, Dict[str, str]]:
        notes_by_id: Dict[str, Dict[str, str]] = {}
        current_id = ""
        file_location = ""
        collecting_notes = False
        note_lines: List[str] = []
        for raw_line in text.splitlines():
            stripped = raw_line.strip()
            if stripped.startswith("## Manual Projects"):
                break
            if stripped.startswith("### "):
                if current_id:
                    notes_by_id[current_id] = {
                        "notes": "\n".join(note_lines).strip(),
                        "file": file_location,
                    }
                header_text = stripped[4:].strip()
                match = re.search(r"\{([A-Za-z]+\d+(?:\.\d+)?)\}", header_text)
                current_id = match.group(1) if match else ""
                file_location = ""
                collecting_notes = False
                note_lines = []
                continue
            if not current_id:
                continue
            if stripped.startswith("- File:"):
                link_match = re.search(r"\(([^)]+)\)", stripped)
                if link_match:
                    file_location = link_match.group(1).strip()
                else:
                    _, _, file_value = stripped.partition(":")
                    file_location = file_value.strip()
                continue
            if stripped.startswith("#### "):
                collecting_notes = stripped.lower().startswith("#### notes")
                if collecting_notes:
                    note_lines = []
                continue
            if collecting_notes:
                if stripped.startswith("- "):
                    note_lines.append(stripped[2:].strip())
                elif stripped == "-":
                    note_lines.append("")
                elif stripped == "":
                    note_lines.append("")
                else:
                    note_lines.append(stripped)
        if current_id:
            notes_by_id[current_id] = {
                "notes": "\n".join(note_lines).strip(),
                "file": file_location,
            }
        return notes_by_id

    def _parse_core(self) -> None:
        core_path = REPO_ROOT / "Core.md"
        if not core_path.exists():
            return
        lines = core_path.read_text(encoding="utf-8").splitlines()
        section = None
        current_aor_id = ""
        current_relationship_id = ""
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("## "):
                header = stripped[3:].strip()
                if header == "Areas of Responsibility":
                    section = "AOR"
                elif header == "Relationships":
                    section = "Relationships"
                else:
                    section = None
                continue
            if section == "AOR":
                if stripped.startswith("### "):
                    name = stripped[4:].strip()
                elif stripped.startswith("- ") and not stripped.startswith("- Goal:"):
                    name = stripped[2:].strip()
                else:
                    name = ""
                if name:
                    tokens = name.split()
                    trailing_people: Set[str] = set()
                    while tokens and tokens[-1].startswith("@"):
                        trailing_people.add(tokens.pop())
                    base_name = " ".join(tokens).strip() or name
                    people = set(f"@{p}" for p in PERSON_PATTERN.findall(base_name)) | trailing_people
                    obj = ParsedObject(
                        type="AOR",
                        file_location="Core.md",
                        colloquial_name=base_name,
                        current_state="Active",
                        people=people,
                    )
                    obj.finalize()
                    ledger_row = self.ledger.get_or_create(
                        object_id="",
                        type_=obj.type,
                        file_location=obj.file_location,
                        canonical_text=obj.canonical_text,
                        colloquial_name=obj.colloquial_name,
                    )
                    object_id = ledger_row.data["Object ID"]
                    obj.object_id = object_id
                    self.objects[object_id] = obj
                    current_aor_id = object_id
                    self.summary_counts["Project"] += 0  # ensure key exists
                    continue
                if stripped.startswith("* ") and "Goal:" in stripped:
                    goal_text = stripped.split("Goal:", 1)[1].strip()
                elif stripped.startswith("- Goal:"):
                    goal_text = stripped.split("Goal:", 1)[1].strip()
                else:
                    goal_text = ""
                if goal_text and current_aor_id:
                    goal_clean = goal_text.rstrip(".") + "."
                    obj = ParsedObject(
                        type="Goal",
                        file_location="Core.md",
                        colloquial_name=goal_clean,
                        parent_id=current_aor_id,
                        current_state="Active",
                    )
                    obj.finalize()
                    ledger_row = self.ledger.get_or_create(
                        object_id="",
                        type_=obj.type,
                        file_location=obj.file_location,
                        canonical_text=obj.canonical_text,
                        colloquial_name=obj.colloquial_name,
                    )
                    object_id = ledger_row.data["Object ID"]
                    obj.object_id = object_id
                    self.objects[object_id] = obj
                    self.child_links[current_aor_id].add(object_id)
                    continue
            if section == "Relationships":
                if stripped.startswith("### "):
                    handle_text = stripped[4:].strip()
                    display_name = handle_text.lstrip("@")
                    handle = handle_text if handle_text.startswith("@") else ""
                elif stripped.startswith("- ") and not stripped.startswith("- Notes:"):
                    payload = stripped[2:].strip()
                    if " — " in payload:
                        handle_part, display_name = payload.split(" — ", 1)
                    else:
                        handle_part, display_name = payload, ""
                    handle = handle_part.strip() if handle_part.strip().startswith("@") else ""
                    if not display_name:
                        display_name = handle_part.lstrip("@") if handle else payload
                else:
                    handle = ""
                    display_name = ""
                if handle or display_name:
                    obj = ParsedObject(
                        type="Relationship",
                        file_location="Core.md",
                        colloquial_name=display_name.strip() or handle.lstrip("@"),
                        current_state="Active",
                        people={handle} if handle else set(),
                    )
                    obj.finalize()
                    ledger_row = self.ledger.get_or_create(
                        object_id="",
                        type_=obj.type,
                        file_location=obj.file_location,
                        canonical_text=obj.canonical_text,
                        colloquial_name=obj.colloquial_name,
                    )
                    object_id = ledger_row.data["Object ID"]
                    obj.object_id = object_id
                    self.objects[object_id] = obj
                    current_relationship_id = object_id
                    continue
                if stripped.startswith("Notes:"):
                    notes = stripped.split("Notes:", 1)[1].strip()
                elif stripped.startswith("- Notes:"):
                    notes = stripped.split("Notes:", 1)[1].strip()
                else:
                    notes = ""
                if notes and current_relationship_id:
                    rel_obj = self.objects.get(current_relationship_id)
                    if rel_obj and rel_obj.type == "Relationship":
                        rel_obj.notes = notes

    def _parse_markdown_files(self) -> None:
        project_files_seen: Dict[str, str] = {}
        for directory in MARKDOWN_DIRECTORIES:
            if not directory.exists():
                continue
            for path in sorted(directory.rglob("*.md")):
                if path.is_relative_to(REPO_ROOT / "Views"):
                    continue
                relative = path.relative_to(REPO_ROOT).as_posix()
                if relative == "Projects.md" or relative == "Core.md" or relative == "S3.md":
                    if relative == "Projects.md":
                        self._sync_project_notes()
                        self._parse_manual_projects(path)
                    if relative == "S3.md":
                        self._parse_s3(path)
                    continue
                if "/" not in relative and relative.startswith("README"):
                    continue
                if relative.startswith("Cards/"):
                    self._parse_card(path)
                elif relative.startswith("Projects/"):
                    project_id = self._parse_project(path)
                    if project_id:
                        project_files_seen[project_id] = relative
                else:
                    self._parse_generic_markdown(path)
        # Ensure project file locations from index are applied if discovered
        for project_id, payload in self.projects_index.items():
            file_path = payload.get("file", "").strip()
            if not file_path and project_id in project_files_seen:
                file_path = project_files_seen[project_id]
            if not file_path:
                continue
            row = self.ledger.by_id.get(project_id)
            if row:
                row.update(**{"File Location": file_path})

    def _sync_project_notes(self) -> None:
        for project_id, payload in self.projects_index.items():
            notes = payload.get("notes", "").strip()
            row = self.ledger.by_id.get(project_id)
            if row is None:
                continue
            row.update(**{"Notes": notes})

    def _parse_manual_projects(self, path: Path) -> None:
        lines = self._read_lines(path)
        in_manual = False
        current_name = ""
        current_object_id = ""
        current_status = ""
        current_file = ""
        current_notes: List[str] = []
        collecting_notes = False
        current_lines: List[str] = []
        current_people: Set[str] = set()
        current_tags: Set[str] = set()

        def finalize_current() -> None:
            nonlocal current_name, current_object_id, current_status, current_file
            nonlocal current_notes, current_lines, current_people, current_tags
            nonlocal collecting_notes
            if not current_name:
                current_object_id = ""
                current_status = ""
                current_file = ""
                current_notes = []
                current_lines = []
                current_people = set()
                current_tags = set()
                collecting_notes = False
                return
            project_obj = ParsedObject(
                type="Project",
                file_location=(current_file or "Projects.md").strip() or "Projects.md",
                colloquial_name=current_name,
                current_state=(current_status or "Active").strip(),
                people=set(current_people),
                tags=set(current_tags),
            )
            if current_notes:
                project_obj.notes = "\n".join(current_notes).strip()
            project_obj.object_id = current_object_id
            project_obj.finalize()
            ledger_row = self.ledger.get_or_create(
                object_id=current_object_id,
                type_=project_obj.type,
                file_location=project_obj.file_location,
                canonical_text=project_obj.canonical_text,
                colloquial_name=project_obj.colloquial_name,
            )
            object_id = ledger_row.data["Object ID"]
            project_obj.object_id = object_id
            self.objects[object_id] = project_obj
            self.summary_counts["Projects"] += 1
            if current_lines:
                self._parse_tasks(
                    path,
                    parent_id=object_id,
                    lines=current_lines,
                )
            current_name = ""
            current_object_id = ""
            current_status = ""
            current_file = ""
            current_notes = []
            current_lines = []
            current_people = set()
            current_tags = set()
            collecting_notes = False

        for raw_line in lines:
            stripped = raw_line.strip()
            if stripped.startswith("## "):
                header_text = stripped[3:].strip().lower()
                if header_text.startswith("manual projects"):
                    if in_manual:
                        finalize_current()
                    in_manual = True
                    continue
                if in_manual:
                    finalize_current()
                    in_manual = False
                continue
            if not in_manual:
                continue
            if stripped.startswith("### "):
                finalize_current()
                heading_text = stripped[4:].strip()
                current_object_id = self._extract_object_id(heading_text)
                name_without_ids = re.sub(r"\{[^}]+\}", "", heading_text).strip()
                current_name = name_without_ids
                current_people = self._extract_people(heading_text)
                current_tags = self._extract_tags(heading_text)
                current_lines = []
                current_status = ""
                current_file = ""
                current_notes = []
                collecting_notes = False
                continue
            if not current_name:
                continue
            current_lines.append(raw_line)
            lowered = stripped.lower()
            if lowered.startswith("- status:"):
                current_status = stripped.split(":", 1)[1].strip()
                continue
            if lowered.startswith("- file:"):
                link_match = re.search(r"\(([^)]+)\)", stripped)
                if link_match:
                    current_file = link_match.group(1).strip()
                else:
                    current_file = stripped.split(":", 1)[1].strip()
                continue
            if lowered.startswith("- people:"):
                current_people.update(f"@{match}" for match in PERSON_PATTERN.findall(stripped))
                continue
            if lowered.startswith("- tags:"):
                for tag in HASHTAG_PATTERN.findall(stripped):
                    current_tags.add(tag)
                continue
            if stripped.startswith("#### "):
                collecting_notes = stripped.lower().startswith("#### notes")
                if not collecting_notes:
                    continue
                current_notes = []
                continue
            if collecting_notes:
                if stripped.startswith("- "):
                    current_notes.append(stripped[2:].strip())
                elif stripped == "-":
                    current_notes.append("")
                else:
                    current_notes.append(stripped)

        if in_manual:
            finalize_current()

    def _read_lines(self, path: Path) -> List[str]:
        try:
            return path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            return path.read_bytes().decode("utf-8", errors="replace").splitlines()

    def _parse_project(self, path: Path) -> Optional[str]:
        relative = path.relative_to(REPO_ROOT).as_posix()
        lines = self._read_lines(path)
        title = ""
        status = ""
        people: Set[str] = set()
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("# "):
                heading = stripped.lstrip("# ").strip()
                match = re.search(r"Project:\s*(.+)", heading, flags=re.IGNORECASE)
                title = match.group(1).strip() if match else heading
            if stripped.startswith("**Status:"):
                status = stripped.split("**Status:", 1)[1].strip().strip("*")
            people.update(f"@{p}" for p in PERSON_PATTERN.findall(stripped))
        if not title:
            title = path.stem.replace("Project-", "").replace("-", " ")
        project_obj = ParsedObject(
            type="Project",
            file_location=relative,
            colloquial_name=title,
            current_state=(status or "Active").strip(),
            people=people,
        )
        project_obj.finalize()
        ledger_row = self.ledger.get_or_create(
            object_id="",
            type_=project_obj.type,
            file_location=project_obj.file_location,
            canonical_text=project_obj.canonical_text,
            colloquial_name=project_obj.colloquial_name,
        )
        project_id = ledger_row.data["Object ID"]
        project_obj.object_id = project_id
        # Merge notes from index if available
        notes_payload = self.projects_index.get(project_id, {})
        if notes_payload:
            project_obj.notes = notes_payload.get("notes", "")
        self.objects[project_id] = project_obj
        self.summary_counts["Projects"] += 1
        self._parse_tasks(path, project_id)
        return project_id

    def _parse_generic_markdown(self, path: Path) -> None:
        relative = path.relative_to(REPO_ROOT).as_posix()
        if relative == "Core.md":
            return
        if relative == "Projects.md":
            return
        self._parse_tasks(path, parent_id="")

    def _parse_card(self, path: Path) -> None:
        relative = path.relative_to(REPO_ROOT).as_posix()
        card_obj = ParsedObject(
            type="Card",
            file_location=relative,
            colloquial_name=path.stem,
        )
        card_obj.finalize()
        ledger_row = self.ledger.get_or_create(
            object_id="",
            type_=card_obj.type,
            file_location=card_obj.file_location,
            canonical_text=card_obj.canonical_text,
            colloquial_name=card_obj.colloquial_name,
        )
        card_id = ledger_row.data["Object ID"]
        card_obj.object_id = card_id
        self.objects[card_id] = card_obj
        self._parse_tasks(path, parent_id=card_id)

    def _parse_s3(self, path: Path) -> None:
        lines = self._read_lines(path)
        coming_up_lines: Set[int] = set()
        section: Optional[str] = None
        bucket_tag = ""
        bucket_pattern = re.compile(r"\((S3-[^)]+)\)")
        for index, raw_line in enumerate(lines):
            stripped = raw_line.strip()
            if stripped.startswith("### "):
                header = stripped[4:].strip().lower()
                if header.startswith("active buckets"):
                    section = "buckets"
                elif header.startswith("coming up"):
                    section = "coming"
                    bucket_tag = ""
                else:
                    section = None
                continue
            if section == "coming" and CHECKBOX_PATTERN.search(raw_line):
                coming_up_lines.add(index)
        self._parse_tasks(
            path,
            parent_id="",
            lines=lines,
            allow_new_lines=coming_up_lines,
        )
        section = None
        bucket_tag = ""
        for raw_line in lines:
            stripped = raw_line.strip()
            if stripped.startswith("### "):
                header = stripped[4:].strip().lower()
                if header.startswith("active buckets"):
                    section = "buckets"
                elif header.startswith("coming up"):
                    section = "coming"
                    bucket_tag = ""
                else:
                    section = None
                continue
            if section == "buckets" and stripped.startswith("#### "):
                match = bucket_pattern.search(stripped)
                bucket_tag = match.group(1) if match else ""
                continue
            if section in {"buckets", "coming"} and stripped.startswith("- "):
                object_id = self._extract_object_id(stripped)
                if not object_id or object_id.upper().startswith("S3-"):
                    continue
                row = self.ledger.by_id.get(object_id)
                if row is None:
                    continue
                existing_tags = split_semicolon(row.data.get("Tags", ""))
                non_s3_tags = [tag for tag in existing_tags if not tag.upper().startswith("S3-")]
                updated_tags = list(non_s3_tags)
                if section == "buckets" and bucket_tag:
                    if bucket_tag not in updated_tags:
                        updated_tags.append(bucket_tag)
                joined = "; ".join(sorted(updated_tags))
                if joined != row.data.get("Tags", ""):
                    row.update(**{"Tags": joined})

    def _parse_tasks(
        self,
        path: Path,
        parent_id: str,
        *,
        lines: Optional[List[str]] = None,
        allow_new_lines: Optional[Set[int]] = None,
    ) -> None:
        relative = path.relative_to(REPO_ROOT).as_posix()
        source_lines = list(lines) if lines is not None else self._read_lines(path)
        task_stack: List[Tuple[int, str]] = []
        for index, raw_line in enumerate(source_lines):
            checkbox_match = CHECKBOX_PATTERN.search(raw_line)
            if not checkbox_match:
                continue
            prefix = raw_line[: checkbox_match.start()]
            indent = len(prefix.expandtabs(4))
            while task_stack and indent <= task_stack[-1][0]:
                task_stack.pop()
            status_char = checkbox_match.group(1)
            status = "Complete" if status_char.lower() == "x" else "Active"
            content = raw_line[checkbox_match.end():].strip()
            object_id = self._extract_object_id(raw_line)
            allow_new = allow_new_lines is None or index in allow_new_lines
            if not object_id and not allow_new:
                continue
            tags = self._extract_tags(raw_line)
            people = self._extract_people(raw_line)
            clean_text = self._clean_task_text(content)
            parsed = ParsedObject(
                type="Task",
                file_location=relative,
                colloquial_name=clean_text,
                current_state=status,
                tags=tags,
                people=people,
            )
            parsed.finalize()
            ledger_row = self.ledger.get_or_create(
                object_id=object_id,
                type_=parsed.type,
                file_location=parsed.file_location,
                canonical_text=parsed.canonical_text,
                colloquial_name=parsed.colloquial_name,
            )
            object_id = ledger_row.data["Object ID"]
            parsed.object_id = object_id
            parent_for_task = parent_id
            if task_stack:
                parent_for_task = task_stack[-1][1]
            parsed.parent_id = parent_for_task
            if parent_for_task:
                self.child_links[parent_for_task].add(object_id)
            self.objects[object_id] = parsed
            task_stack.append((indent, object_id))
            if object_id not in self.counted_objects:
                self.summary_counts["Tasks"] += 1
                if not parsed.tags:
                    self.summary_counts["Unbucketed"] += 1
                self.counted_objects.add(object_id)

    def _extract_object_id(self, line: str) -> str:
        match = OBJECT_ID_PATTERN.search(line)
        if match:
            return match.group(1)
        for block in INLINE_ID_PATTERN.findall(line):
            for token in block.split():
                token = token.strip("{},")
                if re.fullmatch(r"[A-Za-z]+\d+(?:\.\d+)?", token):
                    return token
        return ""

    def _extract_tags(self, line: str) -> Set[str]:
        tags = set()
        for match in HASHTAG_PATTERN.findall(line):
            tags.add(match)
        for block in INLINE_ID_PATTERN.findall(line):
            for token in block.split():
                if token.startswith("#"):
                    tags.add(token.lstrip("#"))
                elif not re.fullmatch(r"[A-Za-z]+\d+(?:\.\d+)?", token) and re.search(r"[A-Za-z]", token):
                    tags.add(token)
        return tags

    def _extract_people(self, line: str) -> Set[str]:
        return {f"@{match}" for match in PERSON_PATTERN.findall(line)}

    def _clean_task_text(self, text: str) -> str:
        text = re.sub(r"\(objectId: [^)]+\)", "", text, flags=re.IGNORECASE)
        text = INLINE_ID_PATTERN.sub("", text)
        if "|" in text:
            text = text.split("|", 1)[0]
        text = re.sub(r"\s+", " ", text)
        cleaned = text.strip()
        cleaned = re.sub(r"^[\*\+\-–—]+\s*", "", cleaned)
        cleaned = re.sub(r"\s*[\*\+\-–—]+$", "", cleaned)
        return cleaned.strip()

    def _apply_updates(self) -> None:
        for object_id, parsed in self.objects.items():
            parsed.finalize()
            row = self.ledger.by_id.get(object_id)
            if not row:
                continue
            canonical_text = parsed.canonical_text
            updates = {
                "Type": parsed.type,
                "Canonical Name (Text)": canonical_text,
                "Canonical Name (Checksum)": checksum(canonical_text),
                "Colloquial Name": parsed.colloquial_name.strip(),
                "File Location": parsed.file_location,
            }
            if parsed.current_state:
                updates["Current State"] = parsed.current_state.strip()
            if parsed.tags or row.data.get("Tags"):
                updates["Tags"] = "; ".join(sorted(parsed.tags))
            if parsed.people or row.data.get("People"):
                updates["People"] = "; ".join(sorted(parsed.people))
            updates["Parent Object ID"] = parsed.parent_id
            existing_notes = row.data.get("Notes", "")
            if parsed.notes and parsed.notes != existing_notes:
                updates["Notes"] = parsed.notes
            elif not parsed.notes:
                updates["Notes"] = existing_notes
            row.update(**updates)
            self.ledger.register_name(row)
        for parent_id, children in self.child_links.items():
            row = self.ledger.by_id.get(parent_id)
            if row:
                row.update(**{"Child Object IDs": "; ".join(sorted(children))})

    def _print_summary(self) -> None:
        projects = self.summary_counts.get("Projects", 0)
        tasks = self.summary_counts.get("Tasks", 0)
        unbucketed = self.summary_counts.get("Unbucketed", 0)
        print(f"Projects: {projects} | Tasks: {tasks} | Unbucketed: {unbucketed}")


def main(argv: Optional[Iterable[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Compile Kinetic ledger and views.")
    parser.add_argument(
        "--generate",
        nargs="+",
        choices=["all", "s3", "projects", "core"],
        help="Regenerate markdown views after compilation.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without writing to disk.",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    ledger = Ledger(LEDGER_PATH)
    compiler = Compiler(ledger)
    compiler.run(dry_run=args.dry_run)

    targets: List[str] = []
    if args.generate:
        selected = set(args.generate)
        if "all" in selected:
            targets = ["s3", "projects", "core"]
        else:
            order = ["s3", "projects", "core"]
            targets = [name for name in order if name in selected]

    if not targets:
        return

    try:
        from kinetic_workflow import (
            generate_core_markdown,
            generate_projects_markdown,
            generate_s3_markdown,
        )
    except ModuleNotFoundError:
        from scripts.kinetic_workflow import (
            generate_core_markdown,
            generate_projects_markdown,
            generate_s3_markdown,
        )

    generator_map = {
        "s3": (generate_s3_markdown, REPO_ROOT / "S3.md"),
        "projects": (generate_projects_markdown, REPO_ROOT / "Projects.md"),
        "core": (generate_core_markdown, REPO_ROOT / "Core.md"),
    }

    for target in targets:
        generator, path = generator_map[target]
        content = generator(ledger)
        relative = path.relative_to(REPO_ROOT).as_posix()
        if args.dry_run:
            print(f"[dry-run] Would update {relative}")
            continue
        path.write_text(content, encoding="utf-8")
        print(f"Updated {relative}")


if __name__ == "__main__":
    main()
