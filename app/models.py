"""Database tables. Follows the Design tab, section 8, with the later decisions applied:
heads hold both directions, direction lives on each item, single account, one user,
remarks on every item, daily balance adjustments."""
from datetime import UTC, datetime
from decimal import Decimal

from flask_login import UserMixin
from sqlalchemy import CheckConstraint, Index, UniqueConstraint
from werkzeug.security import check_password_hash, generate_password_hash

from .extensions import db

Money = db.Numeric(15, 2)
IN, OUT = "IN", "OUT"


def utcnow():
    return datetime.now(UTC).replace(tzinfo=None)

ACTIVE, ON_HOLD, CANCELLED = "ACTIVE", "ON_HOLD", "CANCELLED"
DELETED = "DELETED"     # hidden everywhere; the row stays so History and recurring templates still know it


class TimestampMixin:
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)
    created_by = db.Column(db.Integer, db.ForeignKey("app_user.id"))
    updated_by = db.Column(db.Integer, db.ForeignKey("app_user.id"))


class User(UserMixin, db.Model):
    __tablename__ = "app_user"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    name = db.Column(db.String(120))
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), default="admin", nullable=False)
    active = db.Column(db.Boolean, default=True, nullable=False)

    @property
    def is_active(self):
        return self.active

    def login_token(self):
        """What the login cookie remembers: the user and a fingerprint of the password, so changing a password
        (or deactivating the user) logs them out on every other device."""
        import hashlib
        return f"{self.id}:{hashlib.sha256(self.password_hash.encode()).hexdigest()[:16]}"

    def get_id(self):
        return self.login_token()

    def set_password(self, pw):
        self.password_hash = generate_password_hash(pw)

    def check_password(self, pw):
        return check_password_hash(self.password_hash, pw)


class Head(db.Model):
    """A category. One head can hold both inflow and outflow items. Heads form a tree like the OPEX sheet
    (Salaries > Factory staff > Contract labour); items can sit on any head, and a parent's figures in the
    grid include its sub-heads."""
    __tablename__ = "head"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False)
    sort_order = db.Column(db.Integer, default=0, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    parent_id = db.Column(db.Integer, db.ForeignKey("head.id", name="fk_head_parent"), index=True)
    # Which items may use it: IN = revenue heads, OUT = expense heads, BOTH = either.
    kind = db.Column(db.String(4), nullable=False, default="BOTH", server_default="BOTH")

    parent = db.relationship("Head", remote_side=[id], backref=db.backref("children", order_by="Head.sort_order"))

    def fits(self, direction) -> bool:
        return self.kind == "BOTH" or self.kind == direction

    def ancestors(self):
        out, node = [], self.parent
        while node is not None:
            out.append(node)
            node = node.parent
        return out

    def path(self, sep=" › "):
        return sep.join([a.name for a in reversed(self.ancestors())] + [self.name])

    def subtree_ids(self):
        ids, todo = [], [self]
        while todo:
            h = todo.pop()
            ids.append(h.id)
            todo.extend(h.children)
        return ids


class CostCentre(db.Model):
    __tablename__ = "cost_centre"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)


class Counterparty(db.Model):
    __tablename__ = "counterparty"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(160), unique=True, nullable=False)
    kind = db.Column(db.String(20), default="other", nullable=False)  # customer / vendor / other
    gstin = db.Column(db.String(20))
    is_active = db.Column(db.Boolean, default=True, nullable=False)


class Account(db.Model):
    """Version 1 runs on a single combined account."""
    __tablename__ = "account"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False, default="Main account")
    opening_balance = db.Column(Money, nullable=False, default=Decimal("0"))
    opening_date = db.Column(db.Date, nullable=False)


class RecurringTemplate(TimestampMixin, db.Model):
    __tablename__ = "recurring_template"
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    head_id = db.Column(db.Integer, db.ForeignKey("head.id"), nullable=False)
    direction = db.Column(db.String(3), nullable=False)
    amount = db.Column(Money, nullable=False)
    freq = db.Column(db.String(10), nullable=False)  # DAILY WEEKLY MONTHLY QUARTERLY YEARLY
    interval_n = db.Column(db.SmallInteger, nullable=False, default=1)
    anchor_day = db.Column(db.SmallInteger)  # weekday 0-6 (weekly) or day of month, -1 = last day
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date)
    max_occurrences = db.Column(db.Integer)
    split_pattern = db.Column(db.JSON)  # e.g. [{"pct": 50, "offset_months": 0}, {"pct": 50, "offset_months": 6}]
    horizon_until = db.Column(db.Date)  # NULL = today + default horizon
    resume_from = db.Column(db.Date)  # after a pause: no dates before this are created
    paused_on = db.Column(db.Date)
    supersedes_id = db.Column(db.Integer, db.ForeignKey("recurring_template.id"))
    remarks = db.Column(db.Text)
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    head = db.relationship("Head")


class CashflowEntry(TimestampMixin, db.Model):
    """A planned item or one part of a split. Only leaves (no children) carry money."""
    __tablename__ = "cashflow_entry"
    id = db.Column(db.Integer, primary_key=True)
    parent_id = db.Column(db.Integer, db.ForeignKey("cashflow_entry.id"))
    root_id = db.Column(db.Integer, index=True)
    depth = db.Column(db.SmallInteger, nullable=False, default=0)
    direction = db.Column(db.String(3), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    head_id = db.Column(db.Integer, db.ForeignKey("head.id"), nullable=False)
    cost_centre_id = db.Column(db.Integer, db.ForeignKey("cost_centre.id"))
    counterparty_id = db.Column(db.Integer, db.ForeignKey("counterparty.id"))
    amount = db.Column(Money, nullable=False)
    planned_date = db.Column(db.Date)
    original_planned_date = db.Column(db.Date)
    reschedule_count = db.Column(db.SmallInteger, nullable=False, default=0)
    is_settled = db.Column(db.Boolean, nullable=False, default=False)
    settled_on = db.Column(db.Date)
    lifecycle = db.Column(db.String(10), nullable=False, default=ACTIVE)
    cancel_reason = db.Column(db.String(255))
    # A partner's share of a revenue item, recorded as its own expense (see services.entries.add_partner_share).
    share_of_id = db.Column(db.Integer, db.ForeignKey("cashflow_entry.id", name="fk_entry_share_of"))
    template_id = db.Column(db.Integer, db.ForeignKey("recurring_template.id"))
    occurrence_date = db.Column(db.Date)
    is_overridden = db.Column(db.Boolean, nullable=False, default=False)
    reference_no = db.Column(db.String(60))
    remarks = db.Column(db.Text)
    version = db.Column(db.Integer, nullable=False, default=1)

    head = db.relationship("Head")
    counterparty = db.relationship("Counterparty")
    cost_centre = db.relationship("CostCentre")
    template = db.relationship("RecurringTemplate")
    parent = db.relationship("CashflowEntry", remote_side=[id], back_populates="children", foreign_keys=[parent_id])
    children = db.relationship(
        "CashflowEntry", back_populates="parent", foreign_keys=[parent_id],
        order_by="CashflowEntry.id",
    )
    share_of = db.relationship("CashflowEntry", remote_side=[id], foreign_keys=[share_of_id])

    __table_args__ = (
        UniqueConstraint("template_id", "occurrence_date", name="uq_occurrence"),
        CheckConstraint("amount > 0", name="ck_amount_positive"),
        CheckConstraint("NOT is_settled OR settled_on IS NOT NULL", name="ck_settled_date"),
        Index("ix_planned", "planned_date"),
        Index("ix_settled", "is_settled", "settled_on"),
    )

    @property
    def is_leaf(self):
        return len(self.children) == 0

    @property
    def is_cancelled(self):
        return self.lifecycle == CANCELLED

    @property
    def is_deleted(self):
        return self.lifecycle == DELETED

    def leaves(self):
        if self.is_leaf:
            return [self]
        out = []
        for c in self.children:
            out.extend(c.leaves())
        return out

    def descendants(self):
        out = []
        for c in self.children:
            out.append(c)
            out.extend(c.descendants())
        return out

    def ancestors(self):
        out, node = [], self.parent
        while node is not None:
            out.append(node)
            node = node.parent
        return out

    def path_titles(self):
        return [a.title for a in reversed(self.ancestors())] + [self.title]

    def date_range(self):
        """First and last date of the live leaves (settled date once ticked)."""
        ds = [l.settled_on if l.is_settled else l.planned_date
              for l in self.leaves() if not l.is_cancelled]
        ds = [d for d in ds if d]
        return (min(ds), max(ds)) if ds else (None, None)


class RescheduleLog(db.Model):
    __tablename__ = "reschedule_log"
    id = db.Column(db.Integer, primary_key=True)
    entry_id = db.Column(db.Integer, db.ForeignKey("cashflow_entry.id"), nullable=False, index=True)
    old_date = db.Column(db.Date, nullable=False)
    new_date = db.Column(db.Date, nullable=False)
    remarks = db.Column(db.String(1000))
    user_id = db.Column(db.Integer, db.ForeignKey("app_user.id"))
    at = db.Column(db.DateTime, default=utcnow, nullable=False)


class BalanceAdjustment(TimestampMixin, db.Model):
    """A manual + or - correction to a day's balance (today or earlier)."""
    __tablename__ = "balance_adjustment"
    id = db.Column(db.Integer, primary_key=True)
    adj_date = db.Column(db.Date, nullable=False, index=True)
    amount = db.Column(Money, nullable=False)  # signed
    remarks = db.Column(db.String(1000))


class BalanceSnapshot(TimestampMixin, db.Model):
    """What the bank statement said on a date, to compare with Lekka."""
    __tablename__ = "balance_snapshot"
    id = db.Column(db.Integer, primary_key=True)
    as_of = db.Column(db.Date, nullable=False, unique=True)
    balance = db.Column(Money, nullable=False)
    remarks = db.Column(db.String(1000))


class Category(db.Model):
    """Product categories for the price list, as a tree (Controllers > PLC > Compact)."""
    __tablename__ = "category"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False)
    parent_id = db.Column(db.Integer, db.ForeignKey("category.id", name="fk_category_parent"), index=True)
    sort_order = db.Column(db.Integer, default=0, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)

    parent = db.relationship("Category", remote_side=[id],
                             backref=db.backref("children", order_by="Category.sort_order"))

    def ancestors(self):
        out, node = [], self.parent
        while node is not None:
            out.append(node)
            node = node.parent
        return out

    def path(self, sep=" › "):
        return sep.join([a.name for a in reversed(self.ancestors())] + [self.name])

    def subtree_ids(self):
        ids, todo = [], [self]
        while todo:
            c = todo.pop()
            ids.append(c.id)
            todo.extend(c.children)
        return ids


class PriceItem(TimestampMixin, db.Model):
    """Price list (Master data): a product's price before GST and its GST rate. The GST amount and the
    total are worked out from these two every time, never stored, so they can't disagree."""
    __tablename__ = "price_item"
    id = db.Column(db.Integer, primary_key=True)
    product = db.Column(db.String(160), unique=True, nullable=False)
    price = db.Column(Money, nullable=False)                       # before GST
    gst_rate = db.Column(db.Numeric(5, 2), nullable=False, default=Decimal("18"))
    remarks = db.Column(db.String(255))
    category_id = db.Column(db.Integer, db.ForeignKey("category.id", name="fk_price_item_category"), index=True)

    category = db.relationship("Category")

    __table_args__ = (
        CheckConstraint("price >= 0", name="ck_price_item_price"),
        CheckConstraint("gst_rate >= 0 AND gst_rate <= 100", name="ck_price_item_gst_rate"),
    )

    @property
    def gst_amount(self) -> Decimal:
        from decimal import ROUND_HALF_UP
        return (Decimal(self.price) * Decimal(self.gst_rate) / 100).quantize(Decimal("0.01"), ROUND_HALF_UP)

    @property
    def half_gst(self) -> Decimal:
        """CGST and SGST each (sale within the state); IGST is the full gst_amount."""
        from decimal import ROUND_HALF_UP
        return (self.gst_amount / 2).quantize(Decimal("0.01"), ROUND_HALF_UP)

    @property
    def total(self) -> Decimal:
        return Decimal(self.price) + self.gst_amount


class AuditLog(db.Model):
    __tablename__ = "audit_log"
    id = db.Column(db.Integer, primary_key=True)
    table_name = db.Column(db.String(40), nullable=False)
    record_id = db.Column(db.Integer, nullable=False)
    action = db.Column(db.String(40), nullable=False)
    remarks = db.Column(db.String(1000))
    old_json = db.Column(db.JSON)
    new_json = db.Column(db.JSON)
    user_id = db.Column(db.Integer, db.ForeignKey("app_user.id"))
    at = db.Column(db.DateTime, default=utcnow, nullable=False)

    user = db.relationship("User")

    __table_args__ = (Index("ix_audit_record", "table_name", "record_id"),)
