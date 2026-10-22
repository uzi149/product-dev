# Sentinel: Local VLM Camera Intelligence Platform

Sentinel is a private, fully local system for searching and monitoring camera footage in
plain language. Camera footage and AI processing never leave the local network.

- **Ingests** recorded MP4 files and live RTSP camera streams.
- **Keeps only active footage.** Unchanged scenes are not stored.
- **Describes** each short time window with a local vision-language model (VLM) and
  indexes text and visual embeddings.
- **Searches** footage across one or more cameras with natural-language queries, and
  returns evidence playlists that merge results across clips.
- **Triggers:** operators write custom rules in plain language, assign them to cameras,
  and get live alerts with video evidence.

Full requirements: [`docs/Local VLM Camera Intelligence Platform — Technical Scope.pdf`](docs/).

## Architecture

```
MP4 / RTSP camera
  → Ingestion (decode, rolling buffer, change detection, clips)
  → MinIO (video)          + Redis (analysis jobs)
  → VLM worker (keyframes, descriptions, embeddings)
  → PostgreSQL + pgvector
  → API + query engine + trigger engine
  → Operator UI
```

**Stack:** Python 3.12, FastAPI, SQLAlchemy + Alembic, PostgreSQL + pgvector, MinIO,
Redis, MediaMTX, FFmpeg/OpenCV, Qwen-family VLM (vLLM on GPU, Transformers for local
development), React/Next.js.

## Project structure

```
services/
  api/              FastAPI: cameras, videos, segments, queries, triggers, events, WebSockets
  ingestion/        MP4/RTSP reader, rolling buffer, change detection, clip writer
  analysis/         VLM worker: keyframes, observations, embeddings
  trigger_engine/   trigger compilation and live evaluation
  query_engine/     hybrid search, reranking, verification, evidence playlists
shared/
  database/         SQLAlchemy models and Alembic migrations
  schemas/          Pydantic schemas (API, VLM output, compiled triggers)
  model_adapters/   VLM and embedding model adapters
  storage/          MinIO client
  video/            FFmpeg/OpenCV helpers
frontend/           operator UI (Next.js)
infrastructure/     postgres, mediamtx, minio configuration
test-data/          camera-01/, camera-02/ test videos (video files are not committed)
tests/              ingestion, search, triggers, end_to_end
docs/               requirements and ADRs (docs/ADRs/)
```

## Getting started

Requires Python 3.12.

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows
source .venv/bin/activate         # Linux / macOS

pip install -e ".[dev]"           # backend + test tools
pip install -e ".[dev,vlm]"       # also the local Qwen VLM (torch, transformers; several GB)
```

Dependencies are declared in `pyproject.toml`. Exact versions are pinned in
`requirements.lock`. After adding or changing a dependency, regenerate it:

```bash
pip freeze --exclude-editable > requirements.lock
```

To install the exact pinned versions (e.g. on the pilot machine):

```bash
pip install -r requirements.lock
pip install -e . --no-deps
```

## Run the infrastructure

PostgreSQL (pgvector), MinIO, Redis and MediaMTX run in Docker Compose. The Python services
and the frontend run natively. Requires Docker Desktop.

```bash
copy .env.example .env            # Windows (cp on Linux / macOS); edit if needed
docker compose up -d
docker compose ps                 # postgres, minio, redis healthy; minio-init "Exited (0)"
```

| Service | Address (this machine only) |
|---|---|
| PostgreSQL | `localhost:5433` (host port 5433 so it doesn't clash with a local PostgreSQL) |
| MinIO API / console | `localhost:9000` / http://localhost:9001 (bucket `sentinel`) |
| Redis | `localhost:6380` |
| MediaMTX (RTSP) | `rtsp://localhost:8554` |

Ports listen on `127.0.0.1` only. To allow other machines (e.g. real cameras), set
`BIND_ADDRESS` in `.env` and change the default passwords first.

Check it works: `pytest tests/infrastructure` (skipped when the services are down).

```bash
docker compose down               # stop; data is kept in Docker volumes
docker compose down -v            # stop and delete all data
```

Settings (connections and the ingestion values from the scope §7.2) are read from `.env`
by `shared/config.py`.

## Git workflow

- `develop` is the base branch. Start every piece of work from the latest `develop`
  on a feature branch, e.g. `feature/<short-name>`.
- Feature branches merge into `develop` through a pull request.
- Releases merge `develop` into `main` through a release pull request.
- Never commit directly to `develop` or `main`.
