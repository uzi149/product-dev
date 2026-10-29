from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text

from shared.database.models import Base

EXPECTED_TABLES = {
    "cameras",
    "video_segments",
    "static_periods",
    "analysis_windows",
    "keyframes",
    "text_embeddings",
    "triggers",
    "trigger_cameras",
    "events",
    "event_evidence",
    "query_sessions",
    "query_results",
    "uploads",
    "index_settings",
}


def _tables(url: str) -> set[str]:
    engine = create_engine(url)
    try:
        return set(inspect(engine).get_table_names()) - {"alembic_version"}
    finally:
        engine.dispose()


def test_upgrade_creates_all_tables(migrated_db):
    assert _tables(migrated_db) == EXPECTED_TABLES


def test_downgrade_removes_all_tables_and_upgrade_works_again(migrated_db, alembic_config):
    command.downgrade(alembic_config, "base")
    assert _tables(migrated_db) == set()
    command.upgrade(alembic_config, "head")
    assert _tables(migrated_db) == EXPECTED_TABLES


def test_models_and_migration_are_in_sync(engine):
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == []


def test_indexes_from_scope_5_3_exist(engine):
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT indexname, indexdef FROM pg_indexes")).all()
    defs = {name: definition for name, definition in rows}

    for name in ("ix_keyframes_visual_embedding_hnsw", "ix_text_embeddings_embedding_hnsw"):
        assert "USING hnsw" in defs[name]
        assert "vector_cosine_ops" in defs[name]
    assert "USING gin" in defs["ix_analysis_windows_vlm_description_fts"]

    for expected in (
        "ix_video_segments_camera_id_start_time",
        "ix_video_segments_end_time",
        "ix_video_segments_status",
        "ix_analysis_windows_camera_id_start_time",
        "ix_analysis_windows_end_time",
        "ix_analysis_windows_processing_status",
        "ix_events_trigger_id",
        "ix_events_camera_id_start_time",
        "ix_trigger_cameras_camera_id",
        "ix_uploads_camera_id_created_at",
    ):
        assert expected in defs, expected


def test_uploads_migration_downgrades_on_its_own(migrated_db, alembic_config):
    command.downgrade(alembic_config, "0001")
    assert _tables(migrated_db) == EXPECTED_TABLES - {"uploads", "index_settings"}
    command.upgrade(alembic_config, "head")
    assert "uploads" in _tables(migrated_db)


def test_index_settings_migration_downgrades_on_its_own(migrated_db, alembic_config):
    command.downgrade(alembic_config, "0002")
    assert _tables(migrated_db) == EXPECTED_TABLES - {"index_settings"}
    command.upgrade(alembic_config, "head")
    assert "index_settings" in _tables(migrated_db)


def test_segment_status_changed_at_starts_at_created_at(migrated_db, alembic_config):
    command.downgrade(alembic_config, "0004")
    engine = create_engine(migrated_db)
    try:
        with engine.begin() as conn:
            camera = conn.execute(
                text(
                    "INSERT INTO cameras (display_name, source_type) VALUES ('m', 'file') "
                    "RETURNING id"
                )
            ).scalar_one()
            conn.execute(
                text(
                    "INSERT INTO video_segments (camera_id, start_time, end_time, duration_ms, "
                    "storage_uri, created_at) VALUES (:camera, now(), now(), 1000, 's3://x', "
                    "'2026-01-02T03:04:05Z')"
                ),
                {"camera": camera},
            )
        command.upgrade(alembic_config, "head")
        with engine.connect() as conn:
            same = conn.execute(
                text("SELECT status_changed_at = created_at FROM video_segments")
            ).scalar_one()
        assert same
        command.downgrade(alembic_config, "0004")
        with engine.connect() as conn:
            columns = {c["name"] for c in inspect(conn).get_columns("video_segments")}
        assert "status_changed_at" not in columns
    finally:
        engine.dispose()
