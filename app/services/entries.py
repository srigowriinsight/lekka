"""Everything that changes an item: create, split, tick, untick, reschedule, cancel, edit,
link money to its plan, and daily balance adjustments. Every change is written to the audit log."""
from datetime import date
from decimal import Decimal

from ..extensions import db
from ..models import (ACTIVE, CANCELLED, DELETED, IN, OUT, Account, BalanceAdjustment, CashflowEntry,
                      Head, RescheduleLog)
from .common import LekkaError, audit, inr, money, parse_date, snapshot, today, user_id

ENTRY_FIELDS = ["title", "amount", "planned_date", "is_settled", "settled_on", "lifecycle",
                "head_id", "direction", "remarks", "cancel_reason", "reschedule_count"]
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


def slip_note(e: CashflowEntry) -> str | None:
    """'planned 19 Nov, received 20 Nov, 1 day late' for ticked items whose dates differ."""
    if not (e.is_leaf and e.is_settled and e.planned_date and e.settled_on != e.planned_date):
        return None
    n = (e.settled_on - e.planned_date).days
    word = "received" if e.direction == IN else "paid"
    when = f"{abs(n)} day{'s' if abs(n) != 1 else ''} {'late' if n > 0 else 'early'}"
    return f"planned {e.planned_date.day} {e.planned_date:%b}, {word} {e.settled_on.day} {e.settled_on:%b}, {when}"


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


def _check_head(head_id, allow_inactive=False):
    if not head_id:
        raise LekkaError("Choose a head.")
    head = db.session.get(Head, int(head_id))
    if head is None:
        raise LekkaError("That head does not exist.")
    if not head.is_active and not allow_inactive:
        raise LekkaError(f"The head '{head.name}' is inactive; choose another.")
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
    head = _check_head(head_id)
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
               expected_version=None, remarks_note=None):
    check_version(e, expected_version)
    old = snapshot(e, ENTRY_FIELDS)
    if title is not None:
        e.title = _check_title(title)
    if remarks is not None:
        e.remarks = _check_remarks(remarks)
    if reference_no is not None:
        e.reference_no = reference_no.strip() or None
    if counterparty_id is not None:
        e.counterparty_id = counterparty_id or None
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
        head = _check_head(head_id)
        for node in [e] + e.descendants():
            node.head_id = head.id
    if e.template_id and e.parent_id is None:
        e.is_overridden = True
    _touch(e)
    audit("cashflow_entry", e.id, "edit", remarks_note, old=old, new=snapshot(e, ENTRY_FIELDS))
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
    for p in clean:
        db.session.add(_child(e, p))
    if e.original_planned_date is None:
        e.original_planned_date = e.planned_date
    e.planned_date = None
    _touch(e)
    db.session.flush()
    if log:
        audit("cashflow_entry", e.id, "split", _check_remarks(remarks),
              new={"parts": [{"title": p["title"], "amount": str(p["amount"]),
                              "planned_date": p["planned_date"].isoformat()} for p in clean]})
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
    for c in list(parent.children):
        if c.id not in seen and not (c.is_settled or c.is_cancelled):
            parent.children.remove(c)
            db.session.delete(c)
    for p in editable:
        if p["id"]:
            c = by_id[int(p["id"])]
            c.title, c.amount, c.remarks = p["title"], p["amount"], p["remarks"]
            if c.planned_date != p["planned_date"]:
                c.original_planned_date = c.original_planned_date or c.planned_date
                c.planned_date = p["planned_date"]
            _touch(c)
        else:
            db.session.add(_child(parent, p))
    if len([c for c in parent.children if not c.is_cancelled]) + sum(1 for p in editable if not p["id"]) < 1:
        raise LekkaError("A split needs at least one live part.")
    parent.amount = target
    _touch(parent)
    db.session.flush()
    _recompute_ancestors(parent)
    audit("cashflow_entry", parent.id, "edit_split", _check_remarks(remarks), old={"parts": old},
          new={"parts": [{"id": c.id, "title": c.title, "amount": str(c.amount)} for c in parent.children]})
    return parent


def remove_split(parent: CashflowEntry, remarks=None):
    if parent.is_leaf:
        raise LekkaError("This item is not split.")
    nodes = parent.descendants()
    if any(n.is_settled for n in nodes):
        raise LekkaError("A part of this split is ticked; untick it before removing the split.")
    first, _ = parent.date_range()
    if first is None:
        first = min((n.planned_date for n in nodes if n.planned_date), default=today())
    for n in sorted(nodes, key=lambda x: -x.depth):
        if n.parent is not None:
            n.parent.children.remove(n)
        db.session.delete(n)
    parent.planned_date = first
    parent.lifecycle, parent.cancel_reason = ACTIVE, None
    _touch(parent)
    audit("cashflow_entry", parent.id, "remove_split", _check_remarks(remarks))
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
