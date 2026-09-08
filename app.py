"""CPFP coordination app -- chat, missing/found child board, task log, monthly report.

Prototype. Runs on synthetic data only; see README before it goes anywhere near a
real child record.
"""

import os
from datetime import date, datetime, timezone
from functools import wraps

from flask import (
    Flask, abort, flash, g, jsonify, redirect, render_template,
    request, send_file, session, url_for,
)

import db
import reports

app = Flask(__name__)
# ponytail: dev secret regenerated per start, so sessions drop on restart.
# Set CPFP_SECRET before hosting this for anyone but yourself.
app.secret_key = os.environ.get("CPFP_SECRET") or os.urandom(32)

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    # SameSite=Lax stops another site POSTing to these forms with the user's cookie,
    # which is the CSRF exposure a session-cookie app has. No extra dependency.
    SESSION_COOKIE_SAMESITE="Lax",
    # Set CPFP_HTTPS=1 wherever this is served over TLS so the cookie never travels plain.
    SESSION_COOKIE_SECURE=os.environ.get("CPFP_HTTPS") == "1",
    MAX_CONTENT_LENGTH=1024 * 1024,
)

CASE_ACTIONS = {
    "match": ("matched", "Linked to a matching report"),
    "reunify": ("reunified", "Child reunified with family"),
    "refer": ("referred", "Referred to FTR / case management"),
    "reopen": ("open", "Reopened"),
}


@app.before_request
def load_user():
    g.user = None
    uid = session.get("uid")
    if uid:
        conn = db.connect()
        g.user = conn.execute(
            "SELECT * FROM users WHERE id = ? AND active = 1", (uid,)
        ).fetchone()
        conn.close()


def login_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if g.user is None:
            return redirect(url_for("login", next=request.path))
        return fn(*a, **kw)

    return wrapper


@app.context_processor
def inject():
    return {"user": g.user, "today": date.today().isoformat()}


# --------------------------------------------------------------------------- auth

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        conn = db.connect()
        row = conn.execute(
            "SELECT * FROM users WHERE username = ? AND active = 1",
            (request.form.get("username", "").strip().lower(),),
        ).fetchone()
        if row and db.check_password(row["pw_hash"], request.form.get("password", "")):
            session["uid"] = row["id"]
            db.log_audit(conn, row["id"], "login")
            conn.commit()
            conn.close()
            return redirect(request.args.get("next") or url_for("chat"))
        conn.close()
        flash("Wrong username or password.")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
def home():
    return redirect(url_for("chat") if g.user else url_for("login"))


# --------------------------------------------------------------------------- chat

@app.route("/chat")
@login_required
def chat():
    return render_template("chat.html")


@app.route("/api/messages")
@login_required
def api_messages():
    """Newest-last, only what the client has not seen. ponytail: 3s polling, no sockets."""
    since = request.args.get("since", 0, type=int)
    conn = db.connect()
    rows = conn.execute(
        """SELECT m.id, m.body, m.created_at, u.full_name, u.camp, u.agency, u.id AS user_id
           FROM messages m JOIN users u ON u.id = m.user_id
           WHERE m.id > ? ORDER BY m.id LIMIT 200""",
        (since,),
    ).fetchall()
    conn.close()
    return jsonify([dict(r) | {"mine": r["user_id"] == g.user["id"]} for r in rows])


@app.route("/api/messages", methods=["POST"])
@login_required
def api_post_message():
    body = (request.json or {}).get("body", "").strip()
    if not body:
        return jsonify({"error": "empty"}), 400
    conn = db.connect()
    conn.execute(
        "INSERT INTO messages (user_id, body, created_at) VALUES (?,?,?)",
        (g.user["id"], body[:4000], db.now()),
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


# --------------------------------------------------------------------------- cases

@app.route("/cases")
@login_required
def cases():
    status = request.args.get("status", "active")
    ctype = request.args.get("type", "")
    sql = "SELECT c.*, u.full_name AS reporter FROM cases c JOIN users u ON u.id = c.reported_by"
    where, params = [], []
    if status == "active":
        where.append("c.status IN ('open', 'matched')")
    elif status and status != "all":
        where.append("c.status = ?")
        params.append(status)
    if ctype:
        where.append("c.ctype = ?")
        params.append(ctype)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY c.opened_at DESC LIMIT 300"

    conn = db.connect()
    rows = conn.execute(sql, params).fetchall()
    # The list never reveals identifying data, whatever the viewer's role -- open a
    # case to see that, which is the point at which the read gets audited.
    items = [dict(r) | {"pii_visible": db.can_see_pii(g.user, r)} for r in rows]
    conn.close()
    return render_template("cases.html", cases=items, status=status, ctype=ctype)


@app.route("/cases/new", methods=["GET", "POST"])
@login_required
def case_new():
    conn = db.connect()
    if request.method == "POST":
        f = request.form
        ctype = f.get("ctype")
        if ctype not in db.CASE_TYPES:
            abort(400)
        n = conn.execute("SELECT COUNT(*) c FROM cases").fetchone()["c"] + 1
        ref = "%s-%s-%04d" % (ctype[0].upper(), date.today().year, n)
        cur = conn.execute(
            """INSERT INTO cases (ref, ctype, reported_by, camp_report, camp_origin, block,
                                  sex, age_years, disability, holding_location, status,
                                  opened_at, cpims_case_id, pii_enc)
               VALUES (?,?,?,?,?,?,?,?,?,?, 'open', ?,?,?)""",
            (
                ref, ctype, g.user["id"],
                f.get("camp_report") or g.user["camp"], f.get("camp_origin"),
                f.get("block"), f.get("sex"),
                f.get("age_years", type=int), 1 if f.get("disability") else 0,
                f.get("holding_location"), db.now(), f.get("cpims_case_id"),
                db.encrypt_pii({k: f.get(k) for k in db.PII_FIELDS}),
            ),
        )
        conn.execute(
            "INSERT INTO case_events (case_id, user_id, event, note, created_at) VALUES (?,?,?,?,?)",
            (cur.lastrowid, g.user["id"], "opened", "%s report filed" % ctype, db.now()),
        )
        db.log_audit(conn, g.user["id"], "case_create", "case", cur.lastrowid)
        conn.commit()
        conn.close()
        return redirect(url_for("case_detail", case_id=cur.lastrowid))

    camps = db.ref_list(conn, "camp")
    conn.close()
    return render_template("case_form.html", camps=camps)


@app.route("/cases/<int:case_id>")
@login_required
def case_detail(case_id):
    conn = db.connect()
    row = conn.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
    if row is None:
        conn.close()
        abort(404)
    view = db.case_view(conn, g.user, row)
    view["reporter"] = conn.execute(
        "SELECT full_name, agency, camp, phone FROM users WHERE id = ?", (row["reported_by"],)
    ).fetchone()
    events = conn.execute(
        """SELECT e.*, u.full_name FROM case_events e LEFT JOIN users u ON u.id = e.user_id
           WHERE e.case_id = ? ORDER BY e.id""",
        (case_id,),
    ).fetchall()

    # Candidate matches: the opposite kind of report, still open, same sex, close in age.
    other = "found" if row["ctype"] == "missing" else "missing"
    candidates = conn.execute(
        """SELECT * FROM cases
           WHERE ctype = ? AND status = 'open' AND id != ?
             AND (sex = ? OR sex IS NULL OR ? IS NULL)
             AND (age_years IS NULL OR ? IS NULL OR ABS(age_years - ?) <= 2)
           ORDER BY opened_at DESC LIMIT 20""",
        (other, case_id, row["sex"], row["sex"], row["age_years"], row["age_years"] or 0),
    ).fetchall()

    matched = None
    if row["matched_case_id"]:
        matched = conn.execute(
            "SELECT * FROM cases WHERE id = ?", (row["matched_case_id"],)
        ).fetchone()
    conn.close()
    return render_template(
        "case_detail.html", c=view, events=events, candidates=candidates,
        matched=matched, pii_fields=db.PII_FIELDS,
    )


@app.route("/cases/<int:case_id>/action", methods=["POST"])
@login_required
def case_action(case_id):
    action = request.form.get("action")
    if action not in CASE_ACTIONS:
        abort(400)
    status, note = CASE_ACTIONS[action]

    conn = db.connect()
    row = conn.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
    if row is None:
        conn.close()
        abort(404)

    closed_at = db.now() if status in ("reunified", "referred") else None
    other_id = request.form.get("match_id", type=int)

    conn.execute(
        "UPDATE cases SET status = ?, closed_at = ?, matched_case_id = COALESCE(?, matched_case_id) WHERE id = ?",
        (status, closed_at, other_id, case_id),
    )
    conn.execute(
        "INSERT INTO case_events (case_id, user_id, event, note, created_at) VALUES (?,?,?,?,?)",
        (case_id, g.user["id"], status, request.form.get("note") or note, db.now()),
    )
    # A match is a statement about two reports, so both sides move together.
    if other_id:
        conn.execute(
            "UPDATE cases SET status = ?, closed_at = ?, matched_case_id = ? WHERE id = ?",
            (status, closed_at, case_id, other_id),
        )
        conn.execute(
            "INSERT INTO case_events (case_id, user_id, event, note, created_at) VALUES (?,?,?,?,?)",
            (other_id, g.user["id"], status, "Linked with %s" % row["ref"], db.now()),
        )
    db.log_audit(conn, g.user["id"], "case_" + status, "case", case_id)
    conn.commit()
    conn.close()
    return redirect(url_for("case_detail", case_id=case_id))


# --------------------------------------------------------------------------- tasks

@app.route("/tasks", methods=["GET", "POST"])
@login_required
def tasks():
    conn = db.connect()
    if request.method == "POST":
        f = request.form
        conn.execute(
            """INSERT INTO tasks (user_id, tdate, activity_type, action, outcome, stakeholder,
                                  n_children, n_adults, reference, followup, status, next_step)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                g.user["id"], f.get("tdate") or date.today().isoformat(),
                f.get("activity_type"), f.get("action"), f.get("outcome"),
                f.get("stakeholder"), f.get("n_children", 0, type=int),
                f.get("n_adults", 0, type=int), f.get("reference"),
                f.get("followup"), f.get("status"), f.get("next_step"),
            ),
        )
        conn.commit()
        conn.close()
        return redirect(url_for("tasks"))

    rows = conn.execute(
        "SELECT * FROM tasks WHERE user_id = ? ORDER BY tdate DESC, id DESC LIMIT 200",
        (g.user["id"],),
    ).fetchall()
    ctx = {
        "rows": rows,
        "activity_types": db.ref_list(conn, "activity_type"),
        "stakeholders": db.ref_list(conn, "stakeholder"),
        "statuses": db.ref_list(conn, "status"),
    }
    conn.close()
    return render_template("tasks.html", **ctx)


# --------------------------------------------------------------------------- reports

def _requested_period():
    today = date.today()
    return (
        request.args.get("year", today.year, type=int),
        request.args.get("month", today.month, type=int),
    )


@app.route("/reports")
@login_required
def reports_page():
    year, month = _requested_period()
    conn = db.connect()
    data = reports.monthly(conn, year, month)
    conn.close()
    return render_template("reports.html", d=data)


@app.route("/export.xlsx")
@login_required
def export_xlsx():
    year, month = _requested_period()
    conn = db.connect()
    data = reports.monthly(conn, year, month)
    db.log_audit(conn, g.user["id"], "export_report")
    conn.commit()
    conn.close()
    return send_file(
        reports.to_xlsx(data),
        as_attachment=True,
        download_name="CPFP_Monthly_Report_%d-%02d.xlsx" % (year, month),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


if __name__ == "__main__":
    if not db.DB_PATH.exists():
        raise SystemExit("No database yet. Run `python seed.py` first.")
    db.load_key()  # fail now, loudly, rather than on the first case
    # Debug is off by default: this binds to every interface so a phone on the same
    # wifi can reach it, and the Werkzeug debugger would hand that network a console.
    app.run(host="0.0.0.0", port=5000, debug=os.environ.get("CPFP_DEBUG") == "1")
