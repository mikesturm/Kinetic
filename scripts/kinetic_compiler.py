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
        depth = 0
        current_id = None
        body_lines: List[str] = []
        file_path_map: Dict[str, str] = {}
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if "<details>" in line:
                depth += line.count("<details>")
                continue
            if "</details>" in line:
                depth -= line.count("</details>")
                if depth == 0 and current_id:
                    notes = "\n".join(body_lines).strip()
                    self.projects_index[current_id] = {
                        "notes": notes,
                        "file": file_path_map.get(current_id, ""),
                    }
                    body_lines = []
                    current_id = None
                continue
            if depth == 1 and line.startswith("<summary>") and "{" in line and "}" in line:
                summary_text = re.sub(r"<\\/?summary>", "", line).strip()
                match = re.search(r"\{([A-Za-z]+\d+)\}", summary_text)
                if not match:
                    continue
                current_id = match.group(1)
                file_path_map.setdefault(current_id, "")
                body_lines = []
                continue
            if depth != 1 or current_id is None:
                continue
            # Ignore nested task details blocks
            if line.startswith("<details>") or line.startswith("</details>"):
                continue
            clean = re.sub(r"\*\*(.*?)\*\*", r"\1", line).strip()
            if not clean:
                continue
            if clean.lower().startswith("file "):
                _, _, file_value = clean.partition("file ")
                file_path_map[current_id] = file_value.strip()
            body_lines.append(clean)
        # Merge any file mapping into notes entries
        for object_id, payload in list(self.projects_index.items()):
            file_value = file_path_map.get(object_id, "")
            if file_value:
                payload["file"] = file_value
            self.projects_index[object_id] = payload

    def _parse_core(self) -> None:
        core_path = REPO_ROOT / "Core.md"
        if not core_path.exists():
            return
        lines = core_path.read_text(encoding="utf-8").splitlines()
        section = None
        current_aor_id = ""
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
            if section == "AOR" and stripped.startswith("### "):
                name = stripped[4:].strip()
                obj = ParsedObject(
                    type="AOR",
                    file_location="Core.md",
                    colloquial_name=name,
                    current_state="Active",
                    people=set(f"@{p}" for p in PERSON_PATTERN.findall(name)),
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
            if section == "AOR" and stripped.startswith("* ") and "Goal:" in stripped:
                goal_text = stripped.split("Goal:", 1)[1].strip().strip(".") + "."
                obj = ParsedObject(
                    type="Goal",
                    file_location="Core.md",
                    colloquial_name=goal_text,
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
                if current_aor_id:
                    self.child_links[current_aor_id].add(object_id)
                continue
            if section == "Relationships" and stripped.startswith("### "):
                handle = stripped[4:].strip()
                people = {handle} if handle.startswith("@") else set()
                obj = ParsedObject(
                    type="Relationship",
                    file_location="Core.md",
                    colloquial_name=handle.lstrip("@"),
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
                continue
            if section == "Relationships" and stripped.startswith("Display:"):
                name = stripped.split("Display:", 1)[1].strip()
                if self.objects:
                    last_id = list(self.objects.keys())[-1]
                    obj = self.objects[last_id]
                    if obj.type == "Relationship":
                        obj.colloquial_name = name
                        obj.finalize()
                continue
            if section == "Relationships" and stripped.startswith("Notes:"):
                notes = stripped.split("Notes:", 1)[1].strip()
                if self.objects:
                    last_id = list(self.objects.keys())[-1]
                    obj = self.objects[last_id]
                    if obj.type == "Relationship":
                        obj.notes = notes
                continue

    def _parse_markdown_files(self) -> None:
        project_files_seen: Dict[str, str] = {}
        for directory in MARKDOWN_DIRECTORIES:
            if not directory.exists():
                continue
            for path in sorted(directory.rglob("*.md")):
                relative = path.relative_to(REPO_ROOT).as_posix()
                if relative == "Projects.md" or relative == "Core.md":
                    if relative == "Projects.md":
                        self._sync_project_notes()
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


def main() -> None:
    ledger = Ledger(LEDGER_PATH)
    compiler = Compiler(ledger)
    compiler.run()


if __name__ == "__main__":
    main()
