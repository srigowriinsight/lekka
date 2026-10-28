"""Recurring templates: rules that create dated items up to a planning horizon.

A template needs a title, head, inflow/outflow, amount, frequency, day and start date. The end,
split pattern and horizon are optional (horizon defaults to 12 months ahead). Items are created
once per (template, date), so running generation again never makes duplicates."""
import calendar
from datetime import date, timedelta
from decimal import Decimal

from dateutil.relativedelta import relativedelta
from flask import current_app

from ..extensions import db
from ..models import ACTIVE, IN, OUT, CashflowEntry, RecurringTemplate
from .common import LekkaError, audit, money, parse_date, snapshot, today, user_id
from .entries import _cancel_leaf, _check_head, _check_remarks, _check_title, _recompute_ancestors, create_entry, split_entry

FREQS = ("DAILY", "WEEKLY", "MONTHLY", "QUARTERLY", "YEARLY")
TPL_FIELDS = ["title", "head_id", "direction", "amount", "freq", "interval_n", "anchor_day",
              "start_date", "end_date", "max_occurrences", "horizon_until", "split_pattern", "is_active"]


# ---------------------------------------------------------------- dates

def _month_date(year, month, anchor):
    last = calendar.monthrange(year, month)[1]
    day = last if anchor == -1 or anchor > last else anchor
    return date(year, month, day)


def occurrence_dates(tpl: RecurringTemplate, upto: date):
    """Every scheduled date from the start date up to `upto`, honouring end date and count.
    A day that a month does not have (30th in February, 31st in April) falls on its last day."""
    out = []
    start, step = tpl.start_date, max(1, tpl.interval_n or 1)
    stop = min(upto, tpl.end_date) if tpl.end_date else upto
    limit = tpl.max_occurrences
    if tpl.freq == "DAILY":
        d = start
        while d <= stop and (limit is None or len(out) < limit):
            out.append(d)
            d += timedelta(days=step)
        return out
    if tpl.freq == "WEEKLY":
        anchor = start.weekday() if tpl.anchor_day is None else tpl.anchor_day
        d = start + timedelta(days=(anchor - start.weekday()) % 7)
        while d <= stop and (limit is None or len(out) < limit):
            out.append(d)
            d += timedelta(days=7 * step)
        return out
    months = {"MONTHLY": 1, "QUARTERLY": 3, "YEARLY": 12}[tpl.freq] * step
    anchor = start.day if tpl.anchor_day is None else tpl.anchor_day
    k = 0
    while True:
        m0 = start.year * 12 + (start.month - 1) + k * months
        d = _month_date(m0 // 12, m0 % 12 + 1, anchor)
        k += 1
        if d < start:
            continue
        if d > stop or (limit is not None and len(out) >= limit):
            return out
        out.append(d)


def horizon(tpl: RecurringTemplate) -> date:
    if tpl.horizon_until:
        return tpl.horizon_until
    return today() + relativedelta(months=current_app.config["DEFAULT_HORIZON_MONTHS"])


# ---------------------------------------------------------------- validation

def _validate(data: dict) -> dict:
    out = {}
    out["title"] = _check_title(data.get("title"))
    out["head_id"] = _check_head(data.get("head_id")).id
    if data.get("direction") not in (IN, OUT):
        raise LekkaError("Choose inflow or outflow.")
    out["direction"] = data["direction"]
    amt = money(data.get("amount"))
    if amt <= 0:
        raise LekkaError("The amount must be more than ₹0.")
    out["amount"] = amt
    freq = (data.get("freq") or "").upper()
    if freq not in FREQS:
        raise LekkaError("Choose a frequency.")
    out["freq"] = freq
    try:
        out["interval_n"] = int(data.get("interval_n") or 1)
    except ValueError:
        raise LekkaError("'Every' must be a whole number.")
    if out["interval_n"] < 1:
        raise LekkaError("'Every' must be 1 or more.")
    out["start_date"] = parse_date(data.get("start_date"), "start date")
    anchor = data.get("anchor_day")
    if anchor in (None, ""):
        out["anchor_day"] = None
    else:
        anchor = int(anchor)
        if freq == "WEEKLY" and not 0 <= anchor <= 6:
            raise LekkaError("Choose a weekday.")
        if freq in ("MONTHLY", "QUARTERLY", "YEARLY") and not (anchor == -1 or 1 <= anchor <= 31):
            raise LekkaError("Choose a day of the month from 1 to 31, or the last day.")
        out["anchor_day"] = anchor
    out["end_date"] = parse_date(data["end_date"], "end date") if data.get("end_date") else None
    if out["end_date"] and out["end_date"] < out["start_date"]:
        raise LekkaError("The end date is before the start date.")
    out["max_occurrences"] = int(data["max_occurrences"]) if data.get("max_occurrences") else None
    if out["max_occurrences"] is not None and out["max_occurrences"] < 1:
        raise LekkaError("The number of occurrences must be 1 or more.")
    out["horizon_until"] = parse_date(data["horizon_until"], "plan-until date") if data.get("horizon_until") else None
    pattern = data.get("split_pattern") or None
    if pattern:
        total = sum(Decimal(str(p["pct"])) for p in pattern)
        if total != 100 or any(Decimal(str(p["pct"])) <= 0 for p in pattern) or len(pattern) < 2:
            raise LekkaError("The split pattern needs two or more parts adding up to 100%.")
    out["split_pattern"] = pattern
    out["remarks"] = _check_remarks(data.get("remarks"))
    return out


# ---------------------------------------------------------------- generation

def _make_occurrence(tpl, d):
    e = create_entry(direction=tpl.direction, head_id=tpl.head_id, title=tpl.title, amount=tpl.amount,
                     planned_date=d, template_id=tpl.id, occurrence_date=d, audit_action=None)
    if tpl.split_pattern:
        parts, left = [], tpl.amount
        for i, p in enumerate(tpl.split_pattern):
            last = i == len(tpl.split_pattern) - 1
            amt = left if last else (tpl.amount * Decimal(str(p["pct"])) / 100).quantize(Decimal("0.01"))
            left -= amt
            parts.append({"title": f"{tpl.title} – {p['pct']}%", "amount": amt,
                          "planned_date": d + relativedelta(months=int(p.get("offset_months", 0)))})
        split_entry(e, parts, remarks="Split pattern from the template", log=False)
    return e


def dates_note(made) -> str:
    """'12 dates planned: 24 Oct 2026 – 24 Sep 2027' — one line in History instead of one row per date."""
    if not made:
        return "No new dates"
    ds = sorted(e.occurrence_date for e in made)
    span = f"{ds[0]:%d %b %Y}" if len(ds) == 1 else f"{ds[0]:%d %b %Y} – {ds[-1]:%d %b %Y}"
    return f"{len(ds)} date{'s' if len(ds) != 1 else ''} planned: {span}"


def generate(tpl: RecurringTemplate, log: bool = True):
    """Create the missing dates up to the horizon. Idempotent. With log, one History line sums them up."""
    if not tpl.is_active:
        return []
    upto = horizon(tpl)
    existing = {d for (d,) in db.session.query(CashflowEntry.occurrence_date)
                .filter(CashflowEntry.template_id == tpl.id)}
    blocked = set()
    if tpl.supersedes_id:   # dates still held by kept items of the template this one replaced
        blocked = {d for (d,) in db.session.query(CashflowEntry.occurrence_date)
                   .filter(CashflowEntry.template_id == tpl.supersedes_id)}
    made = []
    for d in occurrence_dates(tpl, upto):
        if d in existing or d in blocked or (tpl.resume_from and d < tpl.resume_from):
            continue
        made.append(_make_occurrence(tpl, d))
    if log and made:
        audit("recurring_template", tpl.id, "plan_dates", dates_note(made))
    return made


def extend_all():
    """Run daily (and on each grid load): keeps every active template filled to its horizon."""
    made = []
    for tpl in RecurringTemplate.query.filter_by(is_active=True).all():
        made.extend(generate(tpl))
    return made


def occurrences(tpl, after: date | None = None, from_date: date | None = None, include_deleted=False):
    q = CashflowEntry.query.filter(CashflowEntry.template_id == tpl.id, CashflowEntry.parent_id.is_(None))
    if not include_deleted:
        q = q.filter(CashflowEntry.lifecycle != "DELETED")
    if after:
        q = q.filter(CashflowEntry.occurrence_date > after)
    if from_date:
        q = q.filter(CashflowEntry.occurrence_date >= from_date)
    return q.order_by(CashflowEntry.occurrence_date).all()


def is_untouched(root: CashflowEntry) -> bool:
    """Created by the template and never handled by a person: safe to replace."""
    nodes = [root] + root.descendants()
    return (not root.is_overridden and root.lifecycle == ACTIVE
            and not any(n.is_settled or n.reschedule_count or n.lifecycle != ACTIVE for n in nodes))


def _delete_tree(root):
    for n in sorted([root] + root.descendants(), key=lambda x: -x.depth):
        if n.parent is not None:
            n.parent.children.remove(n)
        db.session.delete(n)


# ---------------------------------------------------------------- template actions

def create_template(data: dict) -> RecurringTemplate:
    v = _validate(data)
    tpl = RecurringTemplate(**v, created_by=user_id(), updated_by=user_id())
    db.session.add(tpl)
    db.session.flush()
    made = generate(tpl, log=False)
    audit("recurring_template", tpl.id, "create", dates_note(made), new=snapshot(tpl, TPL_FIELDS))
    return tpl


def update_template(tpl: RecurringTemplate, data: dict, scope: str, from_date=None):
    """scope 'series': change the rule; future untouched dates are replaced, handled ones kept.
    scope 'future': the old rule ends the day before `from_date`; a new rule starts there.
    Returns the handled (ticked, edited, split or rescheduled) items that were left unchanged."""
    v = _validate(data)
    t = today()
    if scope == "series":
        old = snapshot(tpl, TPL_FIELDS)
        for k, val in v.items():
            setattr(tpl, k, val)
        tpl.updated_by = user_id()
        kept = []
        for root in occurrences(tpl, after=t):
            if is_untouched(root):
                _delete_tree(root)
            else:
                kept.append(root)
        db.session.flush()
        made = generate(tpl, log=False)
        audit("recurring_template", tpl.id, "edit_series", dates_note(made), old=old, new=snapshot(tpl, TPL_FIELDS))
        return tpl, kept
    if scope == "future":
        from_date = parse_date(from_date, "date the change starts")
        if from_date <= tpl.start_date:
            raise LekkaError("The change starts on or before the first date; use 'whole series' instead.")
        kept = []
        for root in occurrences(tpl, from_date=from_date):
            if is_untouched(root):
                _delete_tree(root)
            else:
                kept.append(root)
        tpl.end_date = from_date - timedelta(days=1)
        tpl.updated_by = user_id()
        v["start_date"] = from_date
        new = RecurringTemplate(**v, supersedes_id=tpl.id, created_by=user_id(), updated_by=user_id())
        db.session.add(new)
        db.session.flush()
        made = generate(new, log=False)
        audit("recurring_template", tpl.id, "split_series", f"Changed from {from_date:%d %b %Y}; {dates_note(made)}",
              new=snapshot(new, TPL_FIELDS))
        return new, kept
    raise LekkaError("Choose which dates the change applies to.")


def pause(tpl: RecurringTemplate, on_date=None):
    on_date = parse_date(on_date) if on_date else today()
    for root in occurrences(tpl, after=on_date):
        if is_untouched(root):
            _delete_tree(root)
    tpl.is_active, tpl.paused_on = False, on_date
    audit("recurring_template", tpl.id, "pause", f"Paused from {on_date:%d %b %Y}")


def resume(tpl: RecurringTemplate, create_skipped: bool):
    """On resume, finance chooses: create the dates skipped while paused, or start from today."""
    paused_on = tpl.paused_on or today()
    tpl.is_active = True
    tpl.resume_from = paused_on + timedelta(days=1) if create_skipped else today()
    tpl.paused_on = None
    made = generate(tpl, log=False)
    audit("recurring_template", tpl.id, "resume",
          ("Skipped dates created; " if create_skipped else "Started again from today; ") + dates_note(made))
    return made


def end_series(tpl: RecurringTemplate, end_date, reason="series ended"):
    end_date = parse_date(end_date, "end date")
    tpl.end_date = end_date
    for root in occurrences(tpl, after=end_date):
        pending = [l for l in root.leaves() if not l.is_settled and l.lifecycle != "CANCELLED"]
        for l in pending:
            _cancel_leaf(l, reason)
        for l in pending:
            _recompute_ancestors(l)
    audit("recurring_template", tpl.id, "end", f"Ends {end_date:%d %b %Y}")


def delete_template(tpl: RecurringTemplate):
    """Delete the template. Dates nobody has handled are removed. Dates someone ticked, moved, changed or
    cancelled are real history: they stay as ordinary one-off items, no longer linked to the template.
    Returns (removed, kept) counts."""
    removed = kept = 0
    for r in occurrences(tpl, include_deleted=True):
        if r.lifecycle == "DELETED":            # already gone from view; just unlink it
            for n in [r] + r.descendants():
                n.template_id, n.occurrence_date = None, None
            continue
        if is_untouched(r):
            _delete_tree(r)
            removed += 1
        else:
            for n in [r] + r.descendants():
                if n.template_id == tpl.id:
                    n.template_id, n.occurrence_date = None, None
            kept += 1
    for other in RecurringTemplate.query.filter_by(supersedes_id=tpl.id).all():
        other.supersedes_id = None
    audit("recurring_template", tpl.id, "delete", f"{removed} planned date(s) removed, {kept} handled item(s) kept",
          old=snapshot(tpl, TPL_FIELDS))
    db.session.delete(tpl)
    return removed, kept
