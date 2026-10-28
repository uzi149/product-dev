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
test-data/          camera-01/, camera-02/, sim/ test videos (video files are not committed)
data/               live ingestion's rolling buffer and outbox (created at run time, git-ignored)
tests/              ingestion, search, triggers, end_to_end
docs/               requirements and ADRs (docs/ADRs/)
```

## Getting started

Requires Python 3.12.

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows
source .venv/bin/activate         # Linux / macOS

pip install -e ".[dev]"           # backend, local models (torch, transformers) and test tools
```

On Linux, install the CPU build of torch first, or pip downloads the much larger CUDA build:

```bash
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
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

On Linux add `--extra-index-url https://download.pytorch.org/whl/cpu` to the first command, so
the CPU build of torch is installed.

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
| MinIO API | `127.0.0.1:9000` (bucket `sentinel`; this MinIO build has no web console, browse with `docker compose exec minio mc ls local/sentinel --recursive`) |
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

## Database migrations

With the infrastructure running (`docker compose up -d`):

```bash
alembic upgrade head                        # create / update all tables
alembic downgrade base                      # remove everything
alembic revision --autogenerate -m "name"   # new migration after changing shared/database/models.py
```

Always read an autogenerated migration before committing it. Database tests
(`pytest tests/database`) run in a scratch database, `sentinel_test`, and never touch the
development data.

## Storage layer

All files (segments, keyframes, snapshots, exports) go through `shared/storage`:
`keys.py` is the only place that knows the object layout, `StorageClient` wraps MinIO
(upload, download, presigned playback URLs, delete). `storage_uri` columns hold
`s3://sentinel/<key>`. To open the UI from another computer, set `MINIO_PUBLIC_ENDPOINT` so
presigned URLs point at an address that computer can reach.

## Run the API

Needs the infrastructure running, the migrations applied, and FFmpeg on `PATH`
(`winget install Gyan.FFmpeg`, then open a new terminal).

```bash
uvicorn services.api.main:app --reload     # http://127.0.0.1:8000  (docs at /docs)
```

- `POST /cameras`, `GET /cameras`, `PATCH /cameras/{id}`, `POST /cameras/{id}/test-connection`
- `GET /cameras` and `GET /cameras/{id}` include a `live` field for RTSP cameras (`null` for
  file cameras): `state` (`online`, `connecting`, `offline`, `disabled`, `not_running` when live
  ingestion isn't running, `unknown` when Redis is down), `since`, `activity`, `last_frame_at`,
  `last_snapshot_at`, `error`
- `GET /cameras/{id}/snapshot`: `307` to the camera's latest picture (refreshed every 5 s while
  it is online), `404` when there is none yet
- `POST /videos/upload` (camera, start time with timezone, video file): the file is checked
  straight away, staged in MinIO and queued (`202`). A worker keeps only the parts with
  activity (plus 10 s before and after), as browser-playable H.264 segments; the still parts
  become static periods with a reference frame
- `GET /videos/uploads?camera_id=`, `GET /videos/uploads/{id}`: upload status (`pending`,
  `processing`, `done`, `failed`) with the number of parts stored or the error
- `GET /segments`, `GET /segments/{id}`, `GET /segments/{id}/playback`
- `GET /static-periods`, `GET /static-periods/{id}/reference-frame`

## Run a worker

Uploads are processed in the background by a Celery worker (ADR 0011), using Redis from the
Docker stack as the queue. Needs FFmpeg on `PATH`, like the API.

```bash
celery -A shared.jobs.celery_app worker --pool=solo -Q default
```

`--pool=solo` is needed on Windows. Without a running worker, uploads stay `pending` and are
processed as soon as one starts.

Camera source URIs are encrypted with `FERNET_KEY` and never returned by the API. The key in
`.env.example` is a **shared development key**: generate a private one
(command in `.env.example`) and re-enter the camera URIs before using real credentials.

## Change detection

Only footage where something happens is kept (ADR 0010). The defaults are the `DETECT_*`
values in `.env.example`. The default changed share is **0.1 %** of the picture
(`DETECT_CHANGED_FRACTION=0.001`, lowered from 0.5 % because one person in a wide view covers
only 0.05–0.3 %). The Celery worker reads it at start-up, so restart the worker after pulling
this change. A camera can override the defaults, and add polygons to ignore, in its
`detection_config` JSON, e.g.
`{"changed_fraction": 0.01, "masks": [[[0, 0], [0.3, 0], [0.3, 0.4]]]}` (points are 0–1 of
the image width/height).

To see what the detector finds in a video, and to try other values:

```bash
python -m shared.video.detect test-data/camera-01/person-enters.mp4
python -m shared.video.detect clip.mp4 --config "{\"changed_fraction\": 0.01}"
```

## Live ingestion

Live RTSP cameras are watched by their own process (ADR 0014). It needs the infrastructure
running, the migrations applied and FFmpeg on `PATH`:

```bash
python -m services.ingestion
```

- It watches every **enabled RTSP camera** and reads the camera list again every 30 s, so
  cameras added, disabled or edited in the UI are picked up without a restart.
- Per camera, one FFmpeg process (one RTSP connection) records the stream into 5 s fragments
  (copied, not re-encoded) and passes one frame per second to the change detector. Only the
  activity is stored, with 10–15 s before and after (whole fragments). Long activity is stored
  as back-to-back segments of about 30 s (ADR 0015). Still stretches become static periods with
  a reference picture, and time while a camera is unreachable becomes an `offline` static period.
- A dropped stream is retried after 1, 2, 5, 10 s, then every 30 s.
- Footage waits in `data/outbox/` until MinIO and PostgreSQL have it, so short outages lose
  nothing. Footage that can't be stored at all (damaged) is moved to `data/outbox-failed/`.
  `data/` is git-ignored.
- Camera status and the latest snapshot go to Redis and MinIO every 5 s; the Live cameras page
  shows them.
- Stop it with **Ctrl+C** (Ctrl+Break and SIGTERM work too): open segments are stored before it
  exits. Only one can run at a time; a second one exits with a message.

The settings are in the "Live ingestion" block of `.env.example`.

### Simulated cameras

The Compose profile `sim` adds two cameras that loop a video into MediaMTX:
`rtsp://127.0.0.1:8554/cam-01` and `rtsp://127.0.0.1:8554/cam-02`.

```bash
python -m tests.ingestion.videos test-data/sim    # make the default clips (once; not committed)
docker compose --profile sim up -d                # start sim-cam-01 and sim-cam-02
```

The default clips are 60 s with one burst of movement (`cam-01`) and 90 s with two (`cam-02`).
To loop other videos instead, set `SIM_CAM01_VIDEO` / `SIM_CAM02_VIDEO` in `.env`. Add the two
cameras on the Cameras page (type RTSP, the URLs above). To test a dropped stream, use
`docker compose stop sim-cam-01`, then `docker compose start sim-cam-01`.
`docker compose --profile sim down` stops everything, including the sim cameras.

## Vision-language model (VLM)

From Phase 3, a VLM describes the stored footage (ADR 0019). The code is adapted from a proven
reference implementation: one OpenAI-compatible client, and one in-process fallback.

- **With an API key** (`VLM_API_KEY`, or `OPENROUTER_API_KEY`, in `.env`): OpenRouter, model
  `qwen/qwen3-vl-30b-a3b-instruct`, about 5–10 s per request. Get a key at
  https://openrouter.ai/keys. **With a key set, frames of the analysed footage are sent to
  OpenRouter: use it only with test videos.**
- **Without a key:** `Qwen/Qwen2.5-VL-3B-Instruct` runs inside the process on the CPU. It needs
  about 7.5 GB of **free** memory, downloads the model (about 7 GB) on first use, and takes a minute or more per
  request.
- **The pilot** points `VLM_BASE_URL` at its own vLLM server (with `VLM_BACKEND=api` and no key),
  so real footage stays on the local network. No code change.

`VLM_BACKEND=auto` (the default) picks the API when a key is set; `api` / `local` force one. The
other `VLM_*` settings are in `.env.example`.

Check the setup with one picture:

```bash
python -m shared.model_adapters.check_vlm                  # a generated picture: a red ball and a blue box
python -m shared.model_adapters.check_vlm photo.jpg        # your own picture(s)
python -m shared.model_adapters.check_vlm --backend local  # force the in-process model
```

It prints the backend, the model, the time taken, the token counts and the answer. Tests that
call the real models are marked `models` and run only with `RUN_MODEL_TESTS=1`
(`RUN_MODEL_TESTS=1 pytest -m models`); CI never runs them.

## Embedding models

Search compares vectors (embeddings): one for each VLM description (the text index) and one for
each keyframe (the visual index). Both models always run locally on the CPU (ADR 0020):

| Index | Model | Memory |
|---|---|---|
| Text: descriptions and typed queries | `BAAI/bge-base-en-v1.5` (768 values) | about 0.4 GB |
| Visual: keyframes, and typed text compared with them | `google/siglip2-base-patch16-224` (768 values) | about 1.5 GB |

Every process that analyses or searches loads its own copy. The models download from Hugging
Face on first use (about 2 GB); fetch them ahead of time, then check sizes, speed and a small
ranking test:

```bash
python -m shared.model_adapters.download_models
python -m shared.model_adapters.check_embeddings
```

After the download a machine can run offline with `HF_HUB_OFFLINE=1`. `EMBEDDING_THREADS` limits
the CPU threads (default: half the cores).

The models are **fixed per installation**: the first use records them in the `index_settings`
table, and a process configured for other models (`TEXT_EMBEDDING_MODEL`,
`IMAGE_EMBEDDING_MODEL`) refuses to start, because vectors from different models can't be
compared. Changing a model means analysing the footage again.

## Run the UI

Needs Node 24 and the API running (see above).

```bash
cd frontend
copy .env.local.example .env.local   # Windows (cp on Linux / macOS); points the UI at the API
npm install
npm run dev                          # http://localhost:3000 (the next free port if 3000 is taken)
```

The UI follows the Sentinel prototype (dark theme). What works so far:

- **Cameras:** add a camera, upload a video with its start time, follow its processing under
  "Recent uploads", and play the stored segments. A camera's **timeline** shows stored
  segments, static periods and offline stretches for the last hour, 6 hours, 24 hours or a
  chosen day; click a segment to play it.
- **Live cameras:** the latest picture of every RTSP camera, refreshed every 5 s, with its
  status (Live, Connecting, Offline, Disabled) and an Activity marker. It says so when live
  ingestion isn't running. A tile opens that camera's timeline.

The other screens are placeholders that say which phase builds them.

Checks: `npm run lint`, `npm test` (unit tests), `npm run build`. CI runs these.

End-to-end tests (Playwright, real Chrome) drive the whole stack on **data of their own**, so
the development data stays clean. Start Docker with the sim cameras
(`docker compose --profile sim up -d`), then run `npm run e2e`. It needs FFmpeg on `PATH` and
the venv in `.venv`, and is not part of CI. Playwright starts:

- `python -m tests.e2e_stack`: an e2e backend with its own PostgreSQL database
  (`sentinel_e2e`, migrated), MinIO bucket (`sentinel-e2e`), Redis database (1) and ingestion
  folder (`data/e2e/`), all emptied at every run. It runs the API on `127.0.0.1:8100`, a worker
  and live ingestion; their logs are in `data/e2e/logs/`.
- a dev server on port 3100 that talks to that API.

Your own API, worker, live ingestion and `npm run dev` can keep running alongside; only ports
8100 and 3100 must be free. The sim cameras are shared, read-only.

## Git workflow

- `develop` is the base branch. Start every piece of work from the latest `develop`
  on a feature branch, e.g. `feature/<short-name>`.
- Feature branches merge into `develop` through a pull request.
- Releases merge `develop` into `main` through a release pull request.
- Never commit directly to `develop` or `main`.
