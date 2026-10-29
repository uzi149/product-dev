"""Query progress on query_sessions, and query_evidence: one row per merged result (step 4.4).

Revision ID: 0006
Revises: 0005
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

QUERY_STATUSES = "'pending', 'running', 'done', 'failed'"
EVIDENCE_STATUSES = "'possible', 'verified', 'rejected', 'unverifiable'"


def upgrade() -> None:
    op.add_column(
        "query_sessions",
        sa.Column("status", sa.String(20), server_default="pending", nullable=False),
    )
    op.create_check_constraint(
        "ck_query_sessions_status", "query_sessions", f"status IN ({QUERY_STATUSES})"
    )
    for name, kind in (
        ("stage", sa.String(20)),
        ("search_text", sa.Text()),
        ("understood_json", postgresql.JSONB()),
        ("timezone", sa.String(64)),
        ("verification", sa.String(10)),
        ("error", sa.Text()),
        ("finished_at", sa.DateTime(timezone=True)),
    ):
        op.add_column("query_sessions", sa.Column(name, kind, nullable=True))

    op.create_table(
        "query_evidence",
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("query_session_id", sa.UUID(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("camera_id", sa.UUID(), nullable=False),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("status", sa.String(20), server_default="possible", nullable=False),
        sa.Column("verification_confidence", sa.Float(), nullable=True),
        sa.Column("verification_reason", sa.Text(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("keyframe_id", sa.UUID(), nullable=True),
        sa.Column("manifest_json", postgresql.JSONB(), nullable=False),
        sa.CheckConstraint(f"status IN ({EVIDENCE_STATUSES})", name="ck_query_evidence_status"),
        sa.ForeignKeyConstraint(
            ["query_session_id"],
            ["query_sessions.id"],
            name="fk_query_evidence_query_session_id_query_sessions",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["camera_id"],
            ["cameras.id"],
            name="fk_query_evidence_camera_id_cameras",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["keyframe_id"],
            ["keyframes.id"],
            name="fk_query_evidence_keyframe_id_keyframes",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_query_evidence"),
    )
    op.create_index(
        "ix_query_evidence_query_session_id_rank", "query_evidence", ["query_session_id", "rank"]
    )


def downgrade() -> None:
    op.drop_index("ix_query_evidence_query_session_id_rank", table_name="query_evidence")
    op.drop_table("query_evidence")
    for name in (
        "finished_at",
        "error",
        "verification",
        "timezone",
        "understood_json",
        "search_text",
        "stage",
    ):
        op.drop_column("query_sessions", name)
    op.drop_constraint("ck_query_sessions_status", "query_sessions", type_="check")
    op.drop_column("query_sessions", "status")
