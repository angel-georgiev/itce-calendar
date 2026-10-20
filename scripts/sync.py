"""Refresh data/sessions.json from https://www.itce.com/training-schedule/.

The website is the source of truth for sessions, dates, language, format,
learning method, description, course link and price. data/enrichment.json
adds what the website does not publish (instructor, category, certification
exam, relevant role). A summary of what changed is written to data/last-sync.md.

Usage: python3 scripts/sync.py            (standard library only)
"""
import html
import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SESSIONS = DATA / "sessions.json"
ENRICHMENT = DATA / "enrichment.json"
REPORT = DATA / "last-sync.md"

BASE = "https://www.itce.com/training-schedule/"
# The site firewall rejects non-browser user agents (403), so identify as a regular browser.
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
MIN_SESSIONS = 20  # refuse to publish if the scrape looks broken
MAX_PAGES = 50

MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}

# Fields compared between runs to report changes.
TRACKED = ["title", "start", "end", "language", "format", "method", "type", "status", "price", "link"]


def fetch(url, retries=3):
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if attempt == retries - 1:
                raise
        except urllib.error.URLError:
            if attempt == retries - 1:
                raise
        time.sleep(2 ** attempt)


def text(fragment):
    fragment = re.sub(r"<br\s*/?>", "\n", fragment)
    fragment = re.sub(r"</p>\s*<p>", "\n\n", fragment)
    fragment = html.unescape(re.sub(r"<[^>]+>", "", fragment))
    return re.sub(r"[ \t\r\f\v]+", " ", fragment).strip()


def parse_range(s):
    """'20 - 22 Oct 2026', '30 Nov - 04 Dec 2026', '28 Dec 2026 - 02 Jan 2027' -> (start, end)."""
    s = re.sub(r"\s+", " ", s.replace("–", "-")).strip()
    m = re.fullmatch(r"(\d{1,2})(?: ([A-Za-z]{3}))?(?: (\d{4}))? - (\d{1,2}) ([A-Za-z]{3}) (\d{4})", s)
    if not m:
        raise ValueError(f"Unrecognised date range: {s!r}")
    d1, m1, y1, d2, m2, y2 = m.groups()
    end = date(int(y2), MONTHS[m2], int(d2))
    month1 = MONTHS[m1] if m1 else end.month
    year1 = int(y1) if y1 else (end.year if month1 <= end.month else end.year - 1)
    return date(year1, month1, int(d1)), end


def parse_session(block, training_id):
    def grab(pattern, default=""):
        m = re.search(pattern, block, re.S)
        return m.group(1) if m else default

    link = html.unescape(grab(r'<h3>\s*<a href="([^"]*)"')).strip()
    code = text(grab(r'<span class="course-code">(.*?)</span>'))
    title = text(grab(r'</span>\s*-\s*(.*?)</a>'))
    start, end = parse_range(text(grab(r'<div class="course-dates">\s*<strong>(.*?)</strong>')))
    meta = {text(k): text(v) for k, v in re.findall(r"<dt>(.*?)</dt>\s*<dd>(.*?)</dd>", block, re.S)}
    badges = [text(b) for b in re.findall(r'<span class="badge[^"]*">(.*?)</span>', block, re.S)]
    description = text(grab(r'<div class="training-details"[^>]*>\s*<div>(.*?)</div>'))

    days = []
    for wd, dm, times in re.findall(
            r"<li><strong><span>(\w+)</span>\s*(\d{1,2} \w{3})</strong>\s*<em>(.*?)</em></li>", block):
        day, mon = dm.split()
        d = date(end.year, MONTHS[mon], int(day))
        if d > end:
            d = d.replace(year=end.year - 1)
        days.append({"date": d.isoformat(), "time": text(times)})

    times = days[0]["time"] if days else ""
    return {
        "id": training_id,
        "code": code,
        "title": title,
        "link": link,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "time": times if all(d["time"] == times for d in days) else "varies",
        "days": days,
        "durationDays": len(days) or working_days(start, end),
        "type": meta.get("Training Type", ""),
        "language": meta.get("Language", ""),
        "method": meta.get("Learning Method", ""),
        "format": meta.get("Format", ""),
        "status": ", ".join(badges),
        "description": description,
    }


def working_days(start, end):
    n, d = 0, start
    while d <= end:
        n += d.weekday() < 5
        d += timedelta(days=1)
    return max(n, 1)


def scrape_schedule():
    sessions = {}
    for page in range(1, MAX_PAGES + 1):
        url = BASE if page == 1 else f"{BASE}page/{page}/"
        body = fetch(url)
        if body is None:
            break
        # Entries are <li class="course"> or <li class="sequence"> with identical inner markup.
        blocks = re.split(r'<li class="[\w -]+" id="training-(\d+)">', body)
        if len(blocks) < 3:
            break
        for training_id, block in zip(blocks[1::2], blocks[2::2]):
            sessions[training_id] = parse_session(block, training_id)
        print(f"  page {page}: {(len(blocks) - 1) // 2} sessions", file=sys.stderr)
    return list(sessions.values())


def scrape_price(url, cache):
    """Course pages show 'CODE | 5 days | €2090' near the title; the price sits in a VAT tooltip span."""
    if not url:
        return None
    if url not in cache:
        body = fetch(url) or ""
        m = re.search(r'<span class="tooltip-container">\s*(?:€|&euro;|&#8364;)\s*(\d[\d\s.,]*)', body)
        price = None
        if m:
            digits = re.sub(r"[^\d]", "", m.group(1).split(",")[0].split(".")[0])
            price = int(digits) if digits else None
        cache[url] = price
    return cache[url]


def main():
    enrichment = json.loads(ENRICHMENT.read_text(encoding="utf-8"))
    courses = enrichment.get("courses", {})
    instructors = enrichment.get("instructors", {})
    previous = {}
    if SESSIONS.exists():
        previous = {s["id"]: s for s in json.loads(SESSIONS.read_text(encoding="utf-8"))["sessions"]}

    print("Scraping training schedule…", file=sys.stderr)
    sessions = scrape_schedule()
    if len(sessions) < MIN_SESSIONS:
        sys.exit(f"Only {len(sessions)} sessions parsed (< {MIN_SESSIONS}); website markup may have changed. Nothing written.")

    price_cache = {}
    for s in sessions:
        extra = courses.get(s["code"], {})
        if not s["link"]:
            s["link"] = extra.get("link", "")
        s["price"] = scrape_price(s["link"], price_cache)
        s["category"] = extra.get("category", "")
        s["certification"] = extra.get("certification", "")
        s["role"] = extra.get("role", "")
        s["instructor"] = instructors.get(f"{s['code']}|{s['start']}", "")
    sessions.sort(key=lambda s: (s["start"], s["code"]))

    now = datetime.now(timezone.utc)
    SESSIONS.write_text(json.dumps(
        {"updated": now.isoformat(timespec="seconds"), "source": BASE, "sessions": sessions},
        ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    REPORT.write_text(report(sessions, previous, instructors, now), encoding="utf-8")
    print(f"Wrote {SESSIONS.relative_to(ROOT)} ({len(sessions)} sessions) and {REPORT.relative_to(ROOT)}", file=sys.stderr)


def label(s):
    return f"{s['code']} {s['title']} ({s['start']} – {s['end']}, {s['language'] or 'language n/a'})"


def report(sessions, previous, instructors, now):
    today = now.date().isoformat()
    current = {s["id"]: s for s in sessions}
    lines = [f"# Sync report — {now:%Y-%m-%d %H:%M} UTC", "", f"{len(sessions)} sessions on itce.com.", ""]

    def section(title, items):
        lines.append(f"## {title} ({len(items)})")
        lines.extend(f"- {i}" for i in items) if items else lines.append("- none")
        lines.append("")

    if previous:
        section("Added", [label(s) for i, s in current.items() if i not in previous])
        section("Removed", [label(s) for i, s in previous.items() if i not in current and s["end"] >= today])
        changed = []
        for i, s in current.items():
            if i in previous:
                diffs = [f"{f}: {previous[i].get(f)!r} → {s.get(f)!r}" for f in TRACKED if previous[i].get(f) != s.get(f)]
                if diffs:
                    changed.append(f"{s['code']} {s['start']}: " + "; ".join(diffs))
        section("Changed", changed)

    keys = {f"{s['code']}|{s['start']}" for s in sessions}
    section("Instructor assignments for upcoming dates with no matching website session",
            sorted(k.replace("|", " on ") + f" — {v}" for k, v in instructors.items()
                   if k.split("|")[1] >= today and k not in keys))
    section("Sessions without instructor", [label(s) for s in sessions if not s["instructor"]])
    section("Sessions without category", [label(s) for s in sessions if not s["category"]])
    section("Sessions without course link", [label(s) for s in sessions if not s["link"]])
    section("Sessions without price on course page", [label(s) for s in sessions if s["link"] and s["price"] is None])
    return "\n".join(lines)


if __name__ == "__main__":
    main()
