"""Evidence marks: boxes around the people and objects of a clip (step 5.7, ADR 0038).

Revision ID: 0013
Revises: 0012
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

MARKS_STATUSES = "'pending', 'running', 'done', 'failed', 'skipped'"
EXPORT_STATUSES = "'none', 'running', 'done', 'failed'"


def upgrade() -> None:
    op.create_table(
        "evidence_marks",
        sa.Column(
            "id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("event_id", UUID(as_uuid=True), nullable=True),
        sa.Column("query_evidence_id", UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(20), server_default="pending", nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("prompt_version", sa.Integer(), nullable=True),
        sa.Column("evidence_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pick_status", sa.String(20), nullable=True),
        sa.Column("keyframes", sa.Integer(), nullable=True),
        sa.Column("tracks_json", JSONB(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(f"status IN ({MARKS_STATUSES})", name="ck_evidence_marks_status"),
        sa.CheckConstraint(
            "num_nonnulls(event_id, query_evidence_id) = 1", name="ck_evidence_marks_one_target"
        ),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["events.id"],
            ondelete="CASCADE",
            name="fk_evidence_marks_event_id_events",
        ),
        sa.ForeignKeyConstraint(
            ["query_evidence_id"],
            ["query_evidence.id"],
            ondelete="CASCADE",
            name="fk_evidence_marks_query_evidence_id_query_evidence",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_evidence_marks"),
    )
    op.create_index("ix_evidence_marks_event_id", "evidence_marks", ["event_id"], unique=True)
    op.create_index("ix_evidence_marks_query_evidence_id", "evidence_marks",
                    ["query_evidence_id"], unique=True)  # fmt: skip

    op.add_column(
        "events",
        sa.Column("marked_export_status", sa.String(20), server_default="none", nullable=False),
    )
    op.create_check_constraint(
        "ck_events_marked_export_status", "events", f"marked_export_status IN ({EXPORT_STATUSES})"
    )
    op.add_column("events", sa.Column("marked_export_uri", sa.Text(), nullable=True))
    op.add_column("events", sa.Column("marked_export_error", sa.Text(), nullable=True))
    op.add_column(
        "events", sa.Column("marked_exported_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    for name in ("marked_exported_at", "marked_export_error", "marked_export_uri"):
        op.drop_column("events", name)
    op.drop_constraint("ck_events_marked_export_status", "events", type_="check")
    op.drop_column("events", "marked_export_status")
    op.drop_index("ix_evidence_marks_query_evidence_id", table_name="evidence_marks")
    op.drop_index("ix_evidence_marks_event_id", table_name="evidence_marks")
    op.drop_table("evidence_marks")
