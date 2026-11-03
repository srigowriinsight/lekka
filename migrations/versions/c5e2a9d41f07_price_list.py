"""price list

A price list on the Master data page: product, price before GST and GST rate. The GST amount and the
total are calculated, not stored.

Revision ID: c5e2a9d41f07
Revises: 7a1c2e9d3b40
Create Date: 2026-11-02 12:50:00
"""
from alembic import op
import sqlalchemy as sa


revision = "c5e2a9d41f07"
down_revision = "7a1c2e9d3b40"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "price_item",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("product", sa.String(length=160), nullable=False),
        sa.Column("price", sa.Numeric(precision=15, scale=2), nullable=False),
        sa.Column("gst_rate", sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column("remarks", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        sa.CheckConstraint("price >= 0", name="ck_price_item_price"),
        sa.CheckConstraint("gst_rate >= 0 AND gst_rate <= 100", name="ck_price_item_gst_rate"),
        sa.ForeignKeyConstraint(["created_by"], ["app_user.id"], name="fk_price_item_created_by"),
        sa.ForeignKeyConstraint(["updated_by"], ["app_user.id"], name="fk_price_item_updated_by"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("product", name="uq_price_item_product"),
    )


def downgrade():
    op.drop_table("price_item")
