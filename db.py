"""Storage, field encryption and access rules for the CPFP coordination app.

Every rule about who may read a child's identifying details lives in this module
(`can_see_pii`), so routes never decide it for themselves.
"""

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError, InvalidHashError
from cryptography.fernet import Fernet

BASE = Path(__file__).resolve().parent
DB_PATH = BASE / "cpfp.sqlite3"
KEY_PATH = BASE / ".cpfp_key"

ROLES = ("cpfp", "cpss_admin")
CASE_TYPES = ("missing", "found")
CASE_STATUS = ("open", "matched", "reunified", "referred", "closed_other")

# Fields held inside the encrypted blob. Nothing here ever reaches a plain column.
PII_FIELDS = (
    "child_name",
    "father_name",
    "mother_name",
    "dob",
    "fcn",
    "progress_id",
    "shelter_no",
    "contact_phone",
)

_ph = PasswordHasher()


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------- keys

def _coerce_key(raw: bytes) -> bytes:
    """Accept a real Fernet key, or derive one from any other secret string.

    Hosting panels generate their own random values for secrets, and those are not
    valid Fernet keys -- without this the app boots and then fails on the first case.
    A derived key is deterministic, so records stay readable across restarts as long
    as the secret does not change.
    """
    try:
        Fernet(raw)
        return raw
    except (ValueError, TypeError):
        import base64
        import hashlib

        return base64.urlsafe_b64encode(hashlib.sha256(raw).digest())


def load_key() -> bytes:
    """Fernet key from CPFP_KEY, else the local key file. Never a default."""
    env = os.environ.get("CPFP_KEY")
    if env:
        return _coerce_key(env.encode())
    if KEY_PATH.exists():
        return _coerce_key(KEY_PATH.read_bytes().strip())
    raise RuntimeError(
        "No encryption key. Set CPFP_KEY, or run `python seed.py` which generates "
        f"{KEY_PATH.name} for local use. Refusing to start without one."
    )


def generate_key_file() -> bytes:
    key = Fernet.generate_key()
    KEY_PATH.write_bytes(key)
    return key


def _fernet() -> Fernet:
    return Fernet(load_key())


def encrypt_pii(data: dict) -> bytes:
    clean = {k: (data.get(k) or "") for k in PII_FIELDS}
    return _fernet().encrypt(json.dumps(clean).encode())


def decrypt_pii(blob) -> dict:
    if not blob:
        return {k: "" for k in PII_FIELDS}
    return json.loads(_fernet().decrypt(bytes(blob)).decode())


# --------------------------------------------------------------------------- passwords

def hash_password(pw: str) -> str:
    return _ph.hash(pw)


def check_password(pw_hash: str, pw: str) -> bool:
    try:
        return _ph.verify(pw_hash, pw)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


# --------------------------------------------------------------------------- schema

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id        INTEGER PRIMARY KEY,
    username  TEXT UNIQUE NOT NULL,
    full_name TEXT NOT NULL,
    agency    TEXT,
    camp      TEXT,
    role      TEXT NOT NULL,
    phone     TEXT,
    pw_hash   TEXT NOT NULL,
    active    INTEGER NOT NULL DEFAULT 1,
    -- Set for accounts created from the roster with a shared starting password.
    -- They cannot reach any page until they choose their own.
    must_change INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id),
    channel    TEXT NOT NULL DEFAULT 'all',
    body       TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cases (
    id               INTEGER PRIMARY KEY,
    ref              TEXT UNIQUE NOT NULL,
    ctype            TEXT NOT NULL,
    reported_by      INTEGER NOT NULL REFERENCES users(id),
    camp_report      TEXT,
    camp_origin      TEXT,
    block            TEXT,
    sex              TEXT,
    age_years        INTEGER,
    disability       INTEGER NOT NULL DEFAULT 0,
    holding_location TEXT,
    status           TEXT NOT NULL DEFAULT 'open',
    opened_at        TEXT NOT NULL,
    closed_at        TEXT,
    cpims_case_id    TEXT,
    matched_case_id  INTEGER REFERENCES cases(id),
    pii_enc          BLOB
);

CREATE TABLE IF NOT EXISTS case_events (
    id         INTEGER PRIMARY KEY,
    case_id    INTEGER NOT NULL REFERENCES cases(id),
    user_id    INTEGER REFERENCES users(id),
    event      TEXT NOT NULL,
    note       TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id            INTEGER PRIMARY KEY,
    user_id       INTEGER NOT NULL REFERENCES users(id),
    tdate         TEXT NOT NULL,
    activity_type TEXT,
    action        TEXT,
    outcome       TEXT,
    stakeholder   TEXT,
    n_children    INTEGER NOT NULL DEFAULT 0,
    n_adults      INTEGER NOT NULL DEFAULT 0,
    reference     TEXT,
    followup      TEXT,
    status        TEXT,
    next_step     TEXT
);

CREATE TABLE IF NOT EXISTS audit (
    id          INTEGER PRIMARY KEY,
    user_id     INTEGER REFERENCES users(id),
    action      TEXT NOT NULL,
    object_type TEXT,
    object_id   INTEGER,
    at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ref_lists (
    id   INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    val  TEXT NOT NULL,
    sort INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS ix_messages_id   ON messages(id);
CREATE INDEX IF NOT EXISTS ix_tasks_date    ON tasks(tdate);
CREATE INDEX IF NOT EXISTS ix_cases_status  ON cases(status);
"""


def connect(path=None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(conn) -> None:
    conn.executescript(SCHEMA)
    _add_missing_columns(conn)
    conn.commit()


def _add_missing_columns(conn) -> None:
    """CREATE TABLE IF NOT EXISTS leaves an older table alone, so add columns here."""
    for table, column, spec in [
        ("users", "must_change", "INTEGER NOT NULL DEFAULT 0"),
    ]:
        have = {r["name"] for r in conn.execute("PRAGMA table_info(%s)" % table)}
        if column not in have:
            conn.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, column, spec))


def ref_list(conn, kind) -> list:
    rows = conn.execute(
        "SELECT val FROM ref_lists WHERE kind = ? ORDER BY sort, val", (kind,)
    ).fetchall()
    return [r["val"] for r in rows]


# --------------------------------------------------------------------------- access

def can_see_pii(user, case) -> bool:
    """The single authority on identifying-data access.

    CPSS admins see everything. A CPFP sees identifying details only for cases they
    filed themselves, or cases touching their own camp -- either where the child was
    reported or where the child came from. Everyone else sees the coordination record
    only, which still carries enough (age, sex, camp, status) to spot a match.
    """
    if user is None or case is None:
        return False
    if not user["active"]:
        return False
    if user["role"] == "cpss_admin":
        return True
    if user["role"] != "cpfp":
        return False
    if case["reported_by"] == user["id"]:
        return True
    camp = (user["camp"] or "").strip().lower()
    if not camp:
        return False
    return camp in {
        (case["camp_report"] or "").strip().lower(),
        (case["camp_origin"] or "").strip().lower(),
    }


def log_audit(conn, user_id, action, object_type=None, object_id=None) -> None:
    conn.execute(
        "INSERT INTO audit (user_id, action, object_type, object_id, at) VALUES (?,?,?,?,?)",
        (user_id, action, object_type, object_id, now()),
    )


def case_view(conn, user, case, audit=True) -> dict:
    """Render one case for `user`, attaching identifying details only if allowed.

    Reading identifying details is itself recorded -- an audit row per case revealed.
    """
    view = {k: case[k] for k in case.keys() if k != "pii_enc"}
    if can_see_pii(user, case):
        view["pii"] = decrypt_pii(case["pii_enc"])
        view["pii_visible"] = True
        if audit:
            log_audit(conn, user["id"], "pii_read", "case", case["id"])
            conn.commit()
    else:
        view["pii"] = None
        view["pii_visible"] = False
    return view
