#!/usr/bin/env python3
"""
update.py — take the newest programming PDF for a program and feed it into
the app, the same way it's been done by hand each month.

The run deliberately stops before anything irreversible:

    pick newest PDF -> verify it's new -> parse -> validate
      -> list exercises that still need coaching content  [STOP if any]
      -> back up -> merge -> rebuild cards               [STOP for review]

Nothing is committed, pushed or deployed. The last step leaves the cards
and data files changed in the working tree for review.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


# --------------------------------------------------------------------------- #
# Picking the file
# --------------------------------------------------------------------------- #
class Ambiguous(Exception):
    """Raised when which file is newest can't be decided without a human."""


def month_in_name(name: str) -> tuple[int, int] | None:
    """(year, month) from a filename like 'STUDIO LFT Programming Sept 2026.pdf'."""
    m = re.search(r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(20\d\d)", name, re.I)
    if m:
        return int(m.group(2)), MONTHS[m.group(1).lower()[:3]]
    return None


def pick_newest_pdf(source_dir: Path, file_type: str = "pdf") -> tuple[Path, str]:
    """The newest document in a folder, with the reason it was chosen.

    Prefers the month written in the filename over the file's timestamp —
    re-downloading an old file would otherwise make it look like the newest.
    If the two disagree, or a month can't be read, the caller is asked
    rather than guessed at.
    """
    if not source_dir.is_dir():
        raise Ambiguous(f"folder not found: {source_dir}")

    stubs = [p.name for p in source_dir.glob(f"*.{file_type}.icloud")] + \
            [p.name for p in source_dir.glob(f".*.{file_type}.icloud")]
    files = [p for p in source_dir.glob(f"*.{file_type}") if p.is_file()]
    if not files:
        extra = (f" {len(stubs)} file(s) are in iCloud but not downloaded to this Mac — "
                 f"open the folder in Finder to download them.") if stubs else ""
        raise Ambiguous(f"no .{file_type} files in {source_dir}.{extra}")

    dated = [(month_in_name(p.name), p) for p in files]
    undated = [p.name for ym, p in dated if ym is None]
    by_mtime = max(files, key=lambda p: p.stat().st_mtime)

    if undated:
        raise Ambiguous(
            f"can't read a month from: {', '.join(sorted(undated)[:4])}. "
            f"Newest by file date is {by_mtime.name!r}; rename the others or say which to use.")

    by_month = max(dated, key=lambda t: t[0])[1]
    if by_month != by_mtime:
        raise Ambiguous(
            f"newest by name is {by_month.name!r} but newest by file date is {by_mtime.name!r}. "
            f"Tell me which one to ingest.")

    ym = month_in_name(by_month.name)
    return by_month, f"{ym[0]}-{ym[1]:02d} is the latest month in the folder"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def check_file(path: Path, file_type: str) -> list[str]:
    problems = []
    if not path.exists():
        return [f"{path} does not exist"]
    if path.stat().st_size == 0:
        problems.append("file is empty")
    if file_type == "pdf":
        with path.open("rb") as fh:
            if fh.read(5) != b"%PDF-":
                problems.append("does not start with %PDF- (not a real PDF?)")
    return problems


# --------------------------------------------------------------------------- #
# Per-program handlers — each knows how its own document becomes app data
# --------------------------------------------------------------------------- #
def norm(s: str) -> str:
    s = s.lower().replace(".", " ").replace("-", " ")
    return re.sub(r"\s+", " ", s).strip()


class RedHandler:
    name = "RED"
    manifest = ROOT / "data" / "day-manifests.json"
    library = ROOT / "data" / "exercise-library.json"
    touches = [manifest, library]

    def parse(self, pdf: Path):
        import parse_june_pdf as P
        days, problems = P.parse_pdf(pdf, P.load_canon(self.library))
        return days, problems

    def needs_authoring(self, days) -> list[str]:
        """Exercises with no entry in the shared library yet."""
        lib = json.loads(self.library.read_text(encoding="utf-8"))
        have = {norm(e["name"]) for e in lib["exercises"]}
        return sorted({ex["name"] for d in days for ex in d["exercises"]
                       if norm(ex["name"]) not in have})

    def validate(self, days, prog) -> list[str]:
        out = []
        for d in days:
            if not d.get("exercises"):
                out.append(f"{d['date']}: no floor exercises")
            if not d.get("format"):
                out.append(f"{d['date']}: no class format")
        return out

    def for_diff(self, days):
        return days

    def merge(self, days) -> int:
        import parse_june_pdf as P
        return P.merge_into_manifests(days, self.manifest)

    def rebuild(self) -> None:
        import build_card
        build_card.main(["--all"])


class LftHandler:
    name = "LFT"
    manifest = ROOT / "data" / "lft-day-manifests.json"
    cues = ROOT / "data" / "lft-exercise-cues.json"
    touches = [manifest]

    def parse(self, pdf: Path):
        import parse_lft_pdf as P
        return P.parse_pdf(pdf, P._identity_canon), []

    def needs_authoring(self, days) -> list[str]:
        """Exercises with no coaching cues yet."""
        have = json.loads(self.cues.read_text(encoding="utf-8"))["cues"]
        return sorted({e["name"] for d in days for s in d["sections"] for e in s["exercises"]
                       if (e.get("name") or "").strip() and norm(e["name"]) not in have})

    def validate(self, days, prog) -> list[str]:
        out = []
        for d in days:
            if not d.get("sections"):
                out.append(f"{d['date']}: no sections")
                continue
            empty = [s["name"] for s in d["sections"] if not s["exercises"]]
            if empty:
                out.append(f"{d['date']}: empty section(s) {', '.join(empty)}")
            if not d.get("focus"):
                out.append(f"{d['date']}: no focus (Lower/Upper/Full)")
        return out

    def for_diff(self, days):
        """Published-programming corrections are re-applied on every merge,
        so preview them here — otherwise a day we deliberately corrected
        shows up as an incoming change every single month."""
        import copy as _copy
        import parse_lft_pdf as P
        preview = _copy.deepcopy(days)
        stored = {d["date"]: d for d in json.loads(self.manifest.read_text(encoding="utf-8"))["days"]}
        pool = list(stored.values()) + [d for d in preview if d["date"] not in stored]
        for d in preview:
            stored[d["date"]] = d
        P.apply_corrections(list(stored.values()), P.DEFAULT_CORRECTIONS)
        return [stored[d["date"]] for d in preview]

    def merge(self, days) -> int:
        import parse_lft_pdf as P
        return P.merge_into_manifests(days, self.manifest)

    def rebuild(self) -> None:
        import build_lft_card
        build_lft_card.main(["--all"])


HANDLERS = {"red": RedHandler, "lft": LftHandler}


# --------------------------------------------------------------------------- #
# Shared checks, backups, state
# --------------------------------------------------------------------------- #
def common_validate(days, prog) -> list[str]:
    """Checks that apply whatever the program is."""
    out = []
    dates = sorted(d["date"] for d in days)
    if not dates:
        return ["parsed no days at all"]
    n = len(set(dates))
    if not (prog["min_days"] <= n <= prog["max_days"]):
        out.append(f"{n} days parsed, expected {prog['min_days']}-{prog['max_days']}")
    if len(dates) != len(set(dates)):
        out.append("the same date appears twice")
    return out


def diff_against_manifest(days, manifest_path: Path) -> dict:
    """What merging these days would do to the manifest.

    Compares the programming itself, ignoring bookkeeping like which file a
    day came from — re-exporting the same month under a new filename isn't
    a change worth reviewing.
    """
    def content(day):
        return {k: v for k, v in day.items() if k not in ("source_file", "correction")}

    existing = {d["date"]: d for d in json.loads(manifest_path.read_text(encoding="utf-8"))["days"]}
    added = [d["date"] for d in days if d["date"] not in existing]
    changed = [d["date"] for d in days
               if d["date"] in existing and content(existing[d["date"]]) != content(d)]
    return {"added": sorted(added), "changed": sorted(changed), "untouched": len(existing) - len(changed)}


def backup(paths, backup_root: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = backup_root / stamp
    dest.mkdir(parents=True, exist_ok=True)
    for p in paths:
        if p.exists():
            shutil.copy2(p, dest / p.name)
    return dest


def load_state(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- #
# One program, start to finish
# --------------------------------------------------------------------------- #
def run_program(prog: dict, cfg: dict, dry_run: bool) -> dict:
    name = prog["name"]
    handler = HANDLERS[prog["handler"]]()
    state_path = ROOT / cfg["state_file"]
    state = load_state(state_path)
    say = lambda msg: print(f"  [{name}] {msg}")

    try:
        pdf, why = pick_newest_pdf(Path(prog["source_dir"]), prog["file_type"])
    except Ambiguous as exc:
        return {"program": name, "status": "stopped", "reason": str(exc)}
    say(f"newest document: {pdf.name}  ({why})")

    problems = check_file(pdf, prog["file_type"])
    if problems:
        return {"program": name, "status": "failed", "reason": "; ".join(problems)}

    digest = sha256(pdf)
    last = state.get(name, {})
    if last.get("sha256") == digest:
        return {"program": name, "status": "no new document",
                "reason": f"{pdf.name} is byte-identical to the last one ingested "
                          f"({last.get('ingested_at', 'unknown date')})"}

    try:
        days, parse_problems = handler.parse(pdf)
    except Exception as exc:
        return {"program": name, "status": "failed", "reason": f"parser raised: {exc}"}
    say(f"parsed {len(days)} day-entries")

    issues = common_validate(days, prog) + handler.validate(days, prog) + list(parse_problems)
    if issues:
        return {"program": name, "status": "failed", "reason": "validation failed",
                "details": issues}

    todo = handler.needs_authoring(days)
    delta = diff_against_manifest(handler.for_diff(days), handler.manifest)
    say(f"would add {len(delta['added'])} day(s), change {len(delta['changed'])}, "
        f"leave {delta['untouched']} untouched")

    if todo:
        return {"program": name, "status": "needs authoring", "file": pdf.name,
                "diff": delta,
                "reason": f"{len(todo)} exercise(s) have no coaching content yet",
                "details": todo}

    if dry_run:
        return {"program": name, "status": "dry run", "file": pdf.name, "diff": delta}

    saved = backup(handler.touches, ROOT / cfg["backup_dir"])
    say(f"backed up to {saved.relative_to(ROOT)}")
    total = handler.merge(days)
    handler.rebuild()
    say(f"merged and rebuilt — {total} days in the app")

    state[name] = {"file": pdf.name, "sha256": digest,
                   "ingested_at": datetime.now().strftime("%Y-%m-%d"),
                   "days_added": delta["added"], "days_changed": delta["changed"]}
    save_state(state_path, state)
    return {"program": name, "status": "updated", "file": pdf.name,
            "diff": delta, "backup": str(saved.relative_to(ROOT))}


def run(cfg: dict, only: str | None, dry_run: bool) -> int:
    progs = [p for p in cfg["programs"] if only is None or p["name"].upper() == only.upper()]
    if not progs:
        sys.exit(f"no program named {only!r} in the config")

    results = []
    for prog in progs:
        print(f"\n=== {prog['name']} ===")
        try:
            results.append(run_program(prog, cfg, dry_run))
        except Exception as exc:  # one program failing must not stop the other
            results.append({"program": prog["name"], "status": "failed",
                            "reason": f"unexpected error: {exc}"})

    print("\n" + "=" * 62)
    print("SUMMARY" + ("  (dry run — nothing was changed)" if dry_run else ""))
    print("=" * 62)
    for r in results:
        print(f"\n{r['program']}: {r['status'].upper()}")
        if r.get("file"):
            print(f"  file:   {r['file']}")
        if r.get("diff"):
            d = r["diff"]
            print(f"  effect: +{len(d['added'])} day(s), ~{len(d['changed'])} changed")
            if d["added"]:
                print(f"          added   {d['added'][0]} .. {d['added'][-1]}")
            if d["changed"]:
                print(f"          changed {', '.join(d['changed'][:6])}"
                      + (" ..." if len(d["changed"]) > 6 else ""))
        if r.get("reason"):
            print(f"  note:   {r['reason']}")
        if r.get("details"):
            for line in r["details"][:25]:
                print(f"            - {line}")
            if len(r["details"]) > 25:
                print(f"            ... and {len(r['details']) - 25} more")
        if r.get("backup"):
            print(f"  rollback: cp {r['backup']}/*.json data/   then rerun the builders")

    bad = [r for r in results if r["status"] in ("failed",)]
    return 1 if bad else 0
