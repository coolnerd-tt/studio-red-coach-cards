---
name: vasa-monthly-update
description: Pull the newest LFT and RED programming PDFs and feed them into the VASA coach-card app. Use at the start or end of each month, or whenever HQ publishes new programming. Also use when asked to ingest a specific month's PDF, check whether new programming is available, or roll back a bad update.
---

# VASA monthly programming update

Takes each program's newest PDF and turns it into coach cards. Run it monthly.

The run stops at two points on purpose:

```
pick newest PDF → verify it's new → parse → validate
   → list exercises with no coaching content yet    ← STOP: we write those
   → back up → merge → rebuild cards                ← STOP: you review, then commit
```

Nothing is committed, pushed or deployed. The app's live site only changes when
you push to `main` yourself.

## Setup (once)

```bash
cd ~/path/to/studio-red-coach-cards
pip install pdfminer.six pypdf playwright
python3 -m playwright install chromium
cp tools/vasa_sync/config.example.json tools/vasa_sync/config.json
```

`config.json` already points at the iCloud folders:

- LFT — `~/Library/Mobile Documents/com~apple~CloudDocs/Vasa/LFT`
- RED — `~/Library/Mobile Documents/com~apple~CloudDocs/Vasa/Red`

Adding a third program later is a new block in that file, plus a handler class in
`tools/vasa_sync/update.py`.

## The monthly run

```bash
# 1. see what would change, without touching anything
python3 tools/vasa_sync/sync.py update --dry-run

# 2. if it looks right, do it
python3 tools/vasa_sync/sync.py update

# one program at a time
python3 tools/vasa_sync/sync.py update --program LFT --dry-run
python3 tools/vasa_sync/sync.py update --program RED
```

The two programs run independently — if LFT fails, RED still completes, and the
summary says which did what.

Then review and ship:

```bash
git diff --stat                 # what changed
git add -A && git commit        # your call
git push                        # this is what deploys the site
```

## What the summary means

| Status | Meaning | What to do |
|---|---|---|
| `UPDATED` | Merged and cards rebuilt | Review the diff, commit |
| `DRY RUN` | Parsed fine, nothing written | Rerun without `--dry-run` |
| `NO NEW DOCUMENT` | Newest file is byte-identical to the one already ingested | Nothing — HQ hasn't published yet |
| `NEEDS AUTHORING` | Parsed fine, but some exercises have no cues/library entry | Ask Claude to write them, then rerun |
| `STOPPED` | Couldn't decide which file is newest | Read the note; usually a filename needs fixing |
| `FAILED` | Validation failed or the parser broke | Send the output to Claude — the PDF layout probably changed |

## When it says NEEDS AUTHORING

Expected, most months. New exercises arrive with no coaching content, and a
script can't write that. Hand the list to Claude:

> The monthly update stopped with these exercises needing content: [paste list].
> Add them the usual way.

RED exercises need a full `data/exercise-library.json` entry (movement, four
cues, muscles, modifications, demo video). LFT exercises need one or two cues in
`data/lft-exercise-cues.json`. Then rerun the update.

## When it says FAILED

Almost always the PDF layout moved — it has nearly every month (the table
shifted 30pt right in September, page size changed in August, benchmark pages
introduced "Deload" headers and F1/F2 row labels). Send Claude the summary and
the PDF; it's a parser fix, not something to work around.

## Rolling back

Every real run backs up the data files first and prints the exact command:

```bash
cp data/.backups/<timestamp>/*.json data/
python3 src/build_card.py --all
python3 src/build_lft_card.py --all
```

If you haven't committed yet, `git checkout -- data/ cards/` also works.

## Downloading from Appspace (in progress)

Today the PDFs are downloaded by hand into those iCloud folders. The browser
half is half-built:

```bash
python3 tools/vasa_sync/sync.py auth      # log in by hand once; session is saved
python3 tools/vasa_sync/sync.py inspect   # dump what the Library folders contain
```

`auth` opens a real browser — password, SSO and MFA all happen there, never in
this code, and nothing is stored but the session cookie. If the session expires,
`inspect` says so; rerun `auth`.

The "pick the newest file and download it" step still needs the output of
`inspect` to be written against. Both commands are read-only against Appspace.

Worth checking first: if `https://vasafitness.cloud.appspace.com/api/v3/docs/`
loads for your account, Appspace's API would replace the browser entirely.

## Scheduling it (optional)

macOS prefers launchd. To run at 9am on the 1st of each month, create
`~/Library/LaunchAgents/com.vasa.monthly.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.vasa.monthly</string>
  <key>WorkingDirectory</key><string>/Users/tylertoone/path/to/studio-red-coach-cards</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/python3</string>
    <string>tools/vasa_sync/sync.py</string>
    <string>update</string>
    <string>--dry-run</string>
  </array>
  <key>StartCalendarInterval</key><dict>
    <key>Day</key><integer>1</integer><key>Hour</key><integer>9</integer><key>Minute</key><integer>0</integer>
  </dict>
  <key>StandardOutPath</key><string>/tmp/vasa-monthly.log</string>
  <key>StandardErrorPath</key><string>/tmp/vasa-monthly.log</string>
</dict></plist>
```

Then `launchctl load ~/Library/LaunchAgents/com.vasa.monthly.plist`.

Keep `--dry-run` in the scheduled job. It tells you whether new programming
landed without changing the app behind your back; do the real run yourself.
