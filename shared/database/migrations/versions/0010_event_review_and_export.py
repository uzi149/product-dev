"""Event review and export (step 5.3).

Revision ID: 0010
Revises: 0009
"""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

EXPORT_STATUSES = "'none', 'running', 'done', 'failed'"


def upgrade() -> None:
    op.add_column("events", sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("events", sa.Column("note", sa.Text(), nullable=True))
    op.add_column(
        "events",
        sa.Column("export_status", sa.String(20), server_default="none", nullable=False),
    )
    op.create_check_constraint(
        "ck_events_export_status", "events", f"export_status IN ({EXPORT_STATUSES})"
    )
    op.add_column("events", sa.Column("export_uri", sa.Text(), nullable=True))
    op.add_column("events", sa.Column("export_error", sa.Text(), nullable=True))
    op.add_column("events", sa.Column("exported_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_events_alerted_at", "events", ["alerted_at"])


def downgrade() -> None:
    op.drop_index("ix_events_alerted_at", table_name="events")
    for name in ("exported_at", "export_error", "export_uri"):
        op.drop_column("events", name)
    op.drop_constraint("ck_events_export_status", "events", type_="check")
    for name in ("export_status", "note", "acknowledged_at"):
        op.drop_column("events", name)
