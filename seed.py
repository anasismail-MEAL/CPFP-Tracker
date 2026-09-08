"""Create the database, pull the agreed reference lists from the v4.0 workbook, and
fill it with demonstration data.

Every person and child in here is invented. The shapes are taken from the real
CPSS Camp Focal Points traffic (found-child notice, missing-child notice, a quoted
"successfully reunified" reply, an incident report), the content is not.
"""

import json
import os
import random
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import db
import make_reference

HERE = Path(__file__).resolve().parent
WORKBOOK = make_reference.WORKBOOK
SNAPSHOT = make_reference.OUT

DEMO_PASSWORD = "demo1234"

USERS = [
    # username,  full name,          agency,  camp,      role
    ("cpss",     "Nusrat Jahan",     "UNICEF", "",        "cpss_admin"),
    ("cpfp18",   "Rahim Uddin",      "CODEC",  "Camp 18", "cpfp"),
    ("cpfp18b",  "Jamal Hoque",      "SCI",    "Camp 18", "cpfp"),
    ("cpfp3",    "Shahin Alam",      "BRAC",   "Camp 3",  "cpfp"),
    ("cpfp11",   "Mongsai Marma",    "SCI",    "Camp 11", "cpfp"),
    ("cpfp20",   "Farzana Akter",    "NRC",    "Camp 20", "cpfp"),
]

CHAT = [
    ("cpfp3",   -6, 9,  "Good morning all. Camp-3 monthly CP actor meeting is confirmed for Thursday 10 AM at the CiC office."),
    ("cpfp18",  -6, 11, "Noted. I will join and bring the updated referral pathway sheet."),
    ("cpfp11",  -5, 14, "Found child notice filed on the board - boy, around 6, found near the Camp-11 CODEC office. Please check if anyone has a matching missing report."),
    ("cpfp20",  -5, 15, "Checking with our volunteers in 20 and 20 Ext now."),
    ("cpss",    -5, 16, "Reminder: please file found and missing children on the board, not only here in chat. The monthly report is generated from the board."),
    ("cpfp18",  -4, 10, "Missing child reported in Camp-18, girl around 5, since this morning. Report filed, ref on the board."),
    ("cpfp11",  -4, 12, "Ours may be the match - similar age. Coordinating with Camp-18 focal now."),
    ("cpfp18",  -4, 13, "Confirmed and reunified. Thank you all, that was under three hours."),
    ("cpss",    -4, 14, "Excellent. That is exactly the turnaround the ToR asks for."),
    ("cpfp20",  -2, 9,  "Heavy rain overnight in Camp-20, eight shelters partially affected, no child casualties. Full incident entry logged under emergency response."),
    ("cpfp3",   -1, 17, "Please remember the CiC monthly report is due next week. I am compiling Camp-3 tomorrow."),
    ("cpfp18",  0,  8,  "Morning. Two follow-ups still open from last week on my task list, will close them today."),
]

# type, reporter, camp reported, camp of origin, sex, age, days ago opened, hours to close,
# final status, holding location, invented identifying details
CASES = [
    ("found",   "cpfp11", "Camp 11", "Camp 18", "Male",   6,  5, 21,   "reunified", "CODEC office, Camp-11", "Anwar Kabir", "Nur Islam", "Rashida Begum"),
    ("missing", "cpfp18", "Camp 18", "Camp 18", "Female", 5,  4, 2.5,  "reunified", "", "Sumaiya Khatun", "Abdul Karim", "Hasina Begum"),
    ("found",   "cpfp3",  "Camp 3",  "Camp 3",  "Male",   7,  4, 6,    "reunified", "CiC office, Camp-3", "Rafiq Ullah", "Zubair Ahmed", "Momtaz Begum"),
    ("found",   "cpfp20", "Camp 20", "",        "Male",   7,  9, 30,   "reunified", "BITA centre, Camp-20", "Salim Hossain", "Nazir Ahmed", "Rehana Khatun"),
    ("missing", "cpfp3",  "Camp 3",  "Camp 3",  "Male",   11, 8, 14,   "reunified", "", "Imran Hossain", "Jahangir Alam", "Sabina Yasmin"),
    ("found",   "cpfp18", "Camp 18", "Camp 11", "Female", 4,  7, 18,   "reunified", "CFS, Camp-18", "Ayesha Siddika", "Mohammed Yunus", "Halima Khatun"),
    ("missing", "cpfp11", "Camp 11", "Camp 11", "Female", 9,  12, 40,  "reunified", "", "Nasrin Akter", "Osman Goni", "Sufia Khatun"),
    ("found",   "cpfp20", "Camp 20", "",        "Male",   5,  15, 8,   "reunified", "Camp-20 CiC office", "Bilal Hossen", "Sirajul Islam", "Rokeya Begum"),
    ("missing", "cpfp18", "Camp 18", "Camp 18", "Male",   8,  18, 52,  "reunified", "", "Kamal Uddin", "Amir Hamza", "Jorina Khatun"),
    ("found",   "cpfp3",  "Camp 3",  "",        "Female", 6,  20, 26,  "reunified", "Camp-3 learning centre", "Tahmina Akter", "Nurul Absar", "Shahida Begum"),
    # still running
    ("found",   "cpfp11", "Camp 11", "",        "Male",   6,  1, None, "open",      "CODEC office, Camp-11", "Not yet identified", "", ""),
    ("missing", "cpfp20", "Camp 20", "Camp 20", "Male",   10, 1, None, "open",      "", "Yasin Arafat", "Delwar Hossain", "Amina Khatun"),
    ("missing", "cpfp18", "Camp 18", "Camp 18", "Female", 13, 3, None, "referred",  "", "Ruma Akter", "Shafiqul Islam", "Nurjahan Begum"),
    ("found",   "cpfp3",  "Camp 3",  "",        "Female", 3,  0, None, "open",      "Camp-3 CiC office", "Not yet identified", "", ""),
]

TASK_TEMPLATES = [
    ("Camp-level coordination", "Monthly camp CP coordination meeting", "Action points shared with all CP actors", "CiC", 0, 12, "No", "Not applicable", ""),
    ("CP actor coordination", "Coordination call with CP partners on lost child", "Matching report identified in neighbouring camp", "CP partner", 1, 4, "Yes", "Closed", ""),
    ("Inter-sector coordination", "Joint walkthrough with Shelter and WASH", "Two unsafe pathways flagged for repair", "Shelter", 0, 6, "Yes", "In progress", "Follow up on repair timeline"),
    ("Referral / follow-up", "Referral follow-up with case management agency", "Case accepted and assigned to a caseworker", "CP partner", 1, 2, "Yes", "In progress", "Check progress next week"),
    ("Service mapping", "Updated camp service map with new facilities", "Service map updated and circulated", "CCCM / Site Management", 0, 3, "No", "Not applicable", ""),
    ("Emergency preparedness", "Monsoon message dissemination with volunteers", "Key messages delivered across four blocks", "Community / volunteer", 0, 18, "No", "Not applicable", ""),
    ("Emergency response", "Heavy rain response - shelter damage assessment", "Eight shelters partially affected, no child casualties", "CCCM / Site Management", 0, 40, "Yes", "In progress", "Submit damage figures to CiC"),
    ("Situation monitoring / field observation", "Direct observation walk in the camp", "Child labour observed near the market area", "Protection", 0, 0, "Yes", "Open", "Raise at next CP actor meeting"),
    ("FTR / reunification support", "Coordinated reunification with FTR team", "Child reunified with family within the day", "FTR partner", 1, 3, "No", "Closed", ""),
    ("Reporting / information sharing", "Prepared monthly CP update for the CiC", "Report submitted on time", "CiC", 0, 1, "No", "Not applicable", ""),
    ("Capacity building / orientation", "Orientation for new CP volunteers", "Twelve volunteers oriented on referral pathways", "Community / volunteer", 0, 12, "Yes", "In progress", "Schedule refresher session"),
    ("Camp-level coordination", "Sector focal point meeting on child safeguarding", "Agreed joint follow-up with the GBV focal point", "GBV", 0, 8, "No", "Closed", ""),
]


def ts(days_ago, hour=9, minute=0):
    d = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return d.replace(hour=hour, minute=minute, second=0, microsecond=0).isoformat(timespec="seconds")


def reference_lists():
    """The live workbook wins; the committed snapshot covers a clone or a deployment."""
    if WORKBOOK.exists():
        return make_reference.read_lists(WORKBOOK), WORKBOOK.name
    if SNAPSHOT.exists():
        return json.loads(SNAPSHOT.read_text(encoding="utf-8")), SNAPSHOT.name
    raise SystemExit(
        "No reference lists. Expected the workbook at %s, or the snapshot %s.\n"
        "These are the agreed CPSS lists and are not retyped here -- run "
        "make_reference.py where the workbook lives." % (WORKBOOK, SNAPSHOT)
    )


def load_reference_lists(conn):
    lists, source = reference_lists()
    print("Reference lists, read from %s:" % source)
    for kind, values in lists.items():
        for i, v in enumerate(values):
            conn.execute("INSERT INTO ref_lists (kind, val, sort) VALUES (?,?,?)", (kind, v, i))
        print("  %-14s %d values" % (kind, len(values)))
    return sum(len(v) for v in lists.values())


def main():
    if db.DB_PATH.exists():
        db.DB_PATH.unlink()
        print("Removed the previous database.")

    if os.environ.get("CPFP_KEY"):
        print("Using the encryption key from CPFP_KEY.")
    elif not db.KEY_PATH.exists():
        db.generate_key_file()
        print("Generated %s -- the encryption key for this install. Do not commit it." % db.KEY_PATH.name)

    conn = db.connect()
    db.init_schema(conn)
    load_reference_lists(conn)

    pw = db.hash_password(DEMO_PASSWORD)  # one hash reused; argon2 is slow by design
    ids = {}
    for username, name, agency, camp, role in USERS:
        cur = conn.execute(
            """INSERT INTO users (username, full_name, agency, camp, role, phone, pw_hash)
               VALUES (?,?,?,?,?,?,?)""",
            (username, name, agency, camp, role, "01" + str(random.randint(300000000, 999999999)), pw),
        )
        ids[username] = cur.lastrowid

    for username, days_ago, hour, body in CHAT:
        conn.execute(
            "INSERT INTO messages (user_id, body, created_at) VALUES (?,?,?)",
            (ids[username], body, ts(-days_ago if days_ago < 0 else days_ago, hour)),
        )

    for i, (ctype, who, camp_r, camp_o, sex, age, days_ago, hours, status, holding,
            child, father, mother) in enumerate(CASES, start=1):
        opened = ts(days_ago, 8 + (i % 9))
        closed = None
        if hours is not None:
            closed = (datetime.fromisoformat(opened) + timedelta(hours=hours)).isoformat(timespec="seconds")
        pii = {
            "child_name": child,
            "father_name": father,
            "mother_name": mother,
            "dob": "",
            "fcn": "%06d" % random.randint(100000, 999999) if father else "",
            "progress_id": "STJ-%08d" % random.randint(1, 99999999) if father else "",
            "shelter_no": "%s-%s-%02dF" % (camp_o.replace("Camp ", "C"), "E07", i) if camp_o else "",
            "contact_phone": "01" + str(random.randint(300000000, 999999999)) if father else "",
        }
        cur = conn.execute(
            """INSERT INTO cases (ref, ctype, reported_by, camp_report, camp_origin, block, sex,
                                  age_years, disability, holding_location, status, opened_at,
                                  closed_at, cpims_case_id, pii_enc)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "%s-2026-%04d" % (ctype[0].upper(), i), ctype, ids[who], camp_r, camp_o,
                random.choice(["A", "B", "C", "D", "E"]), sex, age,
                1 if i == 6 else 0, holding, status, opened, closed, "", pii_blob(pii),
            ),
        )
        conn.execute(
            "INSERT INTO case_events (case_id, user_id, event, note, created_at) VALUES (?,?,?,?,?)",
            (cur.lastrowid, ids[who], "opened", "%s report filed" % ctype, opened),
        )
        if closed:
            conn.execute(
                "INSERT INTO case_events (case_id, user_id, event, note, created_at) VALUES (?,?,?,?,?)",
                (cur.lastrowid, ids[who], status, "Closed as %s" % status, closed),
            )

    rng = random.Random(20260908)  # fixed, so the test fixture stays valid
    cpfps = [u for u in ids if u != "cpss"]
    for days_ago in range(0, 45):
        for username in rng.sample(cpfps, rng.choice([0, 1, 1, 2, 2, 3])):
            t = rng.choice(TASK_TEMPLATES)
            conn.execute(
                """INSERT INTO tasks (user_id, tdate, activity_type, action, outcome, stakeholder,
                                      n_children, n_adults, reference, followup, status, next_step)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    ids[username],
                    (date.today() - timedelta(days=days_ago)).isoformat(),
                    t[0], t[1], t[2], t[3], t[4], t[5], "", t[6], t[7], t[8],
                ),
            )

    conn.commit()
    counts = {
        name: conn.execute("SELECT COUNT(*) c FROM %s" % name).fetchone()["c"]
        for name in ("users", "messages", "cases", "tasks", "case_events")
    }
    conn.close()

    print("\nSeeded: " + ", ".join("%d %s" % (v, k) for k, v in counts.items()))
    print("\nSign in at http://localhost:5000  --  password for every demo account: %s" % DEMO_PASSWORD)
    for username, name, agency, camp, role in USERS:
        print("  %-9s %-16s %-7s %-8s %s" % (username, name, agency, camp or "-", role))
    print("\nAll names above and every child record are invented. Synthetic data only.")


def pii_blob(pii):
    return db.encrypt_pii(pii)


def ensure():
    """Seed only when there is no database yet.

    Called on boot so a test deployment comes up populated. On a host with an
    ephemeral disk this means every redeploy starts from fresh demo data, which is
    what a testing deployment should do -- and is why no real record may live there.

    The real focal point roster is never committed. Upload it to the host as a secret
    file and point CPFP_ROSTER at it, and the accounts are rebuilt on each boot too.
    """
    if db.DB_PATH.exists():
        return False
    main()

    roster = Path(os.environ.get("CPFP_ROSTER", "/etc/secrets/roster.csv"))
    if roster.exists():
        import import_roster

        people, rejected, _ = import_roster.validate(import_roster.load(roster))
        conn = db.connect()
        created, _ = import_roster.apply(
            conn, people, os.environ.get("CPFP_START_PASSWORD", import_roster.DEFAULT_PASSWORD)
        )
        # Once real people can sign in, the demo accounts must not stay open -- their
        # password is published in the README, and this runs on every boot.
        retired = import_roster.retire_demo(conn)
        conn.close()
        print("Roster: %d account(s) created, %d rejected, %d demo account(s) retired."
              % (len(created), len(rejected), retired))
    else:
        print("No roster at %s -- demo accounts only." % roster)
    return True


if __name__ == "__main__":
    main()
