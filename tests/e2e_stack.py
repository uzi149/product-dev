"""The backend the Playwright tests run against, kept apart from the development data.

    python -m tests.e2e_stack          (started by frontend/playwright.config.ts)

It has its own PostgreSQL database (sentinel_e2e), MinIO bucket (sentinel-e2e), Redis database
(1: job queue, camera status, ingestion heartbeat) and ingestion folder (data/e2e), all emptied at
every start. It runs a Celery worker, live ingestion and the API on 127.0.0.1:8100, and stops them
when it is stopped. Only the Docker services and the simulated cameras are shared (read-only).
Logs: data/e2e/logs/.
"""

import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import psycopg
import redis
from alembic import command
from alembic.config import Config
from psycopg import sql

from shared.camera_status import HEARTBEAT_KEY
from shared.config import Settings
from shared.storage import StorageClient

ROOT = Path(__file__).resolve().parents[1]
API_PORT = 8100
# Environment of the e2e processes; values the .env file also sets are overridden.
E2E_ENV = {
    "POSTGRES_DB": "sentinel_e2e",
    "MINIO_BUCKET": "sentinel-e2e",
    "REDIS_DB": "1",
    "INGESTION_DATA_DIR": str(Path("data") / "e2e"),
    # New cameras are picked up quicker than the default 30 s.
    "CAMERA_RELOAD_SECONDS": "5",
}
READY_TIMEOUT = 90


def e2e_settings() -> Settings:
    return Settings(**{key.lower(): value for key, value in E2E_ENV.items()})


def reset(settings: Settings) -> None:
    """Empty database, bucket, Redis database and ingestion folder of the e2e stack."""
    development = Settings()
    if (
        settings.postgres_db == development.postgres_db
        or settings.minio_bucket == development.minio_bucket
        or settings.redis_db == development.redis_db
    ):
        sys.exit("the e2e stack would share the development data (check .env); nothing was reset")

    with psycopg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        user=settings.postgres_user,
        password=settings.postgres_password,
        dbname="postgres",
        autocommit=True,
        connect_timeout=3,
    ) as conn:
        name = sql.Identifier(settings.postgres_db)
        conn.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(name))
        conn.execute(sql.SQL("CREATE DATABASE {}").format(name))
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "shared" / "database" / "migrations"))
    config.attributes["url"] = settings.database_url
    command.upgrade(config, "head")

    storage = StorageClient(settings)
    storage.ensure_bucket()
    storage.delete_prefix("")
    redis.Redis.from_url(settings.redis_url).flushdb()
    shutil.rmtree(ROOT / settings.ingestion_data_dir, ignore_errors=True)


def start(name: str, args: list[str], env: dict[str, str], logs: Path) -> subprocess.Popen:
    log = open(logs / f"{name}.log", "wb")  # noqa: SIM115 (kept open for the process' lifetime)
    return subprocess.Popen(
        [sys.executable, *args], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT
    )


def check_running(processes: dict[str, subprocess.Popen]) -> None:
    for name, process in processes.items():
        if process.poll() is not None:
            sys.exit(f"the e2e {name} exited with code {process.returncode}; see data/e2e/logs")


def wait_until(condition, what: str, processes: dict[str, subprocess.Popen]) -> None:
    deadline = time.monotonic() + READY_TIMEOUT
    while not condition():
        check_running(processes)
        if time.monotonic() > deadline:
            sys.exit(f"timed out waiting for {what}; see data/e2e/logs")
        time.sleep(0.5)


def stop_all(processes: dict[str, subprocess.Popen]) -> None:
    for process in processes.values():
        if process.poll() is not None:
            continue
        if sys.platform == "win32":
            # The whole tree: live ingestion's FFmpeg processes are its children.
            taskkill = ["taskkill", "/T", "/F", "/PID", str(process.pid)]
            subprocess.run(taskkill, check=False, capture_output=True)
        else:
            process.terminate()


def main() -> None:
    settings = e2e_settings()
    reset(settings)
    logs = ROOT / settings.ingestion_data_dir / "logs"
    logs.mkdir(parents=True)
    env = {**os.environ, **E2E_ENV}
    client = redis.Redis.from_url(settings.redis_url)
    processes: dict[str, subprocess.Popen] = {}

    def on_signal(*_):
        raise KeyboardInterrupt

    for name in ("SIGTERM", "SIGBREAK"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), on_signal)

    try:
        processes["worker"] = start(
            "worker",
            ["-m", "celery", "-A", "shared.jobs.celery_app", "worker", "--pool=solo",
             "-Q", "default", "--loglevel=info"],
            env,
            logs,
        )  # fmt: skip
        processes["ingestion"] = start("ingestion", ["-m", "services.ingestion"], env, logs)
        # The API comes last: once it answers, the tests can rely on the worker and ingestion.
        wait_until(
            lambda: b" ready." in (logs / "worker.log").read_bytes(), "the worker", processes
        )
        wait_until(lambda: client.exists(HEARTBEAT_KEY), "live ingestion", processes)
        processes["api"] = start(
            "api",
            ["-m", "uvicorn", "services.api.main:app", "--host", "127.0.0.1",
             "--port", str(API_PORT)],
            env,
            logs,
        )  # fmt: skip
        print(f"e2e stack ready: API on http://127.0.0.1:{API_PORT}", flush=True)
        while True:  # until stopped, or until one of them exits
            check_running(processes)
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        stop_all(processes)


if __name__ == "__main__":
    main()
