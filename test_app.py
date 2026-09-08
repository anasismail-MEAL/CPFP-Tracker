"""Self-check for the parts that would fail silently and dangerously.

Run: python test_app.py
No framework -- plain asserts against a throwaway database.
"""

import os
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

# A key of our own, so the test never touches the real .cpfp_key or the real database.
os.environ["CPFP_KEY"] = Fernet.generate_key().decode()

import db  # noqa: E402
import reports  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="cpfp_test_"))
db.DB_PATH = TMP / "test.sqlite3"

import app as webapp  # noqa: E402  (imported after DB_PATH is redirected)


def iso(days_ago, hour=9):
    d = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return d.replace(hour=hour, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")


def build():
    conn = db.connect()
    db.init_schema(conn)
    for kind, vals in {
        "activity_type": ["Camp-level coordination", "Referral / follow-up"],
        "stakeholder": ["CiC", "Protection"],
        "status": ["Open", "In progress", "Closed", "Not applicable"],
        "camp": ["Camp 3", "Camp 18"],
    }.items():
        for i, v in enumerate(vals):
            conn.execute("INSERT INTO ref_lists (kind, val, sort) VALUES (?,?,?)", (kind, v, i))

    pw = db.hash_password("demo1234")
    users = {}
    for username, name, camp, role in [
        ("c18", "Rahim Uddin", "Camp 18", "cpfp"),
        ("c3", "Shahin Alam", "Camp 3", "cpfp"),
        ("boss", "Nusrat Jahan", "", "cpss_admin"),
    ]:
        users[username] = conn.execute(
            "INSERT INTO users (username, full_name, agency, camp, role, pw_hash) VALUES (?,?,?,?,?,?)",
            (username, name, "TestOrg", camp, role, pw),
        ).lastrowid

    cases = {}
    for key, owner, camp_r, camp_o, child in [
        ("own", "c18", "Camp 18", "Camp 18", "Own Camp Child"),
        ("other", "c3", "Camp 3", "Camp 3", "Other Camp Child"),
        ("origin", "c3", "Camp 3", "Camp 18", "Origin Camp Child"),
    ]:
        cases[key] = conn.execute(
            """INSERT INTO cases (ref, ctype, reported_by, camp_report, camp_origin, sex,
                                  age_years, status, opened_at, pii_enc)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                "T-" + key, "missing", users[owner], camp_r, camp_o, "Male", 6,
                "open", iso(1), db.encrypt_pii({"child_name": child, "fcn": "123456"}),
            ),
        ).lastrowid

    # Fixture for the monthly figures: 4 entries this month for c18, 1 for c3.
    today = date.today()
    first = today.replace(day=1).isoformat()
    rows = [
        ("c18", first, "Camp-level coordination", "CiC", 3, 10, "Yes", "Open"),
        ("c18", first, "Camp-level coordination", "CiC", 2, 5, "Yes", "In progress"),
        ("c18", first, "Referral / follow-up", "Protection", 1, 0, "Yes", "Closed"),
        ("c18", first, "Referral / follow-up", "Protection", 0, 2, "No", "Not applicable"),
        ("c3", first, "Camp-level coordination", "CiC", 4, 4, "Yes", "Open"),
        # Previous month -- must not be counted.
        ("c18", (today.replace(day=1) - timedelta(days=3)).isoformat(),
         "Camp-level coordination", "CiC", 99, 99, "Yes", "Open"),
    ]
    for who, d, act, stake, ch, ad, fu, st in rows:
        conn.execute(
            """INSERT INTO tasks (user_id, tdate, activity_type, action, outcome, stakeholder,
                                  n_children, n_adults, followup, status)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (users[who], d, act, "action", "outcome", stake, ch, ad, fu, st),
        )
    conn.commit()
    return conn, users, cases


def get(conn, table, row_id):
    return conn.execute("SELECT * FROM %s WHERE id = ?" % table, (row_id,)).fetchone()


conn, U, C = build()
checks = 0


def check(label, cond):
    global checks
    assert cond, "FAILED: " + label
    checks += 1
    print("  ok   " + label)


print("Encryption")
blob = db.encrypt_pii({"child_name": "Test Child", "fcn": "999"})
check("round-trips through Fernet", db.decrypt_pii(blob)["child_name"] == "Test Child")
check("unset fields come back empty, not missing", db.decrypt_pii(blob)["mother_name"] == "")
check("child name is not readable in the stored bytes", b"Test Child" not in bytes(blob))
try:
    Fernet(Fernet.generate_key()).decrypt(bytes(blob))
    check("a different key cannot decrypt it", False)
except InvalidToken:
    check("a different key cannot decrypt it", True)

print("\nAccess rules")
u18, u3, boss = (get(conn, "users", U[k]) for k in ("c18", "c3", "boss"))
own, other, origin = (get(conn, "cases", C[k]) for k in ("own", "other", "origin"))
check("a focal point sees their own case", db.can_see_pii(u18, own))
check("a focal point does NOT see another camp's case", not db.can_see_pii(u18, other))
check("a focal point sees a case whose child is from their camp", db.can_see_pii(u18, origin))
check("CPSS sees every case", all(db.can_see_pii(boss, c) for c in (own, other, origin)))
check("a deactivated account sees nothing",
      not db.can_see_pii(dict(u18) | {"active": 0}, own))

print("\nRendering and audit")
before = conn.execute("SELECT COUNT(*) c FROM audit").fetchone()["c"]
view = db.case_view(conn, u18, other)
check("a restricted case renders with no identifying data", view["pii"] is None)
check("the encrypted blob never reaches the template", "pii_enc" not in view)
check("a blocked view writes no audit row",
      conn.execute("SELECT COUNT(*) c FROM audit").fetchone()["c"] == before)

view = db.case_view(conn, u18, own)
check("a permitted case renders the child's name", view["pii"]["child_name"] == "Own Camp Child")
rows = conn.execute(
    "SELECT * FROM audit WHERE action = 'pii_read' AND object_id = ?", (own["id"],)
).fetchall()
check("exactly one audit row per identifying-data read", len(rows) == 1)
check("the audit row names the reader", rows[0]["user_id"] == u18["id"])

print("\nMonthly figures")
d = reports.monthly(conn, date.today().year, date.today().month)
people = {p["full_name"]: p for p in d["people"]}
check("activities counted per focal point", people["Rahim Uddin"]["activities"] == 4)
check("children summed per focal point", people["Rahim Uddin"]["children"] == 6)
check("adults summed per focal point", people["Rahim Uddin"]["adults"] == 17)
# Yes + not Closed/Not applicable -> the Open and In progress rows only.
check("open follow-ups use the workbook's rule", people["Rahim Uddin"]["open_followups"] == 2)
check("the previous month is excluded", d["totals"]["activities"] == 5)
check("totals cover every focal point", d["totals"]["children"] == 10)
acts = {a["k"]: a for a in d["activities"]}
check("activity breakdown splits correctly", acts["Camp-level coordination"]["entries"] == 3)
check("empty activity types still appear as zero rows",
      all("entries" in a for a in d["activities"]))
stakes = {s["k"]: s["entries"] for s in d["stakeholders"]}
check("stakeholder breakdown splits correctly", stakes["Protection"] == 2)

print("\nClosing a case")
webapp.app.config["TESTING"] = True
client = webapp.app.test_client()
client.post("/login", data={"username": "c18", "password": "demo1234"})
resp = client.post(
    "/cases/%d/action" % own["id"],
    data={"action": "reunify", "match_id": str(other["id"]), "note": "Reunified at the CiC office"},
)
check("the action redirects back to the case", resp.status_code == 302)

conn2 = db.connect()
closed = get(conn2, "cases", own["id"])
partner = get(conn2, "cases", other["id"])
check("status becomes reunified", closed["status"] == "reunified")
check("closed_at is stamped", closed["closed_at"] is not None)
check("the linked report is closed too", partner["status"] == "reunified")
check("the link is recorded on both sides",
      closed["matched_case_id"] == other["id"] and partner["matched_case_id"] == own["id"])
events = conn2.execute(
    "SELECT * FROM case_events WHERE case_id = ? ORDER BY id", (own["id"],)
).fetchall()
check("a case event is written", events[-1]["event"] == "reunified")
check("the note is kept", events[-1]["note"] == "Reunified at the CiC office")

d2 = reports.monthly(conn2, date.today().year, date.today().month)
check("the reunification reaches the report", d2["cases"]["reunified"] == 2)
check("hours to reunification are measured", d2["cases"]["median_hours"] is not None)

print("\nExport")
buf = reports.to_xlsx(d2)
check("the workbook is produced", buf.getbuffer().nbytes > 4000)

print("\nAuthentication")
check("a wrong password is refused", not db.check_password(db.hash_password("right"), "wrong"))
check("the right password is accepted", db.check_password(db.hash_password("right"), "right"))
anon = webapp.app.test_client()
check("signed-out users cannot reach the case board",
      anon.get("/cases").status_code == 302)
check("signed-out users cannot reach the chat feed",
      anon.get("/api/messages").status_code == 302)

print("\nForced password change")
conn2.execute(
    """INSERT INTO users (username, full_name, agency, camp, role, pw_hash, must_change)
       VALUES ('new@example.org', 'New Focal', 'TestOrg', 'Camp 3', 'cpfp', ?, 1)""",
    (db.hash_password("CpfpStart2026"),),
)
conn2.commit()

fresh = webapp.app.test_client()
fresh.post("/login", data={"username": "new@example.org", "password": "CpfpStart2026"})
check("a starting-password account is pushed to /password",
      fresh.get("/cases").headers.get("Location", "").endswith("/password"))
check("even the chat is closed to it",
      fresh.get("/chat").headers.get("Location", "").endswith("/password"))
check("the password page itself is reachable", fresh.get("/password").status_code == 200)

fresh.post("/password", data={"new": "short", "again": "short"})
check("a short password is refused",
      fresh.get("/cases").headers.get("Location", "").endswith("/password"))
fresh.post("/password", data={"new": "a-long-enough-one", "again": "a-different-one"})
check("mismatched passwords are refused",
      fresh.get("/cases").headers.get("Location", "").endswith("/password"))
fresh.post("/password", data={"new": "CpfpStart2026", "again": "CpfpStart2026"})
check("reusing the starting password is refused",
      fresh.get("/cases").headers.get("Location", "").endswith("/password"))

fresh.post("/password", data={"new": "chosen-by-the-user", "again": "chosen-by-the-user"})
check("a good password clears the block", fresh.get("/cases").status_code == 200)
row = conn2.execute(
    "SELECT * FROM users WHERE username = 'new@example.org'"
).fetchone()
check("must_change is cleared", row["must_change"] == 0)
check("the new password works", db.check_password(row["pw_hash"], "chosen-by-the-user"))
check("the old one does not", not db.check_password(row["pw_hash"], "CpfpStart2026"))
check("the change is audited",
      conn2.execute("SELECT COUNT(*) c FROM audit WHERE action = 'password_change'"
                    ).fetchone()["c"] == 1)

print("\nRoster import")
import import_roster  # noqa: E402

check("plain camp numbers take the prefix", import_roster.camp_name("18") == "Camp 18")
check("4E maps to the agreed name", import_roster.camp_name("4E") == "Camp 4 Ext")
check("Kutupalong RC maps to KTP", import_roster.camp_name("Kutupalong RC") == "KTP")
check("NYP-RC-Teknaf maps to NYP", import_roster.camp_name("NYP-RC-Teknaf") == "NYP")
check("a multi-number phone cell keeps the first",
      import_roster.clean_phone("01823846880, 01515219382") == "01823846880")

sample = [
    {"full_name": "Good Person", "email": "good@example.org", "agency": "A",
     "camp": "Camp 3", "phone": "1", "position": "primary", "role": "cpfp"},
    {"full_name": "Typo Person", "email": "someone.example@gmail", "agency": "A",
     "camp": "Camp 8E", "phone": "1", "position": "primary", "role": "cpfp"},
    {"full_name": "No Email", "email": "", "agency": "A",
     "camp": "Camp 9", "phone": "1", "position": "backup", "role": "cpfp"},
    {"full_name": "Copycat", "email": "good@example.org", "agency": "A",
     "camp": "Camp 10", "phone": "1", "position": "backup", "role": "cpfp"},
]
good, rejected, fixed = import_roster.validate(sample)
check("a bare provider domain is corrected, not dropped",
      fixed and good[1]["email"] == "someone.example@gmail.com")
check("a real domain is left alone",
      import_roster.fix_email("person@codec.org.bd") == "person@codec.org.bd")
check("an unknown bare domain is not guessed at",
      import_roster.fix_email("person@somecompany") == "person@somecompany")
check("a person with no email is rejected",
      any(r[0] == "No Email" for r in rejected))
check("a duplicate email is rejected", any(r[0] == "Copycat" for r in rejected))
check("valid people survive", len(good) == 2)

before_users = conn2.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
created, updated = import_roster.apply(conn2, good, "CpfpStart2026")
check("import creates accounts", len(created) == 2)
created2, updated2 = import_roster.apply(conn2, good, "CpfpStart2026")
check("re-running creates nothing", len(created2) == 0 and len(updated2) == 2)
check("no duplicate rows",
      conn2.execute("SELECT COUNT(*) c FROM users").fetchone()["c"] == before_users + 2)
imported = conn2.execute(
    "SELECT * FROM users WHERE username = 'good@example.org'"
).fetchone()
check("imported accounts must change their password", imported["must_change"] == 1)
check("imported accounts are focal points, not admins", imported["role"] == "cpfp")

n = import_roster.retire_demo(conn2)
check("retiring deactivates the demo accounts", n >= 3)
check("no short-username account stays active",
      conn2.execute(
          "SELECT COUNT(*) c FROM users WHERE username NOT LIKE '%@%' AND active = 1"
      ).fetchone()["c"] == 0)
check("email accounts are untouched",
      conn2.execute(
          "SELECT active FROM users WHERE username = 'good@example.org'"
      ).fetchone()["active"] == 1)
check("a retired account cannot sign in",
      webapp.app.test_client().post(
          "/login", data={"username": "c18", "password": "demo1234"}
      ).status_code == 200)  # 200 = login page again, not a 302 into the app
check("retiring twice changes nothing more", import_roster.retire_demo(conn2) == 0)

conn.close()
conn2.close()
print("\n%d checks passed." % checks)
