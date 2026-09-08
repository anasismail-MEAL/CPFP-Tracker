# CPFP Coordination App — prototype

A working prototype for the CPSS Cox's Bazar Child Protection Focal Point system: individual
accounts, a shared chat, a missing/found child board with matching and reunification, a
personal daily activity log, and a monthly report.

Built to be demonstrated to the CP Officer and the CPSS Coordinator. **Not yet fit to hold a
real child record** — see *Before real data* below.

## Why it exists

Lost and found children are matched and reunified every day through the `CPSS Camp Focal
Points` WhatsApp group. None of it is recorded. Nobody can say how many children were
reunified last month, how long it took, or which camps carry the load. Chat is a good
coordination channel and a hopeless record.

This app leaves the coordination conversation intact and adds the record underneath it.

## Run it

```bash
pip install flask
```

```bash
python seed.py
```

```bash
python app.py
```

Then open <http://localhost:5000>. `seed.py` prints the demo accounts; the password for every
one of them is `demo1234`.

| Account | Person | Camp | Role |
|---|---|---|---|
| `cpss` | Nusrat Jahan, UNICEF | — | CPSS admin |
| `cpfp18` | Rahim Uddin, CODEC | Camp 18 | Focal point |
| `cpfp18b` | Jamal Hoque, SCI | Camp 18 | Backup focal point |
| `cpfp3` | Shahin Alam, BRAC | Camp 3 | Focal point |
| `cpfp11` | Mongsai Marma, SCI | Camp 11 | Focal point |
| `cpfp20` | Farzana Akter, NRC | Camp 20 | Focal point |

**Every person, message and child record in the seeded database is invented.**

To demo on a phone, run the server and open `http://<your-laptop-ip>:5000` on a phone on the
same wifi — the address is printed at startup. Add to home screen and it runs as an app.

Check it still works:

```bash
python test_app.py
```

## How the data is protected

Identifying details — child name, parents' names, date of birth, FCN, Progress ID, shelter
number, family contact — are held in a single Fernet-encrypted blob (`cases.pii_enc`). They are
never written to an ordinary column, so a database dump or a stolen file reveals nothing without
the key.

Who may decrypt them is decided in exactly one place, `db.can_see_pii()`:

- **CPSS admin** — every case.
- **Focal point** — cases they filed, plus cases where their own camp is either the camp
  reporting or the camp the child belongs to.
- **Everyone else** — the coordination record only: age, sex, camp, status, and who filed it.
  That is still enough to recognise a possible match and phone the focal point who filed it.

Every reveal of identifying data writes a row to `audit`, naming the reader, the case and the
time. Case lists never show identifying data at all, whatever your role — you have to open a
case, and opening it is recorded.

The encryption key lives in `CPFP_KEY`, or in `.cpfp_key` next to the database. The app refuses
to start without one rather than falling back to a default. **Do not commit `.cpfp_key` or
`cpfp.sqlite3`.**

## What it does

- **Chat** — one group channel for all focal points, polled every 3 seconds.
- **Children** — file a missing or found report; filter by status and kind; open a case to see
  its history and, if permitted, the identifying details. Closing a case offers likely matches
  (the opposite kind of report, still open, same sex, within two years of age) and links both
  sides together when you reunify.
- **My tasks** — the same columns as the `Daily Log` sheet of
  `CPFP_Daily_Activity_Tracker_v4_0.xlsx`, so nothing new has to be learned.
- **Reports** — the monthly figures the workbook produces (per focal point, activity type,
  stakeholder, using the workbook's own period rule) plus what the workbook cannot: cases
  opened, children reunified, reunifications within the ToR's 24-hour target, median hours to
  reunification, and reunifications by camp. Downloads as Excel.

The camp, activity type, stakeholder and status lists are read from the v4.0 workbook at seed
time. They are the agreed CPSS lists; change them there, not here.

## Deliberate shortcuts

Marked `ponytail:` in the code.

- Chat polls every 3s rather than using websockets.
- One encryption key from the environment, no KMS. Rotation would be a re-encrypt script.
- SQLite, single file. Fine for a pilot, not for 35 camps at scale.
- The Flask development server. Debug is off by default because the app binds to every
  interface; do not turn it on where anyone else can reach the port.
- The service worker caches the shell only. There is no offline write queue — a stale child
  record is worse than no record.

## Deploying it for testing

The repository holds the application only. The tracker workbook, the ToR and the screen
recording stay outside it — the recording contains real children's names, FCNs and phone
numbers and must never be published anywhere.

`render.yaml` is a Render blueprint: connect the private repo, and it builds, generates
`CPFP_KEY` and `CPFP_SECRET`, serves over HTTPS and seeds the demo data on boot. The same
`Procfile` works on Railway and any Heroku-style host.

Environment variables:

| Variable | Purpose |
|---|---|
| `CPFP_KEY` | Encryption key. A real Fernet key is used as-is; any other secret is hashed into one, so a host's generated value works. |
| `CPFP_SECRET` | Session signing key. Without it, everyone is signed out on each restart. |
| `CPFP_HTTPS` | Set to `1` behind TLS so the session cookie is marked Secure. |
| `CPFP_DEBUG` | Leave unset. Never `1` on anything reachable by others. |

Generate a proper key yourself with:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

**The disk is ephemeral on free tiers.** Every redeploy rebuilds the database from
`seed.py`, so anything typed into the deployed instance disappears. That is deliberate for a
testing deployment, and it is a second reason no real record may be entered there.

Serving on Windows locally uses waitress instead of gunicorn:

```bash
waitress-serve --port=5000 app:app
```

## Before real data

This app stores identifying information about children. Before a single real record is entered
it needs, at minimum:

1. **CPSS Coordinator and CP Officer sign-off.** The CPFP system, its roster and its missing
   child tracking belong to the CP Officer, not to IM.
2. **DPIA coverage and a DPISP position** — where the data sits, who controls it, how long it
   is kept, who may export it, and how it relates to CPIMS+/Primero, which remains the case
   management system of record. This app is a coordination log, not a case file.
3. **Hosting decisions** — TLS, a production WSGI server, backups, key custody, and where the
   server physically sits.
4. **A retention rule.** Nothing here deletes or archives closed cases yet.

Until those exist, run it on the seeded synthetic data only.

## Files

| File | What it holds |
|---|---|
| `db.py` | Schema, Fernet encryption, password hashing, `can_see_pii`, audit |
| `app.py` | Routes |
| `reports.py` | Monthly aggregates and the Excel export |
| `seed.py` | Reference lists, demonstration data, and `ensure()` for first boot |
| `make_reference.py` | Regenerates `reference_lists.json` from the tracker workbook |
| `reference_lists.json` | Committed snapshot of the agreed camp/activity/stakeholder/status lists |
| `test_app.py` | 38 assertions over encryption, access rules, audit, figures and closing a case |
| `templates/`, `static/` | Pages, phone-first CSS, PWA manifest and service worker |
| `requirements.txt`, `Procfile`, `render.yaml` | Deployment |

Not in this repository, deliberately: the tracker workbook, the ToR, the screen recording, the
encryption key (`.cpfp_key`) and the database (`*.sqlite3`).
