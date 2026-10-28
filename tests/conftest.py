import os
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app  # noqa: E402
from app.config import TestConfig  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import Head, User  # noqa: E402


@pytest.fixture
def app():
    app = create_app(TestConfig)
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
