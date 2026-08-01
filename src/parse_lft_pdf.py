#!/usr/bin/env python3
"""
parse_lft_pdf.py — ingest a Studio LFT programming PDF into day-manifests.

Studio LFT is a strictly-weightlifting VASA class, structurally unlike Studio
Red: a PDF page is a *table* (Training Prep / Main LFT / Accessory 1-2 /
Finisher, or LFT 1-4 for benchmark weeks), and — critically — one page can
cover TWO OR THREE calendar days at once (the same session runs on back to
back days, e.g. "06/07 July" = Monday AND Tuesday). The header line also
occasionally spans a month boundary ("31 July – 01/02 Aug").

Row/section boundaries are read from the PDF's own drawn table gridlines
(LTRect) rather than inferred from text baselines: a row's Name / Sets x
Reps / Coaching Notes cells wrap to different numbers of lines, so their
text visually overlaps adjacent rows — but the label-column cell rect for
each row gives its exact y-span, and the "Time Allotted" cell is one rect
merged across a whole section. Using those rects as ground truth avoids the
text-bleeding that a label-position heuristic runs into.

Usage:
    python3 src/parse_lft_pdf.py <pdf_path>            # print parsed JSON
    python3 src/parse_lft_pdf.py <pdf_path> --merge     # merge into lft-day-manifests.json
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import pypdf
from pdfminer.high_level import extract_pages
from pdfminer.layout import LTTextContainer, LTTextLine, LTRect

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MANIFESTS = ROOT / "data" / "lft-day-manifests.json"

MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7,
          "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}

SECTION_NAMES = {"Training Prep", "Main LFT", "Accessory 1", "Accessory 2",
                  "Finisher", "LFT 1", "LFT 2", "LFT 3", "LFT 4"}

# Column x-bands, calibrated against the table header row (Exercise / Sets x
# Reps / Coaching Notes / Time Allotted at x~70/187/270/436).
LABEL_X = (20, 70)
NAME_X = (70, 187)
SETSREPS_X = (187, 270)
NOTES_X = (270, 436)
TIME_X = (436, 519)

# Wording for the "where does the bench go" aside varies too much to
# enumerate ("Place Bench Against Wall in the GAP", "Bench stored in the
# GAP against Wall", "Bench in GAP against WALL") — just require "Bench"
# and one of the station names to both appear somewhere in the row.
INSTRUCTION_RE = re.compile(r"(?=.*\bBench\b)(?=.*\b(?:Wall|GAP|RACK|ALLEY)\b)", re.I)

# A wrapped cell's last line can dip ~1pt past its row rect into the next
# row (font descenders) — this specific "where's the bench" aside is the one
# recurring case that lands in the NAME column rather than NOTES, so strip
# it from a real exercise name rather than try to reattach it correctly.
PLACEMENT_ASIDE_RE = re.compile(r"\b(Bench stored in[^.]*?(GAP|RACK|ALLEY|WALL)[^.]*|against WALL)\b", re.I)


def _near(a: float, b: float, tol: float = 3.0) -> bool:
    return abs(a - b) <= tol


# --------------------------------------------------------------------------- #
# Page text + gridline extraction
# --------------------------------------------------------------------------- #
def page_lines(page) -> list[tuple[float, float, float, str]]:
    """[(x0, y0, x1, text)] for every text line on the page."""
    out = []
    for el in page:
        if isinstance(el, LTTextContainer):
            for line in el:
                if isinstance(line, LTTextLine):
                    t = line.get_text().strip()
                    if t:
                        out.append((line.x0, line.y0, line.x1, t))
    return out


def page_rects(page) -> list[tuple[float, float, float, float]]:
    return [(r.x0, r.x1, r.y0, r.y1) for r in page if isinstance(r, LTRect)]


def month_num(name: str) -> int:
    return MONTHS[name.strip().lower()[:3]]


def parse_header(raw: str, year: int) -> dict:
    """Parse the 'Week N ... / B-Mark ...' header into structured fields."""
    raw = raw.strip()
    m = re.match(r"^Week\s*(\d+)\s*(.*)$", raw, re.I)
    if m:
        week_num = int(m.group(1))
        rest = m.group(2)
        kind = "build"
    else:
        m2 = re.match(r"^B-Mark\s*(.*)$", raw, re.I)
        week_num = None
        rest = m2.group(1)
        kind = "benchmark"

    # The block label has been "Build B<n>" every month so far, then a
    # differently-named block ("Pump B<n>") showed up starting August —
    # don't assume the word, just match "<Word> B<digit>...".
    lm = re.search(r"([A-Za-z]+\s*B\d.*|Benchmark\s*Day\s*\d+)", rest, re.I)
    date_part = rest[: lm.start()].strip(" -–")
    label = re.sub(r"\s+", " ", lm.group(1).strip())

    # Find every "day-group month" pair in the date portion directly, rather
    # than splitting on a dash first — a cross-month range is sometimes
    # dash-separated ("31 July – 01/02 Aug") and sometimes not ("31 Aug 01
    # Sept"), and scanning for the pattern anywhere handles both the same way.
    dates = []
    pairs = re.findall(r"([\d/]+)\s+([A-Za-z]+)", date_part)
    if not pairs:
        raise ValueError(f"unparsable date range {date_part!r} in header {raw!r}")
    for days_str, month_str in pairs:
        mo = month_num(month_str)
        for d in days_str.split("/"):
            dates.append(f"{year:04d}-{mo:02d}-{int(d):02d}")

    focus_m = re.search(r"(Lower|Upper|Full)\s*$", label, re.I)
    focus = focus_m.group(1).capitalize() if focus_m else ("Full" if kind == "benchmark" else None)

    return {"week": week_num, "kind": kind, "label": label, "dates": dates, "focus": focus}


def find_header_line(lines) -> str | None:
    for x0, y, x1, t in lines:
        if re.match(r"^(Week\s*\d+|B-Mark|B-MARK)", t.strip(), re.I):
            return t
    return None


def find_session_notes(lines, table_header_y0: float) -> str:
    """The paragraph beside the 'Session / Notes' label, above the table header row."""
    # Bound above by the "Session"/"Notes" label itself — otherwise the
    # "studio lft" logo text (also x0 >= 70, further up the page) leaks in.
    label_ys = [y for x0, y, x1, t in lines if x0 < 70 and t.strip() in ("Session", "Notes")]
    ceiling = max(label_ys) + 8 if label_ys else 1e9
    frags = [(y, t) for x0, y, x1, t in lines if x0 >= 70 and table_header_y0 + 5 < y < ceiling]
    frags.sort(key=lambda z: -z[0])
    return re.sub(r"\s+", " ", " ".join(t for y, t in frags)).strip()


def col_text(lines, xlo, xhi, ylo, yhi) -> str:
    # Row rects are edge-to-edge with zero gap between them, so any tolerance
    # here would pull a neighboring row's text across the shared boundary —
    # keep this tight and let callers widen ylo/yhi explicitly when (and only
    # when) there is no adjacent row to bleed into (e.g. the page's last row).
    frags = [(y, t) for x0, y, x1, t in lines if xlo <= x0 < xhi and ylo - 0.3 <= y <= yhi + 0.3]
    frags.sort(key=lambda z: -z[0])
    return re.sub(r"\s+", " ", " ".join(t for y, t in frags)).strip()


# --------------------------------------------------------------------------- #
# Geometry: rows and sections from the drawn table gridlines
# --------------------------------------------------------------------------- #
def find_table_header_row(rects, lines) -> tuple[float, float]:
    """The (y0, y1) of the 'Exercise / Sets x Reps / ...' header cell row."""
    for x0, x1, y0, y1 in rects:
        if _near(x0, 70) and _near(x1, 187):
            texts = [t for lx0, ly, lx1, t in lines if y0 - 1 <= ly <= y1 + 1]
            if any(t.strip() == "Exercise" for t in texts):
                return (y0, y1)
    raise ValueError("could not locate the table header row")

def find_row_rects(rects, lines, header_y: tuple[float, float]) -> list[tuple[str, float, float]]:
    """[(label, y0, y1)] for every genuine labeled row.

    Most rows have their own narrow label-column cell rect (x~20-70). A few
    "instruction" rows (e.g. "Place Bench Against Wall in the GAP") are drawn
    as one rect merged across the full row width instead — same detection,
    just a wider x0/x1 to accept.
    """
    candidates = []
    for x0, x1, y0, y1 in rects:
        is_label_col = _near(x0, 20, 3) and _near(x1, 70, 3)
        is_full_width = _near(x0, 21, 3) and _near(x1, 519, 4) and (y1 - y0) < 25
        if not (is_label_col or is_full_width):
            continue
        if _near(y0, header_y[0]) and _near(y1, header_y[1]):
            continue  # the "BUILD" header cell itself
        for lx0, ly, lx1, t in lines:
            if lx0 < 70 and y0 - 0.5 <= ly <= y1 + 0.5 and re.match(r"^\d[a-e]?$", t.strip()):
                candidates.append((t.strip(), y0, y1, ly))
                break

    # A label fragment sitting right at a shared rect boundary can satisfy
    # two adjacent rects' tolerance padding at once (a rare rendering
    # duplicate — two rects, one real label) — keep only the rect that
    # contains it WITHOUT the padding, i.e. the one it actually belongs to,
    # so the same row doesn't get parsed twice.
    by_label_pos: dict[tuple[str, float], tuple[str, float, float, bool]] = {}
    for label, y0, y1, ly in candidates:
        key = (label, round(ly, 1))
        exact = y0 <= ly <= y1
        if key not in by_label_pos or (exact and not by_label_pos[key][3]):
            by_label_pos[key] = (label, y0, y1, exact)

    out = [(label, y0, y1) for label, y0, y1, exact in by_label_pos.values()]
    out.sort(key=lambda z: -z[1])
    return out


def find_section_bars(lines, header_y: tuple[float, float]) -> list[tuple[str, float, float]]:
    """[(section_name, y, y)] anchored on the section-header text itself.

    These gray bars don't reliably get their own distinct fill rect (some
    share one with an adjacent instruction row), but the label text is
    always there — so anchor on the text position directly instead.
    """
    out = []
    for x0, y, x1, t in lines:
        name = t.strip()
        if name in SECTION_NAMES and not (_near(y, header_y[0], 2) or _near(y, header_y[1], 2)):
            out.append((name, y, y))
    out.sort(key=lambda z: -z[1])
    return out


def find_time_rects(rects, header_y: tuple[float, float]) -> list[tuple[float, float]]:
    """[(y0, y1)] for merged 'Time Allotted' cells (one per section, spans its rows)."""
    out = []
    for x0, x1, y0, y1 in rects:
        if _near(x0, 436, 3) and _near(x1, 519, 3):
            if _near(y0, header_y[0]) and _near(y1, header_y[1]):
                continue
            out.append((y0, y1))
    return out


# --------------------------------------------------------------------------- #
# Row / section assembly
# --------------------------------------------------------------------------- #
def _dead_zone_floor(row_rects, ry0) -> float:
    """Row rects don't always touch edge-to-edge — some pages leave a few
    points of unclaimed space between one row and the next (no rect claims
    it at all). A short aside's trailing word can land in that dead zone
    rather than in either neighboring rect. Returns the y just above the
    nearest row-rect below this one (or 0, the page floor, if there is
    none) — safe to read down to, since by definition no OTHER labeled row
    claims that space.
    """
    below = [r[2] for r in row_rects if r[2] <= ry0 + 0.5]
    return max(below) if below else 0


def build_row(lines, label, y0, y1, canon, name_y_bounds=None, row_rects=()) -> dict:
    name = col_text(lines, *NAME_X, y0, y1)
    setsreps_raw = col_text(lines, *SETSREPS_X, y0, y1)
    notes = col_text(lines, *NOTES_X, y0, y1)

    # An instruction row's Sets x Reps cell never contains a digit — a real
    # exercise's always does, even when a stray location fragment bleeds
    # into it (e.g. "3-5RM (bench in GAP"), so gate on that first (see
    # _looks_like_instruction). A few pages render the whole "where's the
    # bench" aside shifted right into the Sets x Reps column instead of the
    # Name column, so check both.
    if (not re.search(r"\d", setsreps_raw) and "Bench" in f"{name} {setsreps_raw}"
            and not INSTRUCTION_RE.search(f"{name} {setsreps_raw}")):
        # Mentions "Bench" but not yet a station name — the station name may
        # have dipped into an unclaimed dead zone below this row (see
        # _dead_zone_floor). Retry with the floor extended that far.
        floor = _dead_zone_floor(row_rects, y0)
        if floor < y0:
            setsreps_raw = col_text(lines, *SETSREPS_X, floor, y1)
            name = col_text(lines, *NAME_X, floor, y1)

    if not re.search(r"\d", setsreps_raw) and INSTRUCTION_RE.search(f"{name} {setsreps_raw}"):
        # The row above can dip its own name text down into this row's NAME
        # cell (see _instruction_own_start_y) — trim anything above where
        # the instruction's own text actually starts so that spillover
        # doesn't get reported as part of the instruction.
        instr_y = _instruction_own_start_y(lines, y0, y1)
        if instr_y is not None:
            name = col_text(lines, *NAME_X, y0, instr_y)
        instruction = re.sub(r"\s+", " ", f"{name} {setsreps_raw}").strip()
        return {"label": label, "instruction": instruction, "notes": notes}

    if name_y_bounds is not None:
        # A row's rect height follows its NOTES cell, which can be shorter
        # than a 2-line wrapped exercise NAME — the name's second line then
        # spills into the next row. Each real exercise has exactly one demo
        # video link though, so its Y position (and the next exercise's)
        # bounds the true name text regardless of the row rect's height.
        ny0, ny1 = name_y_bounds
        name = col_text(lines, *NAME_X, ny0, ny1)

    # Rarely, the whole row (Name + Sets x Reps + start of Coaching Notes)
    # renders as one fused PDF text line instead of three separate ones —
    # recognizable because Sets x Reps then reads back completely empty even
    # though the row clearly isn't blank. The real exercise name always ends
    # right before the Sets x Reps token begins (either the word "Build" or
    # a bare digit), so split there. Must run after the name_y_bounds
    # correction above, since that re-reads the Name column from scratch and
    # would otherwise pull the un-split fused text right back in.
    if not setsreps_raw.strip():
        fuse_m = re.search(r"^(.*?[a-z])\s+(Build\b.*|\d.*)$", name)
        if fuse_m:
            name, setsreps_raw = fuse_m.group(1), fuse_m.group(2)

    if not setsreps_raw and len(name) + len(notes) < 20:
        return None  # genuinely blank table row, left unused that day

    name = re.sub(r"\s+", " ", PLACEMENT_ASIDE_RE.sub("", name)).strip()

    # Rarely, the PDF renders a Sets x Reps cell and the start of the
    # Coaching Notes cell as one continuous text run instead of two. Genuine
    # Sets x Reps values are always short, even the wordy ones ("Build to
    # heavy 6", "Build to a heavy 4 for today"), so only treat this as a
    # merged cell — and split the real notes sentence back out — when the
    # cell is both long and carries a real multi-word sentence.
    prose_m = re.search(r"\b[A-Z][a-z]+(?:\s+[a-z]+){3,}", setsreps_raw)
    if prose_m and len(setsreps_raw) > 40:
        notes = f"{setsreps_raw[prose_m.start():]} {notes}".strip()
        setsreps_raw = setsreps_raw[: prose_m.start()].strip()

    loc_m = re.search(r"\(([^)]*)\)", setsreps_raw)
    location = loc_m.group(1).strip() if loc_m else None
    sets_reps = re.sub(r"\([^)]*\)", "", setsreps_raw).strip(" ,")

    return {
        "label": label,
        "name": canon(name) if name else name,
        "sets_reps": sets_reps,
        "location": location,
        "notes": notes,
        "_y": (y0, y1),
    }


def _looks_like_instruction(content_lines, ry0, ry1, row_rects=()) -> bool:
    # A real exercise sometimes has a stray location fragment bleed into its
    # own Sets x Reps cell (e.g. "3-5RM (bench in GAP") — that still starts
    # with a genuine reps value, unlike an instruction row's Sets x Reps
    # cell, which never contains a digit even when the whole "where's the
    # bench" aside renders shifted into that column instead of Name. Gate on
    # that first so a real lift whose bled-in fragment happens to mention
    # "Bench"+a station name doesn't get misclassified.
    setsreps_raw = col_text(content_lines, *SETSREPS_X, ry0, ry1)
    if re.search(r"\d", setsreps_raw):
        return False
    name = col_text(content_lines, *NAME_X, ry0, ry1)
    combined = f"{name} {setsreps_raw}"
    if INSTRUCTION_RE.search(combined):
        return True
    if "Bench" in combined:
        # The station name may have dipped into an unclaimed dead zone below
        # this row (see _dead_zone_floor) — retry with the floor extended.
        floor = _dead_zone_floor(row_rects, ry0)
        if floor < ry0:
            wide = f"{col_text(content_lines, *NAME_X, floor, ry1)} {col_text(content_lines, *SETSREPS_X, floor, ry1)}"
            return bool(INSTRUCTION_RE.search(wide))
    return False


def _looks_empty(content_lines, ry0, ry1) -> bool:
    """A genuinely blank table row (left unused that day) has no Sets x Reps
    and next to nothing in NAME/NOTES — any text there is just a wrapped
    continuation line bleeding down from the row above (a single trailing
    word or sentence fragment), not real content of its own. Checking NAME
    too (not just NOTES) matters for instruction rows whose whole aside
    lives in the NAME cell with nothing in NOTES at all.
    """
    setsreps_raw = col_text(content_lines, *SETSREPS_X, ry0, ry1)
    name = col_text(content_lines, *NAME_X, ry0, ry1)
    notes = col_text(content_lines, *NOTES_X, ry0, ry1)
    return not setsreps_raw and len(name) + len(notes) < 20


def _instruction_own_start_y(content_lines, ry0, ry1) -> float | None:
    """Within an instruction row, the instruction phrase itself is reliably
    the LOWEST line(s) in its NAME cell — accumulate lines bottom-up until
    they alone satisfy INSTRUCTION_RE. Anything still above that point is
    spillover from the real exercise row above it (its name dipped past its
    own row's bottom edge into this one — the same font-descender bleed
    seen elsewhere, just bad enough here to cross a row boundary). Returns
    the y of the topmost line that's part of the instruction's own text.
    """
    frags = sorted(
        [(y, t) for x0, y, x1, t in content_lines
         if NAME_X[0] <= x0 < NAME_X[1] and ry0 - 0.3 <= y <= ry1 + 0.3],
        key=lambda z: z[0],
    )
    acc = []
    for y, t in frags:
        acc.append((y, t))
        combined = re.sub(r"\s+", " ", " ".join(tt for _, tt in acc)).strip()
        if INSTRUCTION_RE.search(combined):
            return y
    return None


def _name_y_bounds_for_rows(rows_in_section, row_rects, is_instruction, is_empty, all_links, content_lines) -> dict:
    """Match this section's real (non-instruction, non-empty) rows to its
    video links ordinally, matched per-SECTION rather than per-page: a
    miscount in one section (an unusually-worded aside the instruction
    regex misses) would otherwise disable the correction for the whole
    page, including sections that line up cleanly.
    """
    if not all_links:
        return {}
    rows_sorted = sorted(rows_in_section, key=lambda r: -r[1])
    real_rows = [r for r in rows_sorted if not is_instruction.get(r) and not is_empty.get(r)]
    if not real_rows:
        return {}
    ylo = min(r[1] for r in real_rows) - 3
    yhi = max(r[2] for r in real_rows) + 3
    section_links = sorted((l for l in all_links if ylo <= l[1] <= yhi), key=lambda l: -l[1])
    if len(real_rows) != len(section_links):
        return {}

    link_ys = [y for x, y, uri in section_links]
    bounds = {}
    for i, (lbl, ry0, ry1) in enumerate(real_rows):
        top = link_ys[i]
        if i + 1 < len(link_ys):
            bottom = link_ys[i + 1]
            # An instruction row's own placement note sits between this
            # exercise and the next one's link (which skips right over it,
            # since instruction rows carry no video) — don't let the name
            # window reach down into that row's own text.
            intervening = [r for r in row_rects if bottom <= r[2] <= ry0 + 0.5 and is_instruction.get(r)]
            if intervening:
                bottom = max(bottom, max(r[2] for r in intervening) + 1)
        else:
            # No next link within this section (this is its last real row).
            # The row's own bottom edge (ry0) isn't a safe floor: this row's
            # own name can dip a hair past it — same as the boundary between
            # any two rows — but there's no next-row link in THIS section to
            # bound against. Sections stack top-to-bottom with no
            # interleaving, so the next video link anywhere on the page (the
            # next section's first exercise) is still the right anchor.
            below_global = [y for x, y, uri in all_links if y < top - 3]
            bottom = max(below_global) if below_global else ry0
            # If the row-rect immediately below (whatever its exact gap —
            # some pages leave a few points of unclaimed space between rows
            # instead of touching edge-to-edge) is an instruction, its own
            # placement note may sit inside that gap — don't let the window
            # reach past where ITS OWN text actually starts.
            below_rects = [r for r in row_rects if r != (lbl, ry0, ry1) and r[2] <= ry0 + 0.5]
            following = max(below_rects, key=lambda r: r[2]) if below_rects else None
            if following and is_instruction.get(following):
                instr_y = _instruction_own_start_y(content_lines, following[1], following[2])
                if instr_y is not None:
                    bottom = instr_y + 2
        bounds[(lbl, ry0, ry1)] = (bottom, top + 3)
    return bounds


def parse_page(page, canon, links: list[tuple[float, float, str]] | None = None) -> dict | None:
    lines = page_lines(page)
    header_raw = find_header_line(lines)
    if not header_raw:
        return None
    header = parse_header(header_raw, year=2026)

    # Section-header text can dip a point or so past its own bar into the
    # row directly below (same font-descender effect as a wrapped cell's
    # last line) — strip exact section-name lines before any column
    # extraction so they can never leak into a neighboring row's content.
    content_lines = [l for l in lines if l[3].strip() not in SECTION_NAMES]

    rects = page_rects(page)
    table_header_y = find_table_header_row(rects, lines)
    session_notes = find_session_notes(lines, table_header_y[1])

    row_rects = find_row_rects(rects, lines, table_header_y)
    section_bars = find_section_bars(lines, table_header_y)
    time_rects = find_time_rects(rects, table_header_y)

    # The very last row on the page has no sibling below it, so its bottom
    # edge is safe to extend — a wrapped cell's final line can dip a couple
    # points past the drawn rect (font descenders). Every interior boundary
    # keeps its exact bound since rows are edge-to-edge with zero gap.
    page_bottom_y0 = min((ry0 for _, ry0, ry1 in row_rects), default=None)

    is_instruction = {(lbl, ry0, ry1): _looks_like_instruction(content_lines, ry0, ry1, row_rects)
                       for lbl, ry0, ry1 in row_rects}
    is_empty = {(lbl, ry0, ry1): _looks_empty(content_lines, ry0, ry1) for lbl, ry0, ry1 in row_rects}
    deduped_links = dedupe_links(links) if links else []

    # Walk rows and section anchors together in top-to-bottom (descending y)
    # order and assign each row to whichever section anchor was most
    # recently seen — robust to the small numeric offsets between a section
    # anchor's exact y and where its first row's rect actually starts.
    # Position each row by its BOTTOM edge (ry0): a row's top edge can sit
    # slightly above its own section's text anchor (the anchor is padded
    # down from the top of its bar), but its bottom edge is always safely
    # below the anchor that introduces it.
    timeline = ([("section", sy, sname) for sname, sy, _ in section_bars] +
                [("row", ry0, (lbl, ry0, ry1)) for lbl, ry0, ry1 in row_rects])
    timeline.sort(key=lambda z: -z[1])

    sections: list[dict] = []
    current = None
    for kind, y, payload in timeline:
        if kind == "section":
            current = {"name": payload, "_rows": []}
            sections.append(current)
        elif current is not None:
            current["_rows"].append(payload)

    for sec in sections:
        rows_in_section = sec.pop("_rows")
        name_y_bounds = _name_y_bounds_for_rows(rows_in_section, row_rects, is_instruction, is_empty, deduped_links,
                                                 content_lines)
        exercises = [
            row for row in (
                build_row(content_lines, lbl, (0 if ry0 == page_bottom_y0 else ry0), ry1, canon,
                          name_y_bounds.get((lbl, ry0, ry1)), row_rects)
                for lbl, ry0, ry1 in rows_in_section
            ) if row is not None  # drop genuinely blank table rows
        ]

        # Merged Time Allotted cell: it usually spans every row in the
        # section, but an instruction row (a placement note alongside the
        # real lift) can sit outside it with its own blank time cell — so
        # match on any overlap with the section's row range, not full
        # containment, or a lone instruction row would hide a real time value.
        time_allotted, time_note = None, ""
        if rows_in_section:
            span_y0 = min(ry0 for _, ry0, ry1 in rows_in_section)
            span_y1 = max(ry1 for _, ry0, ry1 in rows_in_section)
            for ty0, ty1 in time_rects:
                if ty0 <= span_y1 + 2 and ty1 >= span_y0 - 2:
                    tf = [(y, t) for x0, y, x1, t in content_lines
                          if TIME_X[0] <= x0 < TIME_X[1] and ty0 - 1 <= y <= ty1 + 1]
                    tf.sort(key=lambda z: -z[0])
                    for y, t in tf:
                        if time_allotted is None and re.match(r"^\d+:\d\d$", t.strip()):
                            time_allotted = t.strip()
                        elif t.strip() != time_allotted:
                            time_note += (" " if time_note else "") + t
                    break

        sec["time_allotted"] = time_allotted
        sec["time_note"] = re.sub(r"\s+", " ", time_note).strip()
        sec["exercises"] = [{k: v for k, v in ex.items() if k != "_y"} for ex in exercises]

    return {"header": header, "session_notes": session_notes, "sections": sections}


# --------------------------------------------------------------------------- #
# Video-link matching (same technique as Studio Red: match by row position)
# --------------------------------------------------------------------------- #
def extract_video_links(pdf_path: Path) -> list[list[tuple[float, float, str]]]:
    reader = pypdf.PdfReader(str(pdf_path))
    out = []
    for page in reader.pages:
        links = []
        if "/Annots" in page:
            for a in page["/Annots"]:
                try:
                    o = a.get_object()
                    if o.get("/Subtype") != "/Link":
                        continue
                    act = o.get("/A", {})
                    if hasattr(act, "get_object"):
                        act = act.get_object()
                    uri = act.get("/URI") if act else None
                    rect = o.get("/Rect")
                    if uri and rect:
                        links.append((float(rect[0]), float(rect[1]), str(uri)))
                except Exception:
                    continue
        out.append(links)
    return out


def dedupe_links(links: list[tuple[float, float, str]]) -> list[tuple[float, float, str]]:
    """Collapse same-URI annotations that sit close together to one link.

    A wrapped 2-line exercise name gets one link annotation per line (same
    URI, both lines of the same cell) — left undeduped that consumes two
    slots in ordinal matching and shifts every later row by one.
    """
    deduped: list[tuple[float, float, str]] = []
    for x, y, uri in sorted(links, key=lambda l: -l[1]):
        if any(u == uri and abs(y - dy) < 20 for dx, dy, u in deduped):
            continue
        deduped.append((x, y, uri))
    return deduped


def attach_videos(parsed: dict, deduped_links: list[tuple[float, float, str]]) -> None:
    remaining = list(deduped_links)
    for sec in parsed["sections"]:
        for ex in sec["exercises"]:
            if not ex.get("name"):
                continue
            candidates = [l for l in remaining if NAME_X[0] - 5 <= l[0] <= NAME_X[1] + 40]
            if not candidates:
                continue
            best = max(candidates, key=lambda l: l[1])  # topmost remaining candidate
            ex["video"] = best[2]
            remaining.remove(best)


# --------------------------------------------------------------------------- #
# Assembly: one manifest entry per calendar date
# --------------------------------------------------------------------------- #
def build_day_entries(parsed: dict, source_label: str) -> list[dict]:
    header = parsed["header"]
    return [{
        "date": date,
        "week": header["week"],
        "kind": header["kind"],
        "label": header["label"],
        "focus": header["focus"],
        "session_notes": parsed["session_notes"],
        "sections": parsed["sections"],
        "source_file": source_label,
    } for date in header["dates"]]


def parse_pdf(pdf_path: Path, canon) -> list[dict]:
    pages = list(extract_pages(str(pdf_path)))
    all_links = extract_video_links(pdf_path)
    days = []
    for i, page in enumerate(pages):
        page_links = list(all_links[i])
        parsed = parse_page(page, canon, page_links)
        if not parsed:
            continue
        attach_videos(parsed, dedupe_links(page_links))
        days.extend(build_day_entries(parsed, f"{pdf_path.name} p.{i + 1}"))
    return days


def _identity_canon(name: str) -> str:
    return name


def merge_into_manifests(new_days: list[dict], manifests_path: Path) -> int:
    existing = {"days": []}
    if manifests_path.exists():
        existing = json.loads(manifests_path.read_text(encoding="utf-8"))
    new_dates = {d["date"] for d in new_days}
    kept = [d for d in existing["days"] if d["date"] not in new_dates]
    alldays = sorted(kept + new_days, key=lambda d: d["date"])
    manifests_path.write_text(json.dumps({"days": alldays}, ensure_ascii=False, indent=2), encoding="utf-8")
    return len(alldays)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Parse a Studio LFT PDF into day-manifests.")
    p.add_argument("pdf", type=Path)
    p.add_argument("--manifests", type=Path, default=DEFAULT_MANIFESTS)
    p.add_argument("--merge", action="store_true")
    args = p.parse_args(argv)

    days = parse_pdf(args.pdf, _identity_canon)
    print(f"Parsed {len(days)} day-entries from {args.pdf.name}.")
    for d in days:
        n_ex = sum(1 for s in d["sections"] for e in s["exercises"] if e.get("name"))
        n_instr = sum(1 for s in d["sections"] for e in s["exercises"] if "instruction" in e)
        print(f"  {d['date']}  {d['focus'] or '?':6s}  {d['label']:30s}  {n_ex} exercises, {n_instr} notes")
    if args.merge:
        total = merge_into_manifests(days, args.manifests)
        print(f"\n✓ merged into {args.manifests.relative_to(ROOT)} — {total} days total.")
    else:
        print("\n(dry run — pass --merge to write lft-day-manifests.json)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
