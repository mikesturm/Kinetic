"""Markdown generation helpers for the Kinetic workflow."""
from __future__ import annotations

import csv
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

try:
    from kinetic_compiler import (
        REPO_ROOT,
        Ledger,
        LedgerRow,
        canonicalize_text,
        dedupe_latest,
        object_id_key,
        split_semicolon,
    )
except ModuleNotFoundError:  # Imported via package namespace
    from scripts.kinetic_compiler import (
        REPO_ROOT,
        Ledger,
        LedgerRow,
        canonicalize_text,
        dedupe_latest,
        object_id_key,
        split_semicolon,
    )


def _normalize_spacing(lines: Iterable[str]) -> str:
    cleaned: List[str] = []
    blank_run = 0
    for raw in lines:
        text = raw.rstrip()
        if text:
            if blank_run:
                cleaned.extend([""] * min(blank_run, 2))
                blank_run = 0
            cleaned.append(text)
        else:
            blank_run += 1
    while cleaned and cleaned[-1] == "":
        cleaned.pop()
    text = "\n".join(cleaned)
    text = re.sub(r"\n{2,}(?=### )", "\n\n\n", text)
    text = re.sub(r"\n{2,}(?=## )", "\n\n\n", text)
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    return text.strip() + "\n"


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
    buckets.append(("S3-0", "Unscheduled", "Tasks awaiting bucket assignment."))
    unique: List[Tuple[str, str, str]] = []
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
    candidates: List[Tuple[datetime, Path]] = []
    for path in cards_dir.glob("*-TodayCard.md"):
        stem = path.stem
        match = re.match(r"(\d{4}-\d{2}-\d{2})", stem)
        if match:
            parsed = datetime.strptime(match.group(1), "%Y-%m-%d")
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
            object_id = ledger_row.data.get("Object ID")
            suffix = f" {{{object_id}}}" if object_id else ""
        state_prefix = "[x]" if body.startswith("[x]") else "[ ]"
        rendered.append(f"{rank}. {state_prefix} {text_only}{suffix}")
    interleaved: List[str] = []
    for line in rendered:
        interleaved.append(line)
        interleaved.append("")
    return interleaved[:-1] if interleaved else []


def _render_task_line(row: LedgerRow, *, bucket_tag: Optional[str]) -> List[str]:
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
    return [f"- {checkbox} {text}{suffix}".strip(), ""]


def generate_s3_markdown(ledger: Ledger) -> str:
    today_card = _latest_today_card()
    today_lines = _parse_today_card(today_card, ledger) if today_card else []

    buckets = _load_s3_buckets()
    bucket_lookup: Dict[str, List[LedgerRow]] = {bucket_id: [] for bucket_id, _, _ in buckets}
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

    lines: List[str] = ["## Simplified Scheduled System (S3)", ""]
    lines.extend(["### Today’s Focus", ""])
    if today_lines:
        lines.extend(today_lines)
    else:
        lines.extend(["_No ranked tasks for today._", ""])

    lines.extend(["", "### Active Buckets", ""])
    for bucket_id, name, description in buckets:
        lines.extend([f"#### {name} ({bucket_id})", ""])
        if description:
            lines.extend([description, ""])
        tasks = bucket_lookup.get(bucket_id, [])
        if tasks:
            for row in tasks:
                lines.extend(_render_task_line(row, bucket_tag=bucket_id))
        else:
            lines.extend([f"- [ ] _(No tracked items)_ {{{bucket_id}}}", ""])

    lines.extend(["", "### Coming Up", ""])
    if untagged:
        for row in untagged:
            lines.extend(_render_task_line(row, bucket_tag=None))
    else:
        lines.extend(["_No untagged tasks at the moment._", ""])

    return _normalize_spacing(lines)


def _select_best_project_row(rows: List[LedgerRow]) -> Optional[LedgerRow]:
    if not rows:
        return None
    best_row = rows[0]
    best_score: Optional[Tuple[int, Tuple[str, Tuple[int, ...]]]] = None
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
    rows_by_file: Dict[str, List[LedgerRow]] = {}
    for row in project_rows:
        file_location = row.data.get("File Location", "")
        if file_location:
            rows_by_file.setdefault(file_location, []).append(row)
    rows_by_id = {
        row.data.get("Object ID", ""): row
        for row in project_rows
        if row.data.get("Object ID")
    }

    project_files = sorted((REPO_ROOT / "Projects").rglob("*.md"))

    auto_lines: List[str] = ["# Projects", "", "## Projects with Files", ""]
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
        auto_lines.extend([heading, ""])
        auto_lines.extend([f"- Status: {status or '—'}", ""])
        auto_lines.extend([f"- Last Modified: {modified}", ""])
        auto_lines.extend([f"- Open Tasks: {open_tasks}", ""])
        auto_lines.extend([f"- File: [{relative}]({relative})", ""])
        if notes:
            auto_lines.extend(["#### Notes", ""])
            for note_line in notes.splitlines():
                auto_lines.extend([f"- {note_line}" if note_line else "-", ""])
        auto_lines.append("")

    auto_text = _normalize_spacing(auto_lines)
    manual_text = manual.rstrip()
    if manual_text and not manual_text.startswith("##"):
        manual_text = "## Manual Projects\n\n" + manual_text

    combined = auto_text.rstrip("\n") + "\n\n\n" + manual_text
    if not combined.endswith("\n"):
        combined += "\n"
    return combined


def generate_core_markdown(ledger: Ledger) -> str:
    aor_rows = dedupe_latest(row for row in ledger.rows if row.data.get("Type") == "AOR")
    goal_rows = dedupe_latest(row for row in ledger.rows if row.data.get("Type") == "Goal")
    goals_by_parent: Dict[str, List[LedgerRow]] = {}
    for goal in goal_rows:
        parent_id = goal.data.get("Parent Object ID", "")
        goals_by_parent.setdefault(parent_id, []).append(goal)
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
        lines.extend([f"- {item_text}", ""])
        for goal in goals_by_parent.get(aor.data.get("Object ID", ""), []):
            lines.extend([f"  - Goal: {goal.data.get('Colloquial Name', '').strip()}", ""])
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
        lines.extend([heading.strip(), ""])
        notes = rel.data.get("Notes", "").strip()
        if notes:
            lines.extend([f"  - Notes: {notes}", ""])
        lines.append("")

    return _normalize_spacing(lines)


__all__ = [
    "generate_core_markdown",
    "generate_projects_markdown",
    "generate_s3_markdown",
]
