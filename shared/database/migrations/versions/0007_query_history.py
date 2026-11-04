"""Query history (step 4.6): the request as sent, and an index for the newest-first list.

Revision ID: 0007
Revises: 0006
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("query_sessions", sa.Column("request_json", postgresql.JSONB(), nullable=True))
    op.create_index("ix_query_sessions_created_at", "query_sessions", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_query_sessions_created_at", table_name="query_sessions")
    op.drop_column("query_sessions", "request_json")
