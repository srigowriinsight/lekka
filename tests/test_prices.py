"""Price list on the Master data page: GST worked out, price-includes-GST, edits, deletes, History."""
from decimal import Decimal

from app.extensions import db
from app.models import AuditLog, PriceItem

F = {"X-Requested-With": "fetch"}


def _add(client, **kw):
    data = {"product": "PLC controller CX-200", "price": "10000", "gst_rate": "18"}
    data.update(kw)
    return client.post("/master/prices/add", data=data, headers=F)


def test_add_works_out_gst_and_total(client):
    r = _add(client)
    assert r.status_code == 200, r.get_data(as_text=True)
    p = PriceItem.query.one()
    assert p.price == Decimal("10000.00") and p.gst_rate == Decimal("18.00")
    assert p.gst_amount == Decimal("1800.00") and p.half_gst == Decimal("900.00")
    assert p.total == Decimal("11800.00")
    page = client.get("/master/?tab=prices").get_data(as_text=True)
    assert "PLC controller CX-200" in page and "11,800" in page and "CGST" not in page


def test_price_including_gst_is_worked_back(client):
    _add(client, product="Sensor", price="1,180", includes_gst="1")
    p = PriceItem.query.one()
    assert p.price == Decimal("1000.00") and p.gst_amount == Decimal("180.00") and p.total == Decimal("1180.00")
    _add(client, product="Relay", price="1000", includes_gst="1")
    r = PriceItem.query.filter_by(product="Relay").one()
    assert r.price == Decimal("847.46") and r.total == Decimal("1000.00")


def test_rounding_and_odd_rates(client):
    _add(client, product="Cable", price="99.99", gst_rate="5")
    p = PriceItem.query.one()
    assert p.gst_amount == Decimal("5.00") and p.total == Decimal("104.99")
    _add(client, product="Exempt item", price="500", gst_rate="0")
    assert PriceItem.query.filter_by(product="Exempt item").one().total == Decimal("500.00")


def test_checks(client):
    _add(client)
    assert _add(client, product="plc controller  cx-200").status_code == 400      # same name, any case/spaces
    assert _add(client, product="X", gst_rate="120").status_code == 400
    assert _add(client, product="X", price="-5").status_code == 400
    assert _add(client, product="X", price="abc").status_code == 400
    assert _add(client, product="", price="5").status_code == 400
    assert PriceItem.query.count() == 1


def test_edit_and_delete_are_in_history(client):
    _add(client)
    p = PriceItem.query.one()
    r = client.post(f"/master/prices/{p.id}", data={"product": "PLC controller CX-200", "price": "12000",
                                                    "gst_rate": "5"}, headers=F)
    assert r.status_code == 200
    db.session.refresh(p)
    assert p.total == Decimal("12600.00")
    pid = p.id
    assert client.post(f"/master/prices/{pid}/delete", headers=F).status_code == 200
    assert PriceItem.query.count() == 0
    actions = [a.action for a in AuditLog.query.filter_by(table_name="price_item").order_by(AuditLog.id)]
    assert actions == ["create", "edit", "delete"]
    hist = client.get("/history?kind=prices").get_data(as_text=True)
    assert "Price added" in hist and "Price changed" in hist and "Price removed" in hist
    assert "PLC controller CX-200" in hist
