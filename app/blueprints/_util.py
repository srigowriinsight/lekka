"""Small helpers shared by the pages: popup (fetch) responses, safe redirects, error handling."""
from functools import wraps
from urllib.parse import urlparse

from flask import flash, jsonify, redirect, render_template, request, url_for

from ..extensions import db
from ..services.common import LekkaError


def is_fetch():
    return request.headers.get("X-Requested-With") == "fetch"


def safe_next(default="grid.index"):
    nxt = request.values.get("next") or request.referrer or ""
    p = urlparse(nxt)
    if nxt and not p.netloc and nxt.startswith("/"):
        return nxt
    if p.netloc and p.netloc == request.host:
        return p.path + (f"?{p.query}" if p.query else "")
    return url_for(default)


def done(message=None, nxt=None):
    db.session.commit()
    nxt = nxt or safe_next()
    if message:
        flash(message, "ok")
    if is_fetch():
        return jsonify(redirect=nxt)
    return redirect(nxt)


def fail(message, nxt=None):
    db.session.rollback()
    if is_fetch():
        return jsonify(error=str(message)), 400
    flash(str(message), "error")
    return redirect(nxt or safe_next())


def fragment(template, **ctx):
    """Popups load a bare fragment; opening the same URL directly shows it inside the page frame."""
    if is_fetch() or request.args.get("fragment"):
        return render_template(template, **ctx)
    return render_template("page_wrap.html", inner=template, **ctx)


def handles_errors(view):
    @wraps(view)
    def wrapper(*a, **kw):
        try:
            return view(*a, **kw)
        except LekkaError as e:
            return fail(e)
    return wrapper
