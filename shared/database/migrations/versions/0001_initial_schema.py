"""Initial schema: all tables from Technical Scope §5.2 and the §5.3 indexes.

Revision ID: 0001
Revises:
"""

import pgvector.sqlalchemy
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "cameras",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("source_type", sa.String(length=20), nullable=False),
        sa.Column("source_uri_encrypted", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("analysis_fps", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("retention_days", sa.Integer(), server_default=sa.text("14"), nullable=False),
        sa.Column(
            "detection_config",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("source_type IN ('rtsp', 'file')", name=op.f("ck_cameras_source_type")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cameras")),
    )
    op.create_table(
        "query_sessions",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("query_text", sa.Text(), nullable=False),
        sa.Column("camera_filter", postgresql.ARRAY(sa.UUID()), nullable=True),
        sa.Column("start_filter", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_filter", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_query_sessions")),
    )
    op.create_table(
        "triggers",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("natural_language_rule", sa.Text(), nullable=False),
        sa.Column("compiled_rule_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("minimum_confidence", sa.Float(), server_default=sa.text("0.8"), nullable=False),
        sa.Column("cooldown_seconds", sa.Integer(), server_default=sa.text("120"), nullable=False),
        sa.Column("pre_roll_seconds", sa.Integer(), server_default=sa.text("10"), nullable=False),
        sa.Column("post_roll_seconds", sa.Integer(), server_default=sa.text("20"), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_triggers")),
    )
    op.create_table(
        "events",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("trigger_id", sa.UUID(), nullable=False),
        sa.Column("camera_id", sa.UUID(), nullable=False),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), server_default="new", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('new', 'confirmed', 'dismissed')", name=op.f("ck_events_status")
        ),
        sa.ForeignKeyConstraint(
            ["camera_id"], ["cameras.id"], name=op.f("fk_events_camera_id_cameras")
        ),
        sa.ForeignKeyConstraint(
            ["trigger_id"], ["triggers.id"], name=op.f("fk_events_trigger_id_triggers")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_events")),
    )
    op.create_index(
        "ix_events_camera_id_start_time", "events", ["camera_id", "start_time"], unique=False
    )
    op.create_index("ix_events_end_time", "events", ["end_time"], unique=False)
    op.create_index("ix_events_status", "events", ["status"], unique=False)
    op.create_index("ix_events_trigger_id", "events", ["trigger_id"], unique=False)
    op.create_table(
        "static_periods",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("camera_id", sa.UUID(), nullable=False),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reference_frame_uri", sa.Text(), nullable=True),
        sa.Column("health_status", sa.String(length=20), server_default="ok", nullable=False),
        sa.CheckConstraint(
            "health_status IN ('ok', 'offline')", name=op.f("ck_static_periods_health_status")
        ),
        sa.ForeignKeyConstraint(
            ["camera_id"],
            ["cameras.id"],
            name=op.f("fk_static_periods_camera_id_cameras"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_static_periods")),
    )
    op.create_index(
        "ix_static_periods_camera_id_start_time",
        "static_periods",
        ["camera_id", "start_time"],
        unique=False,
    )
    op.create_table(
        "trigger_cameras",
        sa.Column("trigger_id", sa.UUID(), nullable=False),
        sa.Column("camera_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["camera_id"],
            ["cameras.id"],
            name=op.f("fk_trigger_cameras_camera_id_cameras"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["trigger_id"],
            ["triggers.id"],
            name=op.f("fk_trigger_cameras_trigger_id_triggers"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("trigger_id", "camera_id", name=op.f("pk_trigger_cameras")),
    )
    op.create_index("ix_trigger_cameras_camera_id", "trigger_cameras", ["camera_id"], unique=False)
    op.create_table(
        "video_segments",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("camera_id", sa.UUID(), nullable=False),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_ms", sa.BigInteger(), nullable=False),
        sa.Column("storage_uri", sa.Text(), nullable=False),
        sa.Column("activity_score", sa.Float(), nullable=True),
        sa.Column("status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("codec", sa.String(length=50), nullable=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'done', 'failed')",
            name=op.f("ck_video_segments_status"),
        ),
        sa.ForeignKeyConstraint(
            ["camera_id"],
            ["cameras.id"],
            name=op.f("fk_video_segments_camera_id_cameras"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_video_segments")),
    )
    op.create_index(
        "ix_video_segments_camera_id_start_time",
        "video_segments",
        ["camera_id", "start_time"],
        unique=False,
    )
    op.create_index("ix_video_segments_end_time", "video_segments", ["end_time"], unique=False)
    op.create_index("ix_video_segments_status", "video_segments", ["status"], unique=False)
    op.create_table(
        "analysis_windows",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("segment_id", sa.UUID(), nullable=False),
        sa.Column("camera_id", sa.UUID(), nullable=False),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("vlm_description", sa.Text(), nullable=True),
        sa.Column(
            "structured_observation_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("vlm_model", sa.String(length=200), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column(
            "processing_status", sa.String(length=20), server_default="pending", nullable=False
        ),
        sa.CheckConstraint(
            "processing_status IN ('pending', 'processing', 'done', 'failed')",
            name=op.f("ck_analysis_windows_processing_status"),
        ),
        sa.ForeignKeyConstraint(
            ["camera_id"],
            ["cameras.id"],
            name=op.f("fk_analysis_windows_camera_id_cameras"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["segment_id"],
            ["video_segments.id"],
            name=op.f("fk_analysis_windows_segment_id_video_segments"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_analysis_windows")),
    )
    op.create_index(
        "ix_analysis_windows_camera_id_start_time",
        "analysis_windows",
        ["camera_id", "start_time"],
        unique=False,
    )
    op.create_index("ix_analysis_windows_end_time", "analysis_windows", ["end_time"], unique=False)
    op.create_index(
        "ix_analysis_windows_processing_status",
        "analysis_windows",
        ["processing_status"],
        unique=False,
    )
    op.create_index(
        "ix_analysis_windows_segment_id", "analysis_windows", ["segment_id"], unique=False
    )
    op.create_index(
        "ix_analysis_windows_vlm_description_fts",
        "analysis_windows",
        [sa.literal_column("to_tsvector('english', vlm_description)")],
        unique=False,
        postgresql_using="gin",
    )
    op.create_table(
        "event_evidence",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("event_id", sa.UUID(), nullable=False),
        sa.Column("segment_id", sa.UUID(), nullable=False),
        sa.Column("start_offset_ms", sa.BigInteger(), nullable=False),
        sa.Column("end_offset_ms", sa.BigInteger(), nullable=False),
        sa.Column("evidence_reason", sa.Text(), nullable=True),
        sa.Column("sequence_order", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["events.id"],
            name=op.f("fk_event_evidence_event_id_events"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["segment_id"],
            ["video_segments.id"],
            name=op.f("fk_event_evidence_segment_id_video_segments"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_event_evidence")),
    )
    op.create_index("ix_event_evidence_event_id", "event_evidence", ["event_id"], unique=False)
    op.create_index("ix_event_evidence_segment_id", "event_evidence", ["segment_id"], unique=False)
    op.create_table(
        "keyframes",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("analysis_window_id", sa.UUID(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("storage_uri", sa.Text(), nullable=False),
        sa.Column("visual_embedding", pgvector.sqlalchemy.vector.VECTOR(dim=768), nullable=True),
        sa.ForeignKeyConstraint(
            ["analysis_window_id"],
            ["analysis_windows.id"],
            name=op.f("fk_keyframes_analysis_window_id_analysis_windows"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_keyframes")),
    )
    op.create_index(
        "ix_keyframes_analysis_window_id", "keyframes", ["analysis_window_id"], unique=False
    )
    op.create_index(
        "ix_keyframes_visual_embedding_hnsw",
        "keyframes",
        ["visual_embedding"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_ops={"visual_embedding": "vector_cosine_ops"},
    )
    op.create_table(
        "query_results",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("query_session_id", sa.UUID(), nullable=False),
        sa.Column("analysis_window_id", sa.UUID(), nullable=False),
        sa.Column("vector_score", sa.Float(), nullable=True),
        sa.Column("rerank_score", sa.Float(), nullable=True),
        sa.Column("verification_score", sa.Float(), nullable=True),
        sa.Column("verified", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("sequence_group_id", sa.UUID(), nullable=True),
        sa.ForeignKeyConstraint(
            ["analysis_window_id"],
            ["analysis_windows.id"],
            name=op.f("fk_query_results_analysis_window_id_analysis_windows"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["query_session_id"],
            ["query_sessions.id"],
            name=op.f("fk_query_results_query_session_id_query_sessions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_query_results")),
    )
    op.create_index(
        "ix_query_results_analysis_window_id", "query_results", ["analysis_window_id"], unique=False
    )
    op.create_index(
        "ix_query_results_query_session_id", "query_results", ["query_session_id"], unique=False
    )
    op.create_table(
        "text_embeddings",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("analysis_window_id", sa.UUID(), nullable=False),
        sa.Column(
            "embedding_type", sa.String(length=30), server_default="description", nullable=False
        ),
        sa.Column("embedding", pgvector.sqlalchemy.vector.VECTOR(dim=768), nullable=False),
        sa.Column("source_text", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "embedding_type IN ('description')", name=op.f("ck_text_embeddings_embedding_type")
        ),
        sa.ForeignKeyConstraint(
            ["analysis_window_id"],
            ["analysis_windows.id"],
            name=op.f("fk_text_embeddings_analysis_window_id_analysis_windows"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_text_embeddings")),
    )
    op.create_index(
        "ix_text_embeddings_analysis_window_id",
        "text_embeddings",
        ["analysis_window_id"],
        unique=False,
    )
    op.create_index(
        "ix_text_embeddings_embedding_hnsw",
        "text_embeddings",
        ["embedding"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )


def downgrade() -> None:
    op.drop_index(
        "ix_text_embeddings_embedding_hnsw",
        table_name="text_embeddings",
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.drop_index("ix_text_embeddings_analysis_window_id", table_name="text_embeddings")
    op.drop_table("text_embeddings")
    op.drop_index("ix_query_results_query_session_id", table_name="query_results")
    op.drop_index("ix_query_results_analysis_window_id", table_name="query_results")
    op.drop_table("query_results")
    op.drop_index(
        "ix_keyframes_visual_embedding_hnsw",
        table_name="keyframes",
        postgresql_using="hnsw",
        postgresql_ops={"visual_embedding": "vector_cosine_ops"},
    )
    op.drop_index("ix_keyframes_analysis_window_id", table_name="keyframes")
    op.drop_table("keyframes")
    op.drop_index("ix_event_evidence_segment_id", table_name="event_evidence")
    op.drop_index("ix_event_evidence_event_id", table_name="event_evidence")
    op.drop_table("event_evidence")
    op.drop_index(
        "ix_analysis_windows_vlm_description_fts",
        table_name="analysis_windows",
        postgresql_using="gin",
    )
    op.drop_index("ix_analysis_windows_segment_id", table_name="analysis_windows")
    op.drop_index("ix_analysis_windows_processing_status", table_name="analysis_windows")
    op.drop_index("ix_analysis_windows_end_time", table_name="analysis_windows")
    op.drop_index("ix_analysis_windows_camera_id_start_time", table_name="analysis_windows")
    op.drop_table("analysis_windows")
    op.drop_index("ix_video_segments_status", table_name="video_segments")
    op.drop_index("ix_video_segments_end_time", table_name="video_segments")
    op.drop_index("ix_video_segments_camera_id_start_time", table_name="video_segments")
    op.drop_table("video_segments")
    op.drop_index("ix_trigger_cameras_camera_id", table_name="trigger_cameras")
    op.drop_table("trigger_cameras")
    op.drop_index("ix_static_periods_camera_id_start_time", table_name="static_periods")
    op.drop_table("static_periods")
    op.drop_index("ix_events_trigger_id", table_name="events")
    op.drop_index("ix_events_status", table_name="events")
    op.drop_index("ix_events_end_time", table_name="events")
    op.drop_index("ix_events_camera_id_start_time", table_name="events")
    op.drop_table("events")
    op.drop_table("triggers")
    op.drop_table("query_sessions")
    op.drop_table("cameras")
    # The vector extension is shared by the whole database, so it is left in place.
