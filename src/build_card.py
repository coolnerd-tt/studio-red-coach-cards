#!/usr/bin/env python3
"""
Studio Red coach-card renderer — Phase 1 (BUILD-BRIEF.md).

Loads exercise-library.json + day-manifests.json and renders a self-contained,
offline, mobile-optimized coach reference card (HTML) for a given class date.

Design system (colors, fonts, expand/collapse behavior) is lifted VERBATIM from
the reference card's <style> block per brief §6, so output cannot drift from the
proven design. The §7 iOS-Safari click-handler pattern is reproduced exactly:
  - .exercise-card has NO onclick
  - onclick="toggleCard(this.parentElement)" lives on .exercise-header only
  - the .video-btn anchor carries no JS — it just works

Zero third-party dependencies (stdlib only) so it runs anywhere on the handoff.

Usage:
  python3 src/build_card.py 2026-06-02
  python3 src/build_card.py --all
  python3 src/build_card.py 2026-06-02 --verify
"""
from __future__ import annotations

import argparse
import calendar
import json
import re
import sys
from collections import defaultdict
from datetime import date
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DEFAULT_LIB = DATA / "exercise-library.json"
DEFAULT_MANIFESTS = DATA / "day-manifests.json"
DEFAULT_REFERENCE = DATA / "card-template-reference.html"
DEFAULT_OUT = ROOT / "cards"


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #
def load_library(path: Path) -> dict[str, dict]:
    """Return {exercise_name: entry}. Names are matched case/space-insensitively."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    lib: dict[str, dict] = {}
    for entry in raw["exercises"]:
        lib[_norm(entry["name"])] = entry
    return lib


def load_manifests(path: Path) -> dict[str, dict]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {day["date"]: day for day in raw["days"]}


def _norm(name: str) -> str:
    """Loose key for exact-ish lookup: lowercase, collapse whitespace, strip punctuation noise."""
    s = name.lower().strip()
    s = s.replace(".", " ").replace("-", " ")
    s = re.sub(r"\s+", " ", s)
    return s


def lookup(lib: dict[str, dict], name: str) -> dict | None:
    """Phase 1: normalized exact match. Phase 3 will swap in fuzzy matching."""
    return lib.get(_norm(name))


# --------------------------------------------------------------------------- #
# Small presentation helpers
# --------------------------------------------------------------------------- #
def fmt_date_slashes(iso: str) -> str:
    y, m, d = iso.split("-")
    return f"{m} / {d} / {y}"


def format_label(fmt: str) -> str:
    """'CIRCUIT · 9 MIN' -> uppercase as-is (already in card style)."""
    return fmt.strip()


def format_kind(fmt: str) -> str:
    """First word of the format string: EMOM / CIRCUIT / SUPERSET / LADDER / ..."""
    return fmt.strip().split()[0].upper() if fmt.strip() else ""


# Icon by equipment prefix — keeps the card visually faithful to the hand-built ones.
def exercise_icon(name: str) -> str:
    n = name.lower()
    if n.startswith("db"):
        return "\U0001F4AA"        # 💪
    if n.startswith("kb"):
        return "\U0001F514"        # 🔔
    if n.startswith("trx"):
        return "\U0001F501"        # 🔁
    if n.startswith("fitband"):
        return "\U0001F397️"  # 🎗️
    if n.startswith("ball"):
        return "\U0001F3D0"        # 🏐
    if any(k in n for k in ("jump", "jack", "hop", "split")):
        return "\U0001F4A5"        # 💥
    return "\U0001F938"            # 🤸 (bodyweight / other)


MOD_ICON = {"easier": "⬇️", "harder": "⬆️"}  # ⬇️ ⬆️


def mod_icon(mod_type: str) -> str:
    return MOD_ICON.get(mod_type.lower().strip(), "\U0001FA79")  # 🩹 injury/limitation


INTENSITY_CLASS = {"easy": "easy", "moderate": "moderate", "hard": "hard", "max": "max"}


def intensity_class(intensity: str) -> str:
    first = intensity.strip().split()[0].lower() if intensity.strip() else ""
    return INTENSITY_CLASS.get(first, "moderate")


def mz_class(mz: str) -> str:
    m = (mz or "").strip().lower()
    return m if m in {"yellow", "green", "blue", "red"} else "green"


# Floor-tab structure banner + closing note, by format. Coaching-accurate, format-specific.
FLOOR_COPY = {
    "EMOM": (
        "<strong>EMOM · 9 minutes</strong> · Begin each movement at the top of every "
        "minute. Transition between movements at the 10s mark.",
        "Begin each movement at the <strong>top of every minute</strong> for 9 minutes. "
        "Transition between movements at the <strong>10s mark</strong>.",
    ),
    "CIRCUIT": (
        "<strong>CIRCUIT · 9 minutes</strong> · Move through each station for the work "
        "interval, then rotate. Keep transitions tight and the pace honest.",
        "Rotate through every station once per round. <strong>Keep transitions tight</strong> "
        "so the working heart rate stays elevated.",
    ),
    "SUPERSET": (
        "<strong>SUPERSET · 4:30 each</strong> · Pair the two movements back-to-back "
        "with minimal rest, then switch pairs.",
        "Work each pair <strong>back-to-back with minimal rest</strong>. Coach members to keep "
        "form clean as fatigue builds.",
    ),
    "LADDER": (
        "<strong>LADDER · 9 minutes</strong> · Work the rep ladder down then back up. "
        "Hold form as the reps change.",
        "Descend then ascend the rep ladder. <strong>Form does not change as reps drop</strong> "
        "— only the count does.",
    ),
    "BENCHMARK": (
        "<strong>BENCHMARK · 3 rounds</strong> · Three rounds through the stations. Round 1 is a "
        "lighter warm-up, round 2 is a practice round, and round 3 is the one that counts.",
        "Three rounds. <strong>Round 3 is the round that counts</strong> — push for max effort and "
        "log the reps members hit on each main lift.",
    ),
}
DEFAULT_FLOOR = (
    "<strong>{label}</strong> · Coach every station equally and keep the pace honest.",
    "Coach every station equally. Keep transitions tight.",
)


# --------------------------------------------------------------------------- #
# Section renderers — each returns an HTML fragment string
# --------------------------------------------------------------------------- #
def render_header(day: dict) -> str:
    return f"""<div class="header">
  <div class="header-left">
    <div class="logo-box">V</div>
    <div>
      <div class="header-title">Studio Red</div>
      <div class="header-date">{escape(fmt_date_slashes(day['date']))}</div>
    </div>
  </div>
  <div style="text-align:right">
    <div style="font-family:'Barlow Condensed',sans-serif;font-size:11px;color:rgba(255,255,255,0.6);letter-spacing:1px">COACH GUIDE</div>
    <div style="font-family:'Barlow Condensed',sans-serif;font-size:14px;color:white;font-weight:700">{escape(format_label(day['format']))}</div>
  </div>
</div>"""


def render_tabs() -> str:
    return """<div class="tabs">
  <div class="tab active" onclick="switchTab('warmup')">Warm Up</div>
  <div class="tab" onclick="switchTab('cardio')">Cardio</div>
  <div class="tab" onclick="switchTab('floor')">Floor</div>
  <div class="tab" onclick="switchTab('cooldown')">Cool Down</div>
</div>"""


def render_warmup(day: dict) -> str:
    items = "\n".join(f"        <li>{escape(w)}</li>" for w in day.get("warmup", []))
    # Right column (bikes/treads) is a standard default — the manifest doesn't carry it.
    return f"""<div class="section active" id="tab-warmup">
  <div class="structure-banner">
    <div class="emoji">⏱</div>
    <div class="structure-text"><strong>45 seconds each</strong> · Coach all stations equally · 1-min transition between stations</div>
  </div>
  <div class="warmup-grid">
    <div class="warmup-col">
      <h3>Bench / Rack</h3>
      <ul>
{items}
      </ul>
    </div>
    <div class="warmup-col">
      <h3>Bikes / Treads</h3>
      <ul>
        <li><span class="wu-time">1:30</span> Easy</li>
        <li><span class="wu-time">1:30</span> Moderate</li>
      </ul>
    </div>
  </div>
</div>"""


def render_cardio(day: dict) -> str:
    rows = []
    for row in day.get("cardio", []):
        if "rounds" in row:
            rows.append(
                f'      <tr class="rounds-row"><td colspan="3">{escape(row["rounds"])}</td></tr>'
            )
            continue
        icls = intensity_class(row.get("intensity", ""))
        dcls = mz_class(row.get("mz", ""))
        rows.append(
            f'      <tr class="{icls}">\n'
            f'        <td class="time-cell">{escape(row.get("time", ""))}</td>\n'
            f'        <td><span class="intensity-badge">{escape(row.get("intensity", ""))}</span></td>\n'
            f'        <td><span class="mz-dot"><span class="dot {dcls}"></span>{escape(row.get("mz", ""))}</span></td>\n'
            f"      </tr>"
        )
    body = "\n".join(rows)
    # Optional coach-authored cardio note (trusted markup — may contain <strong>).
    note = day.get("cardio_note")
    note_html = (
        f'\n  <div class="note-box"><strong>Coach Note:</strong> {note}</div>'
        if note
        else ""
    )
    return f"""<div class="section" id="tab-cardio">
  <table class="cardio-table">
    <thead><tr><th>TIME</th><th>INTENSITY</th><th>MZ COLOR</th></tr></thead>
    <tbody>
{body}
    </tbody>
  </table>{note_html}
</div>"""


def render_exercise_card(name: str, reps: str, entry: dict | None) -> str:
    icon = exercise_icon(name)
    reps_html = f'<div class="ex-reps">{escape(reps)}</div>' if reps else ""

    if entry is None:
        # Unmatched: flag for one-time enrichment (Phase 3) but still render a usable card.
        body = """      <div class="detail-section">
        <div class="detail-label">Not in library</div>
        <div class="detail-text">No enriched entry found for this exercise. Add it to exercise-library.json (cues / muscles / modifications / video) to complete this card.</div>
      </div>"""
        return _wrap_card(icon, name, reps_html, body)

    sections = []

    # Movement + video button (video anchor carries NO JS — §7)
    movement = escape(entry.get("movement", ""))
    video = entry.get("video")
    video_html = ""
    if video:
        fallback = entry.get("video_is_search_fallback")
        title = ' title="Search fallback — replace when a real source surfaces"' if fallback else ""
        label = "Watch Video"
        video_html = (
            f'\n        <a class="video-btn" href="{escape(video, quote=True)}" '
            f'target="_blank" rel="noopener"{title}>'
            f'<span class="video-icon">▶</span> {label}</a>'
        )
    sections.append(
        f"""      <div class="detail-section">
        <div class="detail-label">Movement</div>
        <div class="detail-text">{movement}</div>{video_html}
      </div>"""
    )

    # Coaching cues (yellow › arrows; cue text already quoted in the library)
    cues = "\n".join(
        f'          <div class="cue-item"><span class="cue-arrow">›</span>{escape(c)}</div>'
        for c in entry.get("cues", [])
    )
    sections.append(
        f"""      <div class="detail-section">
        <div class="detail-label">Coaching Cues</div>
        <div class="cues-list">
{cues}
        </div>
      </div>"""
    )

    # Muscles (red primary + gray secondary)
    tags = []
    for m in entry.get("muscles_primary", []):
        tags.append(f'          <span class="muscle-tag primary"><span class="muscle-dot"></span>{escape(m)}</span>')
    for m in entry.get("muscles_secondary", []):
        tags.append(f'          <span class="muscle-tag secondary"><span class="muscle-dot"></span>{escape(m)}</span>')
    sections.append(
        f"""      <div class="detail-section">
        <div class="detail-label">Muscles</div>
        <div class="muscles">
{chr(10).join(tags)}
        </div>
      </div>"""
    )

    # Modifications (⬇️ Easier / ⬆️ Harder / 🩹 injury-specific)
    mods = []
    for mod in entry.get("modifications", []):
        mtype = mod.get("type", "")
        mods.append(
            f'          <div class="mod-item"><span class="mod-icon">{mod_icon(mtype)}</span>'
            f'<div class="mod-text"><span class="mod-label">{escape(mtype)}</span>'
            f'{escape(mod.get("text", ""))}</div></div>'
        )
    sections.append(
        f"""      <div class="detail-section">
        <div class="detail-label">Modifications</div>
        <div class="mod-list">
{chr(10).join(mods)}
        </div>
      </div>"""
    )

    return _wrap_card(icon, name, reps_html, "\n".join(sections))


def _wrap_card(icon: str, name: str, reps_html: str, body: str) -> str:
    # §7: NO onclick on .exercise-card; onclick lives on .exercise-header only.
    return f"""  <div class="exercise-card">
    <div class="exercise-header" onclick="toggleCard(this.parentElement)">
      <div class="ex-left">
        <div class="ex-icon">{icon}</div>
        <div>
          <div class="ex-name">{escape(name)}</div>
          {reps_html}
        </div>
      </div>
      <span class="chevron">▼</span>
    </div>
    <div class="exercise-body">
{body}
    </div>
  </div>"""


def render_ladder_banner(day: dict) -> str:
    """If any rep string carries a ladder sequence (e.g. '8→4→8'), show the 🪜 banner."""
    seq = None
    for ex in day.get("exercises", []):
        m = re.search(r"(\d+(?:\s*→\s*\d+)+)", ex.get("reps", ""))
        if m:
            seq = m.group(1)
            break
    if not seq:
        return ""
    nums = [int(n) for n in re.findall(r"\d+", seq)]
    # A two-number ladder (e.g. 15→10) is a per-rep run — expand it inclusively
    # so every rung shows. An explicit multi-number ladder (8→4→8) is used as-is.
    if len(nums) == 2:
        a, b = nums
        step = 1 if b >= a else -1
        nums = list(range(a, b + step, step))
    pills = "".join(f'<span class="ladder-pill">{n}</span>' for n in nums)
    return f"""  <div class="ladder-banner">
    <div class="emoji">\U0001FA9C</div>
    <div class="ladder-text"><strong>Rep Ladder</strong><div class="ladder-pills">{pills}</div></div>
  </div>"""


def render_floor(day: dict, lib: dict[str, dict], unmatched: list[str]) -> str:
    kind = format_kind(day["format"])
    banner_text, note_text = FLOOR_COPY.get(
        kind, (DEFAULT_FLOOR[0].format(label=format_label(day["format"])), DEFAULT_FLOOR[1])
    )

    # Optional station grouping: when exercises carry a "station" label (e.g. "🏋️ Bench"),
    # emit a section-label header each time it changes. Omitted entirely if absent.
    cards = []
    current_station = None
    for ex in day.get("exercises", []):
        name = ex["name"]
        station = ex.get("station")
        if station and station != current_station:
            cards.append(f'  <div class="section-label">{escape(station)}</div>')
            current_station = station
        entry = lookup(lib, name)
        if entry is None:
            unmatched.append(name)
        cards.append(render_exercise_card(name, ex.get("reps", ""), entry))

    note_text = day.get("floor_note", note_text)  # optional per-day override (trusted markup)

    ladder = render_ladder_banner(day)
    ladder_block = (ladder + "\n\n") if ladder else ""

    return f"""<div class="section" id="tab-floor">
  <div class="structure-banner">
    <div class="emoji">⏰</div>
    <div class="structure-text">{banner_text}</div>
  </div>

{ladder_block}{chr(10).join(cards)}

  <div class="note-box" style="margin-top:14px">
    <strong>Floor Note:</strong> {note_text}
  </div>
</div>"""


# Cool Down is identical across all cards (brief §6) — reproduced verbatim from the reference.
COOLDOWN = """<div class="section" id="tab-cooldown">
  <div class="timer-note" style="margin-bottom:14px">
    <div class="big">~45 sec each</div>
    <div class="sub">Choose 3 muscle groups only</div>
  </div>
  <div class="cooldown-grid">
    <div class="stretch-card">
      <div class="stretch-icon">\U0001F9B5</div>
      <div class="stretch-name">Quads</div>
      <div class="stretch-time">45 SECONDS</div>
      <div class="stretch-cue">Standing quad pull or kneeling lunge. Hip forward, tall spine.</div>
    </div>
    <div class="stretch-card">
      <div class="stretch-icon">\U0001F9B6</div>
      <div class="stretch-name">Calves</div>
      <div class="stretch-time">45 SECONDS</div>
      <div class="stretch-cue">Wall calf stretch. Heel down, slight knee bend for soleus.</div>
    </div>
    <div class="stretch-card">
      <div class="stretch-icon">\U0001F4AA</div>
      <div class="stretch-name">Pecs</div>
      <div class="stretch-time">45 SECONDS</div>
      <div class="stretch-cue">Doorframe or corner stretch. Arms at 90°, open chest forward.</div>
    </div>
    <div class="stretch-card">
      <div class="stretch-icon">\U0001F64C</div>
      <div class="stretch-name">Triceps</div>
      <div class="stretch-time">45 SECONDS</div>
      <div class="stretch-cue">Overhead stretch. Elbow up, hand behind neck, gentle assist.</div>
    </div>
  </div>
  <div class="note-box">
    <strong>Coach's Choice:</strong> Pick 3 of the above (or your own) based on what your class needs most today.
  </div>

  <div class="breathing-card">
    <div class="breathing-title">\U0001FAC1 Box Breathing</div>
    <div class="breathing-text"><strong>4 seconds in</strong> through the nose · <strong>4 seconds out</strong> through the mouth. Cue members to drop their HR and reset their nervous system before they head out.</div>
  </div>
</div>"""


SCRIPT = """<script>
  function switchTab(name) {
    document.querySelectorAll('.tab').forEach((t, i) => {
      const names = ['warmup','cardio','floor','cooldown'];
      t.classList.toggle('active', names[i] === name);
    });
    document.querySelectorAll('.section').forEach(s => {
      s.classList.toggle('active', s.id === 'tab-' + name);
    });
    window.scrollTo(0, 0);
  }
  function toggleCard(card) {
    card.classList.toggle('open');
  }
</script>"""


# --------------------------------------------------------------------------- #
# Style block — lifted verbatim from the reference, plus a few additive rules
# --------------------------------------------------------------------------- #
EXTRA_CSS = """
  /* --- build_card.py additions (brief §6: extend, don't alter, the reference) --- */
  .cardio-table tbody tr.rounds-row td { background: var(--card2); border-radius: 8px; text-align: center; font-family: 'Barlow Condensed', sans-serif; font-size: 20px; font-weight: 700; letter-spacing: 2px; text-transform: uppercase; color: var(--yellow); padding: 9px 12px; }
  .max .intensity-badge { background: rgba(255,59,59,0.25); color: white; }
  .dot.red { background: #FF3B3B; }
  .ladder-banner { background: rgba(245,200,66,0.12); border: 1px solid rgba(245,200,66,0.5); border-radius: 10px; padding: 12px 14px; margin-bottom: 14px; display: flex; align-items: center; gap: 10px; }
  .ladder-banner .emoji { font-size: 26px; flex-shrink: 0; }
  .ladder-text { font-family: 'Barlow Condensed', sans-serif; font-weight: 700; font-size: 19px; letter-spacing: 1.5px; text-transform: uppercase; color: var(--yellow); }
  .ladder-pills { display: flex; gap: 6px; margin-top: 6px; }
  .ladder-pill { display: inline-block; background: var(--yellow); color: var(--black); font-weight: 900; font-size: 20px; border-radius: 14px; padding: 2px 12px; letter-spacing: 0; }
"""


MONTH_NAMES = ["", "January", "February", "March", "April", "May", "June",
               "July", "August", "September", "October", "November", "December"]
WEEKDAY_INITIALS = ["S", "M", "T", "W", "T", "F", "S"]  # Sunday-first


def render_calendar_month(year: int, month: int, day_to_date: dict[int, str], manifests: dict[str, dict]) -> str:
    """One month as a 7-column calendar grid. Workout days are red, clickable cells."""
    cal = calendar.Calendar(firstweekday=6)  # 6 = Sunday
    dow = "".join(f'<div class="cal-dow">{w}</div>' for w in WEEKDAY_INITIALS)
    cells = []
    for dnum in (d for week in cal.monthdayscalendar(year, month) for d in week):
        if dnum == 0:
            cells.append('    <div class="cal-cell empty"></div>')
        elif dnum in day_to_date:
            ds = day_to_date[dnum]
            kind = format_kind(manifests[ds]["format"])
            cells.append(
                f'    <a class="cal-cell workout kind-{kind.lower()}" href="studio-red-{ds}.html">'
                f'<span class="cal-day">{dnum}</span>'
                f'<span class="cal-badge">{escape(kind)}</span></a>'
            )
        else:
            cells.append(f'    <div class="cal-cell"><span class="cal-day muted">{dnum}</span></div>')
    grid = "\n".join(cells)
    n = len(day_to_date)
    return f"""<div class="cal-month">
  <div class="cal-title">{MONTH_NAMES[month]} {year} <span class="cal-count">{n} class{'es' if n != 1 else ''}</span></div>
  <div class="cal-grid">
    {dow}
{grid}
  </div>
</div>"""


def render_index(manifests: dict[str, dict], style_inner: str) -> str:
    """A dark-themed launcher: a month calendar per month with classes — tap a day to open its card."""
    # Group classes by (year, month) -> {day_of_month: iso_date}
    by_month: dict[tuple[int, int], dict[int, str]] = defaultdict(dict)
    for d in manifests:
        y, m, dd = (int(x) for x in d.split("-"))
        by_month[(y, m)][dd] = d

    # Most recent month first (matches a coach reaching for the latest classes).
    months_html = "\n".join(
        render_calendar_month(y, m, by_month[(y, m)], manifests)
        for (y, m) in sorted(by_month, reverse=True)
    )

    # Legend: which colour maps to which class format.
    legend = "".join(
        f'<span class="legend-item"><span class="legend-dot kind-{k}"></span>{k.upper()}</span>'
        for k in ("emom", "circuit", "superset", "ladder", "benchmark")
    )

    n = len(manifests)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0">
<title>Studio Red · Coach Cards</title>
<link href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@400;600;700;900&family=Barlow:wght@400;500;600&display=swap" rel="stylesheet">
<style>{style_inner}
  .home-wrap {{ padding: 14px; max-width: 640px; margin: 0 auto; }}
  .home-sub {{ font-family: 'Barlow Condensed', sans-serif; font-weight: 700; font-size: 17px; letter-spacing: 2px; text-transform: uppercase; color: var(--muted); margin: 4px 0 14px; }}
  .cal-month {{ margin-bottom: 26px; }}
  .cal-title {{ font-family: 'Barlow Condensed', sans-serif; font-weight: 900; font-size: 30px; letter-spacing: 1px; text-transform: uppercase; color: white; margin: 6px 2px 12px; }}
  .cal-count {{ font-size: 15px; font-weight: 700; color: var(--muted); letter-spacing: 1px; margin-left: 6px; }}
  .cal-grid {{ display: grid; grid-template-columns: repeat(7, 1fr); gap: 6px; }}
  .cal-dow {{ text-align: center; font-family: 'Barlow Condensed', sans-serif; font-weight: 700; font-size: 14px; letter-spacing: 1px; color: var(--muted); padding-bottom: 2px; }}
  .cal-cell {{ aspect-ratio: 1 / 1; border-radius: 10px; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 3px; background: var(--card); border: 1px solid var(--border); }}
  .cal-cell.empty {{ background: transparent; border: none; }}
  .cal-day {{ font-family: 'Barlow Condensed', sans-serif; font-weight: 700; font-size: 20px; line-height: 1; color: #cfcfcf; }}
  .cal-day.muted {{ color: #555; font-weight: 600; }}
  a.cal-cell.workout {{ text-decoration: none; background: var(--red); border-color: var(--red-dark); box-shadow: 0 2px 8px rgba(212,43,43,0.35); }}
  a.cal-cell.workout .cal-day {{ color: white; font-weight: 900; font-size: 23px; }}
  a.cal-cell.workout:active {{ filter: brightness(1.15); }}
  .cal-badge {{ font-family: 'Barlow Condensed', sans-serif; font-weight: 700; font-size: 9px; letter-spacing: 0.5px; text-transform: uppercase; color: rgba(255,255,255,0.92); padding: 0 2px; text-align: center; line-height: 1.05; }}
  .legend {{ display: flex; flex-wrap: wrap; gap: 12px; margin: 4px 2px 20px; }}
  .legend-item {{ display: inline-flex; align-items: center; gap: 6px; font-family: 'Barlow Condensed', sans-serif; font-weight: 700; font-size: 13px; letter-spacing: 1px; color: var(--muted); }}
  .legend-dot {{ width: 12px; height: 12px; border-radius: 50%; display: inline-block; }}
  .legend-dot.kind-emom {{ background: var(--blue); }}
  .legend-dot.kind-circuit {{ background: var(--green); }}
  .legend-dot.kind-superset {{ background: #FF6B6B; }}
  .legend-dot.kind-ladder {{ background: var(--yellow); }}
  .legend-dot.kind-benchmark {{ background: #B06CE8; }}
</style>
</head>
<body>

{render_header_home()}

<div class="home-wrap">
  <div class="home-sub">{n} class{'es' if n != 1 else ''} · tap a day to open</div>
  <div class="legend">{legend}</div>
{months_html}
</div>

</body>
</html>
"""


def render_header_home() -> str:
    return """<div class="header">
  <div class="header-left">
    <div class="logo-box">V</div>
    <div>
      <div class="header-title">Studio Red</div>
      <div class="header-date">COACH CARDS</div>
    </div>
  </div>
  <div style="text-align:right">
    <div style="font-family:'Barlow Condensed',sans-serif;font-size:11px;color:rgba(255,255,255,0.6);letter-spacing:1px">COACH GUIDE</div>
  </div>
</div>"""


def load_style_block(reference: Path) -> str:
    """Return the inner CSS of the reference <style>...</style>, plus additive rules."""
    html = reference.read_text(encoding="utf-8")
    m = re.search(r"<style>(.*?)</style>", html, re.DOTALL)
    if not m:
        raise SystemExit(f"Could not find <style> block in {reference}")
    return m.group(1).rstrip() + "\n" + EXTRA_CSS


# --------------------------------------------------------------------------- #
# Page assembly
# --------------------------------------------------------------------------- #
def render_card(day: dict, lib: dict[str, dict], style_inner: str) -> tuple[str, list[str]]:
    unmatched: list[str] = []
    title = f"Studio Red · {fmt_date_slashes(day['date'])}"
    sections = "\n\n".join(
        [
            render_warmup(day),
            render_cardio(day),
            render_floor(day, lib, unmatched),
            COOLDOWN,
        ]
    )
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0">
<title>{escape(title)}</title>
<link href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@400;600;700;900&family=Barlow:wght@400;500;600&display=swap" rel="stylesheet">
<style>{style_inner}</style>
</head>
<body>

{render_header(day)}

{render_tabs()}

{sections}

{SCRIPT}

</body>
</html>
"""
    return html, unmatched


# --------------------------------------------------------------------------- #
# §7 invariant check
# --------------------------------------------------------------------------- #
def verify_invariants(html: str) -> dict:
    cards_with_onclick = len(re.findall(r'class="exercise-card"[^>]*onclick', html))
    headers_with_onclick = len(re.findall(r'class="exercise-header" onclick="toggleCard', html))
    video_anchors = html.count('class="video-btn"')
    video_with_onclick = len(re.findall(r'class="video-btn"[^>]*onclick', html))
    return {
        "exercise_cards": html.count('class="exercise-card"'),
        "cards_with_onclick": cards_with_onclick,           # must be 0
        "headers_with_onclick": headers_with_onclick,       # must equal exercise_cards
        "video_buttons": video_anchors,
        "video_with_onclick": video_with_onclick,           # must be 0
        "tabs": html.count('class="tab"') + html.count('class="tab active"'),
    }


def print_verification(html: str, unmatched: list[str]) -> bool:
    inv = verify_invariants(html)
    ok = (
        inv["cards_with_onclick"] == 0
        and inv["video_with_onclick"] == 0
        and inv["headers_with_onclick"] == inv["exercise_cards"]
        and inv["tabs"] == 4
    )
    print("  §7 click-handler invariants:")
    print(f"    exercise cards ............ {inv['exercise_cards']}")
    print(f"    cards w/ onclick (must=0) . {inv['cards_with_onclick']}")
    print(f"    headers w/ onclick (=cards) {inv['headers_with_onclick']}")
    print(f"    video buttons ............. {inv['video_buttons']}")
    print(f"    video w/ onclick (must=0) . {inv['video_with_onclick']}")
    print(f"    tabs (must=4) ............. {inv['tabs']}")
    print(f"  result: {'PASS' if ok else 'FAIL'}")
    if unmatched:
        print(f"  unmatched exercises (need enrichment): {unmatched}")
    return ok


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def build_one(day: dict, lib: dict, style_inner: str, out_dir: Path, verify: bool) -> Path:
    html, unmatched = render_card(day, lib, style_inner)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"studio-red-{day['date']}.html"
    out_path.write_text(html, encoding="utf-8")
    try:
        shown = out_path.relative_to(ROOT)
    except ValueError:
        shown = out_path
    print(f"✓ {shown}  ({day['format']}, {len(day.get('exercises', []))} floor exercises)")
    if verify:
        print_verification(html, unmatched)
    elif unmatched:
        print(f"  ⚠ unmatched (need enrichment): {unmatched}")
    return out_path


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Render a Studio Red coach card for a date.")
    p.add_argument("date", nargs="?", help="Class date, YYYY-MM-DD (e.g. 2026-06-02)")
    p.add_argument("--all", action="store_true", help="Render every day in the manifest + index")
    p.add_argument("--index", action="store_true", help="(Re)build only the index launcher page")
    p.add_argument("--list", action="store_true", help="List available dates and exit")
    p.add_argument("--verify", action="store_true", help="Print §7 invariant check per card")
    p.add_argument("--lib", type=Path, default=DEFAULT_LIB)
    p.add_argument("--manifests", type=Path, default=DEFAULT_MANIFESTS)
    p.add_argument("--reference", type=Path, default=DEFAULT_REFERENCE)
    p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = p.parse_args(argv)

    lib = load_library(args.lib)
    manifests = load_manifests(args.manifests)
    style_inner = load_style_block(args.reference)

    if args.list:
        for d in sorted(manifests):
            print(f"{d}  {manifests[d]['format']}")
        return 0

    if args.all or args.index:
        if args.all:
            for d in sorted(manifests):
                build_one(manifests[d], lib, style_inner, args.out, args.verify)
        args.out.mkdir(parents=True, exist_ok=True)
        idx_path = args.out / "index.html"
        idx_path.write_text(render_index(manifests, style_inner), encoding="utf-8")
        print(f"✓ {idx_path.relative_to(ROOT) if ROOT in idx_path.parents else idx_path}  (launcher · {len(manifests)} dates)")
        return 0

    if not args.date:
        p.error("provide a date (YYYY-MM-DD), or --all, or --list")

    if args.date not in manifests:
        print(f"No manifest for {args.date}. Available:", file=sys.stderr)
        for d in sorted(manifests):
            print(f"  {d}  {manifests[d]['format']}", file=sys.stderr)
        return 1

    build_one(manifests[args.date], lib, style_inner, args.out, args.verify)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
