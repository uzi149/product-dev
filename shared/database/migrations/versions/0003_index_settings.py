"""Index settings: the embedding model each search index was built with (ADR 0020).

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "index_settings",
        sa.Column("index_name", sa.String(length=20), nullable=False),
        sa.Column("model_name", sa.String(length=200), nullable=False),
        sa.Column("dimensions", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "index_name IN ('text', 'visual')", name=op.f("ck_index_settings_index_name")
        ),
        sa.PrimaryKeyConstraint("index_name", name=op.f("pk_index_settings")),
    )


def downgrade() -> None:
    op.drop_table("index_settings")
