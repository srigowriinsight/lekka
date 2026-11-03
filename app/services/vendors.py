"""Parties (Master data): one list of everyone we pay or get paid by, stored in the `counterparty` table.
Every party is offered on both expenses and revenue; choosing one on an item is optional. (The table's `kind`
column is kept but no longer asked: new parties are saved as 'both'.)"""
from types import SimpleNamespace

from ..extensions import db
from ..models import IN, CashflowEntry, Counterparty
from .common import LekkaError, audit, snapshot

KINDS = [("vendor", "Vendor"), ("customer", "Customer"), ("both", "Both")]
LABEL = dict(KINDS) | {"other": "Both"}
FIELDS = ["name", "kind", "gstin", "is_active"]


def kind_of(c) -> str:
    return "both" if c.kind in ("other", None) else c.kind


def for_direction(direction=None, keep_id=None):
    """Every active party, by name (the same list on expenses and revenue). The one already on an item is
    always offered, even if it was deactivated since."""
    return [SimpleNamespace(id=c.id, name=c.name, is_active=c.is_active, fits=True)
            for c in Counterparty.query.order_by(Counterparty.name).all()
            if c.is_active or (keep_id and c.id == int(keep_id))]


def _clean(form, current=None):
    name = " ".join((form.get("name") or "").split())
    if not name:
        raise LekkaError("Enter the party's name.")
    if len(name) > 160:
        raise LekkaError("The name can be at most 160 characters.")
    q = Counterparty.query.filter(db.func.lower(Counterparty.name) == name.lower())
    if current is not None:
        q = q.filter(Counterparty.id != current.id)
    if q.first():
        raise LekkaError(f"'{name}' is already in the party list.")
    kind = "both" if current is None else current.kind
    gstin = (form.get("gstin") or "").strip().upper() or None
    if gstin and len(gstin) > 20:
        raise LekkaError("The GSTIN can be at most 20 characters.")
    return {"name": name, "kind": kind, "gstin": gstin}


def add(form) -> Counterparty:
    v = _clean(form)
    c = Counterparty(**v, is_active=True)
    db.session.add(c)
    db.session.flush()
    audit("counterparty", c.id, "create", c.name, new=snapshot(c, FIELDS))
    return c


def update(c: Counterparty, form) -> bool:
    v = _clean(form, current=c)
    if all(getattr(c, k) == val for k, val in v.items()):
        return False
    old = snapshot(c, FIELDS)
    for k, val in v.items():
        setattr(c, k, val)
    audit("counterparty", c.id, "edit", f"{old['name']} → {c.name}" if old["name"] != c.name else c.name,
          old=old, new=snapshot(c, FIELDS))
    return True


def usage() -> dict:
    """How many items (top items, not deleted) each party is on: {id: {"IN": n, "OUT": n}}."""
    out = {}
    for cid, d, n in (db.session.query(CashflowEntry.counterparty_id, CashflowEntry.direction,
                                       db.func.count(CashflowEntry.id))
                      .filter(CashflowEntry.parent_id.is_(None), CashflowEntry.lifecycle != "DELETED",
                              CashflowEntry.counterparty_id.isnot(None))
                      .group_by(CashflowEntry.counterparty_id, CashflowEntry.direction).all()):
        out.setdefault(cid, {})[d] = n
    return out


def delete(c: Counterparty):
    """Only a party no item uses (not even a deleted one) can be removed; otherwise deactivate it."""
    if CashflowEntry.query.filter(CashflowEntry.counterparty_id == c.id).first():
        raise LekkaError(f"'{c.name}' is on some items, so it can't be removed. Deactivate it instead: "
                         f"it stays on those items but isn't offered for new ones.")
    audit("counterparty", c.id, "delete", c.name, old=snapshot(c, FIELDS))
    db.session.delete(c)


def from_form(form, direction, field="counterparty_id", new_field="new_vendor"):
    """The party chosen on an item form: '' / missing = none, or a name typed into '+ New party', which is
    added to the list too. Returns an id, "" or None (field absent)."""
    new_name = " ".join((form.get(new_field) or "").split())
    if new_name:
        c = Counterparty.query.filter(db.func.lower(Counterparty.name) == new_name.lower()).first()
        if c is None:
            c = add({"name": new_name})
        return c.id
    if field not in form:
        return None
    raw = (form.get(field) or "").strip()
    if raw and db.session.get(Counterparty, int(raw)) is None:
        raise LekkaError("That party no longer exists. Reload and choose again.")
    return int(raw) if raw else ""
