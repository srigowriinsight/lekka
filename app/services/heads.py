"""Heads as a tree (like the OPEX sheet): ordering, labels and moving a head under another."""
from ..extensions import db
from ..models import CashflowEntry, Head, RecurringTemplate
from .common import LekkaError, audit

KINDS = [("OUT", "Expense"), ("IN", "Revenue"), ("BOTH", "Both")]
KIND_LABEL = dict(KINDS)


def _key(h):
    return (h.sort_order, h.name.lower())


def tree_order(heads=None):
    """Heads in tree order (each parent followed by its sub-heads), with `depth` set on each.
    Pass a filtered list (e.g. active only) to keep just those, still in tree order."""
    every = Head.query.all()
    by_parent = {}
    for h in every:
        by_parent.setdefault(h.parent_id, []).append(h)
    ordered = []

    def walk(parent_id, depth):
        for h in sorted(by_parent.get(parent_id, []), key=_key):
            h.depth = depth
            ordered.append(h)
            walk(h.id, depth + 1)

    walk(None, 0)
    seen = {h.id for h in ordered}
    for h in sorted(every, key=_key):          # safety: anything unreachable goes last, top level
        if h.id not in seen:
            h.depth = 0
            ordered.append(h)
    if heads is None:
        return ordered
    keep = {h.id for h in heads}
    return [h for h in ordered if h.id in keep]


def for_direction(direction, keep_id=None):
    """Active heads an item of this direction may use (expense heads + Both, or revenue heads + Both), in tree
    order. The head already on the item is always kept, so an older item still shows its head."""
    hs = [h for h in Head.query.all()
          if (h.is_active and h.fits(direction)) or (keep_id and h.id == int(keep_id))]
    return tree_order(hs)


def read_kind(raw):
    k = (raw or "").strip().upper()
    if k not in KIND_LABEL:
        raise LekkaError("Choose whether this is a revenue head, an expense head, or both.")
    return k


def check_kind_under(kind, parent):
    """A sub-head of a revenue-only (or expense-only) head is of the same kind; under a Both head, any."""
    if parent is not None and parent.kind != "BOTH" and kind != parent.kind:
        raise LekkaError(f"'{parent.name}' is {'a revenue' if parent.kind == 'IN' else 'an expense'} head, so its "
                         f"sub-heads are {'revenue' if parent.kind == 'IN' else 'expense'} heads too.")


def _used_for(head_ids, direction):
    q1 = CashflowEntry.query.filter(CashflowEntry.head_id.in_(head_ids), CashflowEntry.direction == direction,
                                    CashflowEntry.lifecycle != "DELETED").first()
    q2 = RecurringTemplate.query.filter(RecurringTemplate.head_id.in_(head_ids),
                                        RecurringTemplate.direction == direction).first()
    return q1 or q2


def set_kind(h: Head, kind):
    """Change a head to revenue / expense / both. Its sub-heads follow a one-sided head. Refused while items or
    recurring templates of the other side still use it (move them to another head first)."""
    kind = read_kind(kind)
    if kind == h.kind:
        return False
    if h.parent is not None:
        check_kind_under(kind, h.parent)
    tree = h.subtree_ids()
    if kind != "BOTH":
        other = "IN" if kind == "OUT" else "OUT"
        hit = _used_for(tree, other)
        if hit:
            what = "revenue" if other == "IN" else "expense"
            raise LekkaError(f"'{h.name}' (or a sub-head of it) still has {what} items or recurring templates, so it "
                             f"can't become {'an expense' if kind == 'OUT' else 'a revenue'} head. Leave it as Both, "
                             f"or move those to another head first.")
    old = h.kind
    h.kind = kind
    if kind != "BOTH":
        for sid in tree[1:]:
            sub = db.session.get(Head, sid)
            sub.kind = kind
    audit("head", h.id, "edit", f"{h.name}: {KIND_LABEL[old]} → {KIND_LABEL[kind]}"
          + (" (with its sub-heads)" if kind != "BOTH" and len(tree) > 1 else ""))
    return True


def label(h) -> str:
    """Indented name for drop-down lists: sub-heads sit under their parent."""
    depth = getattr(h, "depth", None)
    if depth is None:
        depth = len(h.ancestors())
    return ("    " * depth) + ("↳ " if depth else "") + h.name


def set_parent(h: Head, parent_id):
    """Move a head under another head (or to the top level with None). A head can't go under itself or
    under one of its own sub-heads."""
    parent_id = int(parent_id) if parent_id not in (None, "", "0") else None
    if parent_id == h.parent_id:
        return False
    if parent_id is not None:
        parent = db.session.get(Head, parent_id)
        if parent is None:
            raise LekkaError("That parent head does not exist.")
        if parent_id in h.subtree_ids():
            raise LekkaError(f"'{h.name}' can't go under itself or one of its own sub-heads.")
        check_kind_under(h.kind, parent)
    old = h.parent.name if h.parent else "top level"
    h.parent_id = parent_id
    db.session.flush()
    db.session.refresh(h)
    audit("head", h.id, "edit", f"Moved from {old} to {h.parent.name if h.parent else 'top level'}")
    return True
