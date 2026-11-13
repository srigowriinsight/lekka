"""party on recurring templates

A recurring template can carry a party; every date it creates gets that party.

Revision ID: f7a2c8d1e605
Revises: e3f1b6a9c204
Create Date: 2026-11-12 12:20:00
"""
from alembic import op
import sqlalchemy as sa


revision = "f7a2c8d1e605"
down_revision = "e3f1b6a9c204"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("recurring_template") as t:
        t.add_column(sa.Column("counterparty_id", sa.Integer(), nullable=True))
        t.create_foreign_key("fk_template_counterparty", "counterparty", ["counterparty_id"], ["id"])


def downgrade():
    with op.batch_alter_table("recurring_template") as t:
        t.drop_constraint("fk_template_counterparty", type_="foreignkey")
        t.drop_column("counterparty_id")
