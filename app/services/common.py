"""Shared helpers: today's date in IST, money parsing, user id, audit log, errors."""
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from flask import current_app, has_request_context

from ..extensions import db
from ..models import AuditLog

PAISA = Decimal("0.01")


class LekkaError(ValueError):
    """A rule was broken; the message is shown to the user as is."""


def today() -> date:
    fixed = current_app.config.get("FIXED_TODAY")
    if fixed:
        return fixed if isinstance(fixed, date) else date.fromisoformat(fixed)
    return datetime.now(ZoneInfo(current_app.config.get("TIMEZONE", "Asia/Kolkata"))).date()


def money(value) -> Decimal:
    """Parse an amount typed by a person: '1,00,000' / '100000' / '1000.50'."""
    if isinstance(value, Decimal):
        d = value
    else:
        text = str(value or "").replace(",", "").replace("₹", "").strip()
        if not text:
            raise LekkaError("Enter an amount.")
        try:
            d = Decimal(text)
        except InvalidOperation:
            raise LekkaError(f"'{value}' is not a valid amount; type digits only, for example 1,00,000.")
    if not d.is_finite():
        raise LekkaError("Enter a valid amount.")
    return d.quantize(PAISA, rounding=ROUND_HALF_UP)


def parse_date(value, field="date") -> date:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip())
    except (TypeError, ValueError):
        raise LekkaError(f"Enter a valid {field}.")


def user_id():
    if has_request_context():
        from flask_login import current_user
        if getattr(current_user, "is_authenticated", False):
            return current_user.id
    return None


def _jsonable(v):
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    return v


def snapshot(obj, fields):
    return {f: _jsonable(getattr(obj, f)) for f in fields}


def audit(table, record_id, action, remarks=None, old=None, new=None):
    db.session.add(AuditLog(
        table_name=table, record_id=record_id, action=action,
        remarks=(remarks or None), old_json=old, new_json=new, user_id=user_id(),
    ))


def inr(value, compact=False) -> str:
    """Format with Indian digit grouping: 1234567.5 -> 12,34,567.50 (paise only when present)."""
    if value is None:
        return ""
    d = Decimal(value).quantize(PAISA)
    neg = d < 0
    d = -d if neg else d
    if compact:
        if d >= 10_000_000:
            s = f"{(d / 10_000_000):.2f}".rstrip("0").rstrip(".") + "Cr"
        elif d >= 100_000:
            s = f"{(d / 100_000):.2f}".rstrip("0").rstrip(".") + "L"
        elif d >= 1000:
            s = f"{(d / 1000):.1f}".rstrip("0").rstrip(".") + "K"
        else:
            s = f"{d:.0f}"
        return ("−" if neg else "") + s
    whole, frac = f"{d:.2f}".split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups + [tail])
    s = whole if frac == "00" else f"{whole}.{frac}"
    return ("−" if neg else "") + s
