"""video_segments.status_changed_at: tells a stuck analysis from a running one (step 3.5).

Existing rows get their created_at.

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "video_segments",
        sa.Column("status_changed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute("UPDATE video_segments SET status_changed_at = created_at")
    op.alter_column(
        "video_segments",
        "status_changed_at",
        nullable=False,
        server_default=sa.func.now(),
    )


def downgrade() -> None:
    op.drop_column("video_segments", "status_changed_at")
