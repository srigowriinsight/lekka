"""Users page: add, rename, reset password, change own password, deactivate; all users have the same rights."""
import pytest
from flask import g

from app.extensions import db
from app.models import AuditLog, User

F = {"X-Requested-With": "fetch"}


@pytest.fixture(autouse=True)
def fresh_login_each_request(app):
    """The tests keep one app context open, so Flask-Login's per-request cache of the logged-in user would
    carry over between requests (and between test clients). A real server starts clean on every request."""
    @app.before_request
    def _forget_cached_user():
        g.pop("_login_user", None)


def login(app, username, password):
    c = app.test_client()
    c.post("/login", data={"username": username, "password": password})
    return c


def logged_in(c):
    return c.get("/").status_code == 200


def test_add_user_who_can_do_everything(app, client):
    r = client.post("/users/add", data={"name": "Priya Raman", "username": "Priya", "password": "priya1234",
                                         "password2": "priya1234"}, headers=F)
    assert r.status_code == 200, r.get_data(as_text=True)
    u = User.query.filter_by(username="priya").one()          # stored in small letters
    assert u.name == "Priya Raman" and u.active
    c2 = login(app, "PRIYA", "priya1234")                      # login ignores capitals
    assert logged_in(c2)
    for page in ("/master/", "/users/", "/history", "/account"):
        assert c2.get(page).status_code == 200
    # what she does is recorded under her name
    c2.post("/master/heads/add", data={"name": "Packing", "head_kind": "OUT"}, headers=F)
    a = AuditLog.query.filter_by(table_name="head", action="create").one()
    assert a.user_id == u.id
    page = client.get("/users/").get_data(as_text=True)
    assert "Priya Raman" in page and "priya" in page


def test_checks_on_new_users(client):
    def add(**kw):
        d = {"name": "X", "username": "xuser", "password": "longenough", "password2": "longenough"}
        d.update(kw)
        return client.post("/users/add", data=d, headers=F)
    assert add(username="finance").status_code == 400                      # taken
    assert add(username="two words").status_code == 400
    assert add(password="short", password2="short").status_code == 400
    assert add(password2="different1").status_code == 400
    assert add(name="").status_code == 400
    assert add().status_code == 200


def test_reset_password_logs_them_out(app, client):
    client.post("/users/add", data={"name": "Ravi", "username": "ravi", "password": "ravi12345",
                                     "password2": "ravi12345"}, headers=F)
    ravi = User.query.filter_by(username="ravi").one()
    c2 = login(app, "ravi", "ravi12345")
    assert logged_in(c2)
    r = client.post(f"/users/{ravi.id}/password", data={"password": "newpass99", "password2": "newpass99"}, headers=F)
    assert r.status_code == 200
    assert not logged_in(c2)                                    # old login no longer valid
    assert logged_in(login(app, "ravi", "newpass99"))
    assert "ravi12345" not in str([a.new_json for a in AuditLog.query.all()])   # passwords never stored


def test_change_own_password(app, client):
    me = User.query.filter_by(username="finance").one()
    r = client.post(f"/users/{me.id}/password", data={"current": "wrong", "password": "brandnew1",
                                                     "password2": "brandnew1"}, headers=F)
    assert r.status_code == 400 and "current password" in r.get_json()["error"]
    other = login(app, "finance", "secret")
    r = client.post(f"/users/{me.id}/password", data={"current": "secret", "password": "brandnew1",
                                                     "password2": "brandnew1"}, headers=F)
    assert r.status_code == 200
    assert logged_in(client)                                    # stays logged in where it was changed
    assert not logged_in(other)                                 # other devices are logged out
    assert logged_in(login(app, "finance", "brandnew1"))


def test_deactivate(app, client):
    me = User.query.filter_by(username="finance").one()
    r = client.post(f"/users/{me.id}", data={"toggle": "1"}, headers=F)
    assert r.status_code == 400 and "yourself" in r.get_json()["error"]
    client.post("/users/add", data={"name": "Old staff", "username": "old", "password": "oldpass12",
                                     "password2": "oldpass12"}, headers=F)
    old = User.query.filter_by(username="old").one()
    c2 = login(app, "old", "oldpass12")
    assert logged_in(c2)
    assert client.post(f"/users/{old.id}", data={"toggle": "1"}, headers=F).status_code == 200
    db.session.refresh(old)
    assert not old.active and not logged_in(c2) and not logged_in(login(app, "old", "oldpass12"))
    # renaming; then History shows the user changes
    assert client.post(f"/users/{old.id}", data={"name": "Former staff", "username": "old"}, headers=F).status_code == 200
    hist = client.get("/history?kind=users").get_data(as_text=True)
    assert "User added" in hist and "User deactivated" in hist and "User changed" in hist
    # the last active user can't be deactivated (from another user's session)
    assert client.post(f"/users/{old.id}", data={"toggle": "1"}, headers=F).status_code == 200   # active again
    c3 = login(app, "old", "oldpass12")
    assert c3.post(f"/users/{me.id}", data={"toggle": "1"}, headers=F).status_code == 200      # 1 left: old
    r = client.post(f"/users/{old.id}", data={"toggle": "1"}, headers=F)
    assert not logged_in(client)                                # finance was deactivated, so logged out
