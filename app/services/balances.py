"""The balance engine behind the grid.

One rule decides what counts (Design tab, section 7):
  * a ticked item counts on its settled date (always today or earlier);
  * an unticked item counts on its planned date, but only if that date is after today;
  * an unticked item dated today or earlier counts nowhere until it is ticked or rescheduled
    (it is still shown, amber or red, in its cell).
Adjustments count on their date (today or earlier). Cancelled items never count.
Closing = Opening + In - Out + Adjustments, and each opening is the previous closing.
"""
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from dateutil.relativedelta import relativedelta
from sqlalchemy import and_, exists, or_
from sqlalchemy.orm import aliased

from ..extensions import db
from ..models import ACTIVE, CANCELLED, DELETED, IN, OUT, BalanceAdjustment, CashflowEntry, Head
from .common import today
from .entries import get_account, status

ZERO = Decimal("0")
FREQS = ("day", "week", "month", "quarter", "year", "custom")


# ---------------------------------------------------------------- periods

def fy_start(d: date) -> date:
    return date(d.year if d.month >= 4 else d.year - 1, 4, 1)


def periods(start: date, end: date, freq: str):
    """Columns covering [start, end]. Weeks run Monday-Sunday; quarters and years follow the
    Indian financial year (April-March)."""
    if end < start:
        start, end = end, start
    out = []
    if freq == "custom":
        return [(start, end, f"{start:%d %b %Y} – {end:%d %b %Y}")]
    if freq == "day":
        d = start
        while d <= end:
            out.append((d, d, f"{d:%a} {d.day} {d:%b}"))
            d += timedelta(days=1)
        return out
    if freq == "week":
        d = start - timedelta(days=start.weekday())
        while d <= end:
            e = d + timedelta(days=6)
            out.append((d, e, f"{d.day} {d:%b} – {e.day} {e:%b}"))
            d = e + timedelta(days=1)
        return out
    if freq == "month":
        d = start.replace(day=1)
        while d <= end:
            e = d + relativedelta(months=1) - timedelta(days=1)
            out.append((d, e, f"{d:%b %Y}"))
            d = e + timedelta(days=1)
        return out
    if freq == "quarter":
        fy = fy_start(start)
        d = fy + relativedelta(months=((start.year * 12 + start.month) - (fy.year * 12 + fy.month)) // 3 * 3)
        while d <= end:
            e = d + relativedelta(months=3) - timedelta(days=1)
            q = ((d.year * 12 + d.month) - (fy_start(d).year * 12 + 4)) // 3 + 1
            y = fy_start(d).year
            out.append((d, e, f"Q{q} FY{str(y)[2:]}-{str(y + 1)[2:]}"))
            d = e + timedelta(days=1)
        return out
    if freq == "year":
        d = fy_start(start)
        while d <= end:
            e = d + relativedelta(years=1) - timedelta(days=1)
            out.append((d, e, f"FY {d.year}-{str(d.year + 1)[2:]}"))
            d = e + timedelta(days=1)
        return out
    raise ValueError(f"Unknown frequency {freq}")


# ---------------------------------------------------------------- queries

def _leaf_query():
    child = aliased(CashflowEntry)
    return CashflowEntry.query.filter(~exists().where(child.parent_id == CashflowEntry.id))


def leaves_between(a: date, b: date, include_cancelled=False):
    """Leaves whose display date (settled date once ticked, else planned date) is in [a, b]."""
    q = _leaf_query().filter(or_(
        and_(CashflowEntry.is_settled.is_(True), CashflowEntry.settled_on.between(a, b)),
        and_(CashflowEntry.is_settled.is_(False), CashflowEntry.planned_date.between(a, b)),
    ))
    q = q.filter(CashflowEntry.lifecycle != DELETED)
    if not include_cancelled:
        q = q.filter(CashflowEntry.lifecycle != CANCELLED)
    return q.all()


def display_date(e) -> date:
    return e.settled_on if e.is_settled else e.planned_date


def counts(e, t: date) -> bool:
    if e.lifecycle in (CANCELLED, DELETED):
        return False
    return bool(e.is_settled) or e.planned_date > t


def signed(e) -> Decimal:
    return e.amount if e.direction == IN else -e.amount


def adjustments_between(a: date, b: date):
    return BalanceAdjustment.query.filter(BalanceAdjustment.adj_date.between(a, b)).all()


def balance_before(d: date, t: date | None = None) -> Decimal:
    """Opening balance of day d: account opening + everything that counts from the opening
    date up to d - 1. Items before the account's opening date are already inside it."""
    t = t or today()
    acc = get_account()
    bal = Decimal(acc.opening_balance)
    if d <= acc.opening_date:
        return bal
    last = d - timedelta(days=1)
    for e in leaves_between(acc.opening_date, last):
        if counts(e, t):
            bal += signed(e)
    for adj in adjustments_between(acc.opening_date, last):
        bal += adj.amount
    return bal


def closing_on(d: date, t: date | None = None) -> Decimal:
    return balance_before(d + timedelta(days=1), t)


# ---------------------------------------------------------------- the grid

@dataclass
class Cell:
    amount: Decimal = ZERO          # what counts in the balance
    shown: Decimal = ZERO           # every live item in the cell, counted or not
    items: list = field(default_factory=list)
    by_status: dict = field(default_factory=lambda: defaultdict(lambda: ZERO))
    cancelled: list = field(default_factory=list)   # shown struck through, never counted

    @property
    def cancelled_amount(self):
        return sum((e.amount for e in self.cancelled), ZERO)

    @property
    def count(self):
        return len(self.items)

    @property
    def has_overdue(self):
        return self.by_status.get("OVERDUE", ZERO) > 0

    @property
    def has_due_soon(self):
        return self.by_status.get("DUE_SOON", ZERO) > 0

    @property
    def single_status(self):
        st = {s for s, v in self.by_status.items() if v > 0}
        return st.pop() if len(st) == 1 else None

    def bar(self):
        """Share of money per status, for the status bar: [(status, percent)]."""
        total = sum(self.by_status.values(), ZERO)
        if total <= 0:
            return []
        order = ["SETTLED", "DUE_SOON", "OVERDUE", "PLANNED"]
        return [(s, float(self.by_status[s] / total * 100)) for s in order if self.by_status.get(s, ZERO) > 0]


@dataclass
class Column:
    start: date
    end: date
    label: str
    basis: str
    opening: Decimal = ZERO
    total_in: Decimal = ZERO
    total_out: Decimal = ZERO
    adjustment: Decimal = ZERO
    adjustments: list = field(default_factory=list)
    closing: Decimal = ZERO
    is_empty: bool = True


@dataclass
class Grid:
    columns: list
    rows: dict        # direction -> list of (head, {col_index: Cell}, items_by_entry)
    today: date
    freq: str


def build_grid(start: date, end: date, freq: str = "day", t: date | None = None,
               item_filter: str = "all", hide_empty: bool = False, cost_centre_id=None) -> Grid:
    t = t or today()
    cols = []
    for a, b, label in periods(start, end, freq):
        basis = "actual" if b <= t else ("planned" if a > t else "mixed")
        cols.append(Column(a, b, label, basis))
    if not cols:
        return Grid([], {IN: [], OUT: []}, t, freq)
    range_a, range_b = cols[0].start, cols[-1].end

    acc = get_account()
    leaves = [e for e in leaves_between(range_a, range_b, include_cancelled=True)
              if display_date(e) and display_date(e) >= acc.opening_date]
    if cost_centre_id:
        leaves = [e for e in leaves if e.cost_centre_id == int(cost_centre_id)]
    adjs = adjustments_between(max(range_a, acc.opening_date), range_b)

    def col_index(d):
        for i, c in enumerate(cols):
            if c.start <= d <= c.end:
                return i
        return None

    cells = {IN: defaultdict(lambda: defaultdict(Cell)), OUT: defaultdict(lambda: defaultdict(Cell))}
    item_rows = {IN: defaultdict(dict), OUT: defaultdict(dict)}   # head -> entry -> {col: Cell}
    heads_used = {IN: set(), OUT: set()}
    for e in leaves:
        i = col_index(display_date(e))
        if i is None:
            continue
        if e.lifecycle == CANCELLED:       # visible, struck through grey; no effect on any balance
            if item_filter != "all":
                continue
            cells[e.direction][e.head_id][i].cancelled.append(e)
            ic = item_rows[e.direction][e.head_id].setdefault(e.id, {"entry": e, "cells": {}})
            ic["cells"][i] = {"amount": e.amount, "status": "CANCELLED", "counted": False}
            heads_used[e.direction].add(e.head_id)
            continue
        st = status(e, t)
        counted = counts(e, t)
        visible = (item_filter == "all" or (item_filter == "pending" and not e.is_settled)
                   or (item_filter == "settled" and e.is_settled))
        cols[i].is_empty = False
        if counted:
            if e.direction == IN:
                cols[i].total_in += e.amount
            else:
                cols[i].total_out += e.amount
        if not visible:
            continue
        cell = cells[e.direction][e.head_id][i]
        if counted:
            cell.amount += e.amount
        cell.shown += e.amount
        cell.items.append(e)
        cell.by_status[st] += e.amount
        ic = item_rows[e.direction][e.head_id].setdefault(e.id, {"entry": e, "cells": {}})
        ic["cells"][i] = {"amount": e.amount, "status": st, "counted": counted}
        heads_used[e.direction].add(e.head_id)

    for adj in adjs:
        i = col_index(adj.adj_date)
        if i is not None:
            cols[i].adjustment += adj.amount
            cols[i].adjustments.append(adj)
            cols[i].is_empty = False

    bal = balance_before(range_a, t)
    for c in cols:
        c.opening = bal
        c.closing = bal + c.total_in - c.total_out + c.adjustment
        bal = c.closing

    if hide_empty and freq == "day":
        keep = [i for i, c in enumerate(cols) if not c.is_empty or c.start == t]
        remap = {old: new for new, old in enumerate(keep)}
        cols = [cols[i] for i in keep]
        for d in (IN, OUT):
            for h in list(cells[d]):
                cells[d][h] = defaultdict(Cell, {remap[i]: v for i, v in cells[d][h].items() if i in remap})
                for ent in item_rows[d][h].values():
                    ent["cells"] = {remap[i]: v for i, v in ent["cells"].items() if i in remap}

    heads = {h.id: h for h in Head.query.all()}
    rows = {}
    for d in (IN, OUT):
        ordered = sorted(heads_used[d], key=lambda hid: (heads[hid].sort_order, heads[hid].name.lower()))
        rows[d] = [(heads[hid], cells[d][hid],
                    sorted(item_rows[d][hid].values(),
                           key=lambda x: (min(x["cells"]) if x["cells"] else 0, x["entry"].id)))
                   for hid in ordered]
    return Grid(cols, rows, t, freq)


def cell_items(head_id, direction, a: date, b: date, t: date | None = None):
    """Items behind one grid cell, overdue first, then due soon, planned, settled; cancelled last."""
    t = t or today()
    acc = get_account()
    items = [e for e in leaves_between(a, b, include_cancelled=True)
             if e.head_id == int(head_id) and e.direction == direction and display_date(e) >= acc.opening_date]
    order = {"OVERDUE": 0, "DUE_SOON": 1, "PLANNED": 2, "SETTLED": 3, "CANCELLED": 4}
    items.sort(key=lambda e: (order[status(e, t)], display_date(e), e.id))
    return items


def overdue_items(t: date | None = None):
    """Unticked, live leaves whose planned date has passed: the end-of-day review list."""
    t = t or today()
    q = _leaf_query().filter(CashflowEntry.is_settled.is_(False), CashflowEntry.lifecycle == ACTIVE,
                             CashflowEntry.planned_date < t)
    return q.order_by(CashflowEntry.planned_date, CashflowEntry.id).all()


def due_today_items(t: date | None = None):
    t = t or today()
    q = _leaf_query().filter(CashflowEntry.is_settled.is_(False), CashflowEntry.lifecycle == ACTIVE,
                             CashflowEntry.planned_date == t)
    return q.order_by(CashflowEntry.id).all()
