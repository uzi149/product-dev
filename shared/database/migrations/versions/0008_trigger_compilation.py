"""Trigger compilation status (step 5.1, ADR 0032).

Revision ID: 0008
Revises: 0007
"""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

COMPILE_STATUSES = "'pending', 'done', 'failed'"


def upgrade() -> None:
    op.add_column(
        "triggers",
        sa.Column("compile_status", sa.String(20), server_default="pending", nullable=False),
    )
    op.create_check_constraint(
        "ck_triggers_compile_status", "triggers", f"compile_status IN ({COMPILE_STATUSES})"
    )
    op.add_column("triggers", sa.Column("compile_error", sa.Text(), nullable=True))
    op.add_column(
        "triggers",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("triggers", "updated_at")
    op.drop_column("triggers", "compile_error")
    op.drop_constraint("ck_triggers_compile_status", "triggers", type_="check")
    op.drop_column("triggers", "compile_status")
