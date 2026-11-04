"""Trigger tests on stored footage (step 5.4, ADR 0035).

Revision ID: 0011
Revises: 0010
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

SOURCES = "'video', 'last_hour', 'last_24h', 'range'"
STATUSES = "'pending', 'running', 'done', 'failed'"


def upgrade() -> None:
    op.create_table(
        "trigger_tests",
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("trigger_id", sa.UUID(), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("upload_id", sa.UUID(), nullable=True),
        sa.Column("camera_ids", postgresql.ARRAY(sa.UUID()), nullable=False),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(20), server_default="pending", nullable=False),
        sa.Column("stage", sa.String(20), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("rule_json", postgresql.JSONB(), nullable=False),
        sa.Column("results_json", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(f"source IN ({SOURCES})", name="ck_trigger_tests_source"),
        sa.CheckConstraint(f"status IN ({STATUSES})", name="ck_trigger_tests_status"),
        sa.ForeignKeyConstraint(
            ["trigger_id"],
            ["triggers.id"],
            name="fk_trigger_tests_trigger_id_triggers",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["upload_id"],
            ["uploads.id"],
            name="fk_trigger_tests_upload_id_uploads",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_trigger_tests"),
    )
    op.create_index(
        "ix_trigger_tests_trigger_id_created_at", "trigger_tests", ["trigger_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_trigger_tests_trigger_id_created_at", table_name="trigger_tests")
    op.drop_table("trigger_tests")
