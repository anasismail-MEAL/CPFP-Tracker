"""Create focal point and CPSS accounts from the CPSS focal point roster.

The roster holds real staff names, work emails and mobile numbers, so it is never
committed. Point this at the workbook locally, or at a CSV placed on the host as a
secret file.

    python import_roster.py "../CPSS_focal points list and backup_as of August_2026_ updated.xlsx"
    python import_roster.py roster.csv
    python import_roster.py <source> --csv-out roster.csv    # write a CSV for the host

Re-running is safe. Accounts are keyed on email; an existing account has its name,
agency, camp and phone refreshed and its password left alone.
"""

import argparse
import csv
import re
import sys
from pathlib import Path

import db

DEFAULT_PASSWORD = "CpfpStart2026"

# Camp labels the roster uses that do not simply take a "Camp " prefix.
CAMP_ALIASES = {
    "4E": "Camp 4 Ext",
    "Kutupalong RC": "KTP",
    "NYP-RC-Teknaf": "NYP",
}

# Public mail providers people write without the .com. Correcting these is safe and is
# always reported, never silent. No individual's address is recorded here.
BARE_DOMAINS = ("gmail", "yahoo", "hotmail", "outlook", "googlemail")


def fix_email(email):
    """Repair a missing .com on a well-known provider. Returns the email unchanged otherwise."""
    if "@" not in email:
        return email
    local, _, domain = email.rpartition("@")
    if "." not in domain and domain.lower() in BARE_DOMAINS:
        return "%s@%s.com" % (local, domain.lower())
    return email

# CPSS coordination team. Not in the roster; they hold the admin role.
ADMINS = [
    ("Anas Ismail", "anismail@unicef.org", "UNICEF"),
    ("Taslima Begum", "tbegum@unicef.org", "UNICEF"),
    ("Lillian Kona", "lkona@unicef.org", "UNICEF"),
]

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
HEADER_ROW = 6  # the workbook's header sits on row 6, data starts at row 7
COLS = {"camp": 1, "name": 2, "agency": 3, "email": 4, "phone": 5,
        "b_name": 6, "b_agency": 7, "b_email": 8, "b_phone": 9}


def camp_name(raw):
    raw = (raw or "").strip()
    if not raw:
        return ""
    return CAMP_ALIASES.get(raw, "Camp " + raw)


def clean_phone(raw):
    """Keep the first number when a cell lists several, and normalise spacing."""
    text = str(raw or "").strip()
    first = re.split(r"[,/;]", text)[0]
    return re.sub(r"[^\d+]", "", first)


def read_workbook(path):
    from openpyxl import load_workbook

    ws = load_workbook(path, data_only=True).worksheets[0]
    people = []
    for row in ws.iter_rows(min_row=HEADER_ROW + 1, values_only=True):
        if not any(v is not None for v in row):
            continue
        cell = lambda k: (str(row[COLS[k]]).strip() if row[COLS[k]] is not None else "")
        camp = camp_name(cell("camp"))
        for prefix, position in (("", "primary"), ("b_", "backup")):
            name = cell(prefix + "name")
            if not name:
                continue
            people.append({
                "full_name": name,
                "email": cell(prefix + "email").lower(),
                "agency": cell(prefix + "agency"),
                "camp": camp,
                "phone": clean_phone(cell(prefix + "phone")),
                "position": position,
                "role": "cpfp",
            })
    return people


def read_csv(path):
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return [
            {
                "full_name": r["full_name"].strip(),
                "email": r["email"].strip().lower(),
                "agency": r.get("agency", "").strip(),
                "camp": r.get("camp", "").strip(),
                "phone": r.get("phone", "").strip(),
                "position": r.get("position", "primary").strip(),
                "role": r.get("role", "cpfp").strip() or "cpfp",
            }
            for r in csv.DictReader(fh)
            if r.get("full_name", "").strip()
        ]


def load(source):
    path = Path(source)
    if not path.exists():
        raise SystemExit("Roster not found: %s" % path)
    if path.suffix.lower() == ".csv":
        # A CSV written by --csv-out already carries the admins; adding them again
        # would only be rejected as duplicates.
        return read_csv(path)

    people = read_workbook(path)
    for admin_name, admin_email, agency in ADMINS:
        people.append({
            "full_name": admin_name, "email": admin_email, "agency": agency,
            "camp": "", "phone": "", "position": "admin", "role": "cpss_admin",
        })
    return people


def validate(people):
    """Split into importable and rejected, applying the known email corrections."""
    good, rejected, fixed = [], [], []
    seen = {}
    for p in people:
        repaired = fix_email(p["email"])
        if repaired != p["email"]:
            fixed.append((p["full_name"], p["email"], repaired))
            p["email"] = repaired
        if not p["email"]:
            rejected.append((p["full_name"], p["camp"], "no email address"))
        elif not EMAIL_RE.match(p["email"]):
            rejected.append((p["full_name"], p["camp"], "malformed email: " + p["email"]))
        elif p["email"] in seen:
            rejected.append((p["full_name"], p["camp"],
                             "email already used by " + seen[p["email"]]))
        else:
            seen[p["email"]] = p["full_name"]
            good.append(p)
    return good, rejected, fixed


def apply(conn, people, password=DEFAULT_PASSWORD):
    """Insert new accounts, refresh details on existing ones. Never touches a password."""
    pw = db.hash_password(password)
    created, updated = [], []
    for p in people:
        row = conn.execute(
            "SELECT id, full_name FROM users WHERE username = ?", (p["email"],)
        ).fetchone()
        if row:
            conn.execute(
                """UPDATE users SET full_name = ?, agency = ?, camp = ?, phone = ?,
                                    role = ?, active = 1 WHERE id = ?""",
                (p["full_name"], p["agency"], p["camp"], p["phone"], p["role"], row["id"]),
            )
            updated.append(p)
        else:
            conn.execute(
                """INSERT INTO users (username, full_name, agency, camp, role, phone,
                                      pw_hash, active, must_change)
                   VALUES (?,?,?,?,?,?,?,1,1)""",
                (p["email"], p["full_name"], p["agency"], p["camp"], p["role"],
                 p["phone"], pw),
            )
            created.append(p)
    conn.commit()
    return created, updated


def retire_demo(conn):
    """Deactivate the seeded demo accounts.

    Their password is published in the README, so they must not stay open once real
    people can sign in. Demo usernames are short names; real ones are email addresses.
    """
    cur = conn.execute(
        "UPDATE users SET active = 0 WHERE username NOT LIKE '%@%' AND active = 1"
    )
    conn.commit()
    return cur.rowcount


def write_csv(people, out):
    fields = ["full_name", "email", "agency", "camp", "phone", "position", "role"]
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for p in people:
            w.writerow({k: p[k] for k in fields})


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", help="roster .xlsx or .csv")
    ap.add_argument("--password", default=DEFAULT_PASSWORD,
                    help="starting password for new accounts (default: %(default)s)")
    ap.add_argument("--csv-out", help="also write a CSV of the roster for a host secret file")
    ap.add_argument("--dry-run", action="store_true", help="report only, change nothing")
    ap.add_argument("--retire-demo", action="store_true",
                    help="deactivate the seeded demo accounts, whose password is published "
                         "in the README")
    args = ap.parse_args(argv)

    people = load(args.source)
    good, rejected, fixed = validate(people)

    if fixed:
        print("Corrected %d email address(es):" % len(fixed))
        for name, was, now in fixed:
            print("  %-24s %s -> %s" % (name, was, now))
        print()

    if rejected:
        print("NOT imported (%d):" % len(rejected))
        for name, camp, why in rejected:
            print("  %-24s %-12s %s" % (name, camp, why))
        print()

    if args.csv_out:
        write_csv(good, args.csv_out)
        print("Wrote %s (%d people). Do not commit it.\n" % (args.csv_out, len(good)))

    if args.dry_run:
        print("Dry run: %d account(s) would be created or refreshed." % len(good))
        return 0

    if not db.DB_PATH.exists():
        raise SystemExit("No database at %s. Run seed.py first." % db.DB_PATH)

    conn = db.connect()
    db.init_schema(conn)  # picks up must_change on an older database
    created, updated = apply(conn, good, args.password)

    retired = retire_demo(conn) if args.retire_demo else 0

    camps = sorted({p["camp"] for p in good if p["camp"]})
    have = {r["val"] for r in conn.execute("SELECT val FROM ref_lists WHERE kind = 'camp'")}
    conn.close()

    print("Created %d account(s), refreshed %d." % (len(created), len(updated)))
    if args.retire_demo:
        print("Deactivated %d demo account(s)." % retired)
    print("Focal points: %d   CPSS admins: %d"
          % (sum(1 for p in good if p["role"] == "cpfp"),
             sum(1 for p in good if p["role"] == "cpss_admin")))
    unknown = [c for c in camps if c not in have]
    if unknown:
        print("\nCamps not in the agreed camp list: %s" % ", ".join(unknown))
    uncovered = sorted(have - set(camps))
    if uncovered:
        print("Camps with no focal point in this roster: %s" % ", ".join(uncovered))
    if created:
        print("\nNew accounts sign in with the email address above and the starting "
              "password, then must set their own before reaching any page.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
