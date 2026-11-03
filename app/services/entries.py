"""Everything that changes an item: create, split, tick, untick, reschedule, cancel, edit,
link money to its plan, and daily balance adjustments. Every change is written to the audit log."""
from datetime import date
from decimal import Decimal

from ..extensions import db
from ..models import (ACTIVE, CANCELLED, DELETED, IN, OUT, Account, BalanceAdjustment, CashflowEntry,
                      Counterparty, Head, RescheduleLog)
from .common import LekkaError, audit, inr, money, parse_date, snapshot, today, user_id

ENTRY_FIELDS = ["title", "amount", "planned_date", "is_settled", "settled_on", "lifecycle",
                "head_id", "direction", "remarks", "cancel_reason", "reschedule_count", "counterparty_id"]
PAISA = Decimal("0.01")
MAX_REMARKS = 1000


# ---------------------------------------------------------------- lookups and status

def get_account() -> Account:
    acc = Account.query.first()
    if acc is None:
        acc = Account(name="Main account", opening_balance=Decimal("0"), opening_date=today())
        db.session.add(acc)
        db.session.flush()
    return acc


def get_entry(entry_id, allow_deleted=False) -> CashflowEntry:
    e = db.session.get(CashflowEntry, int(entry_id))
    if e is None or (e.is_deleted and not allow_deleted):
        raise LekkaError("That item no longer exists.")
    return e


def check_version(entry, expected):
    """Stops an older copy (another tab) from overwriting a newer change."""
    if expected not in (None, "") and int(expected) != entry.version:
        raise LekkaError("This item was changed elsewhere. Reload the page and try again.")


def _touch(entry):
    entry.version = (entry.version or 0) + 1
    entry.updated_by = user_id()


def status(e: CashflowEntry, t: date | None = None, due_soon_days: int | None = None) -> str:
    """Leaf status, computed from the planned date and the tick; never stored."""
    from flask import current_app
    t = t or today()
    window = current_app.config["DUE_SOON_DAYS"] if due_soon_days is None else due_soon_days
    if e.lifecycle == DELETED:
        return "DELETED"
    if e.lifecycle == CANCELLED:
        return "CANCELLED"
    if not e.is_leaf:
        live = [l for l in e.leaves() if not l.is_cancelled]
        if not live:
            return "CANCELLED"
        if all(l.is_settled for l in live):
            return "SETTLED"
        if any(status(l, t, window) == "OVERDUE" for l in live):
            return "OVERDUE"
        if any(status(l, t, window) == "DUE_SOON" for l in live):
            return "DUE_SOON"
        return "PLANNED"
    if e.is_settled:
        return "SETTLED"
    if e.planned_date < t:
        return "OVERDUE"
    if (e.planned_date - t).days <= window:
        return "DUE_SOON"
    return "PLANNED"


def status_label(e: CashflowEntry, t: date | None = None) -> str:
    t = t or today()
    s = status(e, t)
    if s == "DELETED":
        return "Deleted"
    if not e.is_leaf and s != "CANCELLED":
        live = [l for l in e.leaves() if not l.is_cancelled]
        done = sum(1 for l in live if l.is_settled)
        word = "received" if e.direction == IN else "settled"
        return f"{done} of {len(live)} {word}"
    if s == "CANCELLED":
        return "Cancelled"
    if s == "SETTLED":
        word = "Received" if e.direction == IN else "Paid"
        return f"{word} {e.settled_on.day} {e.settled_on:%b}"
    if s == "OVERDUE":
        n = (t - e.planned_date).days
        return f"Overdue {n} day{'s' if n != 1 else ''}"
    if s == "DUE_SOON":
        n = (e.planned_date - t).days
        return "Due today" if n == 0 else f"Due in {n} day{'s' if n != 1 else ''}"
    return "Planned"


def _days(n):
    return f"{n} day{'s' if n != 1 else ''}"


def _dm(d):
    return f"{d.day} {d:%b}"


def lateness(e: CashflowEntry):
    """How a ticked item compares with when it was due. Measured from the FIRST due date, so an item that
    went overdue and was then moved still shows how late the money really came.
    Returns (days, text): days > 0 late, < 0 early, 0 on time; None when not ticked."""
    if not (e.is_leaf and e.is_settled and e.planned_date and e.settled_on):
        return None
    word = "Received" if e.direction == IN else "Paid"
    first = e.original_planned_date if (e.reschedule_count and e.original_planned_date) else e.planned_date
    n = (e.settled_on - first).days
    moved = ""
    if first != e.planned_date:
        moved = f"; moved {_days(e.reschedule_count).replace('day', 'time')} to {_dm(e.planned_date)}"
    if n > 0:
        return n, f"{word} {_days(n)} late: due {_dm(first)}, {word.lower()} {_dm(e.settled_on)}{moved}"
    if n < 0:
        return n, f"{word} {_days(-n)} early: due {_dm(first)}, {word.lower()} {_dm(e.settled_on)}{moved}"
    return 0, f"{word} on the due date ({_dm(first)})" + moved


def slip_note(e: CashflowEntry) -> str | None:
    """Short line for lists and popups; only when the money did not move on its due date."""
    r = lateness(e)
    return r[1] if r and r[0] != 0 else None


# ---------------------------------------------------------------- validation helpers

def _check_title(title):
    title = (title or "").strip()
    if not title:
        raise LekkaError("Enter a title.")
    if len(title) > 200:
        raise LekkaError("The title can be at most 200 characters.")
    return title


def _check_remarks(remarks):
    remarks = (remarks or "").strip() or None
    if remarks and len(remarks) > MAX_REMARKS:
        raise LekkaError(f"Remarks can be at most {MAX_REMARKS:,} characters.")
    return remarks


def _check_amount(amount):
    a = money(amount)
    if a <= 0:
        raise LekkaError("The amount must be more than ₹0.")
    if a >= Decimal("10000000000000"):
        raise LekkaError("That amount is too large.")
    return a


def _check_head(head_id, allow_inactive=False, direction=None):
    if not head_id:
        raise LekkaError("Choose a head.")
    head = db.session.get(Head, int(head_id))
    if head is None:
        raise LekkaError("That head does not exist.")
    if not head.is_active and not allow_inactive:
        raise LekkaError(f"The head '{head.name}' is inactive; choose another.")
    if direction and not head.fits(direction):
        raise LekkaError(f"'{head.name}' is {'an expense' if head.kind == 'OUT' else 'a revenue'} head; choose a "
                         f"{'revenue' if direction == IN else 'expense'} head, or set it to Both in Master data.")
    return head


def check_not_before_opening(d: date, what: str = "That date"):
    """Money dated before the account's opening date is already inside the opening balance, so Lekka
    would ignore it. Say so instead of saving something that silently counts nowhere."""
    od = get_account().opening_date
    if d < od:
        when = f"{od.day} {od:%b %Y}"
        raise LekkaError(f"{what} is before the account's opening date ({when}). Money before that date is already "
                         f"in the opening balance, so Lekka would not count it. Use {when} or later, or move the "
                         f"opening date earlier on the Account page.")
    return d


def _check_settled_date(d):
    d = parse_date(d, "settled date")
    if d > today():
        raise LekkaError("The settled date cannot be in the future.")
    return check_not_before_opening(d, "The settled date")


def _recompute_ancestors(node):
    """Parent total = sum of its live parts. A parent whose parts are all cancelled is cancelled too."""
    for a in node.ancestors():
        live = [c for c in a.children if not c.is_cancelled]
        if live:
            a.amount = sum((c.amount for c in live), Decimal("0"))
            if a.lifecycle == CANCELLED:
                a.lifecycle, a.cancel_reason = ACTIVE, None
        elif a.lifecycle != CANCELLED:
            a.lifecycle, a.cancel_reason = CANCELLED, "all parts cancelled"
            audit("cashflow_entry", a.id, "cancel", "all parts cancelled")
        _touch(a)


# ---------------------------------------------------------------- create and edit

def create_entry(*, direction, head_id, title, amount, planned_date, remarks=None,
                 cost_centre_id=None, counterparty_id=None, reference_no=None,
                 settled_on=None, template_id=None, occurrence_date=None, audit_action="create"):
    if direction not in (IN, OUT):
        raise LekkaError("Choose inflow or outflow.")
    head = _check_head(head_id, direction=direction)
    e = CashflowEntry(
        direction=direction, head_id=head.id, title=_check_title(title), amount=_check_amount(amount),
        planned_date=parse_date(planned_date, "planned date"), remarks=_check_remarks(remarks),
        cost_centre_id=cost_centre_id or None, counterparty_id=counterparty_id or None,
        reference_no=(reference_no or "").strip() or None,
        template_id=template_id, occurrence_date=occurrence_date,
        created_by=user_id(), updated_by=user_id(),
    )
    if settled_on:
        e.is_settled, e.settled_on = True, _check_settled_date(settled_on)
    db.session.add(e)
    db.session.flush()
    e.root_id = e.id
    if audit_action:       # dates made by a recurring template are logged once, on the template
        audit("cashflow_entry", e.id, audit_action, e.remarks, new=snapshot(e, ENTRY_FIELDS))
    return e


def edit_entry(e: CashflowEntry, *, title=None, amount=None, remarks=None, head_id=None,
               counterparty_id=None, cost_centre_id=None, reference_no=None,
               expected_version=None, remarks_note=None, follow_shares=True):
    """counterparty_id: None = leave as is, "" = clear (no vendor), an id = set. Set on a top item, it applies
    to all its parts. follow_shares: an amount change inside a shared revenue keeps its partner share at the
    same % (the Edit popup of the revenue itself passes False and asks the % again instead)."""
    check_version(e, expected_version)
    ctx = _share_context(e) if follow_shares and amount is not None else None
    old = snapshot(e, ENTRY_FIELDS)
    if title is not None:
        e.title = _check_title(title)
    if remarks is not None:
        e.remarks = _check_remarks(remarks)
    if reference_no is not None:
        e.reference_no = reference_no.strip() or None
    if counterparty_id is not None:
        cp = int(counterparty_id) if str(counterparty_id).strip() else None
        if cp is not None and db.session.get(Counterparty, cp) is None:
            raise LekkaError("That party no longer exists. Reload and choose again.")
        for node in [e] + (e.descendants() if e.parent_id is None else []):
            node.counterparty_id = cp
    if cost_centre_id is not None:
        e.cost_centre_id = cost_centre_id or None
    if amount is not None:
        new_amount = _check_amount(amount)
        if new_amount != e.amount:
            if not e.is_leaf:
                raise LekkaError("A split item's total comes from its parts; change the parts instead.")
            if e.is_cancelled:
                raise LekkaError("A cancelled item cannot be changed.")
            if e.is_settled:
                # Correcting money that already moved = untick, change the amount, tick again on the same
                # date, done as one step. The balances from that settled date are recalculated.
                note = (f"Amount corrected from ₹{inr(e.amount)} to ₹{inr(new_amount)}; "
                        f"still ticked on {e.settled_on.day} {e.settled_on:%b %Y}")
                remarks_note = f"{remarks_note}; {note}" if remarks_note else note
            e.amount = new_amount
            _recompute_ancestors(e)
    if head_id is not None and int(head_id) != e.head_id:
        if e.parent_id:
            raise LekkaError("Split parts always use their parent's head; change the head on the top item.")
        head = _check_head(head_id, direction=e.direction)
        for node in [e] + e.descendants():
            node.head_id = head.id
    if e.template_id and e.parent_id is None:
        e.is_overridden = True
    _touch(e)
    audit("cashflow_entry", e.id, "edit", remarks_note, old=old, new=snapshot(e, ENTRY_FIELDS))
    e.share_note = _follow_shares(ctx)
    return e


# ---------------------------------------------------------------- splits

def _clean_parts(parts, default_title):
    clean = []
    for i, p in enumerate(parts, start=1):
        if not any(str(p.get(k) or "").strip() for k in ("title", "amount", "planned_date")):
            continue  # an empty row in the form
        clean.append({
            "id": p.get("id") or None,
            "title": _check_title(p.get("title") or f"{default_title} – part {i}"),
            "amount": _check_amount(p.get("amount")),
            "planned_date": parse_date(p.get("planned_date"), f"date for part {i}"),
            "remarks": _check_remarks(p.get("remarks")),
            # date of the matching partner-share part (shared revenue only); blank = keep the old date
            "share_date": (check_not_before_opening(parse_date(p["share_date"], f"partner share date for part {i}"),
                                                    "The partner share date")
                           if str(p.get("share_date") or "").strip() else None),
        })
    return clean


def _sum_message(total, target):
    diff = target - total
    from .common import inr
    if diff > 0:
        return f"The parts add up to ₹{inr(total)}; ₹{inr(diff)} is still unallocated (target ₹{inr(target)})."
    return f"The parts add up to ₹{inr(total)}, over by ₹{inr(-diff)} (target ₹{inr(target)})."


def split_entry(e: CashflowEntry, parts, remarks=None, expected_version=None, log=True):
    """Split a single item into dated parts. There is no limit on how deep splits can go."""
    check_version(e, expected_version)
    if e.is_cancelled:
        raise LekkaError("A cancelled item cannot be split.")
    if not e.is_leaf:
        return edit_split(e, parts, remarks=remarks)
    if e.is_settled:
        raise LekkaError("A ticked item is locked; untick it first.")
    clean = _clean_parts(parts, e.title)
    if len(clean) < 2:
        raise LekkaError("A split needs at least two parts.")
    total = sum((p["amount"] for p in clean), Decimal("0"))
    if total != e.amount:
        raise LekkaError(_sum_message(total, e.amount))
    ctx = _share_context(e)
    for p in clean:
        c = _child(e, p)
        c.share_date_hint = p.get("share_date")
        db.session.add(c)
    if e.original_planned_date is None:
        e.original_planned_date = e.planned_date
    e.planned_date = None
    _touch(e)
    db.session.flush()
    if log:
        audit("cashflow_entry", e.id, "split", _check_remarks(remarks),
              new={"parts": [{"title": p["title"], "amount": str(p["amount"]),
                              "planned_date": p["planned_date"].isoformat()} for p in clean]})
    e.share_note = _follow_shares(ctx)
    return e


def _child(parent, p, settled_on=None):
    c = CashflowEntry(
        parent=parent, root_id=parent.root_id, depth=parent.depth + 1,
        direction=parent.direction, head_id=parent.head_id,
        cost_centre_id=parent.cost_centre_id, counterparty_id=parent.counterparty_id,
        title=p["title"], amount=p["amount"], planned_date=p["planned_date"],
        remarks=p.get("remarks"), created_by=user_id(), updated_by=user_id(),
    )
    if settled_on:
        c.is_settled, c.settled_on = True, settled_on
    return c


def edit_split(parent: CashflowEntry, parts, new_total=None, remarks=None):
    """Change the parts of an existing split. Ticked parts are locked; their amounts still count."""
    if any(not c.is_leaf for c in parent.children):
        raise LekkaError("Some parts are split further; edit those lower levels directly.")
    clean = _clean_parts(parts, parent.title)
    ctx = _share_context(parent)
    by_id = {c.id: c for c in parent.children}
    locked = [c for c in parent.children if c.is_settled or c.is_cancelled]
    seen = set()
    for p in clean:
        if p["id"]:
            c = by_id.get(int(p["id"]))
            if c is None:
                raise LekkaError("One of the parts no longer belongs to this split. Reload and try again.")
            seen.add(c.id)
            if c.is_settled or c.is_cancelled:
                if (p["amount"], p["planned_date"]) != (c.amount, c.settled_on if c.is_settled else c.planned_date):
                    raise LekkaError(f"'{c.title}' is ticked or cancelled and locked; untick it first.")
    target = parent.amount if new_total in (None, "") else _check_amount(new_total)
    locked_total = sum((c.amount for c in locked if not c.is_cancelled), Decimal("0"))
    if target < locked_total:
        from .common import inr
        raise LekkaError(f"The total cannot go below the ₹{inr(locked_total)} already ticked.")
    editable = [p for p in clean if not (p["id"] and (by_id[int(p["id"])].is_settled or by_id[int(p["id"])].is_cancelled))]
    total = locked_total + sum((p["amount"] for p in editable), Decimal("0"))
    if total != target:
        raise LekkaError(_sum_message(total, target))
    old = [{"id": c.id, "title": c.title, "amount": str(c.amount)} for c in parent.children]
    gone = [c for c in parent.children if c.id not in seen and not (c.is_settled or c.is_cancelled)]
    _unlink_share_parts(gone)
    db.session.flush()
    for c in gone:
        parent.children.remove(c)
        db.session.delete(c)
    for p in clean:
        if p["id"] and int(p["id"]) in by_id:
            by_id[int(p["id"])].share_date_hint = p.get("share_date")
    for p in editable:
        if p["id"]:
            c = by_id[int(p["id"])]
            c.title, c.amount, c.remarks = p["title"], p["amount"], p["remarks"]
            if c.planned_date != p["planned_date"]:
                c.original_planned_date = c.original_planned_date or c.planned_date
                c.planned_date = p["planned_date"]
            _touch(c)
        else:
            c = _child(parent, p)
            c.share_date_hint = p.get("share_date")
            db.session.add(c)
    if len([c for c in parent.children if not c.is_cancelled]) + sum(1 for p in editable if not p["id"]) < 1:
        raise LekkaError("A split needs at least one live part.")
    parent.amount = target
    _touch(parent)
    db.session.flush()
    _recompute_ancestors(parent)
    audit("cashflow_entry", parent.id, "edit_split", _check_remarks(remarks), old={"parts": old},
          new={"parts": [{"id": c.id, "title": c.title, "amount": str(c.amount)} for c in parent.children]})
    parent.share_note = _follow_shares(ctx)
    return parent


def remove_split(parent: CashflowEntry, remarks=None):
    if parent.is_leaf:
        raise LekkaError("This item is not split.")
    nodes = parent.descendants()
    if any(n.is_settled for n in nodes):
        raise LekkaError("A part of this split is ticked; untick it before removing the split.")
    ctx = _share_context(parent)
    first, _ = parent.date_range()
    if first is None:
        first = min((n.planned_date for n in nodes if n.planned_date), default=today())
    _unlink_share_parts(nodes)
    db.session.flush()
    for n in sorted(nodes, key=lambda x: -x.depth):
        if n.parent is not None:
            n.parent.children.remove(n)
        db.session.delete(n)
    parent.planned_date = first
    parent.lifecycle, parent.cancel_reason = ACTIVE, None
    _touch(parent)
    audit("cashflow_entry", parent.id, "remove_split", _check_remarks(remarks))
    parent.share_note = _follow_shares(ctx)
    return parent


# ---------------------------------------------------------------- tick, untick, reschedule, cancel

def settle(e: CashflowEntry, settled_on, remarks=None, expected_version=None, action="settle"):
    check_version(e, expected_version)
    if not e.is_leaf:
        raise LekkaError("A split item is ticked part by part; tick its parts.")
    if e.is_cancelled:
        raise LekkaError("A cancelled item cannot be ticked.")
    if e.is_settled:
        raise LekkaError("This item is already ticked.")
    d = _check_settled_date(settled_on)
    old = snapshot(e, ENTRY_FIELDS)
    e.is_settled, e.settled_on = True, d
    _touch(e)
    note = _check_remarks(remarks)
    audit("cashflow_entry", e.id, action, note, old=old,
          new={**snapshot(e, ENTRY_FIELDS), "ticked_on": today().isoformat()})
    return e


def unsettle(e: CashflowEntry, remarks=None, expected_version=None):
    check_version(e, expected_version)
    if not e.is_settled:
        raise LekkaError("This item is not ticked.")
    old = snapshot(e, ENTRY_FIELDS)
    e.is_settled, e.settled_on = False, None
    _touch(e)
    audit("cashflow_entry", e.id, "unsettle", _check_remarks(remarks), old=old, new=snapshot(e, ENTRY_FIELDS))
    return e


def _move_leaf(leaf, new_date, remarks):
    old_date = leaf.planned_date
    if leaf.original_planned_date is None:
        leaf.original_planned_date = old_date
    leaf.planned_date = new_date
    leaf.reschedule_count = (leaf.reschedule_count or 0) + 1
    _touch(leaf)
    db.session.add(RescheduleLog(entry_id=leaf.id, old_date=old_date, new_date=new_date,
                                 remarks=remarks, user_id=user_id()))
    audit("cashflow_entry", leaf.id, "reschedule", remarks,
          old={"planned_date": old_date.isoformat()}, new={"planned_date": new_date.isoformat()})


def reschedule(e: CashflowEntry, new_date, remarks=None, expected_version=None):
    """Give an unticked item a new planned date (today or later). A parent moves all its
    unticked parts by the same number of days; ticked parts stay where they are."""
    check_version(e, expected_version)
    new_date = parse_date(new_date, "new date")
    remarks = _check_remarks(remarks)
    if e.is_cancelled:
        raise LekkaError("A cancelled item cannot be rescheduled.")
    if new_date < today():
        raise LekkaError("The new date must be today or later.")
    if e.is_leaf:
        if e.is_settled:
            raise LekkaError("A ticked item cannot be rescheduled; untick it first.")
        if new_date == e.planned_date:
            raise LekkaError("That is already the planned date.")
        _move_leaf(e, new_date, remarks)
        if e.template_id and e.parent_id is None:
            e.is_overridden = True
        return e
    pending = [l for l in e.leaves() if not l.is_settled and not l.is_cancelled]
    if not pending:
        raise LekkaError("Every part is ticked or cancelled; there is nothing to move.")
    first = min(l.planned_date for l in pending)
    delta = new_date - first
    if delta.days == 0:
        raise LekkaError("That is already the first planned date.")
    for l in pending:
        _move_leaf(l, l.planned_date + delta, remarks)
    if e.template_id and e.parent_id is None:
        e.is_overridden = True
    return e


def move_targets(e: CashflowEntry):
    """Where a cancelled part's money can go: other unticked, live leaves of the same split tree."""
    if e.parent_id is None:
        return []
    root = db.session.get(CashflowEntry, e.root_id)
    return [l for l in root.leaves() if l.id != e.id and not l.is_settled and not l.is_cancelled]


def cancel(e: CashflowEntry, reason, mode=None, target_id=None, expected_version=None):
    """Cancel with a reason (see _cancel_item), then keep a revenue's partner share in line:
    - the whole revenue cancelled: its unticked partner share is cancelled too ("revenue cancelled");
    - a part of it cancelled: the matching part of the share is cancelled, and with Move the share part of
      the part that received the money grows by the same %.
    A partner share already ticked as paid is never changed; e.share_note says what to check."""
    root = e.ancestors()[-1] if e.parent_id else e
    ctx = _share_context(e)
    _cancel_item(e, reason, mode, target_id, expected_version)
    e.share_note = _sync_shares_after_cancel(root, ctx, reason) if ctx else None
    return e


def _sync_shares_after_cancel(root, ctx, reason):
    shares = partner_shares(root)
    if not shares:
        return None
    if root.is_cancelled:
        done_ = []
        for s in shares:
            if any(x.is_settled for x in [s] + s.descendants()):
                return (f"Partner share '{s.title}' is already ticked as paid, so it was kept; "
                        f"check it with the partner")
            cancel(s, f"Revenue '{root.title}' cancelled: {reason}"[:255])
            done_.append(s)
        return f"Its partner share ({', '.join(x.title for x in done_)}) was cancelled too"
    try:
        return _follow_shares(ctx)
    except LekkaError as err:
        return f"Partner share not changed: {err}"


def _cancel_item(e: CashflowEntry, reason, mode=None, target_id=None, expected_version=None):
    """Cancel with a reason. For a part of a split, finance chooses each time: shrink the
    parent's total, or move the amount onto another unticked part. Nothing is pre-selected."""
    check_version(e, expected_version)
    reason = (reason or "").strip()
    if not reason:
        raise LekkaError("Enter a reason for cancelling.")
    if len(reason) > 255:
        raise LekkaError("The reason can be at most 255 characters.")
    if e.is_cancelled:
        raise LekkaError("This item is already cancelled.")
    if e.is_leaf and e.is_settled:
        raise LekkaError("A ticked item cannot be cancelled; untick it first.")

    if not e.is_leaf:  # a parent: cancel every unticked part below it
        pending = [l for l in e.leaves() if not l.is_settled and not l.is_cancelled]
        if not pending:
            raise LekkaError("Every part is ticked; there is nothing left to cancel.")
        for l in pending:
            _cancel_leaf(l, reason)
        for l in pending:
            _recompute_ancestors(l)
        return e

    if e.parent_id is None:
        _cancel_leaf(e, reason)
        return e

    if mode not in ("shrink", "move"):
        raise LekkaError("Choose what happens to the money: shrink the parent's total, or move it to another part.")
    if mode == "move":
        targets = {t.id: t for t in move_targets(e)}
        if not targets:
            raise LekkaError("There is no other unticked part to move the amount to; choose shrink.")
        if not target_id or int(target_id) not in targets:
            raise LekkaError("Choose the part that should receive the amount.")
        t = targets[int(target_id)]
        old = snapshot(t, ENTRY_FIELDS)
        t.amount = t.amount + e.amount
        _touch(t)
        audit("cashflow_entry", t.id, "receive_moved_amount", f"from '{e.title}' (cancelled): {reason}",
              old=old, new=snapshot(t, ENTRY_FIELDS))
        _cancel_leaf(e, reason)
        _recompute_ancestors(t)
        _recompute_ancestors(e)
    else:
        _cancel_leaf(e, reason)
        _recompute_ancestors(e)
    return e


def _cancel_leaf(l, reason):
    old = snapshot(l, ENTRY_FIELDS)
    l.lifecycle, l.cancel_reason = CANCELLED, reason
    _touch(l)
    audit("cashflow_entry", l.id, "cancel", reason, old=old, new=snapshot(l, ENTRY_FIELDS))


def delete_entry(e: CashflowEntry, expected_version=None, remarks=None):
    """Delete an item entered by mistake: it disappears from the grid, the lists and every balance.
    (Cancel is for an item that won't happen: it stays visible, struck through.)
    The whole item goes, with all its parts. The row is kept, hidden, so History can still show what
    was deleted and a recurring template does not create that date again."""
    check_version(e, expected_version)
    if e.is_deleted:
        raise LekkaError("This item is already deleted.")
    if e.parent_id:
        raise LekkaError("A part can't be deleted on its own. Cancel the part, change the split, "
                         "or delete the whole item.")
    nodes = [e] + e.descendants()
    for n in nodes:
        n.lifecycle = DELETED
    _touch(e)
    for s in CashflowEntry.query.filter(CashflowEntry.share_of_id == e.id, CashflowEntry.lifecycle != DELETED).all():
        if not any(x.is_settled for x in [s] + s.descendants()):
            delete_entry(s, remarks=f"Partner share of deleted '{e.title}'")
    audit("cashflow_entry", e.id, "delete", _check_remarks(remarks),
          old={"title": e.title, "amount": str(e.amount), "ticked_parts": sum(1 for n in nodes if n.is_settled)},
          new={"title": e.title})


# ---------------------------------------------------------------- linking money to its plan

def link_candidates(head_id, direction, amount, on_date):
    """Open plans the entered money could belong to: unticked, live leaves with the same head and
    direction, any planned date. Same amount first, then the nearest planned date."""
    amount = money(amount)
    on_date = parse_date(on_date)
    q = CashflowEntry.query.filter_by(head_id=int(head_id), direction=direction,
                                      is_settled=False, lifecycle=ACTIVE)
    leaves = [e for e in q.all() if e.is_leaf]
    leaves.sort(key=lambda e: (e.amount != amount, abs((e.planned_date - on_date).days), e.planned_date, e.id))
    return leaves


# ---------------------------------------------------------------- sharing revenue with partner companies

def read_share(form, amount, revenue_title, revenue_date):
    """Read 'Our share (%)' from the new-revenue form. 100 (the default) = no sharing.
    Below 100: the partner's part becomes its own expense, with the head, title and date given then.
    Returns None, or what add_partner_share needs. Checked before anything is saved."""
    raw = (form.get("share_pct") or "100").strip().rstrip("%")
    try:
        pct = Decimal(raw)
    except Exception:
        raise LekkaError("Our share must be a number between 0 and 100.")
    if pct < 0 or pct > 100:
        raise LekkaError("Our share must be between 0 and 100%.")
    if pct == 100:
        return None
    total = _check_amount(amount)
    part = (total * (Decimal(100) - pct) / Decimal(100)).quantize(Decimal("0.01"))
    if part <= 0:
        return None
    if not form.get("share_head_id"):
        raise LekkaError("Choose the head for the partner's share.")
    title = (form.get("share_title") or "").strip() or f"Partner share – {revenue_title}"
    d = parse_date(form.get("share_date") or revenue_date, "date of the partner's share")
    check_not_before_opening(d, "The date of the partner's share")
    cp = form.get("share_counterparty_id") or None
    if cp and db.session.get(Counterparty, int(cp)) is None:
        raise LekkaError("That party no longer exists. Reload and choose again.")
    return {"pct": pct, "amount": part, "head_id": int(form["share_head_id"]), "title": title, "date": d,
            "counterparty_id": int(cp) if cp else None}


def add_partner_share(revenue: CashflowEntry, share: dict):
    """Record the partner's part of a revenue item as an expense linked to it. We receive the full amount;
    the partner's part goes out as this separate expense on its own date."""
    ours = share["pct"].normalize()
    e = create_entry(direction=OUT, head_id=share["head_id"], title=share["title"], amount=share["amount"],
                     planned_date=share["date"], counterparty_id=share.get("counterparty_id"),
                     remarks=f"{Decimal(100) - share['pct']:.4g}% partner share of '{revenue.title}' "
                             f"(our share {ours:f}%)")
    e.share_of_id = revenue.id
    return e


def partner_shares(revenue: CashflowEntry):
    """The live (not cancelled or deleted) partner-share expenses of a revenue item."""
    return (CashflowEntry.query.filter(CashflowEntry.share_of_id == revenue.id, CashflowEntry.lifecycle == ACTIVE)
            .order_by(CashflowEntry.id).all())


def our_share_pct(revenue: CashflowEntry):
    """Our share in %, worked out from the revenue and its partner shares (100 when not shared)."""
    shares = partner_shares(revenue)
    if not shares or not revenue.amount:
        return Decimal(100)
    part = sum((s.amount for s in shares), Decimal("0"))
    return (Decimal(100) - part * 100 / revenue.amount).quantize(Decimal("0.01")).normalize()


# ---------------------------------------------------------------- partner share follows its revenue

def _share_context(e):
    """Before a change inside a revenue item: its top item, its amount and its live partner shares' total,
    so the shares can follow at the same proportion afterwards. None when there is nothing to follow."""
    root = e if e.parent_id is None else db.session.get(CashflowEntry, e.root_id)
    if root is None or root.direction != IN or root.is_deleted:
        return None
    shares = partner_shares(root)
    if not shares:
        return None
    return root, root.amount, sum((s.amount for s in shares), Decimal("0"))


def _follow_shares(ctx):
    """After a change inside a shared revenue: keep its partner share at the same % and split the same way."""
    if not ctx:
        return None
    root, rev_before, share_before = ctx
    db.session.flush()
    if root.is_cancelled or root.is_deleted:
        return None                       # a cancelled or deleted revenue is handled by cancel() / delete_entry()
    if root.amount == rev_before:
        target = share_before
    else:
        target = (share_before * root.amount / rev_before).quantize(PAISA)
    return sync_partner_shares(root, target)


def _unlink_share_parts(revenue_nodes):
    """Revenue parts about to be removed for good: partner-share parts that mirror them lose the link first."""
    ids = [n.id for n in revenue_nodes if n.id]
    if not ids:
        return
    for x in CashflowEntry.query.filter(CashflowEntry.share_of_id.in_(ids)).all():
        if x.parent_id is not None:       # only parts of a share mirror revenue parts; a share itself stays linked
            x.share_of_id = None


def _date_of(x):
    return x.settled_on if x.is_settled else (x.planned_date or x.original_planned_date)


def _share_default_date(share, leaf, by_leaf):
    """Default date for the share part that mirrors a revenue part: the date of the share part of the nearest
    revenue part above it (the one that was split), else the share's own (old) date."""
    for a in [leaf] + leaf.ancestors():
        p = by_leaf.get(a.id)
        if p is not None and _date_of(p):
            return _date_of(p)
    return _date_of(share) or today()


def _spread(total, weights):
    """Split `total` in proportion to `weights` (amounts); the last one takes the rounding."""
    whole = sum(weights, Decimal("0"))
    out, left = [], total
    for i, w in enumerate(weights):
        amt = left if i == len(weights) - 1 else (total * w / whole).quantize(PAISA)
        left -= amt
        out.append(amt)
    return out


def _locked_msg(s, root):
    return (f"The partner share '{s.title}' is already ticked as paid, so it can't change by itself. "
            f"Untick it first, or keep our share at {our_share_pct(root):f}%.")


def sync_partner_shares(root: CashflowEntry, target_total):
    """Make the revenue's live partner shares add up to `target_total` and mirror the revenue's split:
    one share part per revenue part, each the same % of its revenue part, dated as asked in the split form
    (default: the old date). Everything is checked before anything changes; a share part already ticked as
    paid is never changed silently (the whole save is refused instead). Returns a short note, or None."""
    shares = partner_shares(root)
    if not shares:
        return None
    if target_total <= 0:
        raise LekkaError("The partner share would become ₹0; set our share to 100% instead.")
    old_total = sum((s.amount for s in shares), Decimal("0"))
    targets = _spread(target_total, [s.amount for s in shares]) if old_total else [target_total]
    leaves = [] if root.is_leaf else root.leaves()
    live = [l for l in leaves if not l.is_cancelled]
    plans, notes = [], []
    for s, t in zip(shares, targets):
        plan = _plan_share(s, t, root, leaves, live, notes)
        if plan:
            plans.append(plan)
    for plan in plans:                       # nothing has changed before this point
        plan()
    db.session.flush()
    changed = [s for s in shares if getattr(s, "share_changed", False)]
    if changed:
        parts = []
        for s in changed:
            if s.is_leaf:
                parts.append(f"'{s.title}' ₹{inr(s.amount)}")
            else:
                live_parts = [c for c in s.children if not c.is_cancelled]
                parts.append(f"'{s.title}' ₹{inr(s.amount)} in {len(live_parts)} part"
                             f"{'s' if len(live_parts) != 1 else ''} (" +
                             ", ".join(f"₹{inr(c.amount)} on {_date_of(c).day} {_date_of(c):%b}" for c in live_parts) + ")")
        notes.insert(0, "Partner share follows: " + "; ".join(parts))
    return ". ".join(notes) or None


def _plan_share(s, t, root, leaves, live, notes):
    """Work out (and check) what one partner share needs; return a function that does it, or None."""
    nested = any(not c.is_leaf for c in s.children)
    if nested:
        notes.append(f"Partner share '{s.title}' is split by hand into several levels, so it was not changed; "
                     f"check it")
        return None
    reason = f"Follows revenue '{root.title}'"

    # ---- the revenue is one item: the share is one item too
    if root.is_leaf:
        if s.is_leaf:
            if s.amount == t:
                return None
            if s.is_settled:
                raise LekkaError(_locked_msg(s, root))
            def do():
                edit_entry(s, amount=t, remarks_note=f"Partner share recalculated: {reason}")
                s.share_changed = True
            return do
        if any(c.is_settled for c in s.children):
            raise LekkaError(f"A part of the partner share '{s.title}' is already ticked as paid; untick it "
                             f"before the revenue's split can be removed.")
        hint = getattr(root, "share_date_hint", None)
        def do():
            remove_split(s, reason)
            if hint:
                s.planned_date = hint
            if s.amount != t:
                edit_entry(s, amount=t, remarks_note=f"Partner share recalculated: {reason}")
            s.share_changed = True
        return do

    # ---- the revenue is split: one share part per revenue part, same %
    amounts = dict(zip([l.id for l in live], _spread(t, [l.amount for l in live]))) if live else {}
    if s.is_leaf:
        if s.is_settled:
            if s.amount == t:
                notes.append(f"Partner share '{s.title}' is already paid in full, so it stays one payment")
                return None
            raise LekkaError(_locked_msg(s, root))
        rows = [l for l in live if amounts[l.id] > 0]
        if len(rows) < 2:
            if s.amount == t:
                return None
            def do():
                edit_entry(s, amount=t, remarks_note=f"Partner share recalculated: {reason}")
                s.share_changed = True
            return do
        def do():
            if s.amount != t:
                s.amount = t
            parts = [{"title": f"{s.title} – {l.title}"[:200], "amount": amounts[l.id],
                      "planned_date": getattr(l, "share_date_hint", None) or _share_default_date(s, l, {})}
                     for l in rows]
            split_entry(s, parts, remarks=reason)
            for c, l in zip(sorted(s.children, key=lambda x: x.id), rows):
                c.share_of_id = l.id
            s.share_changed = True
        return do

    # the share is already split: match its parts to the revenue parts they mirror
    by_leaf = {c.share_of_id: c for c in s.children if c.share_of_id and not c.is_cancelled}
    leaf_ids = {l.id for l in leaves}
    steps = []
    for l in live:
        amt = amounts[l.id]
        c = by_leaf.get(l.id)
        hint = getattr(l, "share_date_hint", None)
        if c is None:
            if amt > 0:
                steps.append(("add", l, amt, hint or _share_default_date(s, l, by_leaf)))
            continue
        if c.is_settled:
            if c.amount != amt:
                raise LekkaError(f"The partner share part '{c.title}' is already ticked as paid "
                                 f"(₹{inr(c.amount)}); following the revenue it would become ₹{inr(amt)}. "
                                 f"Untick it first.")
            continue
        if amt == 0:
            steps.append(("drop", c))
        elif c.amount != amt or (hint and hint != c.planned_date):
            steps.append(("set", c, amt, hint))
    for l in leaves:
        if l.is_cancelled and l.id in by_leaf:
            c = by_leaf[l.id]
            if c.is_settled:
                notes.append(f"Partner share part '{c.title}' is already ticked as paid, so it was kept; "
                             f"check it with the partner")
            else:
                steps.append(("cancel", c, f"Revenue part '{l.title}' cancelled: {l.cancel_reason or ''}"[:255]))
    for c in s.children:
        if c.is_cancelled or (c.share_of_id in leaf_ids):
            continue
        if c.is_settled:
            raise LekkaError(f"The partner share part '{c.title}' is already ticked as paid and no longer "
                             f"matches a part of the revenue. Untick it first.")
        steps.append(("drop", c))
    if not steps:
        return None

    def do():
        old = [{"id": c.id, "title": c.title, "amount": str(c.amount)} for c in s.children]
        for st in steps:
            if st[0] == "add":
                _, l, amt, d = st
                c = _child(s, {"title": f"{s.title} – {l.title}"[:200], "amount": amt, "planned_date": d})
                c.share_of_id = l.id
                db.session.add(c)
            elif st[0] == "set":
                _, c, amt, d = st
                c.amount = amt
                if d and d != c.planned_date:
                    c.original_planned_date = c.original_planned_date or c.planned_date
                    c.planned_date = d
                _touch(c)
            elif st[0] == "drop":
                s.children.remove(st[1])
                db.session.delete(st[1])
            elif st[0] == "cancel":
                _cancel_leaf(st[1], st[2])
        db.session.flush()
        live_parts = [c for c in s.children if not c.is_cancelled]
        if live_parts:
            s.amount = sum((c.amount for c in live_parts), Decimal("0"))
            if s.lifecycle == CANCELLED:
                s.lifecycle, s.cancel_reason = ACTIVE, None
        else:
            s.lifecycle, s.cancel_reason = CANCELLED, "all parts cancelled"
        _touch(s)
        audit("cashflow_entry", s.id, "edit_split", reason, old={"parts": old},
              new={"parts": [{"id": c.id, "title": c.title, "amount": str(c.amount)} for c in s.children]})
        s.share_changed = True
    return do


def share_split_defaults(e: CashflowEntry):
    """For the split form of a revenue item (or part) that has a partner share: the fraction that goes to the
    partner, the default share date for a new part, and the current share date for each existing part."""
    ctx = _share_context(e)
    if not ctx:
        return None
    root, rev, share_total = ctx
    shares = partner_shares(root)
    by_leaf = {}
    for s in shares:
        for c in s.children:
            if c.share_of_id and not c.is_cancelled:
                by_leaf.setdefault(c.share_of_id, c)
    s0 = shares[0]
    default = _share_default_date(s0, e, by_leaf)
    per_part = {c.id: (_date_of(by_leaf[c.id]) if c.id in by_leaf else default) for c in e.children}
    return {"fraction": (share_total / rev) if rev else Decimal("0"), "default": default, "per_part": per_part,
            "titles": ", ".join(s.title for s in shares)}


def update_partner_share(revenue: CashflowEntry, form, before=None):
    """After a revenue changes (amount, or the % asked again with the old % as default), bring its partner
    share in line: new partner amount = revenue amount x (100 - our share %). `before` = (revenue amount,
    shares total) before the edit: with the % unchanged, the share keeps exactly the same proportion.
    - 100% now: the partner share is cancelled ('no longer shared').
    - No share yet and below 100%: a new one is added (head, title, date and vendor asked, as for new revenue).
    - A split revenue: the share is split the same way (see sync_partner_shares).
    - A share already ticked as paid is never changed silently: the save is refused with a note.
    Returns a short note for the message, or None when nothing changed."""
    if "share_pct" not in form or revenue.direction != IN or revenue.parent_id:
        return None
    shares = partner_shares(revenue)
    if not shares:
        share = read_share(form, revenue.amount, revenue.title, revenue.planned_date or today())
        if not share:
            return None
        s = add_partner_share(revenue, share)
        if not revenue.is_leaf:
            sync_partner_shares(revenue, s.amount)
        return f"Partner share ₹{inr(s.amount)} added"
    raw = (form.get("share_pct") or "100").strip().rstrip("%")
    try:
        pct = Decimal(raw)
    except Exception:
        raise LekkaError("Our share must be a number between 0 and 100.")
    if pct < 0 or pct > 100:
        raise LekkaError("Our share must be between 0 and 100%.")
    old_total = sum((s.amount for s in shares), Decimal("0"))
    rev_before, share_before = before or (revenue.amount, old_total)
    pct_before = (Decimal(100) - share_before * 100 / rev_before).quantize(PAISA).normalize() if rev_before else None
    if pct_before is not None and pct.quantize(PAISA).normalize() == pct_before:
        new_total = (share_before if revenue.amount == rev_before
                     else (share_before * revenue.amount / rev_before).quantize(PAISA))
    else:
        new_total = (revenue.amount * (Decimal(100) - pct) / Decimal(100)).quantize(PAISA)
    if new_total == 0:
        locked = [s for s in shares if any(x.is_settled for x in [s] + s.descendants())]
        if locked:
            raise LekkaError(_locked_msg(locked[0], revenue))
        for s in shares:
            cancel(s, "Revenue no longer shared")
        return "Partner share cancelled (our share is now 100%)"
    if new_total == old_total and (revenue.is_leaf or all(not s.is_leaf for s in shares)):
        return None
    note = sync_partner_shares(revenue, new_total)
    if note and new_total != old_total:
        note = f"Partner share now ₹{inr(new_total)} (our share {pct.normalize():f}%). " + note.split(": ", 1)[-1]
    return note


def open_plans_like(e: CashflowEntry, limit=5):
    """Other open plans this money might really belong to: same head and direction, not ticked, planned
    today or later. Only for a whole, single item (a part already belongs to its plan)."""
    if e.parent_id or not e.is_leaf or e.is_settled or e.is_cancelled:
        return []
    plans = [c for c in link_candidates(e.head_id, e.direction, e.amount, e.planned_date)
             if c.id != e.id and c.root_id != e.id and c.planned_date >= today()]
    return plans[:limit]


def tick_as_plan(e: CashflowEntry, plan: CashflowEntry, on_date, remarks=None, expected_version=None):
    """The money entered as `e` is really the payment for `plan`: tick the plan with it (splitting the plan
    if less came, recording the extra if more came) and remove `e`, so the money counts once."""
    check_version(e, expected_version)
    if plan.id not in {p.id for p in open_plans_like(e, limit=1000)}:
        raise LekkaError("That plan is no longer open. Reload and choose again.")
    note = f"Money entered as '{e.title}'" + (f": {remarks}" if remarks else "")
    why = f"Counted as the payment for '{plan.title}' planned {plan.planned_date.day} {plan.planned_date:%b %Y}"
    link_and_settle(plan, e.amount, on_date, note, extra_mode="extra")
    delete_entry(e, remarks=why)
    return plan


def link_and_settle(candidate: CashflowEntry, amount, on_date, remarks=None, extra_mode=None):
    """Tick the chosen plan with the date the money really moved; never creates a duplicate.
    Less than planned: the plan is split, the received part ticked, the rest stays pending.
    More than planned: 'extra' ticks the plan and records the difference as a new item;
    'raise' raises the plan to the amount and ticks it."""
    amount = _check_amount(amount)
    on_date = _check_settled_date(on_date)
    remarks = _check_remarks(remarks)
    if not candidate.is_leaf or candidate.is_settled or candidate.is_cancelled:
        raise LekkaError("That plan is no longer open. Reload and choose again.")
    note = f"Linked to an entry of {on_date:%d %b %Y}" + (f": {remarks}" if remarks else "")
    if amount == candidate.amount:
        return settle(candidate, on_date, note, action="link_settle")
    if amount < candidate.amount:
        rest = candidate.amount - amount
        word = "received" if candidate.direction == IN else "paid"
        split_entry(candidate, [
            {"title": f"{candidate.title} ({word})", "amount": amount, "planned_date": candidate.planned_date},
            {"title": f"{candidate.title} (balance)", "amount": rest, "planned_date": candidate.planned_date},
        ], remarks=f"Split on linking ₹{amount} of ₹{candidate.amount}")
        part = candidate.children[0]
        return settle(part, on_date, note, action="link_settle")
    if extra_mode == "raise":
        edit_entry(candidate, amount=amount, remarks_note="Plan raised to match the amount that moved")
        return settle(candidate, on_date, note, action="link_settle")
    if extra_mode == "extra":
        settle(candidate, on_date, note, action="link_settle")
        create_entry(direction=candidate.direction, head_id=candidate.head_id,
                     title=f"{candidate.title} (extra)", amount=amount - candidate.amount,
                     planned_date=on_date, settled_on=on_date, remarks=remarks,
                     counterparty_id=candidate.counterparty_id)
        return candidate
    raise LekkaError("More arrived than planned: choose to record the extra as a new item, or raise the plan.")


# ---------------------------------------------------------------- daily balance adjustments

def add_adjustment(adj_date, amount, remarks=None):
    d = parse_date(adj_date, "date")
    if d > today():
        raise LekkaError("Adjustments can only be made for today or earlier; plan an item for future money.")
    check_not_before_opening(d, "The adjustment date")
    a = money(amount)
    if a == 0:
        raise LekkaError("Enter a + or − amount other than 0.")
    adj = BalanceAdjustment(adj_date=d, amount=a, remarks=_check_remarks(remarks),
                            created_by=user_id(), updated_by=user_id())
    db.session.add(adj)
    db.session.flush()
    audit("balance_adjustment", adj.id, "create", adj.remarks, new=snapshot(adj, ["adj_date", "amount", "remarks"]))
    return adj


def edit_adjustment(adj: BalanceAdjustment, amount=None, remarks=None, adj_date=None):
    old = snapshot(adj, ["adj_date", "amount", "remarks"])
    if adj_date is not None:
        d = parse_date(adj_date)
        if d > today():
            raise LekkaError("Adjustments can only be made for today or earlier.")
        check_not_before_opening(d, "The adjustment date")
        adj.adj_date = d
    if amount is not None:
        a = money(amount)
        if a == 0:
            raise LekkaError("Enter a + or − amount other than 0.")
        adj.amount = a
    if remarks is not None:
        adj.remarks = _check_remarks(remarks)
    adj.updated_by = user_id()
    audit("balance_adjustment", adj.id, "edit", None, old=old, new=snapshot(adj, ["adj_date", "amount", "remarks"]))
    return adj


def delete_adjustment(adj: BalanceAdjustment):
    audit("balance_adjustment", adj.id, "delete", None, old=snapshot(adj, ["adj_date", "amount", "remarks"]))
    db.session.delete(adj)
