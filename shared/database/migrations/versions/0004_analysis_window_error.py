"""analysis_windows.error: why a window's analysis failed (step 3.3).

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("analysis_windows", sa.Column("error", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("analysis_windows", "error")
