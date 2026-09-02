#!/usr/bin/env python3
"""
build_lft_card.py — render Studio LFT coach cards from lft-day-manifests.json.

Studio LFT is VASA's weightlifting class — structurally different from Studio
Red (strength blocks with Sets x Reps, not cardio intervals), so it gets its
own template, own manifest schema, and own calendar, living entirely under
cards/lft/ and data/lft-day-manifests.json. Studio Red's own files are never
touched by this script.

Usage:
  python3 src/build_lft_card.py 2026-07-06        # render one date
  python3 src/build_lft_card.py --all             # render every date + index
  python3 src/build_lft_card.py --list            # list available dates
"""
from __future__ import annotations

import argparse
import calendar
import json
import re
from datetime import date
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MANIFESTS = ROOT / "data" / "lft-day-manifests.json"
DEFAULT_CUES = ROOT / "data" / "lft-exercise-cues.json"
DEFAULT_OUT = ROOT / "cards" / "lft"

FOCUS_COLOR = {"Lower": "lower", "Upper": "upper", "Full": "full"}
MONTH_NAMES = ["", "January", "February", "March", "April", "May", "June",
               "July", "August", "September", "October", "November", "December"]
WEEKDAY_INITIALS = ["S", "M", "T", "W", "T", "F", "S"]

# HQ periodizes Studio LFT in 6-week cycles that rotate Build → Pump →
# Power → Brawn (every 13th week is a testing/technique week instead, which
# is what the benchmark days are). The day's header label carries which one
# it is, e.g. "Pump B2 – W3 Day 2 Upper" = Pump cycle, block 2, week 3.
CYCLE_LENGTH_WEEKS = 6
CYCLES = {
    "build": ("Build", "Strength",
              "Basic strength training using common strength protocols to build a solid base of "
              "strength capacity."),
    "pump": ("Pump", "Hypertrophy",
             "Muscle-building phase focusing on increased volume and some isolation work."),
    "power": ("Power", None,
              "An athletic phase that includes explosive and powerlifting-based protocols to "
              "increase rate of force production."),
    "brawn": ("Brawn", "Advanced Strength",
              "Building on the strength cycle using more advanced protocols to continue to drive "
              "strength gains."),
}
CYCLE_LABEL_RE = re.compile(r"^([A-Za-z]+)\s*B(\d+)\s*[-–]?\s*Wk?\s*(\d+)", re.I)


def cycle_for(day: dict) -> dict | None:
    """The training cycle a day belongs to, from its header label.

    Benchmark weeks aren't part of a cycle (they're the testing week), and
    an unrecognized cycle name is left off rather than guessed at.
    """
    m = CYCLE_LABEL_RE.match(day.get("label") or "")
    if not m or day.get("kind") == "benchmark":
        return None
    entry = CYCLES.get(m.group(1).lower())
    if not entry:
        return None
    name, phase_type, description = entry
    return {"name": name, "type": phase_type, "description": description,
            "block": int(m.group(2)), "week": int(m.group(3))}


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #
def load_manifests(path: Path) -> dict[str, dict]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {day["date"]: day for day in raw["days"]}


def fmt_date_slashes(iso: str) -> str:
    y, m, d = iso.split("-")
    return f"{m} / {d} / {y}"


def norm_name(s: str) -> str:
    """Key format used by lft-exercise-cues.json."""
    s = s.lower().replace(".", " ").replace("-", " ")
    return re.sub(r"\s+", " ", s).strip()


_CUES: dict[str, list[str]] | None = None


def cues_for(name: str, cues_path: Path = DEFAULT_CUES) -> list[str]:
    """The one or two coaching cues for an exercise, if we have any."""
    global _CUES
    if _CUES is None:
        _CUES = json.loads(cues_path.read_text(encoding="utf-8"))["cues"] if cues_path.exists() else {}
    return _CUES.get(norm_name(name or ""), [])


def main_lift_of(day: dict) -> str:
    """The day's headline lift, for the coach's intro script.

    Build/Pump/Power/Brawn weeks label it "Main LFT"; benchmark weeks split
    the session into LFT 1-4 instead, so fall back to the first of those.
    """
    for wanted in ("Main LFT", "LFT 1"):
        for sec in day.get("sections", []):
            if sec["name"] == wanted:
                names = [e["name"] for e in sec["exercises"] if e.get("name")]
                if names:
                    return " and ".join(names)
    return ""


def intro_script(day: dict) -> list[tuple[str, str]]:
    """The opening talk track, with the day's own details filled in."""
    focus = day.get("focus") or ""
    day_name = f"{focus} Body" if focus in ("Upper", "Lower", "Full") else "today's session"
    cycle = cycle_for(day)
    if cycle:
        cycle_phrase = f"we're in the <b>{escape(cycle['name'])}</b> training cycle, week {cycle['week']} of {CYCLE_LENGTH_WEEKS}"
    else:
        cycle_phrase = "this is a <b>benchmark week</b>, so we're testing where your strength is at"
    lift = main_lift_of(day)
    lift_line = (f"The main movement today is <b>{escape(lift)}</b> — I'll walk you through it right after training prep."
                 if lift else "I'll walk you through the main movement right after training prep.")
    return [
        ("Welcome", "Welcome to STUDIO LFT! My name is <span class=\"intro-blank\">your name</span>."),
        ("Today's plan", f"Today's focus is <b>{escape(day_name)}</b> and {cycle_phrase}."),
        ("Safety", "Please use the catches and spot your partner. If you don't have a partner today, call me over and I'll spot you."),
        ("Main lift", lift_line),
        ("Modifications", "If you need a modification on anything as we go, call me over — there's an option for everyone."),
        ("Let's go", "Alright — let's get pumped up to LFT!"),
    ]


# --------------------------------------------------------------------------- #
# Section / row rendering
# --------------------------------------------------------------------------- #
def render_exercise_row(ex: dict) -> str:
    if "instruction" in ex:
        extra = f" — {escape(ex['notes'])}" if ex.get("notes") else ""
        return f"""    <div class="lft-note-row">
      <span class="lft-note-icon">↳</span>{escape(ex["instruction"])}{extra}
    </div>"""

    name = ex.get("name", "")
    video = ex.get("video")
    name_html = (
        f'<a class="lft-ex-name" href="{escape(video, quote=True)}" target="_blank" rel="noopener">{escape(name)}</a>'
        if video else f'<span class="lft-ex-name">{escape(name)}</span>'
    )
    loc = ex.get("location")
    loc_html = f'<span class="lft-loc">{escape(loc)}</span>' if loc else ""
    sets_reps = ex.get("sets_reps", "")
    notes = ex.get("notes", "")

    cue_html = ""
    cues = cues_for(name)
    if cues:
        items = "".join(f'<li>{escape(c)}</li>' for c in cues)
        cue_html = f'\n        <ul class="lft-ex-cues">{items}</ul>'

    return f"""    <div class="lft-ex-row">
      <div class="lft-ex-label">{escape(ex.get("label", ""))}</div>
      <div class="lft-ex-body">
        <div class="lft-ex-top">{name_html}<span class="lft-ex-sr">{escape(sets_reps)}</span>{loc_html}</div>
        <div class="lft-ex-notes">{escape(notes)}</div>{cue_html}
      </div>
    </div>"""


def render_section(sec: dict) -> str:
    time_html = ""
    if sec.get("time_allotted"):
        time_html = f'<span class="lft-time">{escape(sec["time_allotted"])}</span>'
    note_html = (
        f'<div class="lft-section-note">{escape(sec["time_note"])}</div>' if sec.get("time_note") else ""
    )
    rows = "\n".join(render_exercise_row(ex) for ex in sec["exercises"])
    return f"""  <div class="lft-section">
    <div class="lft-section-head"><span>{escape(sec["name"])}</span>{time_html}</div>
    {note_html}
{rows}
  </div>"""


def render_card(day: dict) -> str:
    focus = day.get("focus") or ""
    focus_cls = FOCUS_COLOR.get(focus, "full")
    week_label = f"Week {day['week']}" if day.get("week") else "Benchmark"
    title = f"Studio LFT · {fmt_date_slashes(day['date'])}"
    sections_html = "\n\n".join(render_section(s) for s in day["sections"])

    intro_html = "".join(
        f'      <div class="intro-line"><span class="intro-label">{escape(label)}</span>{text}</div>\n'
        for label, text in intro_script(day)
    )

    cycle = cycle_for(day)
    cycle_tag = f'<span class="cycle-tag">{escape(cycle["name"])}</span>' if cycle else ""
    cycle_banner = ""
    if cycle:
        phase_type = f' · <span class="cycle-type">{escape(cycle["type"])}</span>' if cycle["type"] else ""
        cycle_banner = f"""  <div class="cycle-banner">
    <div class="cycle-banner-head">{escape(cycle["name"])} Cycle{phase_type}
      <span class="cycle-meta">Block {cycle["block"]} · Week {cycle["week"]} of {CYCLE_LENGTH_WEEKS}</span>
    </div>
    <div class="cycle-banner-desc">{escape(cycle["description"])}</div>
  </div>

"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0">
<title>{escape(title)}</title>
<link href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@400;600;700;900&family=Barlow:wght@400;500;600&display=swap" rel="stylesheet">
<style>{STYLE}</style>
</head>
<body>

<div class="header">
  <div class="header-left">
    <div class="logo-box">V</div>
    <div>
      <div class="header-title">Studio LFT</div>
      <div class="header-date">{escape(fmt_date_slashes(day['date']))}</div>
    </div>
  </div>
  <div style="text-align:right">
    <div class="header-week">{escape(week_label)}{cycle_tag}</div>
    <span class="focus-badge focus-{focus_cls}">{escape(focus)}</span>
  </div>
</div>

<div class="lft-wrap">
  <details class="intro-drawer">
    <summary><span class="intro-chev">▸</span>Coach intro &amp; talk track</summary>
    <div class="intro-body">
{intro_html}    </div>
  </details>

{cycle_banner}  <div class="session-notes">{escape(day.get("session_notes", ""))}</div>

{sections_html}
</div>

</body>
</html>
"""


# --------------------------------------------------------------------------- #
# Calendar / dashboard
# --------------------------------------------------------------------------- #
def render_header_home() -> str:
    return """<div class="header">
  <div class="header-left">
    <div class="logo-box">V</div>
    <div>
      <div class="header-title">Studio LFT</div>
      <div class="header-date">COACH GUIDE</div>
    </div>
  </div>
</div>"""


def render_calendar_month(year: int, month: int, day_to_date: dict[int, str], manifests: dict[str, dict]) -> str:
    cal = calendar.Calendar(firstweekday=6)
    dow = "".join(f'<div class="cal-dow">{w}</div>' for w in WEEKDAY_INITIALS)
    cells = []
    for dnum in (d for week in cal.monthdayscalendar(year, month) for d in week):
        if dnum == 0:
            cells.append('    <div class="cal-cell empty"></div>')
        elif dnum in day_to_date:
            ds = day_to_date[dnum]
            focus = manifests[ds].get("focus") or "Full"
            cls = FOCUS_COLOR.get(focus, "full")
            label = "B-MARK" if manifests[ds]["kind"] == "benchmark" else focus.upper()
            cells.append(
                f'    <a class="cal-cell workout kind-{cls}" href="studio-lft-{ds}.html">'
                f'<span class="cal-day">{dnum}</span>'
                f'<span class="cal-badge">{escape(label)}</span></a>'
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


def render_index(manifests: dict[str, dict]) -> str:
    by_month: dict[tuple[int, int], dict[int, str]] = {}
    for d in manifests:
        y, m, dd = (int(x) for x in d.split("-"))
        by_month.setdefault((y, m), {})[dd] = d

    months_html = "\n".join(
        render_calendar_month(y, m, by_month[(y, m)], manifests)
        for (y, m) in sorted(by_month, reverse=True)
    )
    legend = "".join(
        f'<span class="legend-item"><span class="legend-dot kind-{k}"></span>{k.upper()}</span>'
        for k in ("lower", "upper", "full")
    )
    n = len(manifests)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0">
<title>Studio LFT · Coach Cards</title>
<link href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@400;600;700;900&family=Barlow:wght@400;500;600&display=swap" rel="stylesheet">
<style>{STYLE}
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
  .legend-dot.kind-lower {{ background: var(--blue); }}
  .legend-dot.kind-upper {{ background: var(--yellow); }}
  .legend-dot.kind-full {{ background: var(--green); }}
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


# --------------------------------------------------------------------------- #
# Style block
# --------------------------------------------------------------------------- #
STYLE = """
  :root {
    --red: #D42B2B; --red-dark: #A01F1F; --black: #0F0F0F;
    --card: #242424; --card2: #2E2E2E; --border: #3A3A3A;
    --muted: #999; --yellow: #F5C842; --green: #4CAF50; --blue: #4A9EE8;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--black); color: white; font-family: 'Barlow', sans-serif; font-size: 20px; min-height: 100vh; padding-bottom: 40px; }
  a { color: inherit; }
  .header { background: var(--red); padding: 14px 16px 12px; display: flex; align-items: center; justify-content: space-between; position: sticky; top: 0; z-index: 100; border-bottom: 3px solid var(--red-dark); }
  .header-left { display: flex; align-items: center; gap: 10px; }
  .logo-box { width: 36px; height: 36px; background: white; border-radius: 6px; display: flex; align-items: center; justify-content: center; font-family: 'Barlow Condensed', sans-serif; font-weight: 900; font-size: 22px; color: var(--red); letter-spacing: -1px; }
  .header-title { font-family: 'Barlow Condensed', sans-serif; font-weight: 900; font-size: 22px; letter-spacing: 2px; color: white; text-transform: uppercase; }
  .header-date { font-family: 'Barlow Condensed', sans-serif; font-weight: 600; font-size: 14px; color: white; letter-spacing: 1px; }
  .header-week { font-family: 'Barlow Condensed', sans-serif; font-size: 11px; color: rgba(255,255,255,0.75); letter-spacing: 1px; text-transform: uppercase; }
  .focus-badge { display: inline-block; margin-top: 3px; font-family: 'Barlow Condensed', sans-serif; font-weight: 800; font-size: 13px; letter-spacing: 1px; text-transform: uppercase; padding: 2px 10px; border-radius: 12px; background: rgba(255,255,255,0.18); color: white; }
  .focus-lower { background: rgba(74,158,232,0.35); }
  .focus-upper { background: rgba(245,200,66,0.3); color: #3a2e00; }
  .focus-full { background: rgba(76,175,80,0.35); }

  .cycle-tag { display: inline-block; margin-left: 7px; font-family: 'Barlow Condensed', sans-serif; font-weight: 800; font-size: 12px; letter-spacing: 1px; text-transform: uppercase; padding: 1px 8px; border-radius: 10px; background: rgba(255,255,255,0.22); color: white; vertical-align: 1px; }

  .lft-wrap { padding: 14px; max-width: 640px; margin: 0 auto; }

  .intro-drawer { background: var(--card); border: 1px solid var(--border); border-radius: 10px; margin-bottom: 12px; overflow: hidden; }
  .intro-drawer > summary { list-style: none; cursor: pointer; padding: 12px 14px; font-family: 'Barlow Condensed', sans-serif; font-weight: 800; font-size: 18px; letter-spacing: 1px; text-transform: uppercase; color: #ddd; display: flex; align-items: center; gap: 9px; }
  .intro-drawer > summary::-webkit-details-marker { display: none; }
  .intro-drawer > summary:active { background: var(--card2); }
  .intro-chev { color: var(--red); font-size: 15px; transition: transform 0.15s; display: inline-block; }
  .intro-drawer[open] .intro-chev { transform: rotate(90deg); }
  .intro-body { padding: 2px 14px 12px; border-top: 1px solid var(--border); }
  .intro-line { font-size: 16.5px; color: #ddd; line-height: 1.45; margin-top: 10px; }
  .intro-line b { color: var(--yellow); font-weight: 700; }
  .intro-label { display: block; font-family: 'Barlow Condensed', sans-serif; font-weight: 700; font-size: 13px; letter-spacing: 1.2px; text-transform: uppercase; color: var(--muted); margin-bottom: 1px; }
  .intro-blank { color: var(--muted); border-bottom: 1px dashed var(--muted); font-style: italic; }

  .cycle-banner { background: var(--card); border-left: 3px solid var(--yellow); border-radius: 0 8px 8px 0; padding: 10px 13px; margin-bottom: 12px; }
  .cycle-banner-head { font-family: 'Barlow Condensed', sans-serif; font-weight: 800; font-size: 19px; letter-spacing: 1px; text-transform: uppercase; color: var(--yellow); }
  .cycle-type { color: #ffe9a3; font-weight: 700; }
  .cycle-meta { display: inline-block; font-family: 'Barlow', sans-serif; font-weight: 600; font-size: 13.5px; letter-spacing: 0.3px; text-transform: none; color: var(--muted); margin-left: 6px; }
  .cycle-banner-desc { font-size: 16px; color: #ccc; line-height: 1.45; margin-top: 4px; }
  .session-notes { background: var(--card); border-left: 3px solid var(--red); border-radius: 0 8px 8px 0; padding: 11px 13px; font-size: 17px; color: #ddd; line-height: 1.5; margin-bottom: 16px; }

  .lft-section { margin-bottom: 16px; }
  .lft-section-head { display: flex; align-items: center; justify-content: space-between; background: #3d3d3d; border-radius: 8px; padding: 8px 12px; font-family: 'Barlow Condensed', sans-serif; font-weight: 800; font-size: 19px; letter-spacing: 1.5px; text-transform: uppercase; color: white; margin-bottom: 8px; }
  .lft-time { font-family: 'Barlow Condensed', sans-serif; font-weight: 800; font-size: 17px; color: var(--yellow); letter-spacing: 0.5px; }
  .lft-section-note { font-size: 15px; color: var(--muted); padding: 0 4px 8px; line-height: 1.4; }

  .lft-ex-row { display: flex; gap: 10px; background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 10px 12px; margin-bottom: 8px; }
  .lft-ex-label { font-family: 'Barlow Condensed', sans-serif; font-weight: 800; font-size: 19px; color: var(--red); background: rgba(212,43,43,0.15); border-radius: 6px; padding: 2px 7px; align-self: flex-start; flex-shrink: 0; min-width: 20px; text-align: center; }
  .lft-ex-body { flex: 1; min-width: 0; }
  .lft-ex-top { display: flex; flex-wrap: wrap; align-items: baseline; gap: 8px; }
  .lft-ex-name { font-weight: 700; font-size: 20px; text-decoration: none; border-bottom: 1px solid rgba(255,255,255,0.35); }
  a.lft-ex-name:active { color: var(--yellow); }
  .lft-ex-sr { font-family: 'Barlow Condensed', sans-serif; font-weight: 700; font-size: 19px; color: var(--yellow); letter-spacing: 0.5px; }
  .lft-loc { font-size: 13px; font-weight: 700; letter-spacing: 0.5px; text-transform: uppercase; color: var(--blue); background: rgba(74,158,232,0.15); border-radius: 10px; padding: 1px 8px; }
  .lft-ex-notes { font-size: 17.5px; color: #bbb; line-height: 1.45; margin-top: 4px; }
  .lft-ex-cues { list-style: none; margin: 7px 0 0; padding: 7px 0 0; border-top: 1px solid var(--border); }
  .lft-ex-cues li { position: relative; padding-left: 16px; font-size: 16px; font-style: italic; color: var(--yellow); line-height: 1.4; margin-top: 3px; }
  .lft-ex-cues li::before { content: '“'; position: absolute; left: 0; top: 1px; font-style: normal; font-weight: 700; color: rgba(245,200,66,0.55); }
  .lft-ex-cues li::after { content: '”'; font-style: normal; color: rgba(245,200,66,0.55); }

  .lft-note-row { display: flex; align-items: flex-start; gap: 8px; background: rgba(245,200,66,0.08); border: 1px dashed rgba(245,200,66,0.4); border-radius: 8px; padding: 8px 12px; margin-bottom: 8px; font-size: 15.5px; color: var(--yellow); }
  .lft-note-icon { flex-shrink: 0; }
"""


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def build_one(day: dict, out_dir: Path) -> Path:
    html = render_card(day)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"studio-lft-{day['date']}.html"
    out_path.write_text(html, encoding="utf-8")
    n_ex = sum(1 for s in day["sections"] for e in s["exercises"] if e.get("name"))
    try:
        shown = out_path.relative_to(ROOT)
    except ValueError:
        shown = out_path
    print(f"✓ {shown}  ({day.get('focus') or day['kind']}, {n_ex} exercises)")
    return out_path


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Render a Studio LFT coach card for a date.")
    p.add_argument("date", nargs="?", help="Class date, YYYY-MM-DD")
    p.add_argument("--all", action="store_true", help="Render every day in the manifest + index")
    p.add_argument("--index", action="store_true", help="(Re)build only the index/calendar page")
    p.add_argument("--list", action="store_true", help="List available dates and exit")
    p.add_argument("--manifests", type=Path, default=DEFAULT_MANIFESTS)
    p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = p.parse_args(argv)

    manifests = load_manifests(args.manifests)

    if args.list:
        for d in sorted(manifests):
            print(f"{d}  {manifests[d].get('focus') or manifests[d]['kind']}")
        return 0

    if args.all or args.index:
        if args.all:
            for d in sorted(manifests):
                build_one(manifests[d], args.out)
        args.out.mkdir(parents=True, exist_ok=True)
        idx_path = args.out / "index.html"
        idx_path.write_text(render_index(manifests), encoding="utf-8")
        print(f"✓ {idx_path.relative_to(ROOT)}  (launcher · {len(manifests)} dates)")
        return 0

    if not args.date:
        p.error("provide a date (YYYY-MM-DD), or --all, or --list")

    if args.date not in manifests:
        print(f"No manifest for {args.date}. Available:", file=sys.stderr)
        for d in sorted(manifests):
            print(f"  {d}", file=sys.stderr)
        return 1

    build_one(manifests[args.date], args.out)
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(main())
