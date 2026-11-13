"""Review page: date range, expense/revenue, paid/unpaid/overdue, parties, heads, categories, grouping, export."""
from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models import CashflowEntry, Category, Counterparty, Head
from app.services import report

F = {"X-Requested-With": "fetch"}


def _setup(client):
    for n, k in (("Sales", "IN"), ("Rent", "OUT"), ("Rent sub", "OUT")):
        db.session.add(Head(name=n, kind=k))
    db.session.commit()
    sub = Head.query.filter_by(name="Rent sub").one()
    sub.parent_id = Head.query.filter_by(name="Rent").one().id
    db.session.add_all([Counterparty(name="Acme", kind="both"), Counterparty(name="Zen", kind="both")])
    top = Category(name="Controllers")
    db.session.add(top)
    db.session.flush()
    db.session.add(Category(name="PLC", parent_id=top.id))
    db.session.commit()
    h = lambda n: Head.query.filter_by(name=n).one().id
    party = lambda n: Counterparty.query.filter_by(name=n).one().id
    plc = Category.query.filter_by(name="PLC").one().id

    def add(**kw):
        r = client.post("/entries/new", data=kw, headers=F)
        assert r.status_code == 200, r.get_data(as_text=True)

    add(direction="IN", head_id=h("Sales"), title="Sale A", amount="10000", planned_date="2026-11-05",
        counterparty_id=party("Acme"), category_id=plc, already_settled="1", settled_on="2026-11-05")
    add(direction="IN", head_id=h("Sales"), title="Sale B", amount="5000", planned_date="2026-11-28",
        counterparty_id=party("Zen"))
    add(direction="OUT", head_id=h("Rent sub"), title="Godown rent", amount="3000", planned_date="2026-11-10",
        counterparty_id=party("Acme"))                                           # overdue (today = 25 Nov)
    add(direction="OUT", head_id=h("Rent"), title="Office rent", amount="2000", planned_date="2026-12-05")
    client.get("/master/")                                   # shows (and clears) the 'saved' messages
    return h, party, plc


def _titles(page):
    return [t for t in ("Sale A", "Sale B", "Godown rent", "Office rent") if t in page]


def test_filters_and_combinations(client):
    h, party, plc = _setup(client)
    page = client.get("/review?start=2026-11-01&end=2026-11-30").get_data(as_text=True)
    assert _titles(page) == ["Sale A", "Sale B", "Godown rent"]                   # Office rent is in December
    assert "Remarks" not in page
    for q, want in [
        ("type=OUT", ["Godown rent"]),
        ("type=IN&status=unpaid", ["Sale B"]),
        ("status=paid", ["Sale A"]),
        ("status=overdue", ["Godown rent"]),
        (f"party={party('Acme')}", ["Sale A", "Godown rent"]),
        (f"party={party('Acme')}&type=IN", ["Sale A"]),
        ("party=none", []),
        (f"head={h('Rent')}", ["Godown rent"]),                                 # a head includes its sub-heads
        (f"cat={Category.query.filter_by(name='Controllers').one().id}", ["Sale A"]),   # and a category its subs
        ("cat=none", ["Sale B", "Godown rent"]),
        (f"party={party('Acme')}&party={party('Zen')}&status=unpaid", ["Sale B", "Godown rent"]),
    ]:
        page = client.get(f"/review?start=2026-11-01&end=2026-11-30&{q}").get_data(as_text=True)
        assert _titles(page) == want, (q, _titles(page))


def test_totals_groups_presets_and_export(client):
    _setup(client)
    p = report.params(type("A", (), {"get": lambda s, k, d=None: {"start": "2026-11-01", "end": "2026-11-30"}.get(k, d),
                                     "getlist": lambda s, k: []})())
    res = report.run(p)
    assert res.money_in == Decimal("15000") and res.money_out == Decimal("3000") and res.net == Decimal("12000")
    assert res.unpaid_count == 2 and res.overdue_count == 1
    page = client.get("/review?start=2026-11-01&end=2026-12-31&group=party").get_data(as_text=True)
    assert "rv-group" in page and "No party" in page and page.index(">Acme <") < page.index(">Zen <")
    # presets
    assert report.preset_range("this_quarter", date(2026, 11, 12)) == (date(2026, 10, 1), date(2026, 12, 31))
    assert report.preset_range("this_fy", date(2027, 2, 1)) == (date(2026, 4, 1), date(2027, 3, 31))
    page = client.get("/review?preset=this_month").get_data(as_text=True)
    assert "Needs attention" not in page and 'class="pick"' not in page and "Apply filters" in page
    r = client.get("/review/export?start=2026-11-01&end=2026-11-30")
    assert r.status_code == 200 and r.data[:2] == b"PK"


def test_category_on_items(client):
    h, party, plc = _setup(client)
    e = CashflowEntry.query.filter_by(title="Sale A").one()
    assert e.category_id == plc
    b = CashflowEntry.query.filter_by(title="Sale B").one()
    r = client.post(f"/entries/{b.id}/split", headers=F, data={
        "part_id": ["", ""], "part_title": ["B1", "B2"], "part_amount": ["2000", "3000"],
        "part_date": ["2026-11-28", "2026-12-28"], "part_remarks": ["", ""], "version": b.version})
    assert r.status_code == 200
    b = CashflowEntry.query.filter_by(title="Sale B").one()
    client.post(f"/entries/{b.id}/edit", headers=F, data={"title": "Sale B", "head_id": b.head_id,
                                                         "category_id": plc, "version": b.version})
    assert all(c.category_id == plc for c in CashflowEntry.query.filter_by(title="Sale B").one().children)
    form = client.get("/entries/new?direction=IN", headers=F).get_data(as_text=True)
    assert 'name="category_id"' in form and "PLC" in form
