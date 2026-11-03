"""Users page: add people, rename them, reset a password, deactivate. Everyone has the same rights."""
from flask import Blueprint, render_template, request
from flask_login import current_user, login_required, login_user

from ..extensions import db
from ..models import AuditLog, User
from ..services import users as svc
from ..services.common import LekkaError
from ._util import done, fragment, handles_errors, safe_next

bp = Blueprint("users", __name__, url_prefix="/users")


def _user(user_id):
    u = db.session.get(User, user_id)
    if u is None:
        raise LekkaError("That user no longer exists. Reload the page.")
    return u


@bp.route("/")
@login_required
def index():
    users = User.query.order_by(User.active.desc(), db.func.lower(User.name)).all()
    changes = dict(db.session.query(AuditLog.user_id, db.func.count(AuditLog.id)).group_by(AuditLog.user_id).all())
    return render_template("users.html", users=users, changes=changes, min_pw=svc.MIN_PASSWORD)


@bp.route("/add", methods=["POST"])
@login_required
@handles_errors
def add():
    u = svc.add(request.form)
    return done(f"'{u.name}' can now log in as '{u.username}'.")


@bp.route("/<int:user_id>", methods=["POST"])
@login_required
@handles_errors
def update(user_id):
    u = _user(user_id)
    if request.form.get("toggle"):
        svc.set_active(u, not u.active, by=current_user)
        return done(f"'{u.name}' {'can log in again' if u.active else 'is deactivated and can no longer log in'}.")
    changed = svc.update(u, request.form)
    return done(f"'{u.name}' saved." if changed else "Nothing changed.")


@bp.route("/<int:user_id>/password", methods=["GET", "POST"])
@login_required
@handles_errors
def password(user_id):
    u = _user(user_id)
    if request.method == "GET":
        return fragment("partials/user_password.html", u=u, own=u.id == current_user.id,
                        min_pw=svc.MIN_PASSWORD, next=safe_next())
    if u.id == current_user.id:
        svc.change_own_password(u, request.form)
        db.session.flush()
        login_user(u)          # the old login is no longer valid after a password change; stay logged in here
        return done("Your password is changed. Other devices where you were logged in are logged out.")
    svc.reset_password(u, request.form, by=current_user)
    return done(f"New password set for '{u.name}'. Give it to them; they are logged out everywhere until "
                f"they log in with it.")
