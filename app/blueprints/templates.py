"""Recurring templates page."""
from flask import Blueprint, render_template, request
from flask_login import login_required

from ..extensions import db
from ..models import Head, RecurringTemplate
from ..services import recurrence as rec
from ..services.common import LekkaError, today
from ._util import done, fragment, handles_errors, safe_next

bp = Blueprint("templates", __name__, url_prefix="/templates")


def _form_data(f):
    data = {k: f.get(k) for k in ("title", "head_id", "direction", "amount", "freq", "interval_n",
                                  "anchor_day", "start_date", "end_date", "max_occurrences",
                                  "horizon_until", "remarks")}
    if f.get("freq") == "WEEKLY" and f.get("weekday") not in (None, ""):
        data["anchor_day"] = f.get("weekday")
    pcts = [p for p in f.getlist("pattern_pct") if p.strip()]
    if pcts:
        offs = f.getlist("pattern_offset")
        data["split_pattern"] = [{"pct": float(p) if "." in p else int(p),
                                  "offset_months": int(offs[i] or 0) if i < len(offs) else 0}
                                 for i, p in enumerate(pcts)]
    return data


def _next_date(tpl):
    roots = [r for r in rec.occurrences(tpl) if r.occurrence_date >= today() and r.lifecycle == "ACTIVE"]
    return roots[0].occurrence_date if roots else None


@bp.route("/")
@login_required
def index():
    tpls = RecurringTemplate.query.order_by(RecurringTemplate.is_active.desc(), RecurringTemplate.title).all()
    return render_template("templates.html", tpls=tpls, next_date=_next_date)


@bp.route("/new", methods=["GET", "POST"])
@login_required
@handles_errors
def new():
    heads = Head.query.filter_by(is_active=True).order_by(Head.sort_order, Head.name).all()
    if request.method == "GET":
        return fragment("partials/template_form.html", tpl=None, heads=heads, next=safe_next())
    tpl = rec.create_template(_form_data(request.form))
    n = len(rec.occurrences(tpl))
    return done(f"Template '{tpl.title}' saved; {n} dated item(s) created.")


@bp.route("/<int:tpl_id>/edit", methods=["GET", "POST"])
@login_required
@handles_errors
def edit(tpl_id):
    tpl = db.session.get(RecurringTemplate, tpl_id)
    if tpl is None:
        raise LekkaError("Template not found.")
    heads = Head.query.order_by(Head.sort_order, Head.name).all()
    if request.method == "GET":
        upcoming = [r for r in rec.occurrences(tpl) if r.occurrence_date >= today()][:6]
        return fragment("partials/template_form.html", tpl=tpl, heads=heads, upcoming=upcoming, next=safe_next())
    f = request.form
    result, kept = rec.update_template(tpl, _form_data(f), f.get("scope"), f.get("from_date"))
    msg = "Template updated."
    if kept:
        msg += f" {len(kept)} handled item(s) were kept as they were: " + ", ".join(
            f"{k.occurrence_date:%d %b %Y}" for k in kept[:6]) + ("…" if len(kept) > 6 else "")
    return done(msg)


@bp.route("/<int:tpl_id>/more")
@login_required
@handles_errors
def more(tpl_id):
    tpl = db.session.get(RecurringTemplate, tpl_id)
    if tpl is None:
        raise LekkaError("Template not found.")
    roots = rec.occurrences(tpl)
    untouched = sum(1 for r in roots if rec.is_untouched(r))
    return fragment("partials/template_more.html", t=tpl, untouched=untouched, handled=len(roots) - untouched,
                    next=safe_next())


@bp.route("/<int:tpl_id>/action", methods=["POST"])
@login_required
@handles_errors
def action(tpl_id):
    tpl = db.session.get(RecurringTemplate, tpl_id)
    if tpl is None:
        raise LekkaError("Template not found.")
    a = request.form.get("do")
    if a == "pause":
        rec.pause(tpl)
        return done(f"'{tpl.title}' paused.")
    if a == "resume":
        made = rec.resume(tpl, create_skipped=request.form.get("create_skipped") == "1")
        return done(f"'{tpl.title}' resumed; {len(made)} item(s) created.")
    if a == "end":
        rec.end_series(tpl, request.form.get("end_date"))
        return done(f"'{tpl.title}' ends on {request.form.get('end_date')}.")
    if a == "delete":
        title = tpl.title
        removed, kept = rec.delete_template(tpl)
        msg = f"'{title}' deleted; {removed} planned date(s) removed."
        if kept:
            msg += f" {kept} item(s) already ticked or changed stay as one-off items."
        return done(msg)
    raise LekkaError("Unknown action.")
