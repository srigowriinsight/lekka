"""category on items

An item can carry a product category (set when a price-list product is picked on revenue, or chosen by hand),
so reports can filter and show it. Parts of a split item get their top item's category.

Revision ID: a1d4e7b2c930
Revises: f7a2c8d1e605
Create Date: 2026-11-12 14:30:00
"""
from alembic import op
import sqlalchemy as sa


revision = "a1d4e7b2c930"
down_revision = "f7a2c8d1e605"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("cashflow_entry") as t:
        t.add_column(sa.Column("category_id", sa.Integer(), nullable=True))
        t.create_foreign_key("fk_entry_category", "category", ["category_id"], ["id"])
        t.create_index("ix_cashflow_entry_category_id", ["category_id"])


def downgrade():
    with op.batch_alter_table("cashflow_entry") as t:
        t.drop_constraint("fk_entry_category", type_="foreignkey")
        t.drop_index("ix_cashflow_entry_category_id")
        t.drop_column("category_id")
