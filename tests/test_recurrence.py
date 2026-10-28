"""Recurring templates (Test cases tab, section 6; Edge cases section 2)."""
from datetime import date
from decimal import Decimal

import pytest

from app.extensions import db
from app.models import OUT, IN, CashflowEntry, RecurringTemplate
from app.services.common import LekkaError
from app.services.entries import edit_entry, settle
from app.services.recurrence import (create_template, delete_template, end_series, extend_all,
                                     generate, occurrence_dates, occurrences, pause, resume,
                                     update_template)


def tpl_data(head, **kw):
    d = dict(title="Factory rent", head_id=head.id, direction=OUT, amount="1,50,000",
             freq="MONTHLY", interval_n=1, anchor_day=5, start_date="2026-04-01")
    d.update(kw)
    return d


def dates(tpl, upto):
    return occurrence_dates(tpl, date.fromisoformat(upto))


def test_minimal_template_and_horizon(app, head):              # TC-48, TC-49, TC-58
    t = create_template(tpl_data(head("Factory rent")))
    roots = occurrences(t)
    assert roots[0].occurrence_date == date(2026, 4, 5)
    assert roots[-1].occurrence_date == date(2027, 11, 5)       # today 25 Nov 2026 + 12 months
    assert roots[0].head.name == "Factory rent" and roots[0].title == "Factory rent"


def test_required_fields(app, head):                           # TC-50, TC-152
    for bad in ({"title": ""}, {"head_id": None}, {"amount": ""}, {"direction": "X"}):
        with pytest.raises(LekkaError):
            create_template(tpl_data(head("Rent"), **bad))


def test_last_day_and_leap_year(app, head):                    # TC-51, EC-13
    t = RecurringTemplate(freq="MONTHLY", interval_n=1, anchor_day=-1, start_date=date(2026, 11, 30))
    assert dates(t, "2028-03-31")[:4] == [date(2026, 11, 30), date(2026, 12, 31), date(2027, 1, 31), date(2027, 2, 28)]
    assert date(2028, 2, 29) in dates(t, "2028-03-31")


def test_thirtieth_in_february(app):                           # EC-14
    t = RecurringTemplate(freq="MONTHLY", interval_n=1, anchor_day=30, start_date=date(2027, 1, 30))
    assert dates(t, "2027-03-31") == [date(2027, 1, 30), date(2027, 2, 28), date(2027, 3, 30)]


def test_quarterly_and_fortnightly(app):                       # TC-52, TC-53
    q = RecurringTemplate(freq="QUARTERLY", interval_n=1, anchor_day=1, start_date=date(2026, 12, 1))
    assert dates(q, "2027-09-30") == [date(2026, 12, 1), date(2027, 3, 1), date(2027, 6, 1), date(2027, 9, 1)]
    w = RecurringTemplate(freq="WEEKLY", interval_n=2, anchor_day=5, start_date=date(2026, 11, 7))
    assert dates(w, "2026-12-05") == [date(2026, 11, 7), date(2026, 11, 21), date(2026, 12, 5)]


def test_end_and_count(app):
    t = RecurringTemplate(freq="MONTHLY", interval_n=1, anchor_day=10, start_date=date(2026, 1, 10), max_occurrences=3)
    assert len(dates(t, "2030-01-01")) == 3


def test_no_duplicates(app, head):                             # TC-57
    t = create_template(tpl_data(head("Factory rent")))
    n = CashflowEntry.query.count()
    generate(t)
    extend_all()
    assert CashflowEntry.query.count() == n


def test_custom_horizon(app, head):                            # TC-109
    t = create_template(tpl_data(head("Salaries"), title="Salaries", anchor_day=-1,
                                 start_date="2026-11-01", horizon_until="2029-03-31"))
    assert occurrences(t)[-1].occurrence_date == date(2029, 3, 31)


def test_edit_one_occurrence_then_series(app, head):           # TC-54, TC-56, EC-23
    t = create_template(tpl_data(head("Salaries"), title="Salaries", amount=900000, anchor_day=-1,
                                 start_date="2026-11-01"))
    dec = [r for r in occurrences(t) if r.occurrence_date == date(2026, 12, 31)][0]
    edit_entry(dec, amount=980000)
    assert dec.is_overridden
    _, kept = update_template(t, tpl_data(head("Salaries"), title="Salaries", amount=950000, anchor_day=-1,
                                          start_date="2026-11-01"), scope="series")
    amounts = {r.occurrence_date: r.amount for r in occurrences(t)}
    assert amounts[date(2026, 12, 31)] == Decimal("980000") and amounts[date(2027, 1, 31)] == Decimal("950000")
    assert dec in kept


def test_this_and_future(app, head):                           # TC-55
    h = head("Factory rent")
    t = create_template(tpl_data(h))
    new, _ = update_template(t, tpl_data(h, amount=165000), scope="future", from_date="2027-04-05")
    assert t.end_date == date(2027, 4, 4)
    old_amounts = {r.occurrence_date: r.amount for r in occurrences(t)}
    assert max(old_amounts) == date(2027, 3, 5)
    assert occurrences(new)[0].occurrence_date == date(2027, 4, 5)
    assert occurrences(new)[0].amount == Decimal("165000")


def test_future_from_ticked_date(app, head):                   # EC-22
    h = head("Factory rent")
    t = create_template(tpl_data(h, start_date="2026-10-01"))
    nov = [r for r in occurrences(t) if r.occurrence_date == date(2026, 11, 5)][0]
    settle(nov, date(2026, 11, 5))
    new, kept = update_template(t, tpl_data(h, amount=165000, start_date="2026-10-01"),
                                scope="future", from_date="2026-11-05")
    assert nov in kept and nov.amount == Decimal("150000")
    assert occurrences(new)[0].occurrence_date == date(2026, 12, 5)


def test_frequency_change_no_duplicates(app, head):            # EC-24
    h = head("Logistics")
    t = create_template(dict(title="Courier pickup", head_id=h.id, direction=OUT, amount=8000,
                             freq="WEEKLY", anchor_day=4, start_date="2026-11-27"))
    update_template(t, dict(title="Courier pickup", head_id=h.id, direction=OUT, amount=8000, freq="WEEKLY",
                            interval_n=2, anchor_day=4, start_date="2026-11-27"), scope="series")
    ds = [r.occurrence_date for r in occurrences(t)]
    assert len(ds) == len(set(ds)) and ds[1] == date(2026, 12, 11)


def test_split_pattern(app, head):                             # TC-59
    t = create_template(dict(title="Insurance", head_id=head("Insurance").id, direction=OUT, amount=240000,
                             freq="YEARLY", start_date="2027-04-01",
                             split_pattern=[{"pct": 50, "offset_months": 0}, {"pct": 50, "offset_months": 6}]))
    r = occurrences(t)[0]
    assert [c.amount for c in r.children] == [Decimal("120000"), Decimal("120000")]
    assert r.children[1].planned_date == date(2027, 10, 1)


def test_pause_and_resume(app, head, set_today):               # EC-21
    t = create_template(tpl_data(head("Factory rent"), start_date="2026-11-01"))
    pause(t, date(2026, 12, 1))
    assert max(r.occurrence_date for r in occurrences(t)) == date(2026, 11, 5)
    set_today("2027-02-01")
    resume(t, create_skipped=False)
    assert date(2027, 1, 5) not in [r.occurrence_date for r in occurrences(t)]
    assert date(2027, 2, 5) in [r.occurrence_date for r in occurrences(t)]


def test_pause_resume_create_skipped(app, head, set_today):
    t = create_template(tpl_data(head("Factory rent"), start_date="2026-11-01"))
    pause(t, date(2026, 12, 1))
    set_today("2027-02-01")
    resume(t, create_skipped=True)
    assert date(2027, 1, 5) in [r.occurrence_date for r in occurrences(t)]


def test_end_series(app, head):                                # TC-61
    t = create_template(tpl_data(head("Factory rent"), start_date="2026-11-01"))
    end_series(t, date(2027, 6, 1))
    after = [r for r in occurrences(t) if r.occurrence_date > date(2027, 6, 1)]
    assert after and all(r.lifecycle == "CANCELLED" and r.cancel_reason == "series ended" for r in after)


def test_shorten_horizon(app, head):                           # EC-20
    h = head("Salaries")
    t = create_template(tpl_data(h, title="Salaries", horizon_until="2029-03-31", start_date="2026-11-01"))
    keep = [r for r in occurrences(t) if r.occurrence_date == date(2028, 1, 5)][0]
    edit_entry(keep, remarks="handled")      # edited by hand -> kept
    _, kept = update_template(t, tpl_data(h, title="Salaries", horizon_until="2027-03-31",
                                          start_date="2026-11-01"), scope="series")
    ds = [r.occurrence_date for r in occurrences(t)]
    assert date(2027, 3, 5) in ds and date(2027, 5, 5) not in ds and date(2028, 1, 5) in ds
    assert keep in kept


def test_delete_template_rules(app, head):
    # Deleting always works: untouched dates go, a ticked date stays as a one-off item.
    t = create_template(tpl_data(head("Factory rent"), start_date="2026-11-01"))
    first = occurrences(t)[0]
    settle(first, date(2026, 11, 5))
    total = len(occurrences(t))
    removed, kept = delete_template(t)
    db.session.flush()
    assert (removed, kept) == (total - 1, 1)
    assert first.template_id is None and first.is_settled
    assert db.session.get(RecurringTemplate, t.id) is None
