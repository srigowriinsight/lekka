"""The worked example and the balance rule (Test cases tab, sections 1, 3, 4, 10, 13, 14, 17)."""
from datetime import date
from decimal import Decimal

import pytest

from app.demo import load_demo
from app.extensions import db
from app.models import IN, OUT, CashflowEntry
from app.services.balances import build_grid, cell_items, closing_on, periods
from app.services.entries import (add_adjustment, create_entry, get_account, reschedule, settle,
                                  unsettle)
from app.services.common import LekkaError

D = Decimal


@pytest.fixture
def demo(app):
    load_demo(shift=False)   # today is 25 Nov 2026 in TestConfig
    db.session.commit()


def by_title(title):
    return CashflowEntry.query.filter_by(title=title).order_by(CashflowEntry.id).first()


def month_closings():
    g = build_grid(date(2026, 11, 1), date(2027, 1, 31), "month")
    return [c.closing for c in g.columns], g


def test_worked_example_month_view(demo):
    closings, g = month_closings()
    assert closings == [D("2065000"), D("1140000"), D("165000")]
    nov = g.columns[0]
    assert (nov.opening, nov.total_in, nov.total_out) == (D("2500000"), D("915000"), D("1350000"))
    assert g.columns[1].opening == D("2065000")          # TC-124
    assert [c.basis for c in g.columns] == ["mixed", "planned", "planned"]


def test_day_view_actual_then_planned(demo):
    assert closing_on(date(2026, 11, 15)) == D("2605000")   # TC-120: INV-388 not counted
    assert closing_on(date(2026, 11, 20)) == D("2965000")
    assert closing_on(date(2026, 11, 25)) == D("2965000")   # today: Instalment 1 unticked
    assert closing_on(date(2026, 11, 30)) == D("2065000")   # TC-122: nothing carried forward


def test_tick_today_changes_today_and_later(demo):           # TC-25, TC-121, TC-26
    settle(by_title("Instalment 1"), date(2026, 11, 25))
    assert closing_on(date(2026, 11, 25)) == D("2715000")
    assert closing_on(date(2026, 11, 30)) == D("1815000")
    unsettle(by_title("Instalment 1"))                        # TC-129
    assert closing_on(date(2026, 11, 25)) == D("2965000")


def test_reschedule_both_into_december(demo):
    reschedule(by_title("Instalment 1"), date(2026, 12, 2))
    reschedule(by_title("Invoice INV-388"), date(2026, 12, 15))
    closings, _ = month_closings()
    assert closings[:2] == [D("2065000"), D("1090000")]
    inv = by_title("Invoice INV-388")
    assert inv.original_planned_date == date(2026, 11, 15) and inv.reschedule_count == 1


def test_reschedule_single_invoice(demo):                     # TC-36
    reschedule(by_title("Invoice INV-388"), date(2026, 12, 15), "Customer asked for more time")
    closings, _ = month_closings()
    assert closings[:2] == [D("2065000"), D("1340000")]


def test_reschedule_rules(demo):                              # TC-37, TC-38, TC-39
    inv = by_title("Invoice INV-388")
    with pytest.raises(LekkaError):
        reschedule(inv, date(2026, 11, 20))
    with pytest.raises(LekkaError):
        reschedule(by_title("Instalment 1"), date(2026, 11, 25))
    with pytest.raises(LekkaError):
        reschedule(by_title("First payment"), date(2026, 12, 1))


def test_parent_reschedule_moves_unticked_parts(demo):        # TC-40
    reschedule(by_title("Balance"), date(2026, 12, 2))
    assert by_title("Instalment 1").planned_date == date(2026, 12, 2)
    assert by_title("Instalment 2").planned_date == date(2026, 12, 17)


def test_tick_with_earlier_date(demo):                        # TC-128
    settle(by_title("Invoice INV-388"), date(2026, 11, 24))
    assert closing_on(date(2026, 11, 24)) == D("3165000")
    assert closing_on(date(2026, 11, 30)) == D("2265000")


def test_future_settled_date_refused(demo):                   # TC-28
    with pytest.raises(LekkaError):
        settle(by_title("Instalment 1"), date(2026, 11, 26))


def test_pay_december_rent_early(demo):                       # TC-127, EC-25
    dec_rent = CashflowEntry.query.filter_by(title="Factory rent", planned_date=date(2026, 12, 5)).one()
    settle(dec_rent, date(2026, 11, 25))
    closings, g = month_closings()
    assert closing_on(date(2026, 11, 25)) == D("2815000")
    assert closings[:2] == [D("1915000"), D("1140000")]
    assert g.columns[1].total_out == D("1210000")


def test_month_end_turns_plan_into_actual(demo, set_today):   # TC-126
    settle(by_title("Instalment 1"), date(2026, 11, 25))
    set_today("2026-11-28")
    settle(by_title("Invoice INV-388"), date(2026, 11, 28))
    set_today("2026-12-01")
    closings, _ = month_closings()
    assert closings[0] == D("2915000")        # November is now actual
    # Unpaid November salaries stay on 30 Nov (not counted); the AMC due today (1 Dec) is
    # unticked, so it is not counted yet either.
    assert closings[1] == D("2050000")


def test_head_cell_count_status_bar(demo):                    # TC-93 to TC-95
    g = build_grid(date(2026, 11, 1), date(2027, 1, 31), "month")
    head, cells, items = [r for r in g.rows[IN] if r[0].name == "Product sales"][0]
    nov = cells[0]
    assert nov.amount == D("840000") and nov.count == 3 and nov.has_overdue
    bar = dict(nov.bar())
    assert round(bar["SETTLED"]) == 81 and round(bar["OVERDUE"]) == 19
    rent = [r for r in g.rows[OUT] if r[0].name == "Factory rent"][0][1][0]
    assert rent.single_status == "SETTLED" and not rent.has_overdue


def test_cancelled_items_never_count(demo):                   # TC-43
    g = build_grid(date(2026, 11, 1), date(2026, 11, 30), "month")
    # Cancelled items stay visible (struck through) but add nothing to any total or balance.
    row = [r for r in g.rows[OUT] if r[0].name == "Trade shows"][0]
    cell = row[1][0]
    assert cell.amount == 0 and cell.count == 0 and cell.cancelled and cell.cancelled[0].title == "Trade-show booth"
    assert g.columns[0].total_out == sum((c.amount for _, cells, _ in g.rows[OUT] for c in cells.values()), D("0"))
    trade = cell_items(by_title("Trade-show booth").head_id, OUT, date(2026, 11, 1), date(2026, 11, 30))
    assert trade and trade[0].lifecycle == "CANCELLED"


def test_quarter_view(demo):                                  # TC-99
    g = build_grid(date(2026, 10, 1), date(2026, 12, 31), "quarter")
    assert g.columns[0].label == "Q3 FY26-27"
    cell = [r for r in g.rows[IN] if r[0].name == "Product sales"][0][1][0]
    assert cell.amount == D("1200000") and cell.count == 4


def test_periods_week_and_year():                             # TC-08, TC-09
    weeks = periods(date(2026, 11, 30), date(2026, 12, 6), "week")
    assert weeks[0][:2] == (date(2026, 11, 30), date(2026, 12, 6))
    years = periods(date(2026, 11, 1), date(2027, 3, 31), "year")
    assert years[0][:2] == (date(2026, 4, 1), date(2027, 3, 31))
    assert len(periods(date(2026, 11, 1), date(2026, 11, 30), "day")) == 30


def test_drawer_order(demo):                                  # TC-98
    raw = by_title("Instalment 1").head_id
    items = cell_items(raw, OUT, date(2026, 11, 1), date(2026, 11, 30))
    assert [e.title for e in items] == ["Instalment 1", "First payment"]


# ---------------------------------------------------------------- late and forgotten ticks (TC-130..135)

@pytest.fixture
def simple(app, head, set_today):
    acc = get_account()
    acc.opening_balance, acc.opening_date = D("100000"), date(2026, 11, 1)
    h = head("Product sales")
    e = create_entry(direction=IN, head_id=h.id, title="Customer payment", amount=1000,
                     planned_date=date(2026, 11, 19))
    db.session.commit()
    return e


def test_money_arrives_a_day_late(simple, set_today):          # TC-130
    set_today("2026-11-19")
    assert closing_on(date(2026, 11, 19)) == D("100000")
    set_today("2026-11-20")
    settle(simple, date(2026, 11, 20))
    assert closing_on(date(2026, 11, 19)) == D("100000")
    assert closing_on(date(2026, 11, 20)) == D("101000")
    assert closing_on(date(2026, 12, 31)) == D("101000")


def test_forgotten_tick(simple, set_today):                    # TC-132
    set_today("2026-11-25")
    assert closing_on(date(2026, 11, 24)) == D("100000")
    settle(simple, date(2026, 11, 19))
    for d in (date(2026, 11, 19), date(2026, 11, 22), date(2026, 11, 25)):
        assert closing_on(d) == D("101000")


# ---------------------------------------------------------------- adjustments (TC-155..161)

def test_adjustments(demo):
    add_adjustment(date(2026, 11, 25), "-350", "Bank charges")
    assert closing_on(date(2026, 11, 25)) == D("2964650")       # TC-155
    add_adjustment(date(2026, 11, 20), "1,200", "Interest credited")
    assert closing_on(date(2026, 11, 20)) == D("2966200")       # TC-156
    add_adjustment(date(2026, 11, 25), "-150", None)            # TC-157: remarks optional
    g = build_grid(date(2026, 11, 1), date(2026, 11, 30), "month")
    assert g.columns[0].adjustment == D("700")                   # TC-160 (+1,200 -350 -150)
    with pytest.raises(LekkaError):
        add_adjustment(date(2026, 11, 30), "500")                # TC-158
    day = build_grid(date(2026, 11, 25), date(2026, 11, 25), "day")
    assert day.columns[0].adjustment == D("-500") and len(day.columns[0].adjustments) == 2   # TC-161


def test_items_before_opening_date_ignored(demo):              # EC-31
    h = by_title("Invoice INV-388").head_id
    # An unticked item dated before the opening date (e.g. left over after the opening date moved) counts nowhere.
    old = create_entry(direction=IN, head_id=h, title="Old receipt", amount=5000, planned_date=date(2026, 10, 28))
    assert closing_on(date(2026, 11, 30)) == D("2065000")
    # Ticking it, or anything, on a date before the opening date is refused with a clear message.
    with pytest.raises(LekkaError, match="before the account's opening date"):
        settle(old, date(2026, 10, 28))
    with pytest.raises(LekkaError, match="before the account's opening date"):
        add_adjustment(date(2026, 10, 30), "-500")


def test_hide_empty_days(demo):                                 # TC-06
    full = build_grid(date(2026, 11, 1), date(2026, 11, 30), "day")
    short = build_grid(date(2026, 11, 1), date(2026, 11, 30), "day", hide_empty=True)
    assert len(full.columns) == 30 and len(short.columns) < 30
    assert short.columns[-1].closing == full.columns[-1].closing
