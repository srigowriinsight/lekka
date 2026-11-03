"""Master data: heads (where heads are created and managed), vendors, the price list, cost centres."""
from flask import Blueprint, current_app, render_template, request
from flask_login import login_required

from ..extensions import db
from ..models import CashflowEntry, Category, CostCentre, Counterparty, Head, PriceItem
from ..services import prices as price_list
from ..services import vendors as vendor_list
from ..services import categories as cat_svc
from ..services.common import LekkaError, audit
from ..services.heads import KINDS as HEAD_KINDS, check_kind_under, read_kind, set_kind, set_parent, tree_order
from ._util import done, fragment, handles_errors, safe_next

bp = Blueprint("heads", __name__, url_prefix="/master")

KINDS = {"heads": Head, "cost_centres": CostCentre}


@bp.route("/")
@login_required
def index():
    tab = request.args.get("tab", "heads")
    if tab in ("counterparties", "vendors"):
        tab = "parties"
    if tab not in ("heads", "parties", "prices", "categories") and not current_app.config.get("SHOW_COST_CENTRES"):
        tab = "heads"
    usage = dict(db.session.query(CashflowEntry.head_id, db.func.count(CashflowEntry.id))
                 .filter(CashflowEntry.parent_id.is_(None), CashflowEntry.lifecycle != "DELETED")
                 .group_by(CashflowEntry.head_id).all())
    both = {}
    for hid, direction in (db.session.query(CashflowEntry.head_id, CashflowEntry.direction)
                           .filter(CashflowEntry.lifecycle != "DELETED").distinct()):
        both.setdefault(hid, set()).add(direction)
    return render_template("master.html", tab=tab,
                           heads=tree_order(), under=request.args.get("under", ""), head_kinds=HEAD_KINDS,
                           cost_centres=CostCentre.query.order_by(CostCentre.name).all(),
                           vendors=Counterparty.query.order_by(Counterparty.is_active.desc(), Counterparty.name).all(),
                           vendor_kinds=vendor_list.KINDS, vendor_kind=vendor_list.kind_of,
                           vendor_usage=vendor_list.usage() if tab == "parties" else {},
                           usage=usage, used_for=both,
                           prices=_price_rows(), gst_rates=price_list.GST_RATES,
                           price_cat=request.args.get("cat", ""),
                           categories=cat_svc.tree_order() if tab in ("categories", "prices") else [],
                           cat_choices=cat_svc.offered(), cat_label=cat_svc.label,
                           cat_products=({c.id: cat_svc.products_in(c) for c in cat_svc.tree_order()}
                                         if tab == "categories" else {}))


def _price_rows():
    """Price list rows, optionally only one category (with its sub-categories): ?cat=<id>, or ?cat=none."""
    q = PriceItem.query
    cat = request.args.get("cat", "")
    if cat == "none":
        q = q.filter(PriceItem.category_id.is_(None))
    elif cat.isdigit():
        c = db.session.get(Category, int(cat))
        q = q.filter(PriceItem.category_id.in_(c.subtree_ids() if c else [int(cat)]))
    return q.order_by(PriceItem.product).all()


@bp.route("/heads/new")
@login_required
def new_head_form():
    under = request.args.get("under")
    parent = db.session.get(Head, int(under)) if under and under.isdigit() else None
    return fragment("partials/head_new.html", heads=tree_order(Head.query.filter_by(is_active=True).all()),
                    parent=parent, head_kinds=HEAD_KINDS, default_kind=request.args.get("for") or "",
                    next=safe_next())


# ---------------------------------------------------------------- vendors

def _vendor(vendor_id):
    c = db.session.get(Counterparty, vendor_id)
    if c is None:
        raise LekkaError("That party is no longer in the list. Reload the page.")
    return c


@bp.route("/vendors/add", methods=["POST"])
@login_required
@handles_errors
def add_vendor():
    c = vendor_list.add(request.form)
    return done(f"'{c.name}' added to the party list.")


@bp.route("/vendors/<int:vendor_id>", methods=["POST"])
@login_required
@handles_errors
def update_vendor(vendor_id):
    c = _vendor(vendor_id)
    if request.form.get("toggle"):
        c.is_active = not c.is_active
        audit("counterparty", c.id, "activate" if c.is_active else "deactivate", c.name)
        return done(f"'{c.name}' {'activated' if c.is_active else 'deactivated'}.")
    changed = vendor_list.update(c, request.form)
    return done(f"'{c.name}' saved." if changed else "Nothing changed.")


@bp.route("/vendors/<int:vendor_id>/delete", methods=["POST"])
@login_required
@handles_errors
def delete_vendor(vendor_id):
    c = _vendor(vendor_id)
    name = c.name
    vendor_list.delete(c)
    return done(f"'{name}' removed from the party list.")


# ---------------------------------------------------------------- categories

def _category(cat_id):
    c = db.session.get(Category, cat_id)
    if c is None:
        raise LekkaError("That category no longer exists. Reload the page.")
    return c


@bp.route("/categories/add", methods=["POST"])
@login_required
@handles_errors
def add_category():
    c = cat_svc.add(request.form)
    return done(f"'{c.path()}' added.")


@bp.route("/categories/<int:cat_id>", methods=["POST"])
@login_required
@handles_errors
def update_category(cat_id):
    c = _category(cat_id)
    if request.form.get("toggle"):
        cat_svc.set_active(c, not c.is_active)
        return done(f"'{c.name}' {'activated' if c.is_active else 'deactivated'}.")
    changed = cat_svc.update(c, request.form)
    return done("Saved." if changed else "Nothing changed.")


@bp.route("/categories/<int:cat_id>/delete", methods=["POST"])
@login_required
@handles_errors
def delete_category(cat_id):
    c = _category(cat_id)
    name = c.name
    cat_svc.delete(c)
    return done(f"'{name}' removed.")


# ---------------------------------------------------------------- price list

def _price(item_id):
    p = db.session.get(PriceItem, item_id)
    if p is None:
        raise LekkaError("That product is no longer in the price list. Reload the page.")
    return p


@bp.route("/prices/add", methods=["POST"])
@login_required
@handles_errors
def add_price():
    p = price_list.add(request.form)
    return done(f"'{p.product}' added to the price list: {price_list.summary(p)}.")


@bp.route("/prices/<int:item_id>", methods=["POST"])
@login_required
@handles_errors
def update_price(item_id):
    p = _price(item_id)
    changed = price_list.update(p, request.form)
    return done(f"'{p.product}' saved: {price_list.summary(p)}." if changed else "Nothing changed.")


@bp.route("/prices/<int:item_id>/delete", methods=["POST"])
@login_required
@handles_errors
def delete_price(item_id):
    p = _price(item_id)
    name = p.product
    price_list.delete(p)
    return done(f"'{name}' removed from the price list.")


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
        parent_id = request.form.get("parent_id") or None
        if parent_id and db.session.get(Head, int(parent_id)) is None:
            raise LekkaError("That parent head does not exist.")
        obj.parent_id = int(parent_id) if parent_id else None
        parent = db.session.get(Head, obj.parent_id) if obj.parent_id else None
        obj.kind = parent.kind if parent is not None and parent.kind != "BOTH" else read_kind(request.form.get("head_kind"))
        check_kind_under(obj.kind, parent)
        siblings = db.session.query(db.func.max(Head.sort_order)).filter(Head.parent_id == obj.parent_id).scalar()
        obj.sort_order = (siblings or 0) + 1
    db.session.add(obj)
    db.session.flush()
    audit(m.__tablename__, obj.id, "create", name)
    if m is Head and obj.parent_id:
        return done(f"'{name}' added under '{db.session.get(Head, obj.parent_id).name}'.")
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
        try:
            obj.sort_order = int(f["sort_order"])
        except ValueError:
            raise LekkaError("Order must be a whole number.")
    if m is Head and "parent_id" in f:
        set_parent(obj, f.get("parent_id"))
    if m is Head and f.get("head_kind"):
        set_kind(obj, f.get("head_kind"))
    audit(m.__tablename__, obj.id, "edit", f"{old} → {name}")
    return done("Saved.")
