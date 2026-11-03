"""head kind (revenue / expense / both) and price-list categories

Heads get a kind: IN (revenue heads), OUT (expense heads) or BOTH. Existing heads are set from how they are
used: only revenue items or templates -> IN, only expense -> OUT, both or none -> BOTH; an unused sub-head
under a revenue-only or expense-only head takes its parent's kind. Products in the price list get an optional
category, and categories form a tree.

Revision ID: e3f1b6a9c204
Revises: c5e2a9d41f07
Create Date: 2026-11-03 14:10:00
"""
from alembic import op
import sqlalchemy as sa


revision = "e3f1b6a9c204"
down_revision = "c5e2a9d41f07"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("head") as t:
        t.add_column(sa.Column("kind", sa.String(length=4), nullable=False, server_default="BOTH"))

    conn = op.get_bind()
    used = {"IN": set(), "OUT": set()}
    for hid, d in conn.execute(sa.text(
            "SELECT DISTINCT head_id, direction FROM cashflow_entry WHERE lifecycle <> 'DELETED'")):
        used.setdefault(d, set()).add(hid)
    for hid, d in conn.execute(sa.text("SELECT DISTINCT head_id, direction FROM recurring_template")):
        used.setdefault(d, set()).add(hid)
    heads = {hid: pid for hid, pid in conn.execute(sa.text("SELECT id, parent_id FROM head"))}
    kind = {}
    for hid in heads:
        i, o = hid in used["IN"], hid in used["OUT"]
        kind[hid] = "IN" if i and not o else ("OUT" if o and not i else "BOTH")

    def unused(h):
        return h not in used["IN"] and h not in used["OUT"]

    changed = True
    while changed:                       # unused sub-heads follow a one-sided parent, down the tree
        changed = False
        for hid, pid in heads.items():
            if pid and unused(hid) and kind[hid] == "BOTH" and kind.get(pid) in ("IN", "OUT"):
                kind[hid] = kind[pid]
                changed = True
    for hid, k in kind.items():
        if k != "BOTH":
            conn.execute(sa.text("UPDATE head SET kind = :k WHERE id = :i"), {"k": k, "i": hid})

    op.create_table(
        "category",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("parent_id", sa.Integer(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.ForeignKeyConstraint(["parent_id"], ["category.id"], name="fk_category_parent"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_category_name"),
    )
    op.create_index("ix_category_parent_id", "category", ["parent_id"])
    with op.batch_alter_table("price_item") as t:
        t.add_column(sa.Column("category_id", sa.Integer(), nullable=True))
        t.create_foreign_key("fk_price_item_category", "category", ["category_id"], ["id"])
        t.create_index("ix_price_item_category_id", ["category_id"])


def downgrade():
    with op.batch_alter_table("price_item") as t:
        t.drop_constraint("fk_price_item_category", type_="foreignkey")
        t.drop_index("ix_price_item_category_id")
        t.drop_column("category_id")
    op.drop_table("category")             # its index and self-link go with it (MariaDB won't drop them first)
    with op.batch_alter_table("head") as t:
        t.drop_column("kind")
