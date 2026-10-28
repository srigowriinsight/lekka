"""Master data: heads (where heads are created and managed), cost centres, counterparties."""
from flask import Blueprint, current_app, render_template, request
from flask_login import login_required

from ..extensions import db
from ..models import CashflowEntry, CostCentre, Counterparty, Head
from ..services.common import LekkaError, audit
from ._util import done, fragment, handles_errors, safe_next

bp = Blueprint("heads", __name__, url_prefix="/master")

KINDS = {"heads": Head, "cost_centres": CostCentre, "counterparties": Counterparty}


@bp.route("/")
@login_required
def index():
    tab = request.args.get("tab", "heads")
    if not current_app.config.get("SHOW_COST_CENTRES"):
        tab = "heads"
    usage = dict(db.session.query(CashflowEntry.head_id, db.func.count(CashflowEntry.id))
                 .filter(CashflowEntry.parent_id.is_(None), CashflowEntry.lifecycle != "DELETED")
                 .group_by(CashflowEntry.head_id).all())
    both = {}
    for hid, direction in (db.session.query(CashflowEntry.head_id, CashflowEntry.direction)
                           .filter(CashflowEntry.lifecycle != "DELETED").distinct()):
        both.setdefault(hid, set()).add(direction)
    return render_template("master.html", tab=tab,
                           heads=Head.query.order_by(Head.sort_order, Head.name).all(),
                           cost_centres=CostCentre.query.order_by(CostCentre.name).all(),
                           counterparties=Counterparty.query.order_by(Counterparty.name).all(),
                           usage=usage, used_for=both)


@bp.route("/heads/new")
@login_required
def new_head_form():
    return fragment("partials/head_new.html", next=safe_next())


def _model(kind):
    m = KINDS.get(kind)
    if m is None:
        raise LekkaError("Unknown list.")
    return m


@bp.route("/<kind>/add", methods=["POST"])
@login_required
@handles_errors
def add(kind):
    m = _model(kind)
    name = (request.form.get("name") or "").strip()
    if not name:
        raise LekkaError("Enter a name.")
    if m.query.filter(db.func.lower(m.name) == name.lower()).first():
        raise LekkaError(f"'{name}' already exists. One head per name; a head works for both inflow and outflow.")
    obj = m(name=name)
    if m is Head:
        obj.sort_order = (db.session.query(db.func.max(Head.sort_order)).scalar() or 0) + 1
    if m is Counterparty:
        obj.kind = request.form.get("kind_of") or "other"
        obj.gstin = (request.form.get("gstin") or "").strip() or None
    db.session.add(obj)
    db.session.flush()
    audit(m.__tablename__, obj.id, "create", name)
    return done(f"'{name}' added.")


@bp.route("/<kind>/<int:obj_id>", methods=["POST"])
@login_required
@handles_errors
def update(kind, obj_id):
    m = _model(kind)
    obj = db.session.get(m, obj_id)
    if obj is None:
        raise LekkaError("Not found.")
    f = request.form
    if f.get("toggle"):
        obj.is_active = not obj.is_active
        audit(m.__tablename__, obj.id, "activate" if obj.is_active else "deactivate")
        return done(f"'{obj.name}' {'activated' if obj.is_active else 'deactivated'}.")
    name = (f.get("name") or "").strip()
    if not name:
        raise LekkaError("Enter a name.")
    clash = m.query.filter(db.func.lower(m.name) == name.lower(), m.id != obj.id).first()
    if clash:
        raise LekkaError(f"'{name}' already exists.")
    old = obj.name
    obj.name = name
    if m is Head and f.get("sort_order", "").strip():
        obj.sort_order = int(f["sort_order"])
    if m is Counterparty:
        obj.kind = f.get("kind_of") or obj.kind
        obj.gstin = (f.get("gstin") or "").strip() or None
    audit(m.__tablename__, obj.id, "edit", f"{old} → {name}")
    return done("Saved.")
