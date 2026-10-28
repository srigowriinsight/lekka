"""Reset a period: remove everything that was *entered* in a time window (for example test entries made
today between 2 and 4 pm). Items entered earlier stay, even if they were changed in that window.

Removed: expenses and revenue entered then (with all their parts), recurring templates added then (with
every date they planned), and balance adjustments added then. Items are hidden the same way as Delete, so
History still shows what was removed."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from flask import current_app

from ..extensions import db
from ..models import BalanceAdjustment, CashflowEntry, RecurringTemplate
from .common import LekkaError, audit, inr
from .entries import delete_adjustment, delete_entry, get_account


def parse_window(start: str, end: str):
    """'2026-10-27T14:00' in Indian time -> UTC times as stored."""
    tz = ZoneInfo(current_app.config.get("TIMEZONE", "Asia/Kolkata"))
    try:
        a = datetime.fromisoformat(start).replace(tzinfo=tz)
        b = datetime.fromisoformat(end).replace(tzinfo=tz)
    except (TypeError, ValueError):
        raise LekkaError("Enter both the start and the end of the period.")
    if b <= a:
        raise LekkaError("The end of the period must be after its start.")
    to_utc = lambda d: d.astimezone(timezone.utc).replace(tzinfo=None)
    return to_utc(a), to_utc(b), a, b


def find(a, b):
    """What a reset of [a, b) (UTC) would remove."""
    templates = (RecurringTemplate.query.filter(RecurringTemplate.created_at >= a, RecurringTemplate.created_at < b)
                 .order_by(RecurringTemplate.created_at).all())
    items = (CashflowEntry.query.filter(CashflowEntry.parent_id.is_(None), CashflowEntry.template_id.is_(None),
                                        CashflowEntry.lifecycle != "DELETED",
                                        CashflowEntry.created_at >= a, CashflowEntry.created_at < b)
             .order_by(CashflowEntry.created_at).all())
    adjustments = (BalanceAdjustment.query.filter(BalanceAdjustment.created_at >= a, BalanceAdjustment.created_at < b)
                   .order_by(BalanceAdjustment.created_at).all())
    tpl_dates = {t.id: CashflowEntry.query.filter(CashflowEntry.template_id == t.id,
                                                  CashflowEntry.parent_id.is_(None),
                                                  CashflowEntry.lifecycle != "DELETED").count() for t in templates}
    return {"items": items, "templates": templates, "tpl_dates": tpl_dates, "adjustments": adjustments}


def run(a, b, label: str):
    found = find(a, b)
    n_items = n_dates = 0
    for e in found["items"]:
        delete_entry(e, remarks=f"Removed by the reset of {label}")
        n_items += 1
    for t in found["templates"]:
        for r in CashflowEntry.query.filter(CashflowEntry.template_id == t.id, CashflowEntry.parent_id.is_(None)).all():
            if not r.is_deleted:
                delete_entry(r, remarks=f"Removed by the reset of {label}")
                n_dates += 1
            for n in [r] + r.descendants():
                n.template_id, n.occurrence_date = None, None
        for other in RecurringTemplate.query.filter_by(supersedes_id=t.id).all():
            other.supersedes_id = None
        audit("recurring_template", t.id, "delete", f"Removed by the reset of {label}", old={"title": t.title})
        db.session.delete(t)
    total_adj = sum((x.amount for x in found["adjustments"]), 0)
    for x in found["adjustments"]:
        delete_adjustment(x)
    parts = [f"{n_items} item(s)", f"{len(found['templates'])} recurring template(s) with {n_dates} planned date(s)",
             f"{len(found['adjustments'])} adjustment(s)" + (f" (₹{inr(total_adj)})" if found["adjustments"] else "")]
    summary = f"Reset {label}: removed " + ", ".join(parts)
    audit("account", get_account().id, "reset_period", summary)
    return summary
