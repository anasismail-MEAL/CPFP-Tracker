# Changelog

## 2026-09-08 — Demo accounts retired

`import_roster.py --retire-demo` deactivated the six seeded demo accounts on the local
database. Their password is published in `README.md`, so they cannot stay open alongside 69
real sign-ins.

`retire_demo()` was extracted so the CLI flag and the boot path share one implementation, and
`seed.ensure()` now calls it straight after importing the roster. Without that, an ephemeral
host re-seeds the demo accounts on every boot and `demo1234` quietly comes back to life on each
redeploy.

The sign-in page lists demo credentials only while a demo account is actually active. Once the
roster is in it tells people to sign in with their work email instead.

70 checks in `test_app.py`.

## 2026-09-08 — Real accounts from the CPSS roster

Focal points sign in with their **work email address**. Accounts are created by
`import_roster.py` from `CPSS_focal points list and backup_as of August_2026_ updated.xlsx`:
66 focal points (primary and backup across 33 camps) plus 3 CPSS admins.

The roster holds real staff names, emails and mobile numbers, so it is never committed. The
importer reads the workbook locally, or a CSV supplied to a host as a secret file
(`CPFP_ROSTER`, default `/etc/secrets/roster.csv`), which is how a host with an ephemeral disk
rebuilds the accounts on each boot.

Import behaviour worth knowing:

- Keyed on email. Re-running refreshes name, agency, camp and phone, and never touches a
  password.
- Camp labels are translated to the agreed list: `4E` → `Camp 4 Ext`, `Kutupalong RC` → `KTP`,
  `NYP-RC-Teknaf` → `NYP`.
- Missing, malformed or duplicated emails are reported and skipped, never guessed at.
- A missing `.com` on a known public provider (gmail, yahoo, hotmail, outlook, googlemail) is
  repaired and the repair is printed. No individual's address is hardcoded.

**Forced password change.** Roster accounts carry `must_change`, and every route redirects to
`/password` until the person sets their own. One shared starting password should not remain the
standing credential for 69 real people. Minimum 10 characters, cannot reuse the starting
password, and the change is audited.

Roster findings: no missing or duplicate emails across 66 people; one malformed address
(Camp 8E, corrected on import and printed — worth confirming with the focal point);
**Camp 23 has no focal point** in this roster, against 34 camps in the agreed list.

## 2026-09-08 — Deployment

Private repository at `anasismail-MEAL/CPFP-Tracker`. The git root is this `app/` directory,
**not** the parent — the parent holds the screen recording of the CPFP WhatsApp group, which
contains real children's names, FCNs and family phone numbers and must never be published.

- `requirements.txt`, `Procfile`, `render.yaml` for a Render/Railway/Heroku-style host.
- The tracker workbook cannot be committed, so `make_reference.py` generates
  `reference_lists.json` as a snapshot of the agreed camp, activity, stakeholder and status
  lists. `seed.py` prefers the live workbook and falls back to the snapshot, so a clone works.
- `CPFP_KEY` accepts a real Fernet key, and hashes anything else into a valid one. Hosting
  panels generate their own secret values, which Fernet rejects — without this the app booted
  and then failed on the first case.
- Session cookies: `SameSite=Lax` (the CSRF exposure for a session-cookie app, closed without
  a new dependency), `HttpOnly`, and `Secure` when `CPFP_HTTPS=1`.
- `debug` is off unless `CPFP_DEBUG=1`. The app binds to every interface, and the Werkzeug
  debugger would hand that network a console.

## 2026-09-08 — First working prototype

Flask + SQLite served as an installable PWA, so a browser and an Android home screen run one
codebase.

- **Chat** — one group channel for all focal points, 3s polling.
- **Children** — missing and found child reports, candidate matching (opposite kind of report,
  still open, same sex, within two years of age), reunification closing both linked reports
  together, and a per-case history.
- **My tasks** — the `Daily Log` columns from the v4.0 tracker workbook.
- **Reports** — the workbook's monthly figures reproduced with its own period rule, plus cases
  opened, children reunified, the ToR's 24-hour target, median hours to reunification, and
  reunifications by camp. Exports to Excel.

Identifying child data is held in a single Fernet-encrypted blob. Access is decided in one
place, `db.can_see_pii()`: CPSS admins see everything; a focal point sees cases they filed plus
cases touching their own camp; everyone else gets the coordination record only. Case lists never
reveal identifying data to anyone, and every reveal writes an audit row.

**Runs on synthetic data only.** Real records are gated on CPSS Coordinator and CP Officer
sign-off, DPIA/DPISP coverage, hosting and key custody decisions, and a retention rule.
