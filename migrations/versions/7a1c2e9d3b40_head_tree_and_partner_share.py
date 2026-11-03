"""head tree and partner share

Heads get a parent (sub-heads, like the OPEX sheet). An expense can point to the revenue it is a
partner's share of.

Revision ID: 7a1c2e9d3b40
Revises: 4f0cb1bfed44
Create Date: 2026-10-30 10:30:00
"""
from alembic import op
import sqlalchemy as sa


revision = "7a1c2e9d3b40"
down_revision = "4f0cb1bfed44"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("head") as t:
        t.add_column(sa.Column("parent_id", sa.Integer(), nullable=True))
        t.create_foreign_key("fk_head_parent", "head", ["parent_id"], ["id"])
        t.create_index("ix_head_parent_id", ["parent_id"])
    with op.batch_alter_table("cashflow_entry") as t:
        t.add_column(sa.Column("share_of_id", sa.Integer(), nullable=True))
        t.create_foreign_key("fk_entry_share_of", "cashflow_entry", ["share_of_id"], ["id"])


def downgrade():
    with op.batch_alter_table("cashflow_entry") as t:
        t.drop_constraint("fk_entry_share_of", type_="foreignkey")
        t.drop_column("share_of_id")
    with op.batch_alter_table("head") as t:
        t.drop_constraint("fk_head_parent", type_="foreignkey")
        t.drop_index("ix_head_parent_id")
        t.drop_column("parent_id")
