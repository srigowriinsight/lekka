"""The single account (opening balance), bank balance checks, and the audit log."""
from flask import Blueprint, current_app, render_template, request
from flask_login import login_required

from ..extensions import db
from ..models import AuditLog, BalanceSnapshot
from ..services.balances import closing_on
from ..services.common import LekkaError, audit, money, parse_date, snapshot, today, user_id
from ..services.entries import get_account
from ._util import done, handles_errors

bp = Blueprint("accounts", __name__)


@bp.route("/account")
@login_required
def index():
    acc = get_account()
    db.session.commit()
    snaps = BalanceSnapshot.query.order_by(BalanceSnapshot.as_of.desc()).all()
    rows = [(s, closing_on(s.as_of)) for s in snaps]
    return render_template("account.html", acc=acc, rows=rows, today_balance=closing_on(today()))


@bp.route("/account", methods=["POST"])
@login_required
@handles_errors
def save():
    acc = get_account()
    old = snapshot(acc, ["name", "opening_balance", "opening_date"])
    acc.name = (request.form.get("name") or acc.name).strip()
    acc.opening_balance = money(request.form.get("opening_balance"))
    acc.opening_date = parse_date(request.form.get("opening_date"), "opening date")
    audit("account", acc.id, "edit", None, old=old, new=snapshot(acc, ["name", "opening_balance", "opening_date"]))
    return done("Account saved.")


@bp.route("/account/snapshot", methods=["POST"])
@login_required
@handles_errors
def add_snapshot():
    d = parse_date(request.form.get("as_of"), "date")
    if d > today():
        raise LekkaError("A bank balance check is for today or earlier.")
    s = BalanceSnapshot.query.filter_by(as_of=d).first() or BalanceSnapshot(as_of=d, created_by=user_id())
    s.balance = money(request.form.get("balance"))
    s.remarks = (request.form.get("remarks") or "").strip() or None
    db.session.add(s)
    return done("Bank balance recorded.")


@bp.route("/history/reset", methods=["GET", "POST"])
@login_required
@handles_errors
def reset_period():
    """Remove everything entered in a time window: preview first, then confirm."""
    from flask import jsonify
    from ..services import reset as rs
    from ..services.history import when
    from ._util import fragment, is_fetch
    from datetime import datetime, timedelta
    if request.method == "GET":
        now = datetime.now(rs.ZoneInfo(current_app.config.get("TIMEZONE", "Asia/Kolkata")))
        return fragment("partials/reset_period.html", step="ask",
                        start=now.strftime("%Y-%m-%dT00:00"), end=now.strftime("%Y-%m-%dT%H:%M"))
    f = request.form
    a, b, la, lb = rs.parse_window(f.get("start"), f.get("end"))
    label = f"{la.day} {la:%b %Y %I:%M %p} – {lb.day} {lb:%b %Y %I:%M %p}".replace(" 0", " ")
    if f.get("step") == "run":
        if f.get("sure") != "1":
            raise LekkaError("Tick the box to confirm the reset.")
        summary = rs.run(a, b, label)
        return done(summary + ".")
    found = rs.find(a, b)
    html = render_template("partials/reset_period.html", step="preview", found=found, label=label,
                           start=f.get("start"), end=f.get("end"), when=when)
    return jsonify(html=html) if is_fetch() else html


@bp.route("/history")
@login_required
def history():
    from ..services.history import KIND_TABLES, WHAT_ACTIONS, build, utc_bounds
    args = request.args
    try:
        page = max(1, int(args.get("page", 1)))
    except ValueError:
        page = 1
    kind, what, text = args.get("kind", ""), args.get("what", ""), (args.get("q") or "").strip()
    try:
        d_from = parse_date(args["from"]) if args.get("from") else None
        d_to = parse_date(args["to"]) if args.get("to") else None
    except LekkaError:
        d_from = d_to = None
    q = AuditLog.query
    if kind in KIND_TABLES:
        q = q.filter(AuditLog.table_name.in_(KIND_TABLES[kind]))
    if what in WHAT_ACTIONS:
        q = q.filter(AuditLog.action.in_(WHAT_ACTIONS[what]))
    a, b = utc_bounds(d_from, d_to)
    if a:
        q = q.filter(AuditLog.at >= a)
    if b:
        q = q.filter(AuditLog.at < b)
    q = q.order_by(AuditLog.at.desc(), AuditLog.id.desc())
    size = 2000 if text else 200          # a text search looks further back on one page
    raw = q.offset((page - 1) * size).limit(size + 1).all()
    rows = build(raw[:size])
    if text:
        t = text.lower()
        rows = [r for r in rows if t in (r.name or "").lower() or t in (r.remarks or "").lower()
                or t in r.what.lower()]
    filters = {k: v for k, v in {"kind": kind, "what": what, "q": text,
                                 "from": d_from.isoformat() if d_from else "",
                                 "to": d_to.isoformat() if d_to else ""}.items() if v}
    return render_template("history.html", rows=rows, page=page, more=len(raw) > size, f=filters,
                           kinds=[("", "Everything"), ("items", "Items"), ("templates", "Recurring templates"),
                                  ("account", "Account and adjustments"), ("heads", "Heads")],
                           whats=[("", "Any change"), ("added", "Added"), ("ticked", "Ticked"),
                                  ("unticked", "Unticked"), ("rescheduled", "Rescheduled"), ("split", "Split"),
                                  ("cancelled", "Cancelled or ended"), ("changed", "Changed"),
                                  ("deleted", "Deleted")])
