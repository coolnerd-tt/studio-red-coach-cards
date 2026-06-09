# Studio Red Coach App — Build Brief

A handoff spec for building the Vasa Studio Red coach-card app in Claude Code. Everything here was validated by hand first: 8 working cards, a 50-exercise enriched library, and a proven PDF parser are included as seed assets.

---

## 1. What this is

A tool that turns Vasa's weekly Studio Red workout sheets into **mobile-optimized coach reference cards** — one per class date — that a coach uses on their phone during a live HIIT class. Each card is a dark-themed, 4-tab view (Warm Up / Cardio / Floor / Cool Down) of tappable, expandable exercise cards with coaching cues, target muscles, modifications, and a video link per exercise.

Today each card is built by hand. The app should make a new card a 2-minute job instead of a 30-minute one.

## 2. The core insight (read this before architecting)

Do **not** build "a thing that generates a card from a PDF." Build **an exercise library that compounds**, plus a thin assembly layer on top.

The same ~50 exercises recur constantly across dates. The expensive work is the *enrichment* — the 4 coaching cues, muscle tags, and 3 modifications per exercise. That work should happen **once per exercise, ever**, then be reused. A new card is then just: parse the day's sheet → match its exercises to library entries → drop in the day's format/cardio/video links → render.

This flips the unit of work from "per card" to "per new exercise," which trends toward zero over time.

## 3. Recommended architecture

| Layer | Responsibility | MVP choice | Why |
|---|---|---|---|
| **Exercise library** | Canonical enriched data per exercise | `exercise-library.json` (seeded, 50 entries) | The compounding asset. Start as flat JSON; migrate to SQLite/Postgres only when you outgrow it. |
| **Ingestion** | Parse a month PDF → per-day links + (later) exercise/cardio text | `parse_workout_pdf.py` (included, working) | Links are the proven hard part. Text parsing is a documented TODO hook. |
| **Matcher** | Map sheet exercise names → library entries | Fuzzy match (e.g. `rapidfuzz`), threshold ~85 | Names vary ("DB Chest Fly" vs "Dumbbell Chest Fly"). Unmatched → flagged for one-time enrichment. |
| **Enricher** | Generate cues/muscles/mods for *new* exercises only | Claude API call w/ a strict schema + the style guide in §6 | Only runs on cache-miss. Human reviews before it enters the library. |
| **Renderer** | Day data + template → final card | Jinja2 template built from `card-template-reference.html` | Keep output as a self-contained static HTML file (works offline, shareable — a hard requirement). |
| **Frontend** | Pick a date, see/generate the card | Phase 2: a simple web UI; Phase 1: CLI that writes HTML files | Don't over-build the UI before the pipeline is solid. |

**Stack suggestion:** Python backend (matches the existing parser + library tooling), Jinja2 for rendering, optional FastAPI + a minimal React/plain-HTML frontend in Phase 2. Output files stay static HTML.

## 4. Data schemas

### `exercise-library.json`
```json
{
  "exercises": [
    {
      "name": "KB Swing",
      "movement": "KB on floor slightly in front of you, feet shoulder width...",
      "cues": ["Hips are the engine — NOT your arms", "...4 total..."],
      "muscles_primary": ["Glutes", "Hamstrings", "Core"],
      "muscles_secondary": ["Shoulders", "Lower Back", "Grip"],
      "modifications": [
        {"type": "Easier", "text": "Lighter KB. Lower swing height..."},
        {"type": "Harder", "text": "Heavier KB. American swing..."},
        {"type": "Low Back Issues", "text": "Lighter KB. Smaller range..."}
      ],
      "video": "https://www.dropbox.com/s/.../Kettlebell%20Swing.mp4?dl=0",
      "video_is_search_fallback": false
    }
  ]
}
```

### `day-manifests.json` (one entry per date)
```json
{
  "days": [
    {
      "date": "2026-06-02",
      "format": "CIRCUIT · 9 MIN",
      "warmup": ["Alt. Leg Cradles", "Cossack Squat", "Inverted Toe Touch", "Butt Kicks"],
      "cardio": [
        {"time": "2:00", "intensity": "Moderate 7/10", "mz": "Green"},
        {"time": "2:00", "intensity": "Hard 8/10", "mz": "Yellow"},
        {"rounds": "× 2 rounds"},
        {"time": "1:00", "intensity": "Moderate 7/10", "mz": "Green"}
      ],
      "exercises": [{"name": "DB Forward alt. Lunge", "reps": "10 EACH SIDE"}]
    }
  ]
}
```
A rendered card = a day manifest + the library lookups for each exercise name.

## 5. PDF link-extraction (the proven technique)

See `parse_workout_pdf.py`. The non-obvious parts:
- **Page index = day-of-month − 1** (page 1 is the 1st).
- Links live in `page['/Annots']`; subtype `/Link`, action `/A` → `/URI`. `/A` may be an indirect object — resolve with `.get_object()`.
- **Sort annotations by `Rect[1]` (y) descending** = top-to-bottom reading order = the order exercises appear on the sheet. This is how you map links to exercises positionally.
- Dropbox `/s/<hash>/Name.mp4?dl=0` links work (open a preview).
- A given month PDF holds ~270 unique links. When a date's own page is missing a link, **search every page of every available month PDF** for the same filename keyword and reuse it (`find_video()` does this). YouTube *search* URLs are a last resort and must be flagged (`video_is_search_fallback: true`).

## 6. Design system (non-negotiable — match exactly)

Reference file: `card-template-reference.html` (the 05/26 EMOM card). Pull the `<style>` block verbatim into your template.

- **Dark theme:** `--black:#0F0F0F`, `--card:#242424`, `--card2:#2E2E2E`, `--border:#3A3A3A`. **All text white.**
- **Red** `--red:#D42B2B` / `--red-dark:#A01F1F` — header bar, exercise icons, accents only.
- **Yellow** `--yellow:#F5C842` — coaching-cue arrows (›) and superset badges.
- **MZ dots:** yellow `#F5C842`, green `#4CAF50`, blue `#4A9EE8`, red `#FF3B3B`.
- **Fonts:** Barlow Condensed (headers), Barlow (body). Base 26px, exercise names 26px, cues 23px, mods 22px.
- **Header:** white "V" logo box (red letter), "STUDIO RED", date, upper-right `COACH GUIDE` + format strip (e.g. `EMOM · 9 MIN`, `SUPERSET · 4:30 EA`, `CIRCUIT · 9 MIN`, `LADDER · 9 MIN`).
- **Exercise card expands to:** Movement, Coaching Cues (4, quoted, yellow › arrows), Muscles (red primary + gray secondary tags), Modifications (⬇️ Easier, ⬆️ Harder, 🩹 injury-specific), and a blue **▶ Watch Video** button when a link exists.
- **Cool Down tab:** 4 stretch cards (Quads/Calves/Pecs/Triceps, ~45s, "pick 3") + a blue **Box Breathing** card (4s in nose, 4s out mouth).
- **Ladder days:** add the yellow 🪜 Ladder banner with rep-sequence pills.
- Mobile viewport `maximum-scale=1.0`.

**Enrichment style for new exercises** (feed this to the Claude API enricher): cues are short, in quotes, imperative, coach-voice ("Hips are the engine — NOT your arms"). Exactly 4 cues. 3 modifications: one Easier, one Harder, one injury/limitation-specific. Muscles split primary (3-ish) / secondary.

## 7. THE CRITICAL BUG FIX (do not regress)

iOS Safari intercepts taps on links nested inside a click-handling parent. Early cards put `onclick` on the whole `.exercise-card` and the video links wouldn't open.

**Correct pattern:**
- `.exercise-card` div has **no** onclick.
- `onclick="toggleCard(this.parentElement)"` goes on the **`.exercise-header`** div only.
- The `.video-btn` anchor sits in the card body with no JS on it — it just works.
- JS: `function toggleCard(card){ card.classList.toggle('open'); }`

Verify after rendering: 0 `.exercise-card` with onclick, N `.exercise-header` with onclick, video anchors untouched.

## 8. Build phases

1. **Pipeline MVP (CLI):** load seed library → read a day manifest → render an HTML card identical to the hand-built ones. Proves the renderer matches the proven design.
2. **Ingestion:** wire in `parse_workout_pdf.py` so dropping a month PDF + a date produces the day's video links automatically.
3. **Matcher + enricher:** fuzzy-match sheet exercises to the library; for misses, call Claude with the §6 style schema, show the result for human approval, then persist to the library.
4. **Frontend:** date picker → render/preview/download card. Optionally a library editor to fix cues/videos.
5. **Nice-to-haves:** replace the 2 remaining search-fallback videos (DB Chest Fly, KB Push Press) when a real source appears; batch-generate a whole month; print/PDF export.

## 9. Included seed assets

- `exercise-library.json` — **50 enriched exercises** mined from the 8 hand-built cards. Two have `video_is_search_fallback: true` (DB Chest Fly, KB Push Press) — replace when a real video surfaces.
- `day-manifests.json` — 8 fully structured days (2026-04-30 through 2026-06-02) to test the renderer against.
- `parse_workout_pdf.py` — working PDF link extractor with documented gotchas.
- `card-template-reference.html` — the canonical card (05/26) to lift the design system from.
- The original month PDFs (April, May 2026) are the test corpus for ingestion.

## 10. First prompt to give Claude Code

> "Read BUILD-BRIEF.md. Build Phase 1: a Python CLI that loads exercise-library.json and day-manifests.json, and renders a self-contained static HTML coach card for a given date that matches card-template-reference.html exactly — same design system, same expand/collapse behavior, and the §7 click-handler pattern. Start with date 2026-06-02 and diff your output against the structure of the reference card."
