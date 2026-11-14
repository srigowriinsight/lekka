"""product types for the price list; categories become global

The price list gets its own list, Types (a tree, like categories). Categories stay for any expense or revenue
item (optional). Existing data:
  * every category branch that holds a product (a top-level category with a product anywhere under it) is copied
    to Types, and its products point to the matching type;
  * revenue items in those branches (their category came from the product picked) get the type instead and lose
    the category;
  * those categories are then removed from Categories, except ones still used by other items (and the categories
    above them).

Revision ID: b8c3f5a2d917
Revises: a1d4e7b2c930
Create Date: 2026-11-14 17:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = "b8c3f5a2d917"
down_revision = "a1d4e7b2c930"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "product_type",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("parent_id", sa.Integer(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.ForeignKeyConstraint(["parent_id"], ["product_type.id"], name="fk_product_type_parent"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_product_type_name"),
    )
    op.create_index("ix_product_type_parent_id", "product_type", ["parent_id"])
    with op.batch_alter_table("price_item") as t:
        t.add_column(sa.Column("product_type_id", sa.Integer(), nullable=True))
        t.create_foreign_key("fk_price_item_type", "product_type", ["product_type_id"], ["id"])
        t.create_index("ix_price_item_product_type_id", ["product_type_id"])
    with op.batch_alter_table("cashflow_entry") as t:
        t.add_column(sa.Column("product_type_id", sa.Integer(), nullable=True))
        t.create_foreign_key("fk_entry_product_type", "product_type", ["product_type_id"], ["id"])
        t.create_index("ix_cashflow_entry_product_type_id", ["product_type_id"])

    conn = op.get_bind()
    cats = {cid: (name, pid, order, active) for cid, name, pid, order, active in conn.execute(sa.text(
        "SELECT id, name, parent_id, sort_order, is_active FROM category"))}

    def top(cid):
        while cats[cid][1] is not None:
            cid = cats[cid][1]
        return cid

    used_by_products = {r[0] for r in conn.execute(sa.text(
        "SELECT DISTINCT category_id FROM price_item WHERE category_id IS NOT NULL"))}
    product_tops = {top(c) for c in used_by_products if c in cats}
    branch = {c for c in cats if top(c) in product_tops}           # whole branches that hold products

    def depth(cid):
        n = 0
        while cats[cid][1] is not None:
            cid, n = cats[cid][1], n + 1
        return n

    new_id = {}
    pt = sa.table("product_type", sa.column("id", sa.Integer), sa.column("name", sa.String),
                  sa.column("parent_id", sa.Integer), sa.column("sort_order", sa.Integer),
                  sa.column("is_active", sa.Boolean))
    for cid in sorted(branch, key=lambda c: (depth(c), c)):          # parents before their sub-types
        name, pid, order, active = cats[cid]
        conn.execute(pt.insert().values(name=name, parent_id=new_id.get(pid), sort_order=order or 0,
                                        is_active=bool(active)))
        new_id[cid] = conn.execute(sa.text("SELECT id FROM product_type WHERE name = :n"), {"n": name}).scalar()

    for cid, tid in new_id.items():
        conn.execute(sa.text("UPDATE price_item SET product_type_id = :t WHERE category_id = :c"),
                     {"t": tid, "c": cid})
        conn.execute(sa.text("UPDATE cashflow_entry SET product_type_id = :t, category_id = NULL "
                             "WHERE category_id = :c AND direction = 'IN'"), {"t": tid, "c": cid})

    with op.batch_alter_table("price_item") as t:      # products now have types; then the categories can go
        t.drop_constraint("fk_price_item_category", type_="foreignkey")
        t.drop_index("ix_price_item_category_id")
        t.drop_column("category_id")

    still_used = {r[0] for r in conn.execute(sa.text(
        "SELECT DISTINCT category_id FROM cashflow_entry WHERE category_id IS NOT NULL"))}
    keep = set()
    for cid in still_used:
        while cid is not None and cid in cats:
            keep.add(cid)
            cid = cats[cid][1]
    for cid in sorted(branch - keep, key=lambda c: -depth(c)):       # sub-categories first
        conn.execute(sa.text("DELETE FROM category WHERE id = :c"), {"c": cid})


def downgrade():
    """Back to one list: types are copied into Categories (by name) and products point to them again."""
    with op.batch_alter_table("price_item") as t:
        t.add_column(sa.Column("category_id", sa.Integer(), nullable=True))
        t.create_foreign_key("fk_price_item_category", "category", ["category_id"], ["id"])
        t.create_index("ix_price_item_category_id", ["category_id"])
    conn = op.get_bind()
    types = list(conn.execute(sa.text("SELECT id, name, parent_id, sort_order, is_active FROM product_type")))
    by_id = {r[0]: r for r in types}

    def depth(tid):
        n = 0
        while by_id[tid][2] is not None:
            tid, n = by_id[tid][2], n + 1
        return n

    cat_of = {}
    for tid, name, pid, order, active in sorted(types, key=lambda r: (depth(r[0]), r[0])):
        cid = conn.execute(sa.text("SELECT id FROM category WHERE name = :n"), {"n": name}).scalar()
        if cid is None:
            conn.execute(sa.text("INSERT INTO category (name, parent_id, sort_order, is_active) "
                                 "VALUES (:n, :p, :o, :a)"),
                         {"n": name, "p": cat_of.get(pid), "o": order or 0, "a": bool(active)})
            cid = conn.execute(sa.text("SELECT id FROM category WHERE name = :n"), {"n": name}).scalar()
        cat_of[tid] = cid
    for tid, cid in cat_of.items():
        conn.execute(sa.text("UPDATE price_item SET category_id = :c WHERE product_type_id = :t"), {"c": cid, "t": tid})
        conn.execute(sa.text("UPDATE cashflow_entry SET category_id = :c WHERE product_type_id = :t "
                             "AND category_id IS NULL"), {"c": cid, "t": tid})
    with op.batch_alter_table("cashflow_entry") as t:
        t.drop_constraint("fk_entry_product_type", type_="foreignkey")
        t.drop_index("ix_cashflow_entry_product_type_id")
        t.drop_column("product_type_id")
    with op.batch_alter_table("price_item") as t:
        t.drop_constraint("fk_price_item_type", type_="foreignkey")
        t.drop_index("ix_price_item_product_type_id")
        t.drop_column("product_type_id")
    op.drop_table("product_type")
