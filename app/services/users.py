"""Users: everyone who can log in. All users can do everything; the list is only about who is who
(History shows who made each change). Users are deactivated, never deleted, so History keeps their name."""
import re

from ..extensions import db
from ..models import User
from .common import LekkaError, audit

MIN_PASSWORD = 8
_USERNAME = re.compile(r"^[a-z0-9._-]{3,40}$")


def check_password_pair(pw, again):
    pw = pw or ""
    if len(pw) < MIN_PASSWORD:
        raise LekkaError(f"The password must be at least {MIN_PASSWORD} characters.")
    if pw != (again or ""):
        raise LekkaError("The two passwords don't match.")
    return pw


def _username(raw, current=None):
    u = (raw or "").strip().lower()
    if not _USERNAME.match(u):
        raise LekkaError("Username: 3 to 40 characters, using only letters, numbers, dot, dash or underscore "
                         "(no spaces).")
    q = User.query.filter(db.func.lower(User.username) == u)
    if current is not None:
        q = q.filter(User.id != current.id)
    if q.first():
        raise LekkaError(f"The username '{u}' is already taken.")
    return u


def _name(raw):
    n = " ".join((raw or "").split())
    if not n:
        raise LekkaError("Enter the person's name, as it should appear in History.")
    if len(n) > 120:
        raise LekkaError("The name can be at most 120 characters.")
    return n


def add(form) -> User:
    username = _username(form.get("username"))
    name = _name(form.get("name"))
    pw = check_password_pair(form.get("password"), form.get("password2"))
    u = User(username=username, name=name, role="admin", active=True)
    u.set_password(pw)
    db.session.add(u)
    db.session.flush()
    audit("app_user", u.id, "create", f"{name} ({username})", new={"username": username, "name": name})
    return u


def update(u: User, form) -> bool:
    username = _username(form.get("username"), current=u)
    name = _name(form.get("name"))
    if (username, name) == (u.username, u.name):
        return False
    old = {"username": u.username, "name": u.name}
    u.username, u.name = username, name
    audit("app_user", u.id, "edit", f"{old['name']} ({old['username']}) → {name} ({username})",
          old=old, new={"username": username, "name": name})
    return True


def reset_password(u: User, form, by: User):
    """Set a new password for someone (they are logged out everywhere and log in with the new one)."""
    pw = check_password_pair(form.get("password"), form.get("password2"))
    u.set_password(pw)
    audit("app_user", u.id, "password", "Password changed by themselves" if u.id == by.id
          else f"Password reset by {by.name or by.username}")


def change_own_password(u: User, form):
    if not u.check_password(form.get("current") or ""):
        raise LekkaError("Your current password is not right.")
    reset_password(u, form, by=u)


def set_active(u: User, active: bool, by: User):
    if not active:
        if u.id == by.id:
            raise LekkaError("You can't deactivate yourself. Ask another user to do it.")
        others = User.query.filter(User.active.is_(True), User.id != u.id).count()
        if others == 0:
            raise LekkaError("At least one user must stay active.")
    u.active = active
    audit("app_user", u.id, "activate" if active else "deactivate", u.name or u.username)
