#!/usr/bin/env python3
"""
parse_workout_pdf.py — Studio Red workout-sheet ingestion.

Extracts, for any monthly Vasa Studio Red PDF:
  - per-day embedded video links (Dropbox / YouTube), in top-to-bottom order
  - (text parsing of exercises/cardio is a TODO hook below)

Usage:
    python parse_workout_pdf.py <pdf_path> <day_of_month>
    python parse_workout_pdf.py STUDIO_RED_May_2026.pdf 7   # -> 05/07 links

Key facts learned the hard way:
  - Page index = day_of_month - 1 (page 1 == the 1st of the month).
  - Links live in page['/Annots']; subtype '/Link', action '/A' -> '/URI'.
  - '/A' may be an indirect object: resolve with .get_object().
  - Sort annotations by Rect[1] (y) DESCENDING = top-to-bottom reading order,
    which is the order exercises appear on the sheet.
  - Dropbox '/s/<hash>/Name.mp4?dl=0' links DO work; they open a preview.
  - When a date's page lacks a link for an exercise, search ALL pages of every
    available month PDF for the same filename keyword and reuse it. Only fall
    back to a YouTube *search* URL as a last resort, and flag it.
"""
import sys, urllib.parse
import pypdf


def extract_links_for_day(pdf_path, day):
    reader = pypdf.PdfReader(pdf_path)
    page = reader.pages[day - 1]
    links = []
    if '/Annots' not in page:
        return links
    for a in page['/Annots']:
        try:
            obj = a.get_object()
            if obj.get('/Subtype') != '/Link':
                continue
            action = obj.get('/A', {})
            if hasattr(action, 'get_object'):
                action = action.get_object()
            uri = action.get('/URI') if action else None
            rect = obj.get('/Rect')
            if uri and rect:
                links.append((float(rect[1]), str(uri)))
        except Exception:
            continue
    # dedupe preserving first (highest) y, then sort top-to-bottom
    seen = {}
    for y, uri in links:
        if uri not in seen:
            seen[uri] = y
    return [uri for uri, y in sorted(seen.items(), key=lambda kv: -kv[1])]


def all_links(pdf_paths):
    """Every unique video link across multiple month PDFs (for fallback search)."""
    out = {}
    for p in pdf_paths:
        reader = pypdf.PdfReader(p)
        for page in reader.pages:
            if '/Annots' not in page:
                continue
            for a in page['/Annots']:
                try:
                    obj = a.get_object()
                    if obj.get('/Subtype') != '/Link':
                        continue
                    action = obj.get('/A', {})
                    if hasattr(action, 'get_object'):
                        action = action.get_object()
                    uri = action.get('/URI') if action else None
                    if uri:
                        out.setdefault(str(uri), p)
                except Exception:
                    continue
    return out


def find_video(keyword, pdf_paths):
    """Find the nearest real video URL whose filename contains keyword."""
    kw = keyword.lower()
    for uri in all_links(pdf_paths):
        if kw in urllib.parse.unquote(uri).lower():
            return uri
    return None


if __name__ == "__main__":
    pdf, day = sys.argv[1], int(sys.argv[2])
    for i, uri in enumerate(extract_links_for_day(pdf, day), 1):
        print(f"{i:2d}. {uri}")
