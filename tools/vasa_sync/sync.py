#!/usr/bin/env python3
"""
sync.py — fetch the newest LFT / RED programming PDFs from Appspace.

Two subcommands exist so far:

    python3 tools/vasa_sync/sync.py auth
        Opens a real browser window. You log in by hand — password, SSO, MFA,
        whatever the site asks — then press Enter here. The signed-in session
        is saved to disk so later runs don't need a login. Your password is
        never seen by, passed to, or stored by this script.

    python3 tools/vasa_sync/sync.py update [--program LFT] [--dry-run]
        Takes the newest PDF already sitting in each program's folder and
        feeds it into the app. See update.py.

    python3 tools/vasa_sync/sync.py inspect
        Opens each program's folder using that saved session and writes down
        what it finds: the page's HTML, a screenshot, and every row that
        looks like a file. That's the raw material for writing the actual
        "pick the newest file" logic, which can't be written blind.

Read-only: nothing here uploads, deletes, renames or shares anything.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
HERE = Path(__file__).resolve().parent


def load_config(path: Path) -> dict:
    if not path.exists():
        sys.exit(f"No config at {path}.\n"
                 f"Copy {HERE / 'config.example.json'} to {path} and edit the paths.")
    return json.loads(path.read_text(encoding="utf-8"))


def require_playwright():
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError:
        sys.exit("Playwright isn't installed. Run:\n"
                 "    pip install playwright\n"
                 "    python3 -m playwright install chromium")
    from playwright.sync_api import sync_playwright
    return sync_playwright


# --------------------------------------------------------------------------- #
# auth — log in once by hand, keep the session
# --------------------------------------------------------------------------- #
def cmd_auth(cfg: dict) -> int:
    sync_playwright = require_playwright()
    state_path = ROOT / cfg["browser_state"]
    state_path.parent.mkdir(parents=True, exist_ok=True)
    first_url = cfg["programs"][0]["url"]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        ctx = browser.new_context(accept_downloads=True)
        page = ctx.new_page()
        page.goto(first_url, wait_until="domcontentloaded")

        print("\n" + "=" * 68)
        print("A browser window is open. Log in there by hand.")
        print("Finish any SSO/MFA prompts until you can see the Library folder.")
        print("Then come back here and press Enter.")
        print("=" * 68)
        input("\nPress Enter once you're logged in and looking at the folder... ")

        ctx.storage_state(path=str(state_path))
        browser.close()

    print(f"\n✓ Session saved to {state_path.relative_to(ROOT)}")
    print("  It's gitignored. Re-run this command whenever the session expires.")
    return 0


# --------------------------------------------------------------------------- #
# inspect — look at the folder and write down what's there
# --------------------------------------------------------------------------- #
DATE_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}"
    r"|\d{1,2}/\d{1,2}/\d{2,4}"
    r"|\b\d{1,2}\s+\w{3,9}\s+\d{4}\b"
    r"|\b\w{3,9}\s+\d{1,2},?\s+\d{4}\b"
    r"|\b(?:just now|a minute|minutes|an hour|hours|a day|days|a month|months|years?)\s*ago\b",
    re.I,
)


def cmd_inspect(cfg: dict) -> int:
    sync_playwright = require_playwright()
    state_path = ROOT / cfg["browser_state"]
    if not state_path.exists():
        sys.exit(f"No saved session at {state_path}. Run:  sync.py auth")

    out_dir = HERE / "inspect-output"
    out_dir.mkdir(parents=True, exist_ok=True)
    report: dict = {"captured_at": datetime.now().isoformat(timespec="seconds"), "programs": {}}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        ctx = browser.new_context(storage_state=str(state_path), accept_downloads=True)
        for prog in cfg["programs"]:
            name = prog["name"]
            print(f"\n--- {name}: opening folder ...")
            page = ctx.new_page()
            page.goto(prog["url"], wait_until="domcontentloaded")
            # Appspace's console is a single-page app: the folder contents
            # arrive well after domcontentloaded, so give the list a moment
            # to settle rather than racing it.
            page.wait_for_timeout(8000)

            info: dict = {"url": prog["url"], "title": page.title(), "final_url": page.url}

            if looks_logged_out(page):
                info["warning"] = "This looks like a login page — the saved session may have expired."
                print("    ⚠ looks like a login screen; session may have expired")

            (out_dir / f"{name}.html").write_text(page.content(), encoding="utf-8")
            page.screenshot(path=str(out_dir / f"{name}.png"), full_page=True)

            info["candidate_rows"] = scrape_candidates(page, prog["file_type"])
            info["tables"] = page.locator("table").count()
            info["links_with_file_ext"] = page.locator(
                f'a[href*=".{prog["file_type"]}" i]'
            ).count()
            report["programs"][name] = info
            print(f"    title={info['title']!r}")
            print(f"    rows that look like files: {len(info['candidate_rows'])}")
            page.close()
        browser.close()

    (out_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\n✓ Wrote {out_dir.relative_to(ROOT)}/ — report.json, per-program .html and .png")
    print("  Send me report.json and the screenshots and I'll write the download step.")
    return 0


def looks_logged_out(page) -> bool:
    """A rough check so an expired session fails loudly instead of silently."""
    for probe in ('input[type="password"]', 'text=/sign ?in/i', "text=/log ?in/i"):
        try:
            if page.locator(probe).first.is_visible(timeout=1200):
                return True
        except Exception:
            continue
    return False


def scrape_candidates(page, file_type: str) -> list[dict]:
    """Rows that might be files, with whatever date-ish text sits near them.

    Deliberately broad and dumb: the point is to see the page's real shape,
    not to be clever about a layout nobody has looked at yet.
    """
    js = """
    (fileType) => {
      const out = [];
      const sels = ['[role="row"]', 'tr', '[class*="card"]', '[class*="item"]', '[class*="tile"]', 'li'];
      const seen = new Set();
      for (const sel of sels) {
        for (const el of document.querySelectorAll(sel)) {
          const text = (el.innerText || '').trim();
          if (!text || text.length > 400) continue;
          const key = sel + '::' + text.slice(0, 120);
          if (seen.has(key)) continue;
          seen.add(key);
          const mentionsType = text.toLowerCase().includes(fileType.toLowerCase());
          if (!mentionsType && !/\\b(20\\d\\d|ago)\\b/i.test(text)) continue;
          const a = el.querySelector('a[href]');
          out.push({
            selector: sel,
            text: text.replace(/\\s+/g, ' ').slice(0, 300),
            href: a ? a.getAttribute('href') : null,
            title_attr: el.getAttribute('title') || null,
            aria: el.getAttribute('aria-label') || null,
            testid: el.getAttribute('data-testid') || el.getAttribute('data-test') || null,
          });
        }
      }
      return out.slice(0, 80);
    }
    """
    try:
        rows = page.evaluate(js, file_type)
    except Exception as exc:  # pragma: no cover - diagnostic path
        return [{"error": f"scrape failed: {exc}"}]
    for r in rows:
        r["dates_found"] = DATE_RE.findall(r.get("text") or "")
    return rows


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Fetch VASA programming PDFs from Appspace.")
    p.add_argument("command", choices=["auth", "inspect", "update"])
    p.add_argument("--config", type=Path, default=HERE / "config.json")
    p.add_argument("--program", help="run just one program, e.g. --program LFT")
    p.add_argument("--dry-run", action="store_true",
                   help="parse and show what would change, without touching the app")
    args = p.parse_args(argv)
    cfg = load_config(args.config)
    if args.command == "update":
        import update
        return update.run(cfg, args.program, args.dry_run)
    return {"auth": cmd_auth, "inspect": cmd_inspect}[args.command](cfg)


if __name__ == "__main__":
    raise SystemExit(main())
