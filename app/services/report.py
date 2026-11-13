"""Review page: one filterable list of money in and out (each dated payment: a part of a split item counts on its
own), with totals. Filters: dates, expense/revenue, paid/unpaid/overdue, parties, heads (with their sub-heads),
categories (with their sub-categories), in any combination; optionally grouped."""
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import case, exists
from sqlalchemy.orm import aliased

from ..extensions import db
from ..models import IN, OUT, CashflowEntry, Category, Counterparty, Head
from .common import today

PRESETS = [("this_month", "This month"), ("last_month", "Last month"), ("next_30", "Next 30 days"),
           ("this_quarter", "This quarter"), ("this_fy", "This year (Apr–Mar)")]
STATUSES = [("all", "All"), ("paid", "Paid"), ("unpaid", "Unpaid"), ("overdue", "Overdue")]
TYPES = [("all", "All"), ("OUT", "Expense"), ("IN", "Revenue")]
GROUPS = [("", "No grouping"), ("date", "Month"), ("party", "Party"), ("head", "Head"), ("category", "Category")]


def _month_start(d):
    return d.replace(day=1)


def _month_end(d):
    nxt = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
    return nxt - timedelta(days=1)


def preset_range(name, t=None):
    t = t or today()
    if name == "last_month":
        last = _month_start(t) - timedelta(days=1)
        return _month_start(last), last
    if name == "next_30":
        return t, t + timedelta(days=30)
    if name == "this_quarter":                  # Apr–Jun, Jul–Sep, Oct–Dec, Jan–Mar
        start = date(t.year, (t.month - 1) // 3 * 3 + 1, 1)
        return start, _month_end(date(t.year, start.month + 2, 1))
    if name == "this_fy":
        y = t.year if t.month >= 4 else t.year - 1
        return date(y, 4, 1), date(y + 1, 3, 31)
    return _month_start(t), _month_end(t)       # this_month


def _ids(args, key):
    out = []
    for v in args.getlist(key):
        out.extend(x for x in str(v).split(",") if x.strip())
    return [x.strip() for x in out]


def params(args):
    # a quick-range button wins over the dates in the form; then the range kept from before; then own dates
    preset = args.get("preset_btn") or args.get("preset") or ("" if args.get("start") or args.get("end") else "this_month")
    if preset:
        start, end = preset_range(preset)
    else:
        try:
            start = date.fromisoformat(args.get("start")) if args.get("start") else _month_start(today())
            end = date.fromisoformat(args.get("end")) if args.get("end") else _month_end(today())
        except ValueError:
            start, end = _month_start(today()), _month_end(today())
        if end < start:
            start, end = end, start
    if preset not in dict(PRESETS):
        preset = "" if args.get("start") or args.get("end") else "this_month"
    status = args.get("status") or "all"
    return {
        "preset": preset, "start": start, "end": end,
        "type": args.get("type") if args.get("type") in (IN, OUT) else "all",
        "status": status if status in dict(STATUSES) else "all",
        "parties": _ids(args, "party"), "heads": _ids(args, "head"), "cats": _ids(args, "cat"),
        "group": args.get("group") if args.get("group") in dict(GROUPS) else "",
    }


@dataclass
class Group:
    label: str
    rows: list = field(default_factory=list)
    money_in: Decimal = Decimal("0")
    money_out: Decimal = Decimal("0")


@dataclass
class Result:
    rows: list
    groups: list
    money_in: Decimal
    money_out: Decimal
    unpaid_amount: Decimal
    unpaid_count: int
    overdue_count: int

    @property
    def net(self):
        return self.money_in - self.money_out


def when(e):
    return e.settled_on if e.is_settled else e.planned_date


def run(p) -> Result:
    child = aliased(CashflowEntry)
    day = case((CashflowEntry.is_settled.is_(True), CashflowEntry.settled_on), else_=CashflowEntry.planned_date)
    q = CashflowEntry.query.filter(
        CashflowEntry.lifecycle == "ACTIVE",
        ~exists().where(child.parent_id == CashflowEntry.id),
        day >= p["start"], day <= p["end"])
    if p["type"] in (IN, OUT):
        q = q.filter(CashflowEntry.direction == p["type"])
    t = today()
    if p["status"] == "paid":
        q = q.filter(CashflowEntry.is_settled.is_(True))
    elif p["status"] == "unpaid":
        q = q.filter(CashflowEntry.is_settled.is_(False))
    elif p["status"] == "overdue":
        q = q.filter(CashflowEntry.is_settled.is_(False), CashflowEntry.planned_date < t)
    if p["parties"]:
        ids = [int(x) for x in p["parties"] if x.isdigit()]
        conds = [CashflowEntry.counterparty_id.in_(ids)] if ids else []
        if "none" in p["parties"]:
            conds.append(CashflowEntry.counterparty_id.is_(None))
        q = q.filter(db.or_(*conds))
    if p["heads"]:
        ids = set()
        for x in p["heads"]:
            h = db.session.get(Head, int(x)) if x.isdigit() else None
            if h:
                ids.update(h.subtree_ids())
        q = q.filter(CashflowEntry.head_id.in_(ids or [-1]))
    if p["cats"]:
        ids = set()
        for x in p["cats"]:
            c = db.session.get(Category, int(x)) if x.isdigit() else None
            if c:
                ids.update(c.subtree_ids())
        conds = [CashflowEntry.category_id.in_(ids)] if ids else []
        if "none" in p["cats"]:
            conds.append(CashflowEntry.category_id.is_(None))
        q = q.filter(db.or_(*conds) if conds else CashflowEntry.id < 0)
    rows = sorted(q.all(), key=lambda e: (when(e), e.direction, e.id))

    money_in = sum((e.amount for e in rows if e.direction == IN), Decimal("0"))
    money_out = sum((e.amount for e in rows if e.direction == OUT), Decimal("0"))
    unpaid = [e for e in rows if not e.is_settled]
    res = Result(rows, [], money_in, money_out, sum((e.amount for e in unpaid), Decimal("0")), len(unpaid),
                 len([e for e in unpaid if e.planned_date < t]))

    if p["group"]:
        groups = OrderedDict()
        for e in rows:
            if p["group"] == "date":
                key, label = (when(e).year, when(e).month), f"{when(e):%B %Y}"
            elif p["group"] == "party":
                key = label = e.counterparty.name if e.counterparty else "No party"
            elif p["group"] == "head":
                key = label = e.head.path() if e.head else "—"
            else:
                key = label = e.category.path() if e.category else "No category"
            g = groups.setdefault(key, Group(label))
            g.rows.append(e)
            if e.direction == IN:
                g.money_in += e.amount
            else:
                g.money_out += e.amount
        order = sorted(groups) if p["group"] == "date" else sorted(groups, key=lambda k: (k in ("No party", "No category"), str(k).lower()))
        res.groups = [groups[k] for k in order]
    return res


def choices():
    from .categories import tree_order as cat_tree
    from .heads import tree_order as head_tree
    return {
        "parties": Counterparty.query.order_by(Counterparty.name).all(),
        "heads": head_tree(),
        "cats": cat_tree(),
    }
