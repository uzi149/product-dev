"""Event details, the held state and updates (step 5.2, ADR 0033).

Revision ID: 0009
Revises: 0008
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("events", sa.Column("details_json", postgresql.JSONB(), nullable=True))
    op.add_column("events", sa.Column("alerted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "events",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("events", "updated_at")
    op.drop_column("events", "alerted_at")
    op.drop_column("events", "details_json")
