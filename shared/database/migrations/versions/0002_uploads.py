"""Uploads table: status and history of background file ingestion (ADR 0013).

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "uploads",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("camera_id", sa.UUID(), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("segments_created", sa.Integer(), nullable=True),
        sa.Column("static_periods_created", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'done', 'failed')", name=op.f("ck_uploads_status")
        ),
        sa.ForeignKeyConstraint(
            ["camera_id"],
            ["cameras.id"],
            name=op.f("fk_uploads_camera_id_cameras"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_uploads")),
    )
    op.create_index(
        "ix_uploads_camera_id_created_at", "uploads", ["camera_id", "created_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_uploads_camera_id_created_at", table_name="uploads")
    op.drop_table("uploads")
