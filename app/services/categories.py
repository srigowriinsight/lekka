"""Categories for the price list (Master data → Categories), as a tree: a category can have sub-categories."""
from ..extensions import db
from ..models import Category, PriceItem
from .common import LekkaError, audit


def _key(c):
    return (c.sort_order, c.name.lower())


def tree_order(only=None):
    """Categories in tree order (parents before their sub-categories), each with `depth` set."""
    every = Category.query.all()
    by_parent = {}
    for c in every:
        by_parent.setdefault(c.parent_id, []).append(c)
    out = []

    def walk(pid, depth):
        for c in sorted(by_parent.get(pid, []), key=_key):
            c.depth = depth
            out.append(c)
            walk(c.id, depth + 1)

    walk(None, 0)
    if only is None:
        return out
    keep = {c.id for c in only}
    return [c for c in out if c.id in keep]


def label(c) -> str:
    depth = getattr(c, "depth", None)
    if depth is None:
        depth = len(c.ancestors())
    return ("    " * depth) + ("↳ " if depth else "") + c.name


def offered(keep_id=None):
    """Active categories for a drop-down, plus the one already chosen (even if inactive)."""
    return tree_order([c for c in Category.query.all() if c.is_active or (keep_id and c.id == int(keep_id))])


def _name(raw, current=None):
    n = " ".join((raw or "").split())
    if not n:
        raise LekkaError("Enter the category's name.")
    if len(n) > 120:
        raise LekkaError("The name can be at most 120 characters.")
    q = Category.query.filter(db.func.lower(Category.name) == n.lower())
    if current is not None:
        q = q.filter(Category.id != current.id)
    if q.first():
        raise LekkaError(f"'{n}' already exists. One category per name.")
    return n


def _parent(raw, current=None):
    pid = int(raw) if str(raw or "").strip() not in ("", "0") else None
    if pid is None:
        return None
    p = db.session.get(Category, pid)
    if p is None:
        raise LekkaError("That parent category does not exist.")
    if current is not None and pid in current.subtree_ids():
        raise LekkaError(f"'{current.name}' can't go under itself or one of its own sub-categories.")
    return p


def add(form) -> Category:
    name = _name(form.get("name"))
    parent = _parent(form.get("parent_id"))
    top = db.session.query(db.func.max(Category.sort_order)).filter(
        Category.parent_id == (parent.id if parent else None)).scalar()
    c = Category(name=name, parent_id=parent.id if parent else None, sort_order=(top or 0) + 1, is_active=True)
    db.session.add(c)
    db.session.flush()
    audit("category", c.id, "create", c.path(), new={"name": c.name})
    return c


def update(c: Category, form) -> bool:
    name = _name(form.get("name"), current=c)
    parent = _parent(form.get("parent_id"), current=c) if "parent_id" in form else c.parent
    order = c.sort_order
    if str(form.get("sort_order") or "").strip():
        try:
            order = int(form["sort_order"])
        except ValueError:
            raise LekkaError("Order must be a whole number.")
    new_pid = parent.id if parent else None
    if (name, new_pid, order) == (c.name, c.parent_id, c.sort_order):
        return False
    old = c.path()
    c.name, c.parent_id, c.sort_order = name, new_pid, order
    db.session.flush()
    db.session.refresh(c)
    audit("category", c.id, "edit", f"{old} → {c.path()}" if old != c.path() else c.path())
    return True


def set_active(c: Category, active: bool):
    c.is_active = active
    audit("category", c.id, "activate" if active else "deactivate", c.path())


def products_in(c: Category) -> int:
    return PriceItem.query.filter(PriceItem.category_id.in_(c.subtree_ids())).count()


def delete(c: Category):
    if c.children:
        raise LekkaError(f"'{c.name}' has sub-categories; move or remove them first.")
    if PriceItem.query.filter_by(category_id=c.id).first():
        raise LekkaError(f"Some products are in '{c.name}'; give them another category first, or deactivate it.")
    audit("category", c.id, "delete", c.path(), old={"name": c.name})
    db.session.delete(c)
