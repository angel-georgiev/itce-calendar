# ITCE Training Calendar — instructions for Claude

A static site on GitHub Pages, served from `main` / root: https://angel-georgiev.github.io/itce-calendar/
See README.md for the file layout.

## "Update the calendar" procedure

1. `python3 scripts/sync.py`. If it aborts because too few sessions parsed, the itce.com markup changed. Inspect https://www.itce.com/training-schedule/ and fix the parser in `scripts/sync.py`. Never publish a partial result.
2. Read `data/last-sync.md` and summarise it for the user: added, removed and changed sessions, sessions without a course page, and website data issues.
3. Corrections belong on itce.com, not in this repo. Don't patch the data by hand. Tell the user what the website team needs to fix, then re-run the sync once they have fixed it.
4. Optionally check the page locally: `python3 -m http.server`, then load it in Playwright.
5. Commit `data/` and any code changes, then push to `main`, which publishes the site. Also push to the session's working branch if one is assigned.
6. Confirm the live URL shows the new "Last updated" date.

## Notes

- itce.com returns 403 to non-browser user agents. `sync.py` therefore sends a browser UA.
- The schedule lists both `<li class="course">` and `<li class="sequence">` entries.
- Only sessions on the live schedule are shown. `sync.py` reads both the English and the Bulgarian schedule (`/bg/training-schedule/`), and adds any session that appears only on the Bulgarian one, linked to its Bulgarian course page.
- Links must go directly to the course's own page. A link is kept only if the page header shows the same course code. If the schedule doesn't link a course, the script uses the previous run's link, but only after the same check. Sessions with no course page get no link, and the page never falls back to the general schedule.
- Under "Website data issues", `last-sync.md` lists sessions whose daily timetable on itce.com contains dates outside the session's own dates. Tell the user about these, because the website team needs to fix them.
- Do not edit `data/sessions.json` by hand, because the next sync overwrites it.
- Use only what itce.com shows. The Excel schedule and its instructor, certification and role data are retired at the owner's request, so don't reintroduce them or any other outside source.
- Website-only fields are the category (from the course page's categories, named by the schedule's category filter), "Who should attend", and what the price includes (from the VAT note next to the price). Fields a course page doesn't have stay empty.
- Sessions with no course page show a "Request information" mailto button addressed to businessdev@itce.com (`CONTACT_EMAIL` in `assets/app.js`), so customers can ask directly. Don't send emails on the owner's behalf.
