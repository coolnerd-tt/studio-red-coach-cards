"""Copy-editing fixes applied to the published programming on every merge.

HQ's PDFs carry typos, spacing slips and the occasional coaching note pasted
in from a different movement. Fixing those in the manifests by hand doesn't
hold: the next time a month is re-parsed the raw text comes straight back. So
the fixes live in data/text-corrections.json and are re-applied here at the
end of every merge, the same way day-level programming corrections are.

Nothing in here changes a workout. It touches prose, exercise names, and
notes that demonstrably belong to a different exercise — never sets, reps,
loads, or which movement is programmed.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CORRECTIONS = ROOT / "data" / "text-corrections.json"

_SENTENCE_START = re.compile(r"([.!?])(\s+)([a-z])")


def _sentence_case(text: str) -> str:
    """Capitalise a word that starts a sentence after a full stop.

    Only after terminal punctuation plus a space, so "DB in goblet position"
    mid-sentence and abbreviations run together ("alt.partners") are left
    alone. An all-lower token that is really an abbreviation would be
    capitalised too, which reads fine either way.
    """
    return _SENTENCE_START.sub(lambda m: f"{m.group(1)}{m.group(2)}{m.group(3).upper()}", text)


def _apply_replacements(text: str, rules: list[dict], scope: str) -> str:
    if not text:
        return text
    for rule in rules:
        if rule.get("scope") not in (scope, "both"):
            continue
        if rule.get("sentence_case"):
            text = _sentence_case(text)
        elif "regex" in rule:
            text = re.sub(rule["regex"], rule["replace"], text)
        elif rule["find"] in text:
            text = text.replace(rule["find"], rule["replace"])
    return text.strip()


def _load(path: Path) -> dict:
    if not path.exists():
        return {"replacements": [], "note_overrides": [], "renames": []}
    return json.loads(path.read_text(encoding="utf-8"))


def apply_text_corrections(days: list[dict], scope: str,
                           path: Path = DEFAULT_CORRECTIONS) -> dict[str, int]:
    """Edit `days` in place. `scope` is "lft" or "red". Returns a tally."""
    doc = _load(path)
    rules = doc.get("replacements", [])
    renames = {r["from"]: r["to"] for r in doc.get("renames", []) if r.get("scope") == scope}
    overrides = [o for o in doc.get("note_overrides", []) if o.get("scope") == scope]
    tally = {"text": 0, "renamed": 0, "notes_replaced": 0}

    def fix(text: str) -> str:
        return _apply_replacements(text, rules, scope)

    def count(before: str, after: str) -> None:
        if before != after:
            tally["text"] += 1

    for day in days:
        if scope == "lft":
            before = day.get("session_notes") or ""
            day["session_notes"] = fix(before)
            count(before, day["session_notes"])
            for section in day.get("sections", []):
                for ex in section.get("exercises", []):
                    name = (ex.get("name") or "").strip()
                    if name in renames:
                        ex["name"] = renames[name]
                        tally["renamed"] += 1
                        name = ex["name"]
                    for field in ("notes", "instruction"):
                        if ex.get(field) is None:
                            continue
                        hit = next(
                            (o for o in overrides
                             if field == "notes" and o["exercise"] == name
                             and ex[field].startswith(o["note_startswith"])),
                            None,
                        )
                        if hit:
                            ex[field] = hit["new_note"]
                            tally["notes_replaced"] += 1
                            continue
                        was = ex[field]
                        ex[field] = fix(was)
                        count(was, ex[field])
        else:
            before = day.get("cardio_note") or ""
            if before:
                day["cardio_note"] = fix(before)
                count(before, day["cardio_note"])
            for ex in day.get("exercises", []):
                if (ex.get("name") or "").strip() in renames:
                    ex["name"] = renames[ex["name"].strip()]
                    tally["renamed"] += 1
    return tally


def rename_library(path: Path, corrections: Path = DEFAULT_CORRECTIONS) -> int:
    """Carry the Red renames into the exercise library, so an entry is still
    found by the name the manifests now use."""
    doc = _load(corrections)
    renames = {r["from"]: r["to"] for r in doc.get("renames", []) if r.get("scope") == "red"}
    if not renames:
        return 0
    lib = json.loads(path.read_text(encoding="utf-8"))
    n = 0
    for entry in lib["exercises"]:
        if entry["name"] in renames:
            entry["name"] = renames[entry["name"]]
            n += 1
    if n:
        path.write_text(json.dumps(lib, ensure_ascii=False, indent=1), encoding="utf-8")
    return n
