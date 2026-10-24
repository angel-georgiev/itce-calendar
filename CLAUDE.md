# ITCE Training Calendar — instructions for Claude

A static site on GitHub Pages, served from `main` / root: https://angel-georgiev.github.io/itce-calendar/
See README.md for the file layout.

## "Update the calendar" procedure

1. `python3 scripts/sync.py`. If it aborts because too few sessions parsed, the itce.com markup changed. Inspect https://www.itce.com/training-schedule/ and fix the parser in `scripts/sync.py`. Never publish a partial result.
2. Read `data/last-sync.md` and summarise it for the user: added, removed and changed sessions, sessions without an instructor or category, and spreadsheet instructor assignments that no longer match a website session.
3. Apply any corrections the user gives. Instructors go in `instructors` and course metadata goes in `courses`, both in `data/enrichment.json`. Then re-run the sync.
4. Optionally check the page locally: `python3 -m http.server`, then load it in Playwright.
5. Commit `data/` and any code changes, then push to `main`, which publishes the site. Also push to the session's working branch if one is assigned.
6. Confirm the live URL shows the new "Last updated" date.

## Notes

- itce.com returns 403 to non-browser user agents. `sync.py` therefore sends a browser UA.
- The schedule lists both `<li class="course">` and `<li class="sequence">` entries.
- Only sessions on the live schedule are shown. `sync.py` reads both the English and the Bulgarian schedule (`/bg/training-schedule/`), and adds any session that appears only on the Bulgarian one, linked to its Bulgarian course page.
- Links must go directly to the course's own page. A link is kept only if the page header shows the same course code. If the schedule doesn't link a course, the script uses the previous or spreadsheet link, but only after the same check. Sessions with no course page get no link, and the page never falls back to the general schedule.
- Under "Website data issues", `last-sync.md` lists sessions whose daily timetable on itce.com contains dates outside the session's own dates. Tell the user about these, because the website team needs to fix them.
- Do not edit `data/sessions.json` by hand, because the next sync overwrites it.
- Keep the source xlsx out of the repo, because it contains internal QA notes.
