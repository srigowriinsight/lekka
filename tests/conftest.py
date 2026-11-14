import os
import re
import sys
from datetime import date

import pytest
from flask.testing import FlaskClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app  # noqa: E402
from app.config import TestConfig  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import Counterparty, Head, User  # noqa: E402

# Every item and template needs a party. Tests about something else get "Test party" when they don't choose one;
# tests about the party itself pass counterparty_id (even "") and are left alone.
PARTY_FORMS = re.compile(r"^(/lekka)?/(entries/new|entries/\d+/edit|templates/new|templates/\d+/edit)$")


def test_party_id():
    p = Counterparty.query.filter_by(name="Test party").first()
    if p is None:
        p = Counterparty(name="Test party")
        db.session.add(p)
        db.session.commit()
    return p.id


class PartyClient(FlaskClient):
    def post(self, url, *a, **kw):
        data = kw.get("data")
        if isinstance(data, dict) and PARTY_FORMS.match(url.split("?")[0]):
            data = dict(data)
            if "counterparty_id" not in data and not data.get("new_vendor"):
                data["counterparty_id"] = test_party_id()
            if str(data.get("share_pct", "100")).strip() not in ("", "100") and "share_counterparty_id" not in data:
                data["share_counterparty_id"] = test_party_id()
            kw["data"] = data
        return super().post(url, *a, **kw)


@pytest.fixture
def app():
    app = create_app(TestConfig)
    app.test_client_class = PartyClient
    with app.app_context():
        db.create_all()
        # Tests that do not load the demo work across 2026; open the account early so back-dated ticks count.
        from app.services.entries import get_account
        get_account().opening_date = date(2026, 1, 1)
        db.session.commit()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def set_today(app):
    def _set(d):
        app.config["FIXED_TODAY"] = d if isinstance(d, date) else date.fromisoformat(d)
    return _set


@pytest.fixture
def head(app):
    def _head(name):
        h = Head.query.filter_by(name=name).first()
        if not h:
            h = Head(name=name)
            db.session.add(h)
            db.session.flush()
        return h
    return _head


@pytest.fixture
def client(app):
    u = User(username="finance", name="Finance")
    u.set_password("secret")
    db.session.add(u)
    db.session.commit()
    c = app.test_client()
    c.post("/login", data={"username": "finance", "password": "secret"})
    return c
