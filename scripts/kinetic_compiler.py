#!/usr/bin/env python3
"""Kinetic compiler rebuilt from scratch.

This script synchronizes Markdown-defined objects with the Kinetic ID ledger.
"""
from __future__ import annotations

import csv
import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

REPO_ROOT = Path(__file__).resolve().parent
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

    def next_id(self, prefix: str) -> str:
        self.id_counters[prefix] += 1
        return f"{prefix}{self.id_counters[prefix]}"

    def get_or_create(self, object_id: str, *, type_: str, file_location: str, canonical_text: str) -> LedgerRow:
        if object_id:
            row = self.by_id.get(object_id)
            if row is None:
                row = self._create_row(object_id)
            return row
        key = (type_, file_location, canonical_text)
        existing_id = self.index.get(key)
        if existing_id:
            return self.by_id[existing_id]
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

    def run(self) -> None:
        self._parse_projects_index()
        self._parse_core()
        self._parse_markdown_files()
        self._apply_updates()
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
                relative = path.relative_to(REPO_ROOT).as_posix()
                if relative == "Projects.md" or relative == "Core.md" or relative == "S3.md":
                    if relative == "Projects.md":
                        self._sync_project_notes()
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
        )
        card_id = ledger_row.data["Object ID"]
        card_obj.object_id = card_id
        self.objects[card_id] = card_obj
        self._parse_tasks(path, parent_id=card_id)

    def _parse_s3(self, path: Path) -> None:
        self._parse_tasks(path, parent_id="")
        lines = self._read_lines(path)
        section = None
        bucket_tag = ""
        bucket_pattern = re.compile(r"\((S3-[^)]+)\)")
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

    def _parse_tasks(self, path: Path, parent_id: str) -> None:
        relative = path.relative_to(REPO_ROOT).as_posix()
        lines = self._read_lines(path)
        task_stack: List[Tuple[int, str]] = []
        for raw_line in lines:
            checkbox_match = CHECKBOX_PATTERN.search(raw_line)
            if not checkbox_match:
                continue
            status_char = checkbox_match.group(1)
            status = "Complete" if status_char.lower() == "x" else "Active"
            prefix = raw_line[: checkbox_match.start()]  # leading segment
            indent = len(prefix.expandtabs(4))
            content = raw_line[checkbox_match.end():].strip()
            object_id = self._extract_object_id(raw_line)
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
            )
            object_id = ledger_row.data["Object ID"]
            parsed.object_id = object_id
            parent_for_task = parent_id
            while task_stack and indent <= task_stack[-1][0]:
                task_stack.pop()
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
        return text.strip().strip("-").strip()

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
        for parent_id, children in self.child_links.items():
            row = self.ledger.by_id.get(parent_id)
            if row:
                row.update(**{"Child Object IDs": "; ".join(sorted(children))})

    def _print_summary(self) -> None:
        projects = self.summary_counts.get("Projects", 0)
        tasks = self.summary_counts.get("Tasks", 0)
        unbucketed = self.summary_counts.get("Unbucketed", 0)
        print(f"Projects: {projects} | Tasks: {tasks} | Unbucketed: {unbucketed}")


def _load_s3_buckets() -> List[Tuple[str, str, str]]:
    buckets_path = REPO_ROOT / "S3-Buckets.csv"
    buckets: List[Tuple[str, str, str]] = []
    if buckets_path.exists():
        with buckets_path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                buckets.append(
                    (
                        row.get("Canonical ID", "").strip(),
                        row.get("Display Name", "").strip(),
                        row.get("Notes", "").strip(),
                    )
                )
    # Ensure a catch-all bucket exists for unplanned items
    buckets.append(("S3-0", "Unscheduled", "Tasks awaiting bucket assignment."))
    unique = []
    seen = set()
    for bucket in buckets:
        if bucket[0] and bucket[0] not in seen:
            unique.append(bucket)
            seen.add(bucket[0])
    return unique


def _latest_today_card() -> Optional[Path]:
    cards_dir = REPO_ROOT / "Cards"
    if not cards_dir.exists():
        return None
    candidates = []
    for path in cards_dir.glob("*-TodayCard.md"):
        stem = path.stem
        match = re.match(r"(\d{4}-\d{2}-\d{2})", stem)
        if match:
            date_part = match.group(1)
            parsed = datetime.strptime(date_part, "%Y-%m-%d")
        else:
            match = re.match(r"(\d{8})", stem)
            if not match:
                continue
            parsed = datetime.strptime(match.group(1), "%Y%m%d")
        candidates.append((parsed, path))
    if not candidates:
        return None
    candidates.sort()
    return candidates[-1][1]


def _parse_today_card(path: Path, ledger: Ledger) -> List[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    rows: List[Tuple[int, str]] = []
    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("|") or stripped.startswith("|:"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if len(cells) < 2 or not cells[0].isdigit():
            continue
        rank = int(cells[0])
        description = cells[1]
        checkbox = "[ ]"
        match = re.match(r"\[( |x|X)\]\s*(.*)", description)
        text = description
        if match:
            checkbox = "[x]" if match.group(1).lower() == "x" else "[ ]"
            text = match.group(2).strip()
        else:
            text = description.strip()
        rows.append((rank, f"{checkbox} {text}".strip()))
    if not rows:
        return []
    card_relative = path.relative_to(REPO_ROOT).as_posix()
    ledger_rows = {
        row.data.get("Canonical Name (Text)", ""): row
        for row in ledger.rows
        if row.data.get("Type") == "Task"
        and row.data.get("File Location") == card_relative
    }
    rendered: List[str] = []
    for rank, body in sorted(rows, key=lambda item: item[0]):
        text_only = re.sub(r"^\[[xX ]\]\s*", "", body).strip()
        canonical = canonicalize_text(text_only)
        ledger_row = ledger_rows.get(canonical)
        suffix = ""
        if ledger_row:
            tags = split_semicolon(ledger_row.data.get("Tags", ""))
            extra_tags = [tag for tag in tags if not tag.upper().startswith("S3-")]
            people = split_semicolon(ledger_row.data.get("People", ""))
            if extra_tags:
                text_only = f"{text_only} {' '.join(f'#'+tag for tag in extra_tags)}".strip()
            if people:
                text_only = f"{text_only} {' '.join(people)}".strip()
            suffix = f" {{{ledger_row.data.get('Object ID')}}}" if ledger_row.data.get("Object ID") else ""
        rendered.append(f"{rank}. [ ] {text_only}{suffix}" if body.startswith("[ ]") else f"{rank}. [x] {text_only}{suffix}")
    return rendered


def _render_task_line(row: LedgerRow, *, bucket_tag: Optional[str]) -> str:
    checkbox = "[x]" if row.data.get("Current State", "").lower() == "complete" else "[ ]"
    text = row.data.get("Colloquial Name", "").strip()
    tags = split_semicolon(row.data.get("Tags", ""))
    display_tags = {tag for tag in tags if not tag.upper().startswith("S3-")}
    if display_tags:
        text = f"{text} {' '.join(f'#'+tag for tag in sorted(display_tags))}".strip()
    people = split_semicolon(row.data.get("People", ""))
    if people:
        text = f"{text} {' '.join(people)}".strip()
    object_id = row.data.get("Object ID", "")
    suffix = f" {{{object_id}}}" if object_id else ""
    return f"- {checkbox} {text}{suffix}".strip()


def _select_best_project_row(rows: List[LedgerRow]) -> Optional[LedgerRow]:
    if not rows:
        return None
    best_row = rows[0]
    best_score = None
    for row in rows:
        name = row.data.get("Colloquial Name", "")
        score = 0
        if row.data.get("Notes", "").strip():
            score += 3
        if row.data.get("Tags", "").strip():
            score += 1
        if row.data.get("Current State", "").strip():
            score += 1
        if any(char in name for char in "{}[]"):
            score -= 2
        if "#" in name:
            score -= 1
        key = (score, object_id_key(row.data.get("Object ID", "")))
        if best_score is None or key > best_score:
            best_score = key
            best_row = row
    return best_row


def generate_s3_markdown(ledger: Ledger) -> str:
    today_card = _latest_today_card()
    today_lines = _parse_today_card(today_card, ledger) if today_card else []

    buckets = _load_s3_buckets()
    bucket_lookup = {bucket_id: [] for bucket_id, _, _ in buckets}
    untagged: List[LedgerRow] = []

    s3_tasks = dedupe_latest(
        row
        for row in ledger.rows
        if row.data.get("Type") == "Task"
        and row.data.get("Current State", "").lower() != "complete"
        and row.data.get("File Location") == "S3.md"
    )

    for row in s3_tasks:
        tags = split_semicolon(row.data.get("Tags", ""))
        bucket_tag = next((tag for tag in tags if tag.upper().startswith("S3-")), "")
        if bucket_tag and bucket_tag in bucket_lookup:
            bucket_lookup[bucket_tag].append(row)
        elif bucket_tag:
            bucket_lookup.setdefault(bucket_tag, []).append(row)
        else:
            untagged.append(row)

    lines: List[str] = ["## Simplified Scheduled System (S3)", "", "### Today’s Focus", ""]
    if today_lines:
        lines.extend(today_lines)
    else:
        lines.append("_No ranked tasks for today._")

    lines.extend(["", "### Active Buckets", ""])
    for bucket_id, name, description in buckets:
        lines.append(f"#### {name} ({bucket_id})")
        if description:
            lines.append(f"{description}")
        tasks = bucket_lookup.get(bucket_id, [])
        if tasks:
            for row in tasks:
                lines.append(_render_task_line(row, bucket_tag=bucket_id))
        else:
            lines.append(f"- [ ] _(No tracked items)_ {{{bucket_id}}}")
        lines.append("")

    lines.extend(["### Coming Up", ""])
    if untagged:
        for row in untagged:
            lines.append(_render_task_line(row, bucket_tag=None))
    else:
        lines.append("_No untagged tasks at the moment._")

    return "\n".join(line.rstrip() for line in lines).strip() + "\n"


def _extract_manual_projects_section() -> str:
    projects_path = REPO_ROOT / "Projects.md"
    if not projects_path.exists():
        return "## Manual Projects\n\n"
    text = projects_path.read_text(encoding="utf-8")
    match = re.search(r"^## Manual Projects\b.*", text, flags=re.MULTILINE | re.DOTALL)
    if match:
        return text[match.start():].rstrip() + "\n"
    return "## Manual Projects\n\n"


def generate_projects_markdown(ledger: Ledger, *, manual_section: Optional[str] = None) -> str:
    manual = manual_section if manual_section is not None else _extract_manual_projects_section()

    project_rows = dedupe_latest(row for row in ledger.rows if row.data.get("Type") == "Project")
    rows_by_file: Dict[str, List[LedgerRow]] = defaultdict(list)
    for row in project_rows:
        file_location = row.data.get("File Location", "")
        if file_location:
            rows_by_file[file_location].append(row)
    rows_by_id = {row.data.get("Object ID", ""): row for row in project_rows if row.data.get("Object ID")}

    project_files = sorted((REPO_ROOT / "Projects").rglob("*.md"))

    auto_lines: List[str] = ["# Projects", "", "## Project Files", ""]
    for path in project_files:
        relative = path.relative_to(REPO_ROOT).as_posix()
        row = _select_best_project_row(rows_by_file.get(relative, []))
        name = row.data.get("Colloquial Name", "").strip() if row else path.stem.replace("-", " ")
        object_id = row.data.get("Object ID", "") if row else ""
        status = row.data.get("Current State", "").strip() if row else ""
        modified = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d")
        tasks = []
        if object_id and object_id in rows_by_id:
            tasks = [
                task_row
                for task_row in ledger.rows
                if task_row.data.get("Type") == "Task"
                and task_row.data.get("Parent Object ID") == object_id
            ]
        open_tasks = sum(
            1
            for task_row in dedupe_latest(tasks)
            if task_row.data.get("Current State", "").lower() != "complete"
        )
        notes = row.data.get("Notes", "").strip() if row else ""

        heading = f"### {name}"
        if object_id:
            heading = f"{heading} {{{object_id}}}"
        auto_lines.append(heading)
        auto_lines.append(f"- Status: {status or '—'}")
        auto_lines.append(f"- Last Modified: {modified}")
        auto_lines.append(f"- Open Tasks: {open_tasks}")
        auto_lines.append(f"- File: [{relative}]({relative})")
        if notes:
            auto_lines.append("")
            auto_lines.append("#### Notes")
            for note_line in notes.splitlines():
                auto_lines.append(f"- {note_line}" if note_line else "-")
        auto_lines.append("")

    auto_text = "\n".join(line.rstrip() for line in auto_lines).rstrip()
    manual_text = manual.rstrip()
    if manual_text and not manual_text.startswith("##"):
        manual_text = "## Manual Projects\n\n" + manual_text

    return auto_text + "\n\n\n" + manual_text + ("\n" if not manual_text.endswith("\n") else "")


def generate_core_markdown(ledger: Ledger) -> str:
    aor_rows = dedupe_latest(row for row in ledger.rows if row.data.get("Type") == "AOR")
    goal_rows = dedupe_latest(row for row in ledger.rows if row.data.get("Type") == "Goal")
    goals_by_parent: Dict[str, List[LedgerRow]] = defaultdict(list)
    for goal in goal_rows:
        parent_id = goal.data.get("Parent Object ID", "")
        goals_by_parent[parent_id].append(goal)
    for goal_list in goals_by_parent.values():
        goal_list.sort(key=lambda row: row.data.get("Colloquial Name", ""))

    relationship_rows = dedupe_latest(
        row for row in ledger.rows if row.data.get("Type") == "Relationship"
    )
    relationship_rows.sort(key=lambda row: row.data.get("Colloquial Name", ""))

    lines: List[str] = ["# The Core", "", "## Areas of Responsibility", ""]
    for aor in aor_rows:
        title = aor.data.get("Colloquial Name", "").strip()
        appended_people = []
        for person in split_semicolon(aor.data.get("People", "")):
            if person and person not in title:
                appended_people.append(person)
        people = " ".join(appended_people)
        item_text = title if not people else f"{title} {people}".strip()
        lines.append(f"- {item_text}")
        for goal in goals_by_parent.get(aor.data.get("Object ID", ""), []):
            lines.append(f"  - Goal: {goal.data.get('Colloquial Name', '').strip()}")
        lines.append("")

    lines.extend(["", "## Relationships", ""])
    for rel in relationship_rows:
        handle = ""
        people = split_semicolon(rel.data.get("People", ""))
        if people:
            handle = people[0]
        name = rel.data.get("Colloquial Name", "").strip()
        if handle and name and name.lower() != handle.lstrip("@").replace(" ", "").lower():
            heading = f"- {handle} — {name}"
        elif handle:
            heading = f"- {handle}"
        else:
            heading = f"- {name}"
        lines.append(heading.strip())
        notes = rel.data.get("Notes", "").strip()
        if notes:
            lines.append(f"  - Notes: {notes}")
        lines.append("")

    # Clean trailing whitespace and enforce spacing rules
    cleaned: List[str] = []
    previous_blank = False
    for line in lines:
        stripped = line.rstrip()
        if stripped:
            cleaned.append(stripped)
            previous_blank = False
        else:
            if not previous_blank:
                cleaned.append("")
            previous_blank = True
    text = "\n".join(cleaned).rstrip()
    # Ensure two blank lines between sections
    text = text.replace("\n\n## Relationships", "\n\n\n## Relationships")
    return text + "\n"


def main() -> None:
    ledger = Ledger(LEDGER_PATH)
    compiler = Compiler(ledger)
    compiler.run()


if __name__ == "__main__":
    main()
