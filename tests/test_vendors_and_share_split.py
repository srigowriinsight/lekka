"""Vendors in Master data and on items, the vendor filter, and a partner share that follows its revenue's split."""
from datetime import date
from decimal import Decimal

import pytest

from app.demo import load_demo
from app.extensions import db
from app.models import CashflowEntry, Counterparty, Head
from app.services.balances import closing_on

F = {"X-Requested-With": "fetch"}


@pytest.fixture
def demo_client(client):
    load_demo(shift=False)
    db.session.commit()
    return client


def by_title(t):
    return CashflowEntry.query.filter_by(title=t).first()


def head(name):
    return Head.query.filter_by(name=name).first()


def ok(r):
    assert r.status_code == 200, r.get_data(as_text=True)
    j = r.get_json()
    assert j and not j.get("error"), j
    return j


# ---------------------------------------------------------------- vendors

def test_vendor_list_in_master_data(demo_client):
    c = demo_client
    ok(c.post("/master/vendors/add", data={"name": "Sharma  Electricals", "kind_of": "vendor", "gstin": "29abcde1234f1z5"},
              headers=F))
    v = Counterparty.query.filter_by(name="Sharma Electricals").one()
    assert v.kind == "both" and v.gstin == "29ABCDE1234F1Z5"
    r = c.post("/master/vendors/add", data={"name": "sharma electricals", "kind_of": "both"}, headers=F)
    assert r.status_code == 400 and "already" in r.get_json()["error"]
    ok(c.post(f"/master/vendors/{v.id}", data={"name": "Sharma Electricals Pvt", "kind_of": "both", "gstin": ""}, headers=F))
    db.session.refresh(v)
    assert v.name == "Sharma Electricals Pvt" and v.kind == "both" and v.gstin is None
    page = c.get("/master/?tab=parties").get_data(as_text=True)
    assert "Sharma Electricals Pvt" in page and "+ Add party" in page and "Customer" not in page
    # used on an item: can't be removed, only deactivated
    ok(c.post("/entries/new", data={"direction": "OUT", "head_id": head("Factory rent").id, "title": "Wiring job",
                                    "amount": "40000", "planned_date": "2026-12-10", "counterparty_id": v.id}, headers=F))
    r = c.post(f"/master/vendors/{v.id}/delete", headers=F)
    assert r.status_code == 400 and "Deactivate" in r.get_json()["error"]
    ok(c.post(f"/master/vendors/{v.id}", data={"toggle": "1"}, headers=F))
    db.session.refresh(v)
    assert not v.is_active
    # an unused one can be removed
    ok(c.post("/master/vendors/add", data={"name": "Typo vendor", "kind_of": "vendor"}, headers=F))
    t = Counterparty.query.filter_by(name="Typo vendor").one()
    ok(c.post(f"/master/vendors/{t.id}/delete", headers=F))
    assert Counterparty.query.filter_by(name="Typo vendor").first() is None
    hist = c.get("/history?kind=vendors").get_data(as_text=True)
    assert "Party added" in hist and "Party changed" in hist and "Party removed" in hist


def test_vendor_on_items_is_optional_and_offered_by_kind(demo_client):
    c = demo_client
    for name, kind in [("Acme Components", "vendor"), ("Big OEM Ltd", "customer"), ("ABC Partners", "both")]:
        ok(c.post("/master/vendors/add", data={"name": name, "kind_of": kind}, headers=F))
    out_form = c.get("/entries/new?direction=OUT", headers=F).get_data(as_text=True)
    in_form = c.get("/entries/new?direction=IN", headers=F).get_data(as_text=True)
    # one party list, the same on both pages
    assert all(n in out_form for n in ("Acme Components", "ABC Partners", "Big OEM Ltd"))
    assert all(n in in_form for n in ("Acme Components", "ABC Partners", "Big OEM Ltd"))
    assert "Party" in out_form and "+ New party" in in_form
    # optional: no vendor is fine
    ok(c.post("/entries/new", data={"direction": "OUT", "head_id": head("Factory rent").id, "title": "No vendor item",
                                    "amount": "1000", "planned_date": "2026-12-10"}, headers=F))
    assert by_title("No vendor item").counterparty_id is None
    # a new vendor typed into the item lands in the list too
    ok(c.post("/entries/new", data={"direction": "OUT", "head_id": head("Factory rent").id, "title": "Paint job",
                                    "amount": "5000", "planned_date": "2026-12-10", "counterparty_id": "",
                                    "new_vendor": "Fresh Paints"}, headers=F))
    fresh = Counterparty.query.filter_by(name="Fresh Paints").one()
    assert by_title("Paint job").counterparty_id == fresh.id
    # the vendor of a top item applies to all its parts; parts made later inherit it
    e = by_title("Paint job")
    ok(c.post(f"/entries/{e.id}/split", data={"part_id": ["", ""], "part_title": ["P1", "P2"],
                                               "part_amount": ["2000", "3000"],
                                               "part_date": ["2026-12-10", "2026-12-20"],
                                               "part_remarks": ["", ""], "version": e.version}, headers=F))
    assert by_title("P1").counterparty_id == fresh.id
    acme = Counterparty.query.filter_by(name="Acme Components").one()
    e = by_title("Paint job")
    ok(c.post(f"/entries/{e.id}/edit", data={"title": "Paint job", "head_id": e.head_id, "counterparty_id": acme.id,
                                              "version": e.version}, headers=F))
    assert by_title("P2").counterparty_id == acme.id
    # clearing it
    e = by_title("Paint job")
    ok(c.post(f"/entries/{e.id}/edit", data={"title": "Paint job", "head_id": e.head_id, "counterparty_id": "",
                                              "version": e.version}, headers=F))
    assert by_title("Paint job").counterparty_id is None and by_title("P1").counterparty_id is None


def test_vendor_filter_on_expense_page(demo_client):
    c = demo_client
    ok(c.post("/master/vendors/add", data={"name": "Acme Components", "kind_of": "vendor"}, headers=F))
    acme = Counterparty.query.filter_by(name="Acme Components").one()
    rent = head("Factory rent")
    ok(c.post("/entries/new", data={"direction": "OUT", "head_id": rent.id, "title": "Acme order",
                                    "amount": "1000", "planned_date": "2026-11-28", "counterparty_id": acme.id}, headers=F))
    ok(c.post("/entries/new", data={"direction": "OUT", "head_id": rent.id, "title": "Other order",
                                    "amount": "2000", "planned_date": "2026-11-28"}, headers=F))
    c.get("/expenses")                                   # shows (and clears) the 'saved' messages
    page = c.get(f"/expenses?vendor={acme.id}").get_data(as_text=True)
    assert "Acme order" in page and "Other order" not in page and "Acme Components" in page
    page = c.get("/expenses?vendor=none").get_data(as_text=True)
    assert "Other order" in page and "Acme order" not in page
    # combined with head and dates
    page = c.get(f"/expenses?vendor={acme.id}&head={rent.id}&start=2026-12-01&end=2026-12-31").get_data(as_text=True)
    assert "Acme order" not in page


# ---------------------------------------------------------------- partner share follows the split

def _shared(c, title="Joint order", share_title="ABC share", vendor=None):
    data = {"direction": "IN", "head_id": head("Product sales").id, "title": title, "amount": "5,00,000",
            "planned_date": "2026-12-01", "share_pct": "70", "share_head_id": head("Factory rent").id,
            "share_title": share_title, "share_date": "2026-12-05"}
    if vendor:
        data["share_counterparty_id"] = vendor
    ok(c.post("/entries/new", data=data, headers=F))
    return by_title(title), by_title(share_title)


def _split(c, e, rows, share_dates=None, new_total=None):
    data = {"part_id": [r[0] or "" for r in rows], "part_title": [r[1] for r in rows],
            "part_amount": [r[2] for r in rows], "part_date": [r[3] for r in rows],
            "part_remarks": ["" for _ in rows], "version": e.version}
    if share_dates is not None:
        data["part_share_date"] = share_dates
    if new_total:
        data["new_total"] = new_total
    return c.post(f"/entries/{e.id}/split", data=data, headers=F)


def _parts(s):
    db.session.refresh(s)
    return sorted([(p.title, p.amount, p.planned_date, p.lifecycle) for p in s.children], key=lambda x: x[0])


def test_share_vendor_and_split_asks_dates(demo_client):
    c = demo_client
    ok(c.post("/master/vendors/add", data={"name": "ABC Partners", "kind_of": "both"}, headers=F))
    abc = Counterparty.query.filter_by(name="ABC Partners").one()
    rev, share = _shared(c, vendor=abc.id)
    assert share.counterparty_id == abc.id and share.amount == Decimal("150000")
    before = closing_on(date(2027, 1, 31))
    form = c.get(f"/entries/{rev.id}/split", headers=F).get_data(as_text=True)
    assert "Partner share goes out" in form and 'name="part_share_date"' in form and 'value="2026-12-05"' in form
    j = ok(_split(c, rev, [(None, "Advance", "2,00,000", "2026-12-01"), (None, "On delivery", "3,00,000", "2026-12-20")],
                  share_dates=["2026-12-06", "2026-12-24"]))
    share = by_title("ABC share")
    assert _parts(share) == [("ABC share – Advance", Decimal("60000"), date(2026, 12, 6), "ACTIVE"),
                             ("ABC share – On delivery", Decimal("90000"), date(2026, 12, 24), "ACTIVE")]
    assert all(p.counterparty_id == abc.id for p in share.children)
    assert {p.share_of_id for p in share.children} == {by_title("Advance").id, by_title("On delivery").id}
    msg = c.get("/").get_data(as_text=True)
    assert "Split saved" in msg and "Partner share follows" in msg and "₹60,000 on 6 Dec" in msg
    assert closing_on(date(2027, 1, 31)) == before        # same money, only spread over dates


def test_share_split_defaults_to_old_date_and_follows_edits(demo_client):
    c = demo_client
    rev, share = _shared(c)
    ok(_split(c, rev, [(None, "A", "2,00,000", "2026-12-01"), (None, "B", "3,00,000", "2026-12-20")]))
    share = by_title("ABC share")
    assert [p[2] for p in _parts(share)] == [date(2026, 12, 5)] * 2          # default: the old share date
    # edit the split: amounts change, a third part is added -> share follows, same 30%
    rev = by_title("Joint order")
    a, b = by_title("A"), by_title("B")
    ok(_split(c, rev, [(a.id, "A", "1,00,000", "2026-12-01"), (b.id, "B", "3,00,000", "2026-12-20"),
                       (None, "C", "1,00,000", "2027-01-10")],
              share_dates=["2026-12-05", "2026-12-26", "2027-01-15"]))
    assert _parts(by_title("ABC share")) == [("ABC share – A", Decimal("30000"), date(2026, 12, 5), "ACTIVE"),
                                             ("ABC share – B", Decimal("90000"), date(2026, 12, 26), "ACTIVE"),
                                             ("ABC share – C", Decimal("30000"), date(2027, 1, 15), "ACTIVE")]
    # a new total: share follows at the same %
    rev = by_title("Joint order")
    a, b, cc = by_title("A"), by_title("B"), by_title("C")
    ok(_split(c, rev, [(a.id, "A", "1,00,000", "2026-12-01"), (b.id, "B", "3,00,000", "2026-12-20"),
                       (cc.id, "C", "2,00,000", "2027-01-10")], new_total="6,00,000"))
    assert by_title("ABC share").amount == Decimal("180000")
    # split a part again: the share part of B is replaced by two, dated like B's share part by default
    b = by_title("B")
    ok(_split(c, b, [(None, "B1", "1,00,000", "2026-12-20"), (None, "B2", "2,00,000", "2026-12-30")]))
    titles = [p[0] for p in _parts(by_title("ABC share"))]
    assert titles == ["ABC share – A", "ABC share – B1", "ABC share – B2", "ABC share – C"]
    p_b2 = by_title("ABC share – B2")
    assert p_b2.amount == Decimal("60000") and p_b2.planned_date == date(2026, 12, 26)
    # remove the revenue's split: the share is one item again
    rev = by_title("Joint order")
    ok(c.post(f"/entries/{by_title('B').id}/remove-split", headers=F))
    ok(c.post(f"/entries/{rev.id}/remove-split", headers=F))
    share = by_title("ABC share")
    assert share.is_leaf and share.amount == Decimal("180000") and share.lifecycle == "ACTIVE"


def test_ticked_share_part_is_locked(demo_client):
    c = demo_client
    rev, share = _shared(c)
    ok(_split(c, rev, [(None, "A", "2,00,000", "2026-12-01"), (None, "B", "3,00,000", "2026-12-20")]))
    sa = by_title("ABC share – A")
    ok(c.post(f"/entries/{sa.id}/settle", data={"when": "today"}, headers=F))
    rev, a, b = by_title("Joint order"), by_title("A"), by_title("B")
    r = _split(c, rev, [(a.id, "A", "2,50,000", "2026-12-01"), (b.id, "B", "2,50,000", "2026-12-20")])
    assert r.status_code == 400 and "already ticked as paid" in r.get_json()["error"]
    assert by_title("A").amount == Decimal("200000")                      # nothing saved
    # a change that leaves the paid part alone goes through
    b = by_title("B")
    ok(_split(c, b, [(None, "B1", "1,00,000", "2026-12-20"), (None, "B2", "2,00,000", "2027-01-05")]))
    assert by_title("ABC share – A").is_settled and by_title("ABC share – B1").amount == Decimal("30000")


def test_cancel_and_move_parts_of_a_shared_revenue(demo_client):
    c = demo_client
    rev, share = _shared(c)
    ok(_split(c, rev, [(None, "A", "1,00,000", "2026-12-01"), (None, "B", "2,00,000", "2026-12-10"),
                       (None, "C", "2,00,000", "2026-12-20")]))
    # Shrink: the share part of B is cancelled, the share drops to 30% of what is left
    b = by_title("B")
    ok(c.post(f"/entries/{b.id}/cancel", data={"reason": "Dropped", "mode": "shrink"}, headers=F))
    sb = by_title("ABC share – B")
    assert sb.lifecycle == "CANCELLED" and "Dropped" in sb.cancel_reason
    assert by_title("ABC share").amount == Decimal("90000")
    # Move: A's money goes onto C -> C's share part grows by the same 30%
    a, cc = by_title("A"), by_title("C")
    ok(c.post(f"/entries/{a.id}/cancel", data={"reason": "Paid with C", "mode": "move", "target_id": cc.id}, headers=F))
    assert by_title("ABC share – C").amount == Decimal("90000")
    assert by_title("ABC share – A").lifecycle == "CANCELLED" and by_title("ABC share").amount == Decimal("90000")


def test_less_money_linked_splits_the_share_too(demo_client):
    c = demo_client
    rev, share = _shared(c)
    # 2,00,000 of the 5,00,000 arrives early and is entered as received -> plan split into received + balance
    sales = head("Product sales")
    r = ok(c.post("/entries/new", data={"direction": "IN", "head_id": sales.id, "title": "Money from OEM",
                                        "amount": "2,00,000", "planned_date": "2026-11-20", "already_settled": "1",
                                        "link_choice": rev.id}, headers=F))
    share = by_title("ABC share")
    assert sorted(p.amount for p in share.children) == [Decimal("60000"), Decimal("90000")]
    assert all(p.planned_date == date(2026, 12, 5) for p in share.children)


def test_changing_pct_on_a_split_revenue(demo_client):
    c = demo_client
    rev, share = _shared(c)
    ok(_split(c, rev, [(None, "A", "2,00,000", "2026-12-01"), (None, "B", "3,00,000", "2026-12-20")]))
    rev = by_title("Joint order")
    pop = c.get(f"/entries/{rev.id}/edit", headers=F).get_data(as_text=True)
    assert 'value="70"' in pop
    ok(c.post(f"/entries/{rev.id}/edit", data={"title": "Joint order", "head_id": rev.head_id, "share_pct": "80",
                                                "version": rev.version}, headers=F))
    assert by_title("ABC share").amount == Decimal("100000")
    assert by_title("ABC share – A").amount == Decimal("40000") and by_title("ABC share – B").amount == Decimal("60000")
    # a part's own amount changed with a double-click: share keeps 20% of it
    a = by_title("A")
    ok(c.post(f"/entries/{a.id}/amount", data={"amount": "3,00,000", "version": a.version}, headers=F))
    assert by_title("ABC share – A").amount == Decimal("60000") and by_title("ABC share").amount == Decimal("120000")
