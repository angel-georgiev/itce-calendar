"""Refresh data/sessions.json from https://www.itce.com/training-schedule/.

The website is the source of truth for sessions, dates, language, format,
learning method, description, course link and price. Both the English and the
Bulgarian schedule are read; a session listed only on the Bulgarian one is added
too. A course link is kept only if it opens that course's own page (the page
header shows the same course code). data/enrichment.json
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
BASE_BG = "https://www.itce.com/bg/training-schedule/"
# The site firewall rejects non-browser user agents (403), so identify as a regular browser.
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
MIN_SESSIONS = 20  # refuse to publish if the scrape looks broken
MAX_PAGES = 50

MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}
# Bulgarian schedule abbreviations ("ян.", "фев.", "май", "юни", …), matched without the dot.
MONTHS_BG = {m: i for i, m in enumerate(
    ["ян", "фев", "мар", "апр", "май", "юни", "юли", "авг", "сеп", "окт", "ное", "дек"], 1)}

EN = {"months": MONTHS, "type": "Training Type", "language": "Language", "method": "Learning Method",
      "format": "Format", "values": {}}
BG = {"months": MONTHS_BG, "type": "Вид", "language": "Език", "method": "Метод", "format": "Формат",
      "values": {"Курс": "Course", "Английски": "English", "Български": "Bulgarian", "Виртуално": "Virtual",
                 "Присъствено": "Classroom", "Хибридно": "Hybrid", "Целодневно": "All day",
                 "Полудневно": "Half day", "Запълнен": "Full", "Нов": "New"}}

# Problems in the website's own data, listed in the report for the website team.
# (code, start, end) -> dates the website's daily schedule lists outside the session's own dates.
WEBSITE_ISSUES = {}

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


def month(token, months):
    m = months.get(token.rstrip("."))
    if not m:
        raise ValueError(f"Unrecognised month: {token!r}")
    return m


def parse_range(s, months=MONTHS):
    """'20 - 22 Oct 2026', '30 Nov - 04 Dec 2026', '28 Dec 2026 - 02 Jan 2027' -> (start, end)."""
    s = re.sub(r"\s+", " ", s.replace("–", "-")).strip()
    m = re.fullmatch(r"(\d{1,2})(?: ([^\d\s]+))?(?: (\d{4}))? - (\d{1,2}) ([^\d\s]+) (\d{4})", s)
    if not m:
        raise ValueError(f"Unrecognised date range: {s!r}")
    d1, m1, y1, d2, m2, y2 = m.groups()
    end = date(int(y2), month(m2, months), int(d2))
    month1 = month(m1, months) if m1 else end.month
    year1 = int(y1) if y1 else (end.year if month1 <= end.month else end.year - 1)
    return date(year1, month1, int(d1)), end


def parse_session(block, training_id, loc=EN):
    def grab(pattern, default=""):
        m = re.search(pattern, block, re.S)
        return m.group(1) if m else default

    link = html.unescape(grab(r'<h3>\s*<a href="([^"]*)"')).strip()
    code = text(grab(r'<span class="course-code">(.*?)</span>'))
    title = text(grab(r'</span>\s*-\s*(.*?)</a>'))
    start, end = parse_range(text(grab(r'<div class="course-dates">\s*<strong>(.*?)</strong>')), loc["months"])
    tr = lambda v: loc["values"].get(v, v)
    meta = {text(k): tr(text(v)) for k, v in re.findall(r"<dt>(.*?)</dt>\s*<dd>(.*?)</dd>", block, re.S)}
    badges = [tr(text(b)) for b in re.findall(r'<span class="badge[^"]*">(.*?)</span>', block, re.S)]
    description = text(grab(r'<div class="training-details"[^>]*>\s*<div>(.*?)</div>'))

    days = []
    for wd, day, mon, times in re.findall(
            r"<li><strong><span>(\w+)</span>\s*(\d{1,2}) ([^\s<]+)</strong>\s*<em>(.*?)</em></li>", block):
        d = date(end.year, month(mon, loc["months"]), int(day))
        if d > end:
            d = d.replace(year=end.year - 1)
        if not start <= d <= end:
            WEBSITE_ISSUES.setdefault((code, start.isoformat(), end.isoformat()), set()).add((d.month, d.day))
            continue
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
        "type": meta.get(loc["type"], ""),
        "language": meta.get(loc["language"], ""),
        "method": meta.get(loc["method"], ""),
        "format": meta.get(loc["format"], ""),
        "status": ", ".join(badges),
        "description": description,
    }


def working_days(start, end):
    n, d = 0, start
    while d <= end:
        n += d.weekday() < 5
        d += timedelta(days=1)
    return max(n, 1)


def scrape_schedule(base=BASE, loc=EN):
    sessions = {}
    for page in range(1, MAX_PAGES + 1):
        url = base if page == 1 else f"{base}page/{page}/"
        body = fetch(url)
        if body is None:
            break
        # Entries are <li class="course"> or <li class="sequence"> with identical inner markup.
        blocks = re.split(r'<li class="[\w -]+" id="training-(\d+)">', body)
        if len(blocks) < 3:
            break
        for training_id, block in zip(blocks[1::2], blocks[2::2]):
            sessions[training_id] = parse_session(block, training_id, loc)
        print(f"  {base} page {page}: {(len(blocks) - 1) // 2} sessions", file=sys.stderr)
    return list(sessions.values())


def course_page(url, cache):
    """Course pages show 'CODE | 5 days | €2090' near the title; the price sits in a VAT tooltip span.

    Returns {"code", "price", "en_url", "excerpt"}, or None if the page is missing.
    """
    if url not in cache:
        body = fetch(url)
        info = None
        if body:
            code = re.search(r'<footer class="course-meta">\s*<span>(.*?)</span>', body, re.S)
            m = re.search(r'<span class="tooltip-container">\s*(?:€|&euro;|&#8364;)\s*(\d[\d\s.,]*)', body)
            price = None
            if m:
                digits = re.sub(r"[^\d]", "", m.group(1).split(",")[0].split(".")[0])
                price = int(digits) if digits else None
            en = re.search(r'<link rel="alternate" href="([^"]+)" hreflang="en"', body)
            excerpt = re.search(r'<div class="course-excerpt">(.*?)</div>', body, re.S)
            info = {"code": text(code.group(1)) if code else None, "price": price,
                    "en_url": html.unescape(en.group(1)) if en else None,
                    "excerpt": text(excerpt.group(1)) if excerpt else ""}
        cache[url] = info
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
    sessions_bg = scrape_schedule(BASE_BG, BG)
    if len(sessions) < MIN_SESSIONS or len(sessions_bg) < MIN_SESSIONS:
        sys.exit(f"Only {len(sessions)} English / {len(sessions_bg)} Bulgarian sessions parsed (< {MIN_SESSIONS}); "
                 "website markup may have changed. Nothing written.")

    page_cache = {}
    # Sessions listed only on the Bulgarian schedule. Prefer the English title and description.
    known = {(s["code"], s["start"]) for s in sessions}
    bg_only = [s for s in sessions_bg if (s["code"], s["start"]) not in known]
    for s in bg_only:
        s["source"] = "bg"
        info = course_page(s["link"], page_cache) if s["link"] else None
        en = course_page(info["en_url"], page_cache) if info and info["en_url"] else None
        if en and en["code"] == s["code"] and en["excerpt"]:
            s["description"] = en["excerpt"]
    sessions += bg_only

    rejected, recovered = [], []
    previous_links = {p["code"]: p["link"] for p in previous.values() if p.get("link")}
    for s in sessions:
        extra = courses.get(s["code"], {})
        info = course_page(s["link"], page_cache) if s["link"] else None
        if s["link"] and (not info or info["code"] != s["code"]):
            rejected.append(f"{label(s)}: {s['link']} (page code: {info and info['code']!r})")
            s["link"], info = "", None
        if not s["link"]:
            # The schedule entry has no (valid) link: fall back to a known course page, but only if that
            # page is live and is this course's own page.
            for url in (previous_links.get(s["code"]), extra.get("link")):
                cand = course_page(url, page_cache) if url else None
                if cand and cand["code"] == s["code"]:
                    s["link"], info = url, cand
                    recovered.append(f"{label(s)}: {url}")
                    break
        s["price"] = info["price"] if info else None
        s["category"] = extra.get("category", "")
        s["certification"] = extra.get("certification", "")
        s["role"] = extra.get("role", "")
        s["instructor"] = instructors.get(f"{s['code']}|{s['start']}", "")
    sessions.sort(key=lambda s: (s["start"], s["code"]))

    now = datetime.now(timezone.utc)
    SESSIONS.write_text(json.dumps(
        {"updated": now.isoformat(timespec="seconds"), "source": BASE, "sessions": sessions},
        ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    REPORT.write_text(report(sessions, previous, instructors, now, bg_only, rejected, recovered), encoding="utf-8")
    print(f"Wrote {SESSIONS.relative_to(ROOT)} ({len(sessions)} sessions) and {REPORT.relative_to(ROOT)}", file=sys.stderr)


def label(s):
    return f"{s['code']} {s['title']} ({s['start']} – {s['end']}, {s['language'] or 'language n/a'})"


def report(sessions, previous, instructors, now, bg_only, rejected, recovered):
    today = now.date().isoformat()
    current = {s["id"]: s for s in sessions}
    lines = [f"# Sync report — {now:%Y-%m-%d %H:%M} UTC", "",
             f"{len(sessions)} sessions on itce.com ({len(bg_only)} only on the Bulgarian schedule).", ""]

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

    section("Only on the Bulgarian schedule (included)", [label(s) for s in bg_only])
    section("Course links rejected (not that course's own page)", rejected)
    section("Not linked on the schedule; verified course page used", recovered)
    section("Website data issues: daily schedule outside the session dates (those days left out)",
            [f"{c} {a} – {b}: website lists " + ", ".join(date(2000, m, dd).strftime("%d %b") for m, dd in sorted(d))
             for (c, a, b), d in sorted(WEBSITE_ISSUES.items())])

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
