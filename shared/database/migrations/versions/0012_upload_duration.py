"""The length of each uploaded file, to refuse overlapping uploads (step 5.6, ADR 0037).

Revision ID: 0012
Revises: 0011
"""

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("uploads", sa.Column("duration_ms", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column("uploads", "duration_ms")
