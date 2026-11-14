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


def test_types_tree_and_price_list(client):
    from app.models import ProductType
    ok(client.post("/master/types/add", data={"name": "Controllers"}, headers=F))
    top = ProductType.query.filter_by(name="Controllers").one()
    ok(client.post("/master/types/add", data={"name": "PLC", "parent_id": top.id}, headers=F))
    plc = ProductType.query.filter_by(name="PLC").one()
    assert plc.path() == "Controllers › PLC"
    assert "already exists" in err(client.post("/master/types/add", data={"name": "plc"}, headers=F))
    assert "itself" in err(client.post(f"/master/types/{top.id}", data={"name": "Controllers",
                                                                        "parent_id": plc.id}, headers=F))
    ok(client.post("/master/prices/add", data={"product": "CX-200", "price": "10000", "gst_rate": "18",
                                               "product_type_id": plc.id}, headers=F))
    ok(client.post("/master/prices/add", data={"product": "Cable", "price": "100", "gst_rate": "18"}, headers=F))
    assert PriceItem.query.filter_by(product="CX-200").one().product_type_id == plc.id
    page = client.get("/master/?tab=prices").get_data(as_text=True)
    assert "<th>Type</th>" in page and "<th>Category</th>" not in page and 'name="product_type_id"' in page
    page = client.get(f"/master/?tab=prices&type={top.id}").get_data(as_text=True)   # a type includes its subs
    assert "CX-200" in page and 'value="Cable"' not in page
    page = client.get("/master/?tab=prices&type=none").get_data(as_text=True)
    assert 'value="Cable"' in page and 'value="CX-200"' not in page
    page = client.get("/master/?tab=types").get_data(as_text=True)
    assert "+ Sub-type" in page and "Products" in page and f"/master/types/new?under={top.id}" in page
    assert "Some products use" in err(client.post(f"/master/types/{plc.id}/delete", headers=F))
    assert "sub-types" in err(client.post(f"/master/types/{top.id}/delete", headers=F))
    cx = PriceItem.query.filter_by(product="CX-200").one()
    ok(client.post(f"/master/prices/{cx.id}", data={"product": "CX-200", "price": "10000", "gst_rate": "18",
                                                    "product_type_id": ""}, headers=F))
    ok(client.post(f"/master/types/{plc.id}/delete", headers=F))
    assert ProductType.query.filter_by(name="PLC").first() is None
    hist = client.get("/history?kind=prices").get_data(as_text=True)
    assert "Type added" in hist and "Type removed" in hist


def test_categories_are_for_any_item(client):
    ok(client.post("/master/heads/add", data={"name": "Office", "head_kind": "BOTH"}, headers=F))
    ok(client.post("/master/categories/add", data={"name": "Stationery"}, headers=F))
    cat = Category.query.one()
    for d in ("OUT", "IN"):
        assert 'name="category_id"' in client.get(f"/entries/new?direction={d}", headers=F).get_data(as_text=True)
    ok(client.post("/entries/new", data={"direction": "OUT", "head_id": head("Office").id, "title": "Pens",
                                         "amount": "200", "planned_date": "2026-12-01", "category_id": cat.id},
                   headers=F))
    assert CashflowEntry.query.filter_by(title="Pens").one().category_id == cat.id
    page = client.get("/master/?tab=categories").get_data(as_text=True)
    assert "+ Sub-category" in page and "<th class=\"num\">Items</th>" in page and "Products" not in page
    assert "Some items use" in err(client.post(f"/master/categories/{cat.id}/delete", headers=F))


def test_product_picker_on_new_revenue(client):
    from app.models import ProductType
    ok(client.post("/master/heads/add", data={"name": "Sales", "head_kind": "IN"}, headers=F))
    ok(client.post("/master/types/add", data={"name": "Controllers"}, headers=F))
    ok(client.post("/master/categories/add", data={"name": "Exports"}, headers=F))
    t, cat = ProductType.query.one(), Category.query.one()
    ok(client.post("/master/prices/add", data={"product": "CX-200", "price": "10000", "gst_rate": "18",
                                               "product_type_id": t.id}, headers=F))
    cx = PriceItem.query.one()
    in_form = client.get("/entries/new?direction=IN", headers=F).get_data(as_text=True)
    assert 'name="product_id" class="full js-product"' in in_form and 'data-total="11800.00"' in in_form
    assert '<optgroup label="Controllers">' in in_form and "js-qty" in in_form
    assert "js-product" not in client.get("/entries/new?direction=OUT", headers=F).get_data(as_text=True)
    # the product's type goes onto the revenue item; the category is the user's own choice
    ok(client.post("/entries/new", data={"direction": "IN", "head_id": head("Sales").id, "title": "CX-200",
                                         "amount": "23600", "planned_date": "2026-12-01", "product_id": cx.id,
                                         "category_id": cat.id}, headers=F))
    e = CashflowEntry.query.filter_by(title="CX-200").one()
    assert e.product_type_id == t.id and e.category_id == cat.id
    # its parts keep the type
    ok(client.post(f"/entries/{e.id}/split", data={"part_id": ["", ""], "part_title": ["Advance", "Balance"],
                                                   "part_amount": ["10000", "13600"],
                                                   "part_date": ["2026-12-01", "2026-12-20"],
                                                   "part_remarks": ["", ""], "version": e.version}, headers=F))
    assert {c.product_type_id for c in CashflowEntry.query.filter(CashflowEntry.parent_id == e.id)} == {t.id}
    detail = client.get(f"/entries/{e.id}", headers=F).get_data(as_text=True)
    assert "<div>Type</div><div>Controllers</div>" in detail
    # no product, no type
    ok(client.post("/entries/new", data={"direction": "IN", "head_id": head("Sales").id, "title": "Service visit",
                                         "amount": "500", "planned_date": "2026-12-01"}, headers=F))
    assert CashflowEntry.query.filter_by(title="Service visit").one().product_type_id is None


def test_sub_category_popup(client):
    ok(client.post("/master/categories/add", data={"name": "Controllers"}, headers=F))
    top = Category.query.one()
    pop = client.get(f"/master/categories/new?under={top.id}", headers=F).get_data(as_text=True)
    assert "Add sub-category under Controllers" in pop and f'value="{top.id}" selected' in pop
    page = client.get("/master/?tab=categories").get_data(as_text=True)
    assert f"/master/categories/new?under={top.id}" in page
    ok(client.post("/master/categories/add", data={"name": "PLC", "parent_id": top.id}, headers=F))
    assert Category.query.filter_by(name="PLC").one().parent_id == top.id
