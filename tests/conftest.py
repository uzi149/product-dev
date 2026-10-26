"""Shared fixtures. Database tests use a scratch database, never the development one."""

import socket
from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from psycopg import sql
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from shared.config import Settings
from shared.storage import StorageClient

TEST_DB = "sentinel_test"
ROOT = Path(__file__).resolve().parents[1]


def pytest_collection_modifyitems(items):
    for item in items:
        node = item.nodeid.replace("\\", "/")
        if node.startswith(
            (
                "tests/database",
                "tests/api",
                "tests/ingestion/test_file_ingest",
                "tests/ingestion/test_tasks",
                "tests/ingestion/test_live_pipeline",
                "tests/ingestion/test_live_rtsp",
                "tests/ingestion/test_outbox",
                "tests/ingestion/test_supervisor",
            )
        ):
            item.add_marker(pytest.mark.integration)


def _admin_connect(settings: Settings) -> psycopg.Connection:
    return psycopg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        user=settings.postgres_user,
        password=settings.postgres_password,
        dbname="postgres",
        autocommit=True,
        connect_timeout=3,
    )


@pytest.fixture(scope="session")
def settings() -> Settings:
    return Settings()


@pytest.fixture(scope="session")
def test_db_url(settings) -> str:
    try:
        _admin_connect(settings).close()
    except psycopg.OperationalError:
        pytest.skip("PostgreSQL is not reachable (run `docker compose up -d`)")
    return settings.model_copy(update={"postgres_db": TEST_DB}).database_url


@pytest.fixture
def empty_db(settings, test_db_url):
    """A freshly created, empty scratch database."""
    name = sql.Identifier(TEST_DB)
    with _admin_connect(settings) as conn:
        conn.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(name))
        conn.execute(sql.SQL("CREATE DATABASE {}").format(name))
    yield test_db_url
    with _admin_connect(settings) as conn:
        conn.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(name))


@pytest.fixture
def alembic_config(empty_db) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "shared" / "database" / "migrations"))
    config.attributes["url"] = empty_db
    return config


@pytest.fixture
def migrated_db(empty_db, alembic_config):
    command.upgrade(alembic_config, "head")
    return empty_db


@pytest.fixture
def engine(migrated_db):
    engine = create_engine(migrated_db)
    yield engine
    engine.dispose()


@pytest.fixture
def session(engine):
    with Session(engine) as session:
        yield session


@pytest.fixture
def storage():
    """The real MinIO from Docker Compose (the development bucket)."""
    settings = Settings()
    try:
        socket.create_connection((settings.minio_host, settings.minio_port), timeout=2).close()
    except OSError:
        pytest.skip("MinIO is not reachable (run `docker compose up -d`)")
    client = StorageClient(settings)
    client.ensure_bucket()
    return client
