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

INSTRUCTION_RE = re.compile(r"^(Place|Keep|Bench stored)\b.*(Bench|Wall|GAP|RACK|ALLEY)", re.I)

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

    lm = re.search(r"(Build\s*B\d.*|Benchmark\s*Day\s*\d+)", rest, re.I)
    date_part = rest[: lm.start()].strip(" -–")
    label = re.sub(r"\s+", " ", lm.group(1).strip())

    dates = []
    for chunk in re.split(r"\s*[-–]\s*", date_part):
        cm = re.match(r"^([\d/]+)\s+([A-Za-z]+)$", chunk.strip())
        if not cm:
            raise ValueError(f"unparsable date chunk {chunk!r} in header {raw!r}")
        mo = month_num(cm.group(2))
        for d in cm.group(1).split("/"):
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
    out = []
    seen_y = set()
    for x0, x1, y0, y1 in rects:
        is_label_col = _near(x0, 20, 3) and _near(x1, 70, 3)
        is_full_width = _near(x0, 21, 3) and _near(x1, 519, 4) and (y1 - y0) < 25
        if not (is_label_col or is_full_width):
            continue
        if _near(y0, header_y[0]) and _near(y1, header_y[1]):
            continue  # the "BUILD" header cell itself
        texts = [t for lx0, ly, lx1, t in lines if y0 - 0.5 <= ly <= y1 + 0.5 and lx0 < 70]
        label = next((t.strip() for t in texts if re.match(r"^\d[a-e]?$", t.strip())), None)
        if label and (y0, y1) not in seen_y:
            out.append((label, y0, y1))
            seen_y.add((y0, y1))
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
def build_row(lines, label, y0, y1, canon, name_y_bounds=None) -> dict:
    name = col_text(lines, *NAME_X, y0, y1)
    setsreps_raw = col_text(lines, *SETSREPS_X, y0, y1)
    notes = col_text(lines, *NOTES_X, y0, y1)

    combined = f"{name} {setsreps_raw}".strip()
    if INSTRUCTION_RE.search(combined):
        return {"label": label, "instruction": combined, "notes": notes}

    if not setsreps_raw and len(notes) < 20:
        return None  # genuinely blank table row, left unused that day

    if name_y_bounds is not None:
        # A row's rect height follows its NOTES cell, which can be shorter
        # than a 2-line wrapped exercise NAME — the name's second line then
        # spills into the next row. Each real exercise has exactly one demo
        # video link though, so its Y position (and the next exercise's)
        # bounds the true name text regardless of the row rect's height.
        ny0, ny1 = name_y_bounds
        name = col_text(lines, *NAME_X, ny0, ny1)

    name = re.sub(r"\s+", " ", PLACEMENT_ASIDE_RE.sub("", name)).strip()

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


def _looks_like_instruction(content_lines, ry0, ry1) -> bool:
    name = col_text(content_lines, *NAME_X, ry0, ry1)
    setsreps_raw = col_text(content_lines, *SETSREPS_X, ry0, ry1)
    return bool(INSTRUCTION_RE.search(f"{name} {setsreps_raw}".strip()))


def _looks_empty(content_lines, ry0, ry1) -> bool:
    """A genuinely blank table row (left unused that day) has no Sets x Reps
    and no real Coaching Notes — any text in its NAME/NOTES cells is just a
    wrapped continuation line bleeding down from the row above (a single
    trailing sentence fragment), not real content of its own.
    """
    setsreps_raw = col_text(content_lines, *SETSREPS_X, ry0, ry1)
    notes = col_text(content_lines, *NOTES_X, ry0, ry1)
    return not setsreps_raw and len(notes) < 20


def _name_y_bounds_for_rows(rows_in_section, row_rects, is_instruction, is_empty, all_links) -> dict:
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
        bottom = link_ys[i + 1] if i + 1 < len(link_ys) else ry0
        # An instruction row's own placement note sits between this exercise
        # and the next one's link (which skips right over it, since
        # instruction rows carry no video) — don't let the name window
        # reach down into that row's own text.
        intervening = [r for r in row_rects if bottom <= r[2] <= ry0 + 0.5 and is_instruction.get(r)]
        if intervening:
            bottom = max(bottom, max(r[2] for r in intervening) + 1)
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

    is_instruction = {(lbl, ry0, ry1): _looks_like_instruction(content_lines, ry0, ry1)
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
        name_y_bounds = _name_y_bounds_for_rows(rows_in_section, row_rects, is_instruction, is_empty, deduped_links)
        exercises = [
            row for row in (
                build_row(content_lines, lbl, (0 if ry0 == page_bottom_y0 else ry0), ry1, canon,
                          name_y_bounds.get((lbl, ry0, ry1)))
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
