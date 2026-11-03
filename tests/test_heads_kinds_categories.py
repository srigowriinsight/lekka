"""Heads marked Revenue / Expense / Both; price-list categories; the product picker on new revenue."""
from decimal import Decimal

from app.extensions import db
from app.models import CashflowEntry, Category, Head, PriceItem

F = {"X-Requested-With": "fetch"}


def head(name):
    return Head.query.filter_by(name=name).first()


def err(r):
    assert r.status_code == 400, r.get_data(as_text=True)
    return r.get_json()["error"]


def ok(r):
    assert r.status_code == 200, r.get_data(as_text=True)
    return r


def test_heads_are_revenue_expense_or_both(client):
    assert "revenue head, an expense head" in err(client.post("/master/heads/add", data={"name": "Rent"}, headers=F))
    ok(client.post("/master/heads/add", data={"name": "Rent", "head_kind": "OUT"}, headers=F))
    ok(client.post("/master/heads/add", data={"name": "Sales", "head_kind": "IN"}, headers=F))
    ok(client.post("/master/heads/add", data={"name": "Misc", "head_kind": "BOTH"}, headers=F))
    out_form = client.get("/entries/new?direction=OUT", headers=F).get_data(as_text=True)
    in_form = client.get("/entries/new?direction=IN", headers=F).get_data(as_text=True)
    assert "Rent" in out_form and "Misc" in out_form and ">Sales<" not in out_form
    assert ">Sales<" in in_form and "Misc" in in_form and ">Rent<" not in in_form.split("share_head_id")[0]
    # the server checks it too
    e = err(client.post("/entries/new", data={"direction": "IN", "head_id": head("Rent").id, "title": "x",
                                              "amount": "10", "planned_date": "2026-12-01"}, headers=F))
    assert "expense head" in e
    # sub-heads of an expense head are expense heads
    ok(client.post("/master/heads/add", data={"name": "Godown rent", "parent_id": head("Rent").id,
                                              "head_kind": "IN"}, headers=F))
    assert head("Godown rent").kind == "OUT"
    # a head with revenue items can't become an expense head
    ok(client.post("/entries/new", data={"direction": "IN", "head_id": head("Misc").id, "title": "Scrap sale",
                                         "amount": "500", "planned_date": "2026-12-01"}, headers=F))
    m = head("Misc")
    assert "still has revenue" in err(client.post(f"/master/heads/{m.id}", data={"name": "Misc", "head_kind": "OUT"},
                                                  headers=F))
    # a Both head turned into Revenue takes its sub-heads with it
    ok(client.post("/master/heads/add", data={"name": "Misc sub", "parent_id": m.id, "head_kind": "BOTH"}, headers=F))
    ok(client.post(f"/master/heads/{m.id}", data={"name": "Misc", "head_kind": "IN"}, headers=F))
    assert head("Misc").kind == "IN" and head("Misc sub").kind == "IN"
    # '+ New head' typed into a revenue item becomes a revenue head
    ok(client.post("/entries/new", data={"direction": "IN", "head_id": "", "new_head": "Service income",
                                         "title": "AMC visit", "amount": "2000", "planned_date": "2026-12-01"},
                   headers=F))
    assert head("Service income").kind == "IN"
    # recurring templates check it too
    e = err(client.post("/templates/new", data={"title": "Rent", "head_id": head("Rent").id, "direction": "IN",
                                                "amount": "1000", "freq": "MONTHLY", "interval_n": "1",
                                                "start_date": "2026-12-01"}, headers=F))
    assert "expense head" in e


def test_categories_tree_and_price_list(client):
    ok(client.post("/master/categories/add", data={"name": "Controllers"}, headers=F))
    top = Category.query.filter_by(name="Controllers").one()
    ok(client.post("/master/categories/add", data={"name": "PLC", "parent_id": top.id}, headers=F))
    plc = Category.query.filter_by(name="PLC").one()
    assert plc.path() == "Controllers › PLC"
    assert "already exists" in err(client.post("/master/categories/add", data={"name": "plc"}, headers=F))
    assert "itself" in err(client.post(f"/master/categories/{top.id}", data={"name": "Controllers",
                                                                             "parent_id": plc.id}, headers=F))
    ok(client.post("/master/prices/add", data={"product": "CX-200", "price": "10000", "gst_rate": "18",
                                               "category_id": plc.id}, headers=F))
    ok(client.post("/master/prices/add", data={"product": "Cable", "price": "100", "gst_rate": "18"}, headers=F))
    assert PriceItem.query.filter_by(product="CX-200").one().category_id == plc.id
    page = client.get(f"/master/?tab=prices&cat={top.id}").get_data(as_text=True)   # a category includes its subs
    assert "CX-200" in page and 'value="Cable"' not in page
    page = client.get("/master/?tab=prices&cat=none").get_data(as_text=True)
    assert 'value="Cable"' in page and 'value="CX-200"' not in page
    assert "Some products" in err(client.post(f"/master/categories/{plc.id}/delete", headers=F))
    assert "sub-categories" in err(client.post(f"/master/categories/{top.id}/delete", headers=F))
    cx = PriceItem.query.filter_by(product="CX-200").one()
    ok(client.post(f"/master/prices/{cx.id}", data={"product": "CX-200", "price": "10000", "gst_rate": "18",
                                                    "category_id": ""}, headers=F))
    ok(client.post(f"/master/categories/{plc.id}/delete", headers=F))
    assert Category.query.filter_by(name="PLC").first() is None
    hist = client.get("/history?kind=prices").get_data(as_text=True)
    assert "Category added" in hist and "Category removed" in hist


def test_product_picker_on_new_revenue(client):
    ok(client.post("/master/categories/add", data={"name": "Controllers"}, headers=F))
    c = Category.query.one()
    ok(client.post("/master/prices/add", data={"product": "CX-200", "price": "10000", "gst_rate": "18",
                                               "category_id": c.id}, headers=F))
    in_form = client.get("/entries/new?direction=IN", headers=F).get_data(as_text=True)
    assert 'class="full js-product"' in in_form and 'data-total="11800.00"' in in_form
    assert '<optgroup label="Controllers">' in in_form and "js-qty" in in_form
    assert "js-product" not in client.get("/entries/new?direction=OUT", headers=F).get_data(as_text=True)
