"""Price list: products with a price before GST and a GST rate. GST and the total are calculated."""
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from ..extensions import db
from ..models import Category, PriceItem
from .common import LekkaError, audit, inr, snapshot, user_id

# GST slabs since 22 Sep 2025 (GST 2.0): 0, 5, 18 and 40 (sin and luxury goods); 3 for gold and silver.
# Any rate from 0 to 100 can still be typed.
GST_RATES = ["0", "3", "5", "18", "40"]
FIELDS = ["product", "price", "gst_rate", "remarks", "category_id"]
PAISA = Decimal("0.01")


def _num(raw, what):
    s = str(raw or "").replace(",", "").replace("₹", "").replace("%", "").strip()
    if not s:
        raise LekkaError(f"Enter the {what}.")
    try:
        d = Decimal(s)
    except InvalidOperation:
        raise LekkaError(f"The {what} must be a number.")
    if not d.is_finite():
        raise LekkaError(f"The {what} must be a number.")
    return d


def read(form, current=None):
    """Product, price before GST and GST rate from a form. With 'Price includes GST' ticked, the price typed
    is the total, and the price before GST is worked back from it: total x 100 / (100 + rate)."""
    product = " ".join((form.get("product") or "").split())
    if not product:
        raise LekkaError("Enter the product.")
    if len(product) > 160:
        raise LekkaError("The product name can be at most 160 characters.")
    clash = PriceItem.query.filter(db.func.lower(PriceItem.product) == product.lower())
    if current is not None:
        clash = clash.filter(PriceItem.id != current.id)
    if clash.first():
        raise LekkaError(f"'{product}' is already in the price list.")
    rate = _num(form.get("gst_rate"), "GST %")
    if rate < 0 or rate > 100:
        raise LekkaError("GST % must be between 0 and 100.")
    rate = rate.quantize(PAISA, ROUND_HALF_UP)
    price = _num(form.get("price"), "price")
    if price < 0:
        raise LekkaError("The price can't be negative.")
    if form.get("includes_gst"):
        price = price * 100 / (100 + rate)
    price = price.quantize(PAISA, ROUND_HALF_UP)
    if price >= Decimal("10000000000000"):
        raise LekkaError("That price is too large.")
    remarks = (form.get("remarks") or "").strip() or None
    if remarks and len(remarks) > 255:
        raise LekkaError("Remarks can be at most 255 characters.")
    out = {"product": product, "price": price, "gst_rate": rate, "remarks": remarks}
    if "category_id" in form:                 # optional; blank = no category
        raw = (form.get("category_id") or "").strip()
        cid = int(raw) if raw else None
        if cid is not None and db.session.get(Category, cid) is None:
            raise LekkaError("That category no longer exists. Reload and choose again.")
        out["category_id"] = cid
    return out


def add(form) -> PriceItem:
    v = read(form)
    p = PriceItem(**v, created_by=user_id(), updated_by=user_id())
    db.session.add(p)
    db.session.flush()
    audit("price_item", p.id, "create", summary(p), new=snapshot(p, FIELDS))
    return p


def update(p: PriceItem, form) -> bool:
    v = read(form, current=p)
    old = snapshot(p, FIELDS)
    if all(getattr(p, k) == val for k, val in v.items()):
        return False
    for k, val in v.items():
        setattr(p, k, val)
    p.updated_by = user_id()
    audit("price_item", p.id, "edit", summary(p), old=old, new=snapshot(p, FIELDS))
    return True


def delete(p: PriceItem):
    audit("price_item", p.id, "delete", summary(p), old=snapshot(p, FIELDS))
    db.session.delete(p)


def for_revenue():
    """Products for the revenue form's drop-down: by category path, then name."""
    items = PriceItem.query.all()
    return sorted(items, key=lambda p: ((p.category.path() if p.category else "~"), p.product.lower()))


def summary(p: PriceItem) -> str:
    return f"₹{inr(p.price)} + {p.gst_rate.normalize():f}% GST ₹{inr(p.gst_amount)} = ₹{inr(p.total)}"
