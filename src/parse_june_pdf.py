#!/usr/bin/env python3
"""
parse_june_pdf.py — ingest a monthly Studio Red workout PDF into day-manifests.

Reads the Vasa "Studio Red Workouts" monthly sheet (cover page at index 0, then
one workout per day: page index == day-of-month) and emits a day-manifest entry
per date, with the floor exercises split into their two PDF stations — Bench and
Rack — so the rendered coach card groups them exactly as the sheet does.

What it extracts per page, and how:
  - date           the MM/DD/YYYY line in the workout-description band
  - format         the class kind, read from the closing NOTES (EMOM / CIRCUIT /
                   LADDER / SUPERSET) or the "BENCHMARK DAY" banner
  - warmup         the Bench/Rack warm-up column (top of the sheet)
  - cardio         the Time / Intensity / MZ table, with "x2"/"x3" round markers
  - exercises      the Bench (left) and Rack (right) floor columns; each name is
                   reassembled from its stacked text fragments and the rep token
                   in the same cell ((10) -> "10 REPS", (10e) -> "10 EACH SIDE").
                   Names are snapped to exercise-library.json when they match, so
                   line-wrapped names (e.g. "Counterbalanc"+"e Squat") are healed.
  - cardio_note    the prose NOTES paragraph above the floor (optional)

The floor section can sit at different heights per page, so the name window is
bounded by the actual "BENCH" header y rather than a fixed cutoff — otherwise the
first exercise (which rides high on some pages) is lost.

Usage:
    python3 src/parse_june_pdf.py <pdf_path>            # print parsed JSON
    python3 src/parse_june_pdf.py <pdf_path> --merge    # merge into day-manifests.json

Requires pdfminer.six (text + coordinate extraction).
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from pdfminer.high_level import extract_pages
from pdfminer.layout import LTTextContainer, LTTextLine

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LIB = ROOT / "data" / "exercise-library.json"
DEFAULT_MANIFESTS = ROOT / "data" / "day-manifests.json"

# Floor-text noise: closing-NOTES lines share the Bench column's x-band, so drop
# any reassembled "name" that carries coaching-copy keywords or runs too long.
JUNK = re.compile(
    r"Coach|COOL DOWN|10s mark|same weight|weight for|Breathing|guidelines|"
    r"Note how|stations equally|bodyweight|Restart|Ladder|Circuit|switching|recover"
)
INT_WORD = {6: "Easy", 7: "Moderate", 8: "Hard", 9: "Max", 10: "Max"}


# --------------------------------------------------------------------------- #
# Library-assisted name canonicalisation
# --------------------------------------------------------------------------- #
def _norm(s: str) -> str:
    s = s.lower().strip().replace(".", " ").replace("-", " ")
    return re.sub(r"\s+", " ", s)


def _tight(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def load_canon(lib_path: Path):
    lib = json.loads(lib_path.read_text(encoding="utf-8"))
    by_norm = {_norm(e["name"]): e["name"] for e in lib["exercises"]}
    by_tight: dict[str, str] = {}
    for e in lib["exercises"]:
        by_tight.setdefault(_tight(e["name"]), e["name"])

    def canon(name: str) -> str:
        if _norm(name) in by_norm:
            return by_norm[_norm(name)]
        if _tight(name) in by_tight:  # heals mid-word line wraps when in-library
            return by_tight[_tight(name)]
        return name  # new exercise — keep as parsed (enrichment is a later phase)

    return canon


# --------------------------------------------------------------------------- #
# Page geometry helpers
# --------------------------------------------------------------------------- #
def page_lines(page) -> list[tuple[int, int, str]]:
    out = []
    for el in page:
        if isinstance(el, LTTextContainer):
            for line in el:
                if isinstance(line, LTTextLine):
                    t = line.get_text().strip()
                    if t:
                        out.append((round(line.x0), round(line.y0), t))
    return out


def header_y(lines, label, xlo, xhi) -> int:
    ys = [y for x, y, t in lines if t == label and xlo <= x <= xhi]
    return max(ys) if ys else 345


def col_exercises(lines, xlo, xhi, hy) -> list[tuple[str, str | None]]:
    """[(name, rep_token)] top->bottom for one floor column, bounded above by hy."""
    frags = [
        (y, x, t)
        for x, y, t in lines
        if xlo <= x <= xhi and 82 < y < hy and t not in ("BENCH", "RACK") and not t.startswith("**")
    ]
    frags.sort(key=lambda z: -z[0])
    clusters, cur = [], []
    for y, x, t in frags:
        if cur and (cur[-1][0] - y) > 20:  # vertical gap => next exercise
            clusters.append(cur)
            cur = []
        cur.append((y, x, t))
    if cur:
        clusters.append(cur)

    out = []
    for c in clusters:
        rep, parts = None, []
        for y, x, t in c:
            if re.match(r"^\(.+\)$", t):
                rep = t
            else:
                parts.append(t)
        name = " ".join(parts).strip()
        if not name or JUNK.search(name) or len(name) > 50:
            continue
        out.append((name, rep))
    return out


def reptext(tok, kind, ladder) -> str:
    each = bool(tok) and ("e" in tok.lower() or "each" in tok.lower())
    nums = re.findall(r"\d+", tok or "")
    if kind == "LADDER":
        base = f"LADDER {ladder[0]}→{ladder[1]}" if ladder else "LADDER"
        return base + (" · EACH SIDE" if each else "")
    if kind in ("EMOM", "BENCHMARK"):
        return ""  # time- / max-effort-based, no fixed rep count
    if not nums:
        return "EACH SIDE" if each else ""
    return f"{nums[0]} EACH SIDE" if each else f"{nums[0]} REPS"


def intensity(s: str) -> str:
    m = re.search(r"(\d+)\s*/\s*10", s)
    if not m:
        return s.strip()
    n = int(m.group(1))
    return f"{INT_WORD.get(n, 'Moderate')} {n}/10"


def mz(s: str) -> str:
    s = s.strip()
    return "Red" if s.lower() == "max" else s.capitalize()


def parse_cardio(lines) -> list[dict]:
    times = sorted(
        [(y, t) for x, y, t in lines if 100 <= x <= 135 and 395 < y < 570 and re.match(r"^[\.:]?\d", t)],
        key=lambda z: -z[0],
    )
    inten = [(y, t) for x, y, t in lines if 255 <= x <= 292 and 395 < y < 570]
    mzs = [(y, t) for x, y, t in lines if 458 <= x <= 488 and 395 < y < 570]
    mult = [(y, t) for x, y, t in lines if 290 <= x <= 315 and 395 < y < 570 and re.search(r"x\s*\d", t.lower())]
    rows = []
    for y, tt in times:
        iv = min(inten, key=lambda z: abs(z[0] - y))[1] if inten else ""
        mv = min(mzs, key=lambda z: abs(z[0] - y))[1] if mzs else ""
        rows.append((y, tt.strip(), intensity(iv), mz(mv)))
    out = []
    for i, (y, t, iv, mv) in enumerate(rows):
        out.append({"time": t, "intensity": iv, "mz": mv})
        nexty = rows[i + 1][0] if i + 1 < len(rows) else -999
        for my, mt in mult:
            if nexty < my < y:
                m = re.search(r"x\s*(\d)", mt.lower())
                if m:
                    out.append({"rounds": f"× {m.group(1)} rounds"})
    return out


def parse_format(lines, alltext) -> tuple[str, str]:
    if "BENCHMARK" in alltext.upper():
        return "BENCHMARK · 3 ROUNDS", "BENCHMARK"
    bot = " ".join(t for x, y, t in lines if y < 110).lower()
    if "top of every minute" in bot:
        return "EMOM · 9 MIN", "EMOM"
    if "ladder" in bot:
        return "LADDER · 9 MIN", "LADDER"
    if "superset" in bot or "4:30" in bot:
        return "SUPERSET · 4:30 EA", "SUPERSET"
    if "circuit" in bot:
        return "CIRCUIT · 9 MIN", "CIRCUIT"
    return "EMOM · 9 MIN", "EMOM"


def parse_ladder(lines):
    bot = " ".join(t for x, y, t in lines if y < 115)
    s = re.search(r"starting at\s*(\d+)", bot)
    e = re.search(r"(?:once|until|reach(?:es)?)\s*(\d+)", bot)
    if s and e:
        return (int(s.group(1)), int(e.group(1)))
    if s:
        return (int(s.group(1)), int(s.group(1)) - 5)
    return None


def parse_warmup(lines) -> list[str]:
    items = [(y, t) for x, y, t in lines if 215 <= x <= 242 and y >= 678 and t != "BENCH/RACK"]
    items.sort(key=lambda z: -z[0])
    return [t for y, t in items]


def parse_cardio_note(lines, bhy, top):
    # Sits between the cardio table bottom (top) and the BENCH header (bhy).
    frags = [(y, t) for x, y, t in lines if 33 <= x <= 60 and bhy < y < top - 5]
    frags.sort(key=lambda z: -z[0])
    txt = re.sub(r"^NOTES:?\s*", "", " ".join(t for y, t in frags).strip())
    return txt if len(txt) >= 40 else None


# --------------------------------------------------------------------------- #
# Per-page assembly
# --------------------------------------------------------------------------- #
def parse_pdf(pdf_path: Path, canon) -> tuple[list[dict], list[str]]:
    pages = list(extract_pages(str(pdf_path)))
    days, problems = [], []
    for i in range(1, len(pages)):  # page 0 is the cover; page i == day i
        L = page_lines(pages[i])
        alltext = " ".join(t for x, y, t in L)
        date = next((t[:10] for x, y, t in L if re.match(r"\d\d/\d\d/\d{4}", t)), None)
        if not date:
            continue
        mm, dd, yy = date.split("/")
        iso = f"{yy}-{mm}-{dd}"
        fmt, kind = parse_format(L, alltext)
        ladder = parse_ladder(L) if kind == "LADDER" else None
        bhy = header_y(L, "BENCH", 100, 200)
        rhy = header_y(L, "RACK", 380, 470)

        exs = []
        for nm, rep in col_exercises(L, 40, 100, bhy):
            exs.append({"name": canon(nm), "reps": reptext(rep, kind, ladder), "station": "\U0001F3CB️ Bench"})
        for nm, rep in col_exercises(L, 305, 350, rhy):
            exs.append({"name": canon(nm), "reps": reptext(rep, kind, ladder), "station": "\U0001F535 Rack"})

        cardio = parse_cardio(L)
        if kind == "BENCHMARK" and not cardio:
            cardio = [{"rounds": "BENCHMARK TEST · MAX DISTANCE"}]

        day = {
            "date": iso,
            "format": fmt,
            "warmup": parse_warmup(L),
            "cardio": cardio,
            "exercises": exs,
            "source_file": f"{pdf_path.name} p.{i}",
        }
        cardio_ys = [y for x, y, t in L if 100 <= x <= 135 and 395 < y < 570 and re.match(r"^[\.:]?\d", t)]
        note = parse_cardio_note(L, bhy, min(cardio_ys) if cardio_ys else 430)
        if note and kind != "BENCHMARK" and len(note) < 320:
            day["cardio_note"] = note
        if kind == "BENCHMARK":
            day["floor_note"] = (
                "<strong>Benchmark Day · 3 rounds.</strong> Round 1 is a lighter warm-up; "
                "round 2 sets your working weight; <strong>round 3 is the round that counts</strong> "
                "— log reps for each main lift."
            )
        days.append(day)

        nb = sum(1 for e in exs if "Bench" in e["station"])
        nr = len(exs) - nb
        expect = (4, 4) if kind == "SUPERSET" else (3, 3)
        if (nb, nr) != expect:
            problems.append(f"{iso} {kind}: bench={nb} rack={nr} (expected {expect})")
    return days, problems


def merge_into_manifests(new_days: list[dict], manifests_path: Path) -> int:
    existing = json.loads(manifests_path.read_text(encoding="utf-8"))
    new_dates = {d["date"] for d in new_days}
    kept = [d for d in existing["days"] if d["date"] not in new_dates]
    alldays = sorted(kept + new_days, key=lambda d: d["date"])
    manifests_path.write_text(
        json.dumps({"days": alldays}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return len(alldays)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Parse a monthly Studio Red PDF into day-manifests.")
    p.add_argument("pdf", type=Path, help="Path to the monthly workout PDF")
    p.add_argument("--lib", type=Path, default=DEFAULT_LIB)
    p.add_argument("--manifests", type=Path, default=DEFAULT_MANIFESTS)
    p.add_argument("--merge", action="store_true", help="Merge results into day-manifests.json")
    args = p.parse_args(argv)

    canon = load_canon(args.lib)
    days, problems = parse_pdf(args.pdf, canon)
    print(f"Parsed {len(days)} days from {args.pdf.name}.")
    for d in days:
        print(f"  {d['date']}  {d['format']:20s}  {len(d['exercises'])} floor exercises")
    if problems:
        print("\n⚠ unexpected exercise counts (verify against the sheet):")
        for pr in problems:
            print("  ", pr)
    if args.merge:
        total = merge_into_manifests(days, args.manifests)
        print(f"\n✓ merged into {args.manifests.relative_to(ROOT)} — {total} days total.")
    else:
        print("\n(dry run — pass --merge to write day-manifests.json)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
