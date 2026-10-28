from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import login_required, login_user, logout_user

from ..models import User

bp = Blueprint("auth", __name__)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        u = User.query.filter_by(username=(request.form.get("username") or "").strip()).first()
        if u and u.active and u.check_password(request.form.get("password") or ""):
            login_user(u, remember=bool(request.form.get("remember")))
            nxt = request.args.get("next") or ""
            return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("grid.index"))
        flash("Wrong username or password.", "error")
    return render_template("login.html")


@bp.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))
