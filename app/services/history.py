"""History in plain words: friendly labels, Indian time, and one line for a batch of recurring dates."""
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from flask import current_app

from ..extensions import db
from ..models import CashflowEntry, Head, RecurringTemplate

LABELS = {
    ("cashflow_entry", "create"): "Added",
    ("cashflow_entry", "generate"): "Dates planned",
    ("cashflow_entry", "settle"): "Ticked",
    ("cashflow_entry", "link_settle"): "Ticked (money already moved)",
    ("cashflow_entry", "unsettle"): "Unticked",
    ("cashflow_entry", "reschedule"): "Rescheduled",
    ("cashflow_entry", "split"): "Split",
    ("cashflow_entry", "edit_split"): "Split changed",
    ("cashflow_entry", "remove_split"): "Split removed",
    ("cashflow_entry", "cancel"): "Cancelled",
    ("cashflow_entry", "edit"): "Changed",
    ("cashflow_entry", "delete"): "Deleted",
    ("cashflow_entry", "receive_moved_amount"): "Amount moved in",
    ("recurring_template", "create"): "Recurring template added",
    ("recurring_template", "plan_dates"): "Dates planned",
    ("recurring_template", "edit_series"): "Recurring template changed",
    ("recurring_template", "split_series"): "Recurring template changed from a date",
    ("recurring_template", "pause"): "Paused",
    ("recurring_template", "resume"): "Resumed",
    ("recurring_template", "end"): "Series ended",
    ("recurring_template", "delete"): "Recurring template deleted",
    ("account", "edit"): "Opening balance changed",
    ("account", "reset_period"): "Period reset",
    ("balance_adjustment", "create"): "Adjustment added",
    ("balance_adjustment", "edit"): "Adjustment changed",
    ("balance_adjustment", "delete"): "Adjustment removed",
    ("head", "create"): "Head added",
    ("head", "edit"): "Head renamed",
    ("head", "activate"): "Head activated",
    ("head", "deactivate"): "Head deactivated",
}

KINDS = {"cashflow_entry": "Item", "recurring_template": "Recurring template", "account": "Account",
         "balance_adjustment": "Adjustment", "head": "Head", "cost_centre": "Cost centre",
         "counterparty": "Counterparty"}


# History filters: what the user picks -> which audit rows it means.
KIND_TABLES = {"items": ["cashflow_entry"], "templates": ["recurring_template"],
               "account": ["account", "balance_adjustment"], "heads": ["head"]}
WHAT_ACTIONS = {
    "added": ["create", "generate", "plan_dates"],
    "ticked": ["settle", "link_settle"],
    "unticked": ["unsettle"],
    "rescheduled": ["reschedule"],
    "split": ["split", "edit_split", "remove_split", "receive_moved_amount"],
    "cancelled": ["cancel", "end"],
    "changed": ["edit", "edit_series", "split_series", "pause", "resume", "activate", "deactivate"],
    "deleted": ["delete", "reset_period"],
}


def utc_bounds(day_from, day_to):
    """Indian calendar days -> UTC times, because audit times are stored in UTC."""
    tz = ZoneInfo(current_app.config.get("TIMEZONE", "Asia/Kolkata"))
    a = b = None
    if day_from:
        a = datetime.combine(day_from, datetime.min.time(), tz).astimezone(timezone.utc).replace(tzinfo=None)
    if day_to:
        b = (datetime.combine(day_to, datetime.min.time(), tz) + timedelta(days=1)).astimezone(timezone.utc)
        b = b.replace(tzinfo=None)
    return a, b


def label(table: str, action: str) -> str:
    return LABELS.get((table, action)) or action.replace("_", " ").capitalize()


def to_local(dt: datetime) -> datetime:
    """Audit times are stored in UTC; show them in Indian time."""
    if dt is None:
        return dt
    tz = ZoneInfo(current_app.config.get("TIMEZONE", "Asia/Kolkata"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(tz)


def when(dt: datetime) -> str:
    d = to_local(dt)
    return f"{d.day} {d:%b %Y}, {d:%I:%M %p}".replace(" 0", " ") if d else ""


@dataclass
class Row:
    at: datetime
    what: str
    kind: str
    name: str
    remarks: str | None
    who: str
    entry_id: int | None = None
    count: int = 1
    dates: list = field(default_factory=list)


def _name(h, cache):
    j = h.new_json or h.old_json or {}
    if isinstance(j, dict) and j.get("title"):
        return j["title"]
    key = (h.table_name, h.record_id)
    if key not in cache:
        model = {"cashflow_entry": CashflowEntry, "recurring_template": RecurringTemplate, "head": Head}.get(h.table_name)
        obj = db.session.get(model, h.record_id) if model else None
        cache[key] = getattr(obj, "title", None) or getattr(obj, "name", None) or ""
    if h.table_name == "balance_adjustment" and isinstance(j, dict) and j.get("amount"):
        return f"₹{j['amount']}"
    return cache[key]


def build(raw) -> list[Row]:
    """Turn audit rows (newest first) into History lines. Older data logged each recurring date on its own
    line; those runs are folded into one 'Dates planned' line, joined with the template line next to them."""
    cache, out = {}, []
    for h in raw:
        who = h.user.name if h.user else "—"
        name = _name(h, cache)
        prev = out[-1] if out else None
        if h.table_name == "cashflow_entry" and h.action == "generate":
            pd = (h.new_json or {}).get("planned_date")
            if (prev and prev.what == "Dates planned" and prev.kind == "Recurring template" and prev.name == name
                    and prev.who == who and abs(prev.at - h.at) <= timedelta(minutes=2)):
                prev.count += 1
                if pd:
                    prev.dates.append(pd)
                continue
            out.append(Row(h.at, "Dates planned", "Recurring template", name, None, who, dates=[pd] if pd else []))
            continue
        if (h.table_name == "recurring_template" and h.action == "create" and prev and prev.what == "Dates planned"
                and prev.name == name and prev.who == who and abs(prev.at - h.at) <= timedelta(minutes=2)
                and prev.dates):
            prev.what, prev.at = "Recurring template added", h.at     # one line for "added + its dates"
            continue
        out.append(Row(h.at, label(h.table_name, h.action), KINDS.get(h.table_name, h.table_name), name, h.remarks,
                       who, entry_id=h.record_id if h.table_name == "cashflow_entry" else None))
    for r in out:
        if r.dates:
            ds = sorted(datetime.fromisoformat(d).date() for d in r.dates)
            span = f"{ds[0]:%d %b %Y}" if len(ds) == 1 else f"{ds[0]:%d %b %Y} – {ds[-1]:%d %b %Y}"
            r.remarks = f"{len(ds)} date{'s' if len(ds) != 1 else ''} planned: {span}"
    return out
