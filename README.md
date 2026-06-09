# Studio Red Coach Card App

Turns Vasa Studio Red weekly workout sheets into mobile coach reference cards.
See [BUILD-BRIEF.md](files/BUILD-BRIEF.md) for the full spec and roadmap.

**📱 Live (phone-ready):** https://coolnerd-tt.github.io/studio-red-coach-cards/
— open in Safari → Share → *Add to Home Screen*. Auto-redeploys on every push to
`main` (see [.github/workflows/pages.yml](.github/workflows/pages.yml)); the published
site is the `cards/` folder.

## Phase 1 — Renderer CLI (done)

Loads the seed exercise library + day manifests and renders a self-contained,
offline, mobile coach card per date. Zero third-party dependencies (Python 3
stdlib only — runs anywhere without `pip install`).

```bash
python3 src/build_card.py 2026-06-09          # render one date -> cards/
python3 src/build_card.py 2026-06-09 --verify # + print §7 click-handler invariant check
python3 src/build_card.py --all               # render every date + the index launcher
python3 src/build_card.py --index             # rebuild just the launcher page
python3 src/build_card.py --list              # list available dates
```

Output: `cards/studio-red-<date>.html` — a single static file (works offline, shareable).

### The app (launcher)

`--all` also writes `cards/index.html` — a dark-themed, mobile launcher that lists
every class date with its format badge; tap a date to open that day's card. Just open
`cards/index.html` in any browser (works offline, no server needed):

```bash
open cards/index.html      # macOS
```

Currently loaded: 9 dates (2026-04-30 → 2026-06-09). The seed library has grown to
**56 exercises** (50 seed + 6 mined from the June 9 sheet).

### Fidelity to the reference card

- The `<style>` block is **lifted verbatim** from `data/card-template-reference.html`
  at build time (brief §6), so the design system can't drift. A few additive rules
  (`.dot.red`, `.max` intensity badge, ladder banner, rounds row) extend it without
  altering it.
- **§7 critical bug fix is enforced and checked:** `.exercise-card` has no `onclick`;
  the handler lives on `.exercise-header` only; the `.video-btn` anchor carries no JS.
  `--verify` asserts: 0 cards-with-onclick, headers-with-onclick == card count,
  0 video-with-onclick.
- Structural parity proven: rendering the `05/26` manifest (with the optional station
  + cardio-note fields populated) against the reference card yields **0 structural diffs**.

### Optional manifest fields (backward-compatible)

The base schema in brief §4 renders fully. Two optional fields close the only two
gaps vs. a hand-built card, when the data is available:

- `day.cardio_note` — coach note under the cardio table (trusted markup; may contain `<strong>`).
- `exercise.station` — e.g. `"🏋️ Bench"`; emits a floor section-label header when it changes.
- `day.floor_note` — override the format's default floor note (trusted markup).

Without them, the cardio note and floor station-labels are simply omitted (no fabricated copy).

## Layout

```
data/    seed assets (exercise-library.json, day-manifests.json, reference card, parser)
src/     build_card.py  — the renderer + CLI
cards/   generated output (one HTML file per date)
files/   original handoff bundle (BUILD-BRIEF.md + seed assets)
```

## Not yet built (later phases, see brief §8)

2. Ingestion — wire in `data/parse_workout_pdf.py` (drop a month PDF + date → video links).
   **Note for the parser:** the June 2026 PDF has a **cover page at index 0**, so for that
   file `page_index = day-of-month` (not `day − 1` as in brief §5). Detect/skip a cover page
   rather than assuming a fixed offset. June 9 was ingested from page index 9 by reading the
   sheet text directly (pypdf `extract_text`) since the parser currently extracts links only.
3. Matcher + enricher — fuzzy-match sheet names to the library; Claude enrich on miss.
4. Frontend — date picker → preview/download.
