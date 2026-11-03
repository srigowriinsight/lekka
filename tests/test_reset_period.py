"""Reset a period with the default window (today 00:00 to now), including a shared revenue and a split."""
import re
from decimal import Decimal

from app.extensions import db
from app.models import CashflowEntry, Head

F = {"X-Requested-With": "fetch"}


def test_reset_today_with_shared_revenue_and_splits(client):
    for n in ("Sales", "Partner"):
        db.session.add(Head(name=n))
    db.session.commit()
    h = lambda n: Head.query.filter_by(name=n).one().id
    t = lambda x: CashflowEntry.query.filter_by(title=x).one()
    r = client.post("/entries/new", data={"direction": "IN", "head_id": h("Sales"), "title": "Rev", "amount": "100000",
                                          "planned_date": "2026-12-10", "share_pct": "50",
                                          "share_head_id": h("Partner"), "share_title": "Share",
                                          "share_date": "2026-12-12"}, headers=F)
    assert r.status_code == 200
    rev = t("Rev")
    client.post(f"/entries/{rev.id}/split", data={"part_id": ["", ""], "part_title": ["A", "B"],
                                                  "part_amount": ["50000", "50000"],
                                                  "part_date": ["2026-12-10", "2026-12-20"], "part_remarks": ["", ""],
                                                  "version": rev.version}, headers=F)
    client.post("/entries/new", data={"direction": "OUT", "head_id": h("Partner"), "title": "Plain",
                                      "amount": "100", "planned_date": "2026-12-10"}, headers=F)
    # the popup's own default window: today 00:00 to now (to the minute)
    pop = client.get("/history/reset", headers=F).get_data(as_text=True)
    start = re.search(r'name="start"[^>]*value="([^"]+)"', pop).group(1)
    end = re.search(r'name="end"[^>]*value="([^"]+)"', pop).group(1)
    prev = client.post("/history/reset", data={"start": start, "end": end, "step": "preview"}, headers=F)
    html = prev.get_json()["html"]
    assert "Rev" in html and "Share" in html and "Plain" in html, html
    r = client.post("/history/reset", data={"start": start, "end": end, "step": "run", "sure": "1"}, headers=F)
    assert r.status_code == 200, r.get_data(as_text=True)
    assert all(e.is_deleted for e in CashflowEntry.query.all())
