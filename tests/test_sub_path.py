"""Lekka running under a sub-address (https://server/lekka/), as on the company server: after saving, the browser
must come back to a page inside /lekka, not to the server's root (which gives a 404)."""
import re

from app.extensions import db
from app.models import Head, User

F = {"X-Requested-With": "fetch"}
B = "http://server/lekka"


def login(app):
    u = User(username="finance", name="Finance")
    u.set_password("secret")
    db.session.add(u)
    db.session.add(Head(name="Rent", kind="OUT"))
    db.session.commit()
    c = app.test_client()
    r = c.post("/login?next=/lekka/templates/", data={"username": "finance", "password": "secret"}, base_url=B)
    assert r.headers["Location"].endswith("/lekka/templates/")
    return c


def test_saving_returns_inside_the_sub_path(app):
    c = login(app)
    hid = Head.query.one().id
    page = c.get("/templates/", base_url=B).get_data(as_text=True)
    link = re.search(r'data-dialog="([^"]*templates/new[^"]*)"', page).group(1)
    assert link.startswith("/lekka/templates/new?next=/lekka/templates/")
    form = c.get(link[len("/lekka"):], base_url=B, headers=F).get_data(as_text=True)
    nxt = re.search(r'name="next" value="([^"]*)"', form).group(1)
    assert nxt.startswith("/lekka/templates/")
    r = c.post("/templates/new", base_url=B, headers=F,
               data={"title": "Rent", "head_id": hid, "direction": "OUT", "amount": "1000", "freq": "MONTHLY",
                     "interval_n": "1", "start_date": "2026-12-01", "next": nxt})
    assert r.get_json()["redirect"].startswith("/lekka/templates/")
    # a page opened before the fix still sends the address without /lekka: it is put back
    r = c.post("/entries/new", base_url=B, headers=F,
               data={"direction": "OUT", "head_id": hid, "title": "Diesel", "amount": "500",
                     "planned_date": "2026-12-02", "next": "/entries/?direction=OUT"})
    assert r.get_json()["redirect"] == "/lekka/entries/?direction=OUT"
    # and nothing changes when Lekka runs at the root
    r = c.post("/entries/new", headers=F, data={"direction": "OUT", "head_id": hid, "title": "Tea", "amount": "50",
                                                "planned_date": "2026-12-02", "next": "/entries/?direction=OUT"})
    assert r.get_json()["redirect"] == "/entries/?direction=OUT"
