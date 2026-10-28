"""Expense and Revenue pages and every popup that acts on an item: add (with linking to a plan),
edit, split, tick, untick, reschedule, cancel; plus daily balance adjustments."""
from datetime import timedelta

from dateutil.relativedelta import relativedelta
from flask import Blueprint, jsonify, render_template, request, url_for
from flask_login import login_required
from sqlalchemy import or_

from ..extensions import db
from ..models import (ACTIVE, CANCELLED, IN, OUT, AuditLog, BalanceAdjustment, CashflowEntry,
                      CostCentre, Counterparty, Head, RescheduleLog)
from ..services import entries as svc
from ..services.balances import adjustments_between
from ..services.common import LekkaError, inr, money, parse_date, today
from ._util import done, fail, fragment, handles_errors, is_fetch, safe_next

bp = Blueprint("entries", __name__)


def _lists():
    return dict(
        heads=Head.query.filter_by(is_active=True).order_by(Head.sort_order, Head.name).all(),
        cost_centres=CostCentre.query.filter_by(is_active=True).order_by(CostCentre.name).all(),
        counterparties=Counterparty.query.filter_by(is_active=True).order_by(Counterparty.name).all(),
    )


def _head_from_form(f):
    """Use the chosen head, or create one typed into '+ New head' (it lands in Master data too)."""
    new_name = (f.get("new_head") or "").strip()
    if new_name:
        h = Head.query.filter(Head.name == new_name).first()
        if h is None:
            h = Head(name=new_name, sort_order=(db.session.query(db.func.max(Head.sort_order)).scalar() or 0) + 1)
            db.session.add(h)
            db.session.flush()
        return h.id
    return f.get("head_id")


# ---------------------------------------------------------------- Expense and Revenue pages

def _list_page(direction):
    t = today()
    month_start = t.replace(day=1)
    try:
        a = parse_date(request.args["start"]) if request.args.get("start") else month_start
        b = parse_date(request.args["end"]) if request.args.get("end") else month_start + relativedelta(months=1) - timedelta(days=1)
    except LekkaError:
        a, b = month_start, month_start + relativedelta(months=1) - timedelta(days=1)
    status_filter = request.args.get("status", "all")
    q = (request.args.get("q") or "").strip()
    head_id = request.args.get("head") or None

    roots = CashflowEntry.query.filter(CashflowEntry.parent_id.is_(None), CashflowEntry.direction == direction,
                                       CashflowEntry.lifecycle != "DELETED")
    if head_id:
        roots = roots.filter(CashflowEntry.head_id == int(head_id))
    roots = roots.all()

    def leaf_in(l):
        d = l.settled_on if l.is_settled else l.planned_date
        if status_filter == "overdue":       # every overdue item, whatever its date
            return svc.status(l, t) == "OVERDUE"
        if not (a <= d <= b):
            return False
        s = svc.status(l, t)
        return {"all": True, "pending": s in ("PLANNED", "DUE_SOON", "OVERDUE"),
                "settled": s == "SETTLED", "cancelled": s == "CANCELLED"}.get(status_filter, True)

    def matches(root):
        if not q:
            return True
        ql = q.lower()
        for n in [root] + root.descendants():
            if ql in (n.title or "").lower() or ql in (n.remarks or "").lower() or ql in (n.reference_no or "").lower():
                return True
        return False

    shown = [r for r in roots if any(leaf_in(l) for l in r.leaves()) and matches(r)]
    groups = {}
    for r in shown:
        groups.setdefault(r.head_id, []).append(r)
    heads = {h.id: h for h in Head.query.all()}
    ordered = sorted(groups.items(), key=lambda kv: (heads[kv[0]].sort_order, heads[kv[0]].name.lower()))
    groups = [(heads[hid], sorted(rs, key=lambda r: (r.date_range()[0] or r.planned_date or t, r.id)))
              for hid, rs in ordered]

    all_leaves = [l for r in roots for l in r.leaves() if l.direction == direction]
    m_a, m_b = month_start, month_start + relativedelta(months=1) - timedelta(days=1)
    summary = dict(
        pending=sum((l.amount for l in all_leaves if not l.is_settled and not l.is_cancelled
                     and m_a <= l.planned_date <= m_b and l.planned_date >= t), 0),
        overdue=sum((l.amount for l in all_leaves if svc.status(l, t) == "OVERDUE"), 0),
        settled=sum((l.amount for l in all_leaves if l.is_settled and m_a <= l.settled_on <= m_b), 0),
    )
    return render_template("entries.html", direction=direction, groups=groups, a=a, b=b,
                           status_filter=status_filter, q=q, head_id=head_id, summary=summary,
                           leaf_in=leaf_in, **_lists())


@bp.route("/expenses")
@login_required
def expenses():
    return _list_page(OUT)


@bp.route("/revenue")
@login_required
def revenue():
    return _list_page(IN)


# ---------------------------------------------------------------- add, with linking to a plan

@bp.route("/entries/new", methods=["GET"])
@login_required
def new_form():
    direction = request.args.get("direction", OUT)
    d = request.args.get("date") or today().isoformat()
    return fragment("partials/entry_new.html", direction=direction, head_id=request.args.get("head_id"),
                    date=d, next=safe_next(), **_lists())


@bp.route("/entries/new", methods=["POST"])
@login_required
@handles_errors
def create():
    f = request.form
    direction = f.get("direction")
    head_id = _head_from_form(f)
    planned = parse_date(f.get("planned_date"), "date")
    svc.check_not_before_opening(planned, "The planned date")
    already = bool(f.get("already_settled")) and planned <= today()
    title = (f.get("title") or "").strip()
    amount = f.get("amount")
    if already and f.get("link_choice") is None:
        svc._check_amount(amount)
        svc._check_title(title)
        cands = svc.link_candidates(head_id, direction, amount, planned)
        if cands:
            db.session.flush()
            html = render_template("partials/link_prompt.html", form=f, head_id=head_id, cands=cands,
                                   amount=money(amount), on_date=planned, next=safe_next())
            db.session.commit()   # keep a head created inline
            return jsonify(html=html) if is_fetch() else fragment("partials/link_prompt.html", form=f,
                                                                    head_id=head_id, cands=cands, amount=money(amount),
                                                                    on_date=planned, next=safe_next())
    choice = f.get("link_choice")
    if already and choice and choice != "none":
        cand = svc.get_entry(choice)
        svc.link_and_settle(cand, amount, planned, f.get("remarks"), extra_mode=f.get("extra_mode") or None)
        return done(f"Linked to '{cand.title}' and ticked.")
    e = svc.create_entry(direction=direction, head_id=head_id, title=title, amount=amount,
                         planned_date=planned, remarks=f.get("remarks"),
                         cost_centre_id=f.get("cost_centre_id") or None,
                         counterparty_id=f.get("counterparty_id") or None,
                         reference_no=f.get("reference_no"),
                         settled_on=planned if already else None)
    if f.get("then_split"):
        db.session.commit()
        url = url_for("entries.split_form", entry_id=e.id, next=safe_next())
        return jsonify(open=url) if is_fetch() else done(None, url)
    return done(f"'{e.title}' saved.")


# ---------------------------------------------------------------- one item

@bp.route("/entries/<int:entry_id>")
@login_required
def detail(entry_id):
    e = svc.get_entry(entry_id, allow_deleted=True)
    root = db.session.get(CashflowEntry, e.root_id)
    ids = [n.id for n in [root] + root.descendants()]
    history = (AuditLog.query.filter(AuditLog.table_name == "cashflow_entry", AuditLog.record_id.in_(ids))
               .order_by(AuditLog.at.desc(), AuditLog.id.desc()).all())
    moves = RescheduleLog.query.filter(RescheduleLog.entry_id.in_(ids)).order_by(RescheduleLog.at.desc()).all()
    titles = {n.id: n.title for n in [root] + root.descendants()}
    return fragment("partials/entry_detail.html", e=e, root=root, history=history, moves=moves, titles=titles,
                    next=safe_next())


@bp.route("/entries/<int:entry_id>/edit", methods=["GET", "POST"])
@login_required
@handles_errors
def edit(entry_id):
    e = svc.get_entry(entry_id)
    if request.method == "GET":
        return fragment("partials/entry_edit.html", e=e, next=safe_next(), **_lists())
    f = request.form
    svc.edit_entry(e, title=f.get("title"), amount=f.get("amount") if e.is_leaf else None,
                   remarks=f.get("remarks", ""), head_id=_head_from_form(f) if e.parent_id is None else None,
                   counterparty_id=f.get("counterparty_id") or None, cost_centre_id=f.get("cost_centre_id") or None,
                   reference_no=f.get("reference_no", ""), expected_version=f.get("version"))
    return done("Saved.")


@bp.route("/entries/<int:entry_id>/amount", methods=["POST"])
@login_required
@handles_errors
def quick_amount(entry_id):
    """Double-click on a grid figure: change just the amount of one item."""
    e = svc.get_entry(entry_id)
    svc.edit_entry(e, amount=request.form.get("amount"), expected_version=request.form.get("version"))
    return done(f"'{e.title}' changed to ₹{inr(e.amount)}.")


@bp.route("/entries/<int:entry_id>/split", methods=["GET", "POST"])
@login_required
@handles_errors
def split_form(entry_id):
    e = svc.get_entry(entry_id)
    if request.method == "GET":
        return fragment("partials/split.html", e=e, next=safe_next())
    f = request.form
    parts = [{"id": i or None, "title": t, "amount": a, "planned_date": d, "remarks": r}
             for i, t, a, d, r in zip(f.getlist("part_id"), f.getlist("part_title"), f.getlist("part_amount"),
                                      f.getlist("part_date"), f.getlist("part_remarks"))]
    if e.is_leaf:
        svc.split_entry(e, parts, remarks=f.get("remarks"), expected_version=f.get("version"))
    else:
        svc.edit_split(e, parts, new_total=f.get("new_total") or None, remarks=f.get("remarks"))
    return done("Split saved.")


@bp.route("/entries/<int:entry_id>/remove-split", methods=["POST"])
@login_required
@handles_errors
def remove_split(entry_id):
    svc.remove_split(svc.get_entry(entry_id), request.form.get("remarks"))
    return done("Split removed.")


@bp.route("/entries/<int:entry_id>/settle", methods=["GET", "POST"])
@login_required
@handles_errors
def settle(entry_id):
    e = svc.get_entry(entry_id)
    if request.method == "GET":
        return fragment("partials/settle.html", e=e, plans=svc.open_plans_like(e), next=safe_next())
    f = request.form
    choice = f.get("when", "today")
    if choice == "planned":
        d = e.planned_date
    elif choice == "today":
        d = today()
    else:
        d = parse_date(f.get("settled_on"), "settled date")
    link_to = f.get("link_to")
    if link_to and link_to != "none":
        plan = svc.get_entry(link_to)
        svc.tick_as_plan(e, plan, d, f.get("remarks"), expected_version=f.get("version"))
        return done(f"Ticked '{plan.title}' with this money; the extra entry '{e.title}' was removed so it "
                    f"isn't counted twice.")
    svc.settle(e, d, f.get("remarks"), expected_version=f.get("version"))
    return done(f"'{e.title}' ticked.")


@bp.route("/entries/settle-many", methods=["POST"])
@login_required
@handles_errors
def settle_many():
    ids = request.form.getlist("ids")
    if not ids:
        raise LekkaError("Select at least one item.")
    d = today() if request.form.get("when", "today") == "today" else parse_date(request.form.get("settled_on"))
    for i in ids:
        svc.settle(svc.get_entry(i), d, request.form.get("remarks"))
    return done(f"{len(ids)} item(s) ticked.")


@bp.route("/entries/<int:entry_id>/unsettle", methods=["POST"])
@login_required
@handles_errors
def unsettle(entry_id):
    e = svc.get_entry(entry_id)
    svc.unsettle(e, request.form.get("remarks"), expected_version=request.form.get("version"))
    return done(f"'{e.title}' unticked.")


@bp.route("/entries/<int:entry_id>/reschedule", methods=["GET", "POST"])
@login_required
@handles_errors
def reschedule(entry_id):
    e = svc.get_entry(entry_id)
    if request.method == "GET":
        return fragment("partials/reschedule.html", e=e, next=safe_next())
    new_date = parse_date(request.form.get("new_date"), "new date")
    svc.reschedule(e, new_date, request.form.get("remarks"), expected_version=request.form.get("version"))
    return done(f"'{e.title}' moved to {new_date.day} {new_date:%b %Y}.")


@bp.route("/entries/reschedule-many", methods=["POST"])
@login_required
@handles_errors
def reschedule_many():
    ids = request.form.getlist("ids")
    if not ids:
        raise LekkaError("Select at least one item.")
    new_date = parse_date(request.form.get("new_date"), "new date")
    for i in ids:
        svc.reschedule(svc.get_entry(i), new_date, request.form.get("remarks"))
    return done(f"{len(ids)} item(s) moved to {new_date.day} {new_date:%b %Y}.")


@bp.route("/entries/<int:entry_id>/cancel", methods=["GET", "POST"])
@login_required
@handles_errors
def cancel(entry_id):
    e = svc.get_entry(entry_id)
    if request.method == "GET":
        return fragment("partials/cancel.html", e=e, targets=svc.move_targets(e) if e.is_leaf else [],
                        next=safe_next())
    f = request.form
    svc.cancel(e, f.get("reason"), mode=f.get("mode") or None, target_id=f.get("target_id") or None,
               expected_version=f.get("version"))
    return done(f"'{e.title}' cancelled.")


@bp.route("/entries/<int:entry_id>/delete", methods=["POST"])
@login_required
@handles_errors
def delete(entry_id):
    e = svc.get_entry(entry_id)
    svc.delete_entry(e, expected_version=request.form.get("version"))
    return done(f"'{e.title}' deleted.")


# ---------------------------------------------------------------- daily balance adjustments

@bp.route("/adjustments")
@login_required
def adjustments():
    a = parse_date(request.args["a"]) if request.args.get("a") else today().replace(day=1)
    b = parse_date(request.args["b"]) if request.args.get("b") else today()
    items = sorted(adjustments_between(a, b), key=lambda x: (x.adj_date, x.id))
    return fragment("partials/adjustments.html", items=items, a=a, b=b, next=safe_next(),
                    default_date=min(b, today()))


@bp.route("/adjustments/new", methods=["POST"])
@login_required
@handles_errors
def adjustment_new():
    f = request.form
    amt = money(f.get("amount"))
    if f.get("sign") == "-" and amt > 0:
        amt = -amt
    svc.add_adjustment(f.get("adj_date"), amt, f.get("remarks"))
    return done("Adjustment saved.")


@bp.route("/adjustments/<int:adj_id>/edit", methods=["POST"])
@login_required
@handles_errors
def adjustment_edit(adj_id):
    adj = db.session.get(BalanceAdjustment, adj_id)
    if adj is None:
        raise LekkaError("That adjustment no longer exists.")
    f = request.form
    if f.get("delete"):
        svc.delete_adjustment(adj)
        return done("Adjustment removed.")
    svc.edit_adjustment(adj, amount=f.get("amount"), remarks=f.get("remarks", ""))
    return done("Adjustment saved.")
