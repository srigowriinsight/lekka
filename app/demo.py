"""The design doc's worked example (opening ₹25,00,000 on 1 Nov 2026, viewed on 25 Nov 2026).
With shift=True every date moves so that 25 Nov 2026 lands on today, so the demo looks live."""
from datetime import date, timedelta
from decimal import Decimal

from .extensions import db
from .models import IN, OUT, Head
from .services.common import today
from .services.entries import cancel, create_entry, get_account, settle, split_entry

BASE_TODAY = date(2026, 11, 25)


def load_demo(shift=True):
    off = (today() - BASE_TODAY) if shift else timedelta(0)

    def d(y, m, dd):
        return date(y, m, dd) + off

    heads = {}
    for i, name in enumerate(["Product sales", "Calibration contract", "Raw materials and components",
                              "Salaries", "Factory rent", "SMT machine AMC", "Trade shows", "Scrap"]):
        h = Head.query.filter_by(name=name).first() or Head(name=name, sort_order=i)
        db.session.add(h)
        heads[name] = h
    db.session.flush()

    acc = get_account()
    acc.opening_balance, acc.opening_date = Decimal("2500000"), d(2026, 11, 1)
    from .services.common import audit
    audit("account", acc.id, "edit", "demo data")

    def item(direction, head, title, amount, when, **kw):
        return create_entry(direction=direction, head_id=heads[head].id, title=title,
                            amount=amount, planned_date=when, **kw)

    po = item(IN, "Product sales", "PO-1042 OEM order", 1200000, d(2026, 11, 5), reference_no="PO-1042")
    split_entry(po, [{"title": "Advance 40%", "amount": 480000, "planned_date": d(2026, 11, 5)},
                     {"title": "On delivery 60%", "amount": 720000, "planned_date": d(2026, 11, 20)}])
    adv, delivery = po.children
    split_entry(delivery, [{"title": "Part 1", "amount": 360000, "planned_date": d(2026, 11, 20)},
                           {"title": "Part 2", "amount": 360000, "planned_date": d(2026, 12, 5)}])
    settle(adv, d(2026, 11, 5))
    settle(delivery.children[0], d(2026, 11, 20))

    item(IN, "Product sales", "Invoice INV-388", 200000, d(2026, 11, 15), reference_no="INV-388")

    for m, y in ((11, 2026), (12, 2026), (1, 2027)):
        fee = item(IN, "Calibration contract", "Calibration contract", 75000, d(y, m, 10))
        rent = item(OUT, "Factory rent", "Factory rent", 150000, d(y, m, 5))
        last = {11: 30, 12: 31, 1: 31}[m]
        item(OUT, "Salaries", "Salaries", 900000, d(y, m, last))
        if m == 11:
            settle(fee, d(2026, 11, 10))
            settle(rent, d(2026, 11, 5))

    pov = item(OUT, "Raw materials and components", "PO-V-221 components", 800000, d(2026, 11, 10),
               reference_no="PO-V-221", remarks="Vendor: 3 payments agreed")
    split_entry(pov, [{"title": "First payment", "amount": 300000, "planned_date": d(2026, 11, 10)},
                      {"title": "Balance", "amount": 500000, "planned_date": d(2026, 11, 25)}])
    first, balance = pov.children
    split_entry(balance, [{"title": "Instalment 1", "amount": 250000, "planned_date": d(2026, 11, 25)},
                          {"title": "Instalment 2", "amount": 250000, "planned_date": d(2026, 12, 10)}])
    settle(first, d(2026, 11, 10))

    item(OUT, "SMT machine AMC", "SMT machine AMC", 60000, d(2026, 12, 1))
    booth = item(OUT, "Trade shows", "Trade-show booth", 100000, d(2026, 11, 18))
    cancel(booth, "Event postponed")
    db.session.flush()
