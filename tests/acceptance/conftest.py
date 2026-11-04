"""The acceptance runs' own scratch database and bucket, so development data is never touched."""

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from psycopg import sql
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from shared.config import Settings
from shared.storage import StorageClient
from tests.conftest import ROOT, _admin_connect

ACCEPTANCE_DB = "sentinel_acceptance"
ACCEPTANCE_BUCKET = "sentinel-acceptance"
# Folders of ground-truth YAML files; ACCEPTANCE_DIRS (separated by os.pathsep) overrides them.
DEFAULT_DIRS = ("test-data/acceptance/virat", "test-data/camera-01", "test-data/camera-02")


def acceptance_dirs() -> list[Path]:
    text = os.environ.get("ACCEPTANCE_DIRS")
    names = text.split(os.pathsep) if text else DEFAULT_DIRS
    return [ROOT / name for name in names if (ROOT / name).is_dir()]


def keep() -> bool:
    """ACCEPTANCE_KEEP=1: leave the database and bucket for a look (emptied at the next run)."""
    return os.environ.get("ACCEPTANCE_KEEP") == "1"


@pytest.fixture(scope="module")
def acceptance_settings() -> Settings:
    """The .env settings (VLM key, models) with the acceptance database and bucket."""
    return Settings().model_copy(
        update={"postgres_db": ACCEPTANCE_DB, "minio_bucket": ACCEPTANCE_BUCKET}
    )


@pytest.fixture(scope="module")
def acceptance_db(acceptance_settings):
    name = sql.Identifier(ACCEPTANCE_DB)
    with _admin_connect(acceptance_settings) as conn:
        conn.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(name))
        conn.execute(sql.SQL("CREATE DATABASE {}").format(name))
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "shared" / "database" / "migrations"))
    config.attributes["url"] = acceptance_settings.database_url
    command.upgrade(config, "head")
    engine = create_engine(acceptance_settings.database_url)
    yield sessionmaker(bind=engine, class_=Session, expire_on_commit=False)
    engine.dispose()
    if keep():
        return
    with _admin_connect(acceptance_settings) as conn:
        conn.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(name))


@pytest.fixture(scope="module")
def acceptance_storage(acceptance_settings):
    storage = StorageClient(acceptance_settings)
    storage.ensure_bucket()
    storage.delete_prefix("")
    yield storage
    if not keep():
        storage.delete_prefix("")
