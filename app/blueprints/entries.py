"""Expense and Revenue pages and every popup that acts on an item: add (with linking to a plan),
edit, split, tick, untick, reschedule, cancel; plus daily balance adjustments."""
from datetime import timedelta
from decimal import Decimal

from dateutil.relativedelta import relativedelta
from flask import Blueprint, jsonify, render_template, request, url_for
from flask_login import login_required
from sqlalchemy import or_

from ..extensions import db
from ..models import (ACTIVE, CANCELLED, IN, OUT, AuditLog, BalanceAdjustment, CashflowEntry,
                      CostCentre, Counterparty, Head, RescheduleLog)
from ..services import entries as svc
from ..services import vendors
from ..services.balances import adjustments_between
from ..services.common import LekkaError, inr, money, parse_date, today
from ..services import heads as head_svc
from ..services.heads import tree_order
from ._util import done, fail, fragment, handles_errors, is_fetch, safe_next

bp = Blueprint("entries", __name__)


def _lists(direction=OUT, keep_vendor=None, keep_head=None):
    return dict(
        heads=head_svc.for_direction(direction, keep_head),
        share_heads=head_svc.for_direction(OUT),          # the partner share is an expense
        cost_centres=CostCentre.query.filter_by(is_active=True).order_by(CostCentre.name).all(),
        vendors=vendors.for_direction(direction, keep_vendor),
        share_vendors=vendors.for_direction(OUT),          # the partner is paid, so vendors for the share
    )


def _with(msg, *notes):
    extra = " ".join(f"{n}." for n in notes if n)
    return f"{msg} {extra}".strip()


def _head_from_form(f, direction):
    """Use the chosen head, or create one typed into '+ New head' (it lands in Master data too, as a revenue
    head when typed into a revenue item and an expense head when typed into an expense)."""
    new_name = (f.get("new_head") or "").strip()
    if new_name:
        h = Head.query.filter(Head.name == new_name).first()
        if h is None:
            parent_id = int(f["new_head_parent"]) if f.get("new_head_parent") else None
            parent = db.session.get(Head, parent_id) if parent_id else None
            if parent_id and parent is None:
                raise LekkaError("That parent head does not exist.")
            head_svc.check_kind_under(direction, parent)
            top = db.session.query(db.func.max(Head.sort_order)).filter(Head.parent_id == parent_id).scalar()
            h = Head(name=new_name, parent_id=parent_id, sort_order=(top or 0) + 1, kind=direction)
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
    vendor = request.args.get("vendor") or ""          # "" = all, "none" = no vendor, else an id

    roots = CashflowEntry.query.filter(CashflowEntry.parent_id.is_(None), CashflowEntry.direction == direction,
                                       CashflowEntry.lifecycle != "DELETED")
    if head_id:                              # a head and all its sub-heads
        h = db.session.get(Head, int(head_id))
        roots = roots.filter(CashflowEntry.head_id.in_(h.subtree_ids() if h else [int(head_id)]))
    if vendor == "none":
        roots = roots.filter(CashflowEntry.counterparty_id.is_(None))
    elif vendor.isdigit():
        roots = roots.filter(CashflowEntry.counterparty_id == int(vendor))
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
    tree = tree_order()
    heads = {h.id: h for h in tree}
    pos = {h.id: n for n, h in enumerate(tree)}
    ordered = sorted(groups.items(), key=lambda kv: pos[kv[0]])
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
                           status_filter=status_filter, q=q, head_id=head_id, vendor=vendor, summary=summary,
                           leaf_in=leaf_in, all_vendors=vendors.for_direction(direction, int(vendor) if vendor.isdigit() else None),
                           **_lists(direction))


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
    from ..services import prices as price_list
    return fragment("partials/entry_new.html", direction=direction, head_id=request.args.get("head_id"),
                    date=d, next=safe_next(), products=price_list.for_revenue() if direction == IN else [],
                    **_lists(direction))


@bp.route("/entries/new", methods=["POST"])
@login_required
@handles_errors
def create():
    f = request.form
    direction = f.get("direction")
    head_id = _head_from_form(f, direction)
    planned = parse_date(f.get("planned_date"), "date")
    svc.check_not_before_opening(planned, "The planned date")
    already = bool(f.get("already_settled")) and planned <= today()
    title = (f.get("title") or "").strip()
    amount = f.get("amount")
    share = svc.read_share(f, amount, title, planned) if direction == IN else None
    vendor_id = vendors.from_form(f, direction) or None
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
        msg = f"Linked to '{cand.title}' and ticked."
        if share:
            top = cand.ancestors()[-1] if cand.parent_id else cand
            p = svc.add_partner_share(top, share)
            if not top.is_leaf:
                svc.sync_partner_shares(top, p.amount)
            msg += f" Partner share ₹{inr(p.amount)} added as an expense on {p.planned_date.day} {p.planned_date:%b}."
        return done(msg)
    e = svc.create_entry(direction=direction, head_id=head_id, title=title, amount=amount,
                         planned_date=planned, remarks=f.get("remarks"),
                         cost_centre_id=f.get("cost_centre_id") or None,
                         counterparty_id=vendor_id,
                         reference_no=f.get("reference_no"),
                         settled_on=planned if already else None)
    shared = svc.add_partner_share(e, share) if share else None
    if f.get("then_split"):
        db.session.commit()
        url = url_for("entries.split_form", entry_id=e.id, next=safe_next())
        return jsonify(open=url) if is_fetch() else done(None, url)
    if shared:
        return done(f"'{e.title}' saved (₹{inr(e.amount)} in). Partner share ₹{inr(shared.amount)} added as an "
                    f"expense '{shared.title}' on {shared.planned_date.day} {shared.planned_date:%b}.")
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
    shares = (CashflowEntry.query.filter(CashflowEntry.share_of_id == root.id, CashflowEntry.lifecycle != "DELETED")
              .order_by(CashflowEntry.id).all())
    return fragment("partials/entry_detail.html", e=e, root=root, history=history, moves=moves, titles=titles,
                    shares=shares, next=safe_next())


@bp.route("/entries/<int:entry_id>/edit", methods=["GET", "POST"])
@login_required
@handles_errors
def edit(entry_id):
    e = svc.get_entry(entry_id)
    if request.method == "GET":
        shared = e.direction == IN and e.parent_id is None
        return fragment("partials/entry_edit.html", e=e, next=safe_next(),
                        new_amount=request.args.get("amount"),
                        shares=svc.partner_shares(e) if shared else [],
                        share_pct=format(svc.our_share_pct(e), "f") if shared else None,
                        **_lists(e.direction, e.counterparty_id, e.head_id))
    f = request.form
    asks_share = e.direction == IN and e.parent_id is None and "share_pct" in f
    before = (e.amount, sum((s.amount for s in svc.partner_shares(e)), Decimal("0"))) if asks_share else None
    svc.edit_entry(e, title=f.get("title"), amount=f.get("amount") if e.is_leaf else None,
                   remarks=f.get("remarks", ""), head_id=_head_from_form(f, e.direction) if e.parent_id is None else None,
                   counterparty_id=vendors.from_form(f, e.direction) if e.parent_id is None else None,
                   cost_centre_id=f.get("cost_centre_id") or None,
                   reference_no=f.get("reference_no", ""), expected_version=f.get("version"),
                   follow_shares=not asks_share)
    note = svc.update_partner_share(e, f, before=before) if asks_share else getattr(e, "share_note", None)
    return done(_with("Saved.", note))


@bp.route("/entries/<int:entry_id>/amount", methods=["POST"])
@login_required
@handles_errors
def quick_amount(entry_id):
    """Double-click on a grid figure: change just the amount of one item."""
    e = svc.get_entry(entry_id)
    if e.direction == IN and e.parent_id is None and svc.partner_shares(e):
        # Shared with a partner: ask the share % again (old % as default) in the Edit popup, with the new amount.
        svc.check_version(e, request.form.get("version"))
        svc._check_amount(request.form.get("amount"))
        url = url_for("entries.edit", entry_id=e.id, amount=request.form.get("amount"), next=safe_next())
        return jsonify(open=url) if is_fetch() else done(None, url)
    svc.edit_entry(e, amount=request.form.get("amount"), expected_version=request.form.get("version"))
    return done(_with(f"'{e.title}' changed to ₹{inr(e.amount)}.", getattr(e, "share_note", None)))


@bp.route("/entries/<int:entry_id>/split", methods=["GET", "POST"])
@login_required
@handles_errors
def split_form(entry_id):
    e = svc.get_entry(entry_id)
    if request.method == "GET":
        return fragment("partials/split.html", e=e, share=svc.share_split_defaults(e), next=safe_next())
    f = request.form
    share_dates = f.getlist("part_share_date")
    parts = [{"id": i or None, "title": t, "amount": a, "planned_date": d, "remarks": r,
              "share_date": share_dates[n] if n < len(share_dates) else None}
             for n, (i, t, a, d, r) in enumerate(zip(f.getlist("part_id"), f.getlist("part_title"),
                                                     f.getlist("part_amount"), f.getlist("part_date"),
                                                     f.getlist("part_remarks")))]
    if e.is_leaf:
        svc.split_entry(e, parts, remarks=f.get("remarks"), expected_version=f.get("version"))
    else:
        svc.edit_split(e, parts, new_total=f.get("new_total") or None, remarks=f.get("remarks"))
    return done(_with("Split saved.", getattr(e, "share_note", None)))


@bp.route("/entries/<int:entry_id>/remove-split", methods=["POST"])
@login_required
@handles_errors
def remove_split(entry_id):
    e = svc.remove_split(svc.get_entry(entry_id), request.form.get("remarks"))
    return done(_with("Split removed.", getattr(e, "share_note", None)))


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
    note = getattr(e, "share_note", None)
    return done(f"'{e.title}' cancelled." + (f" {note}." if note else ""))


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
