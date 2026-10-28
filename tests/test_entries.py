"""Splits, cancelling, editing and linking (Test cases tab, sections 2, 5, 9, 11, 15; Edge cases)."""
from datetime import date
from decimal import Decimal

import pytest

from app.extensions import db
from app.models import IN, OUT, AuditLog, CashflowEntry
from app.services.balances import closing_on
from app.services.common import LekkaError, inr, money
from app.services.entries import (cancel, create_entry, edit_entry, edit_split, get_account,
                                  link_and_settle, link_candidates, move_targets, remove_split,
                                  reschedule, settle, split_entry, status, status_label, unsettle)

D = Decimal


@pytest.fixture
def raw(app, head):
    return head("Raw materials and components")


@pytest.fixture
def po(raw):
    """PO-V-221: First payment (ticked 10 Nov), Balance -> Instalment 1 (25 Nov), Instalment 2 (10 Dec)."""
    e = create_entry(direction=OUT, head_id=raw.id, title="PO-V-221 components", amount=800000,
                     planned_date=date(2026, 11, 10))
    split_entry(e, [{"title": "First payment", "amount": 300000, "planned_date": "2026-11-10"},
                    {"title": "Balance", "amount": 500000, "planned_date": "2026-11-25"}])
    split_entry(e.children[1], [{"title": "Instalment 1", "amount": 250000, "planned_date": "2026-11-25"},
                                {"title": "Instalment 2", "amount": 250000, "planned_date": "2026-12-10"}])
    settle(e.children[0], date(2026, 11, 10))
    db.session.commit()
    return e


def kids(e):
    first, balance = e.children
    i1, i2 = balance.children
    return first, balance, i1, i2


def test_create_validation(raw):                               # TC-02, TC-03, TC-77
    for bad in ({"amount": 0}, {"amount": "-500"}, {"planned_date": ""}, {"head_id": None}, {"title": " "}):
        args = dict(direction=OUT, head_id=raw.id, title="Courier", amount=8000, planned_date="2026-11-27")
        args.update(bad)
        with pytest.raises(LekkaError):
            create_entry(**args)


def test_money_and_inr_format():                               # money is exact
    assert money("1,00,000") == D("100000.00")
    assert sum([money("10000.10")] * 3) == D("30000.30")       # TC-67
    assert inr(D("2965000")) == "29,65,000" and inr(D("-15000")) == "−15,000"
    assert inr(D("840000"), compact=True) == "8.4L"
    with pytest.raises(LekkaError):
        money("1 lakh")


def test_split_sum_and_dates(raw):                             # TC-13, TC-16..18
    e = create_entry(direction=IN, head_id=raw.id, title="PO-1042", amount=1200000, planned_date="2026-11-05")
    with pytest.raises(LekkaError, match="unallocated"):
        split_entry(e, [{"amount": 400000, "planned_date": "2026-11-05"}, {"amount": 700000, "planned_date": "2026-11-20"}])
    with pytest.raises(LekkaError, match="over by"):
        split_entry(e, [{"amount": 500000, "planned_date": "2026-11-05"}, {"amount": 750000, "planned_date": "2026-11-20"}])
    with pytest.raises(LekkaError):
        split_entry(e, [{"amount": 480000, "planned_date": "2026-11-05"}, {"amount": 720000, "planned_date": ""}])
    split_entry(e, [{"title": "Advance", "amount": 480000, "planned_date": "2026-11-05"},
                    {"title": "Delivery", "amount": 720000, "planned_date": "2026-11-20"}])
    assert not e.is_leaf and e.planned_date is None and e.children[0].head_id == e.head_id


def test_no_depth_limit(raw):                                  # TC-20
    e = create_entry(direction=OUT, head_id=raw.id, title="Deep", amount=1024, planned_date="2026-12-01")
    node = e
    for level in range(10):
        split_entry(node, [{"title": f"L{level}a", "amount": node.amount / 2, "planned_date": "2026-12-01"},
                           {"title": f"L{level}b", "amount": node.amount / 2, "planned_date": "2026-12-02"}])
        node = node.children[0]
    assert node.depth == 10 and e.amount == D("1024")


def test_ticked_part_locked_and_total_floor(po):              # TC-22, EC-05
    first, balance, i1, i2 = kids(po)
    with pytest.raises(LekkaError, match="split further"):   # Balance is itself split
        edit_split(po, [{"id": first.id, "title": first.title, "amount": 300000, "planned_date": "2026-11-10"}])
    settle(i1, date(2026, 11, 25))
    with pytest.raises(LekkaError, match="locked"):
        edit_split(balance, [{"id": i1.id, "title": "Instalment 1", "amount": 200000, "planned_date": "2026-11-25"},
                             {"id": i2.id, "title": "Instalment 2", "amount": 300000, "planned_date": "2026-12-10"}])


def test_edit_split_renegotiated_total(raw):                    # EC-05
    e = create_entry(direction=OUT, head_id=raw.id, title="PO", amount=800000, planned_date="2026-11-10")
    split_entry(e, [{"title": "First", "amount": 300000, "planned_date": "2026-11-10"},
                    {"title": "Rest", "amount": 500000, "planned_date": "2026-12-10"}])
    first, rest = e.children
    settle(first, date(2026, 11, 10))
    with pytest.raises(LekkaError, match="below"):
        edit_split(e, [{"id": rest.id, "title": "Rest", "amount": 0.01, "planned_date": "2026-12-10"}], new_total=250000)
    edit_split(e, [{"id": first.id, "title": "First", "amount": 300000, "planned_date": "2026-11-10"},
                   {"id": rest.id, "title": "Rest", "amount": 400000, "planned_date": "2026-12-10"}], new_total=700000)
    assert e.amount == D("700000") and rest.amount == D("400000")


def test_remove_split(raw, po):                                 # TC-23, TC-24
    with pytest.raises(LekkaError):
        remove_split(po)
    e = create_entry(direction=OUT, head_id=raw.id, title="Simple", amount=1000, planned_date="2026-12-01")
    split_entry(e, [{"amount": 400, "planned_date": "2026-12-03"}, {"amount": 600, "planned_date": "2026-12-05"}])
    remove_split(e)
    assert e.is_leaf and e.planned_date == date(2026, 12, 3)


def test_cancel_part_needs_a_choice(po):                        # EC-07: nothing by default
    _, _, i1, i2 = kids(po)
    with pytest.raises(LekkaError, match="Choose"):
        cancel(i2, "Order reduced")


def test_cancel_shrink(po):                                     # TC-88
    _, balance, i1, i2 = kids(po)
    cancel(i2, "Order reduced", mode="shrink")
    assert po.amount == D("550000") and balance.amount == D("250000")


def test_cancel_move(po):                                       # TC-102, TC-103
    first, balance, i1, i2 = kids(po)
    assert [t.id for t in move_targets(i2)] == [i1.id]          # ticked First payment not offered
    cancel(i2, "Merge payments", mode="move", target_id=i1.id)
    assert i1.amount == D("500000") and po.amount == D("800000")


def test_cancel_last_unticked_part_only_shrink(raw):            # EC-06, EC-09
    e = create_entry(direction=OUT, head_id=raw.id, title="Two parts", amount=1000, planned_date="2026-11-10")
    split_entry(e, [{"amount": 400, "planned_date": "2026-11-10"}, {"amount": 600, "planned_date": "2026-12-01"}])
    a, b = e.children
    settle(a, date(2026, 11, 10))
    with pytest.raises(LekkaError, match="no other"):
        cancel(b, "x", mode="move", target_id=a.id)
    unsettle(a)
    cancel(a, "x", mode="shrink")
    cancel(b, "y", mode="shrink")
    assert e.lifecycle == "CANCELLED" and e.cancel_reason == "all parts cancelled"


def test_ticked_cannot_be_cancelled(po):                        # TC-46, TC-89, EC-11
    first, *_ = kids(po)
    with pytest.raises(LekkaError, match="untick"):
        cancel(first, "x", mode="shrink")
    unsettle(first)
    cancel(first, "x", mode="shrink")
    assert first.lifecycle == "CANCELLED"


def test_cancel_parent_keeps_ticked(po):                        # TC-45
    first, balance, i1, i2 = kids(po)
    cancel(po, "Supplier closed")
    assert first.is_settled and first.lifecycle == "ACTIVE"
    assert i1.lifecycle == i2.lifecycle == "CANCELLED" and po.amount == D("300000")


def test_head_change_moves_tree_parts_locked(po, head):         # EC-26, TC-104
    packing = head("Packing")
    first, balance, i1, i2 = kids(po)
    with pytest.raises(LekkaError):
        edit_entry(i1, head_id=packing.id)
    edit_entry(po, head_id=packing.id)
    assert {n.head_id for n in [po, first, balance, i1, i2]} == {packing.id}


def test_status_and_labels(po, set_today):
    first, _, i1, i2 = kids(po)
    assert status(i1) == "DUE_SOON" and status_label(i1) == "Due today"   # EC-33
    assert status_label(first) == "Paid 10 Nov" and status_label(po) == "1 of 3 settled"
    set_today("2026-11-26")
    assert status(i1) == "OVERDUE" and status_label(i1) == "Overdue 1 day"


def test_version_check(po):                                     # EC-36
    _, _, i1, _ = kids(po)
    v = i1.version
    edit_entry(i1, remarks="Vendor confirmed date", expected_version=v)
    with pytest.raises(LekkaError, match="changed elsewhere"):
        edit_entry(i1, remarks="old copy", expected_version=v)


def test_everything_logged(po):                                 # TC-65, TC-149
    _, _, i1, _ = kids(po)
    settle(i1, date(2026, 11, 25), "Paid by NEFT")
    unsettle(i1)
    reschedule(i1, date(2026, 12, 1), "Vendor asked")
    actions = [a.action for a in AuditLog.query.filter_by(record_id=i1.id).order_by(AuditLog.id)]
    assert actions[-3:] == ["settle", "unsettle", "reschedule"]
    assert AuditLog.query.filter_by(record_id=i1.id, action="settle").one().remarks == "Paid by NEFT"


def test_remarks_limit(raw):                                    # TC-87
    create_entry(direction=OUT, head_id=raw.id, title="ok", amount=1, planned_date="2026-12-01", remarks="")
    with pytest.raises(LekkaError):
        create_entry(direction=OUT, head_id=raw.id, title="x", amount=1, planned_date="2026-12-01", remarks="a" * 1001)


# ---------------------------------------------------------------- linking (TC-136..147)

@pytest.fixture
def plan(app, head):
    acc = get_account()
    acc.opening_balance, acc.opening_date = D("100000"), date(2026, 11, 1)
    h = head("Product sales")
    e = create_entry(direction=IN, head_id=h.id, title="Customer payment", amount=1000, planned_date="2026-11-19")
    db.session.commit()
    return e


def test_link_candidates_order(plan, set_today):                # TC-136, TC-142, TC-143
    h = plan.head_id
    late = create_entry(direction=IN, head_id=h, title="Customer payment", amount=1000, planned_date="2026-11-28")
    other = create_entry(direction=IN, head_id=h, title="Big", amount=1500, planned_date="2026-11-20")
    c = link_candidates(h, IN, 1000, date(2026, 11, 20))
    assert [x.id for x in c] == [plan.id, late.id, other.id]
    assert link_candidates(h, OUT, 1000, date(2026, 11, 20)) == []


def test_link_and_tick(plan, set_today):                        # TC-137
    set_today("2026-11-20")
    link_and_settle(plan, 1000, date(2026, 11, 20))
    assert plan.is_settled and plan.settled_on == date(2026, 11, 20)
    assert CashflowEntry.query.count() == 1
    assert closing_on(date(2026, 11, 20)) == D("101000")


def test_link_less_than_planned(plan, set_today):              # TC-140
    set_today("2026-11-20")
    link_and_settle(plan, 600, date(2026, 11, 20))
    got, rest = plan.children
    assert got.is_settled and got.amount == D("600") and rest.amount == D("400") and not rest.is_settled
    assert rest.planned_date == date(2026, 11, 19)


def test_link_more_than_planned(plan, set_today):              # TC-141
    set_today("2026-11-20")
    with pytest.raises(LekkaError):
        link_and_settle(plan, 1200, date(2026, 11, 20))
    link_and_settle(plan, 1200, date(2026, 11, 20), extra_mode="extra")
    extra = CashflowEntry.query.filter(CashflowEntry.title.like("%extra%")).one()
    assert extra.amount == D("200") and extra.is_settled and closing_on(date(2026, 11, 20)) == D("101200")


def test_link_raise_plan(plan, set_today):                     # PS-11 style
    set_today("2026-11-20")
    link_and_settle(plan, 1200, date(2026, 11, 20), extra_mode="raise")
    assert plan.amount == D("1200") and plan.is_settled


def test_link_paid_early_counts_on_entry_date(plan, set_today): # TC-144
    h = plan.head_id
    dec = create_entry(direction=IN, head_id=h, title="December payment", amount=5000, planned_date="2026-12-05")
    set_today("2026-11-25")
    link_and_settle(dec, 5000, date(2026, 11, 25))
    assert closing_on(date(2026, 11, 25)) == D("105000")


def test_link_offers_parts_not_parents(plan):                  # TC-145
    split_entry(plan, [{"amount": 500, "planned_date": "2026-11-19"}, {"amount": 500, "planned_date": "2026-11-26"}])
    c = link_candidates(plan.head_id, IN, 500, date(2026, 11, 20))
    assert plan.id not in [x.id for x in c] and len(c) == 2


def test_undo_link(plan, set_today):                           # TC-147
    set_today("2026-11-20")
    link_and_settle(plan, 1000, date(2026, 11, 20))
    unsettle(plan)
    assert closing_on(date(2026, 11, 20)) == D("100000") and plan.planned_date == date(2026, 11, 19)


def test_two_instalments(plan, set_today):                     # PS-09 style
    set_today("2026-11-20")
    link_and_settle(plan, 400, date(2026, 11, 20))
    rest = plan.children[1]
    set_today("2026-11-27")
    c = link_candidates(plan.head_id, IN, 600, date(2026, 11, 27))
    assert c[0].id == rest.id
    link_and_settle(rest, 600, date(2026, 11, 27))
    assert closing_on(date(2026, 11, 27)) == D("101000")
