"""A list of names kept as a tree (A › B › C) in Master data: Categories and Types share this."""
from ..extensions import db
from .common import LekkaError, audit


class TreeList:
    def __init__(self, model, table, noun, subs, uses):
        """noun: 'category' / 'type'; subs: 'sub-categories' / 'sub-types';
        uses: [(column, what)] that point to an entry of this list, e.g. (PriceItem.product_type_id, 'products')."""
        self.model, self.table, self.noun, self.subs, self.uses = model, table, noun, subs, uses

    def tree_order(self, only=None):
        """Every entry, parents before their subs, A–Z at every level, each with `depth` set."""
        by_parent = {}
        for c in self.model.query.all():
            by_parent.setdefault(c.parent_id, []).append(c)
        out = []

        def walk(pid, depth):
            for c in sorted(by_parent.get(pid, []), key=lambda x: x.name.lower()):
                c.depth = depth
                out.append(c)
                walk(c.id, depth + 1)

        walk(None, 0)
        if only is None:
            return out
        keep = {c.id for c in only}
        return [c for c in out if c.id in keep]

    @staticmethod
    def label(c) -> str:
        depth = getattr(c, "depth", None)
        if depth is None:
            depth = len(c.ancestors())
        return ("    " * depth) + ("↳ " if depth else "") + c.name

    def offered(self, keep_id=None):
        """Active entries for a drop-down, plus the one already chosen (even if inactive)."""
        return self.tree_order([c for c in self.model.query.all()
                                if c.is_active or (keep_id and str(c.id) == str(keep_id))])

    def get(self, raw, what=None):
        """The entry chosen on a form: None for blank, or an error if it is gone."""
        if raw in (None, "", "0"):
            return None
        c = db.session.get(self.model, int(raw))
        if c is None:
            raise LekkaError(f"That {what or self.noun} no longer exists. Reload and choose again.")
        return c

    def _name(self, raw, current=None):
        n = " ".join((raw or "").split())
        if not n:
            raise LekkaError(f"Enter the {self.noun}'s name.")
        if len(n) > 120:
            raise LekkaError("The name can be at most 120 characters.")
        q = self.model.query.filter(db.func.lower(self.model.name) == n.lower())
        if current is not None:
            q = q.filter(self.model.id != current.id)
        if q.first():
            raise LekkaError(f"'{n}' already exists. One {self.noun} per name.")
        return n

    def _parent(self, raw, current=None):
        p = self.get(raw, f"parent {self.noun}")
        if p is not None and current is not None and p.id in current.subtree_ids():
            raise LekkaError(f"'{current.name}' can't go under itself or one of its own {self.subs}.")
        return p

    def add(self, form):
        name = self._name(form.get("name"))
        parent = self._parent(form.get("parent_id"))
        M = self.model
        top = db.session.query(db.func.max(M.sort_order)).filter(M.parent_id == (parent.id if parent else None)).scalar()
        c = M(name=name, parent_id=parent.id if parent else None, sort_order=(top or 0) + 1, is_active=True)
        db.session.add(c)
        db.session.flush()
        audit(self.table, c.id, "create", c.path(), new={"name": c.name})
        return c

    def update(self, c, form) -> bool:
        name = self._name(form.get("name"), current=c)
        parent = self._parent(form.get("parent_id"), current=c) if "parent_id" in form else c.parent
        new_pid = parent.id if parent else None
        if (name, new_pid) == (c.name, c.parent_id):
            return False
        old = c.path()
        c.name, c.parent_id = name, new_pid
        db.session.flush()
        db.session.refresh(c)
        audit(self.table, c.id, "edit", f"{old} → {c.path()}" if old != c.path() else c.path())
        return True

    def set_active(self, c, active: bool):
        c.is_active = active
        audit(self.table, c.id, "activate" if active else "deactivate", c.path())

    def usage(self, c, whole_branch=True):
        """How much points to it (and to its subs): {'products': 3, 'items': 5}. Items are counted as top items
        (a split item counts once) and deleted ones are left out."""
        from ..models import CashflowEntry
        ids = c.subtree_ids() if whole_branch else [c.id]
        out = {}
        for col, what in self.uses:
            q = col.class_.query.filter(col.in_(ids))
            if col.class_ is CashflowEntry:
                q = q.filter(CashflowEntry.parent_id.is_(None), CashflowEntry.lifecycle != "DELETED")
            out[what] = q.count()
        return out

    def delete(self, c):
        if c.children:
            raise LekkaError(f"'{c.name}' has {self.subs}; move or remove them first.")
        for col, what in self.uses:
            if col.class_.query.filter(col == c.id).first():
                raise LekkaError(f"Some {what} use '{c.name}'; change them first, or deactivate it.")
        audit(self.table, c.id, "delete", c.path(), old={"name": c.name})
        db.session.delete(c)
