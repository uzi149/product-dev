# MVP Implementation Plan

Plan for building the Sentinel MVP: **Phases 1–5** of the Technical Scope (§15), built
**phase by phase**. Each phase is finished and its acceptance test passes before the next
one starts. Phase 6 (real CCTV pilot) is out of scope.

**Guiding rule: keep it simple.**
- Follow the scope document. Where it is silent, pick the simplest option that works on
  the development laptop.
- No extra infrastructure and no packages added "just in case".
- Anything not needed for the Phase 1–5 acceptance tests waits (see "Deferred" at the end).

References like §7.2 point to sections of the Technical Scope PDF.

---

## 1. What the MVP delivers

At the end of Phase 5, the first milestone from §18 works end to end:

```
One MP4 → simulated RTSP stream → activity-based segment → MinIO
  → PostgreSQL metadata → VLM observation → text + visual embeddings
  → natural-language query → merged evidence playback
  → one custom trigger → stored event and UI notification
```

### Runtime pieces (development laptop)

| Piece | Runs as | What it does |
|---|---|---|
| PostgreSQL + pgvector, MinIO, Redis, MediaMTX | Docker Compose | Database, video storage, job queue, RTSP server |
| Simulated cameras | Docker Compose (profile `sim`) | FFmpeg loops test MP4s into MediaMTX as `cam-01`, `cam-02` |
| `api` | `uvicorn` (venv) | REST API + WebSockets |
| `ingestion` | Python process (venv) | Watches RTSP cameras, writes active segments |
| `worker` | Celery worker (venv) | File ingestion, VLM analysis, embeddings, trigger evaluation |
| `query-worker` | Celery worker (venv) | Search queries and trigger tests, so they don't wait behind analysis |
| `frontend` | `npm run dev` | Operator UI |

The app runs natively on Windows in the venv. Only the infrastructure runs in Docker.

---

## 2. Decisions

All open questions are settled here. Items marked *default* took the recommended option
and can still be changed during review.

| Topic | Decision |
|---|---|
| Scope / order | Phases 1–5, phase by phase. No deadline set. |
| Repo | Monorepo, one `pyproject.toml`, frontend in `frontend/` *(A4 default)* |
| Python | 3.12, `venv` + `pip`, versions pinned in `requirements.lock` |
| Dev machine | Native Windows, 16 GB RAM, no GPU. Infrastructure in Docker Desktop. FFmpeg installed on PATH (`winget install Gyan.FFmpeg`). |
| Git | Feature branch from `develop` → PR → `develop`. Release PR `develop` → `main`. CI starts with the first code PR. |
| VLM | OpenRouter `qwen/qwen3-vl-30b-a3b-instruct` when `OPENROUTER_API_KEY` is set. Otherwise local `Qwen/Qwen2.5-VL-3B-Instruct` on CPU (bfloat16, fallback only, slow). The client speaks the OpenAI-compatible API, so the pilot can point it at vLLM through config. |
| Pilot model / machine | Decided later (not part of the MVP) |
| Embeddings | **Local only**, fixed per installation. Text: `BAAI/bge-base-en-v1.5` (768-dim). Visual: `google/siglip-base-patch16-224` (768-dim, also embeds query text for text→image search). |
| Analysis windows | One VLM description per **12 s window**, windows overlap by **3 s** (start every 9 s), **6 keyframes** per window (§8–9) |
| Indexes | Two, as in §9.2: text index (VLM descriptions) and visual index (keyframe images) |
| Reranking | Weighted score fusion, then VLM verification. No cross-encoder. *(default)* |
| Query filters | Camera names and dates parsed with simple rules, plus explicit UI filters *(default)* |
| Trigger compilation | Same VLM in text-only mode turns the rule into `compiled_rule_json` *(default)* |
| Action vocabulary | Starting list in §3.3 below, extended as needed *(default)* |
| Confidence | VLM `overall_confidence` × verification confidence *(default)* |
| Language | English only |
| Video decode / record | FFmpeg records the stream (stream copy, 5 s fragments). OpenCV reads frames at 1 fps for change detection. *(default)* |
| Change detection | Grayscale downscale + difference against a running background, SSIM as a second check. Thresholds per camera. *(default)* |
| Region masks | Polygons in the camera's config JSON. No UI editor. *(default)* |
| Uploaded MP4 time | User enters start date/time on upload; defaults to upload time |
| Uploaded MP4 handling | Same change detection as live; only active parts stored *(default)* |
| Playback | H.264 MP4 served through a MinIO presigned URL. Re-encode only when the source isn't H.264. *(default)* |
| Live view | Latest snapshot per camera, refreshed every 5 s. No live video. *(default)* |
| Export | FFmpeg joins evidence parts and inserts a short "gap" card between separated parts *(default)* |
| Retention | Default 14 days, set per camera. Configuration only in the MVP; the cleanup job is Phase 6. *(default)* |
| Jobs | Celery + Redis. On Windows, workers run with `--pool=solo`. |
| DB access | Sync SQLAlchemy 2 + psycopg 3. Async only for WebSockets. *(default)* |
| Camera credentials | Source URI encrypted with Fernet from day 1, key in `.env` *(default)* |
| Login | None in the MVP; LAN only *(default)* |
| UI screens | Overview, Live cameras, Events, Investigate, Triggers, Cameras, Settings *(Team & access later)* |
| Dashboard | Simple counts only: cameras online, active triggers, events to review *(default)* |
| Frontend | Next.js (App Router) + TypeScript + Tailwind + shadcn/ui. The prototype site is the visual reference. *(default)* |
| Notifications | In the UI only: WebSocket toast + events list *(default)* |
| Test data | Recorded by the team (§6 stage 1). Ground truth in a YAML file next to each video. *(default)* |
| Quality target | ≥ 80% of scripted events found, ≤ 1 false trigger per hour of test footage *(default)* |
| Sites | Single site, single installation *(default)* |
| Time | Stored in UTC (`timestamptz`), shown in local time in the UI |

---

## 3. Shared foundations

### 3.1 Database

All tables from §5.2 are created in Phase 1, in one migration, so later phases only add
code: `cameras`, `video_segments`, `static_periods`, `analysis_windows`, `keyframes`,
`text_embeddings`, `triggers`, `trigger_cameras`, `events`, `event_evidence`,
`query_sessions`, `query_results`.

Two small additions to the §5.2 columns:
- `cameras.detection_config` (JSONB): mask polygons and change thresholds (§7.3).
- `cameras.source_uri_encrypted` holds the Fernet-encrypted URI; it's never returned by the API.

Indexes (§5.3): HNSW on `keyframes.visual_embedding` and `text_embeddings.embedding`
(cosine). B-tree on `camera_id`, `start_time`, `end_time`, `trigger_id`,
`processing_status`.

Camera online/offline status is kept in Redis (a key with a short expiry, refreshed by
ingestion), not in a table.

### 3.2 MinIO object layout (bucket `sentinel`)

```
segments/{camera_id}/{yyyy}/{mm}/{dd}/{segment_id}.mp4
keyframes/{camera_id}/{window_id}/{n}.jpg
static/{camera_id}/{static_period_id}.jpg
snapshots/{camera_id}/latest.jpg
exports/{event_id}.mp4
```

### 3.3 VLM observation (§8.1)

Every window gets the JSON from §8.1: `summary`, `entities`, `actions`,
`visible_objects`, `uncertainties`, `overall_confidence`. It's validated with a Pydantic
model in `shared/schemas/`. Action `type` must come from the vocabulary:

`enter, leave, walk, run, stand, sit, loiter, place_object, pick_up_object,
carry_object, leave_object_behind, vehicle_arrive, vehicle_depart, vehicle_park,
interact_with_person, fall, open_door, close_door, ring_doorbell`

Temporary IDs (`person_1`) are only valid inside one window; there is no identity across
windows or cameras.

### 3.4 Reference code

VLM-related code is adapted from the reference repository listed in `AGENTS.md`. It's
copied and renamed, never installed. Main sources:

| Need | Reference file |
|---|---|
| OpenAI-compatible client (retries, rate-limit cooldown, base64 images) | `llm/qwen_api.py` |
| Local Qwen loader (device selection, load once, generation lock) | `llm/qwen_loader.py` |
| Multi-image prompt, `_resize`, `_try_parse_json`, frame sampling | `vision/qwen_vlm/detector.py` |
| NL → strict JSON prompt style | `prompts/templates.py` |

### 3.5 Packages added during the MVP

Only these are added, each in the branch that first needs it:

| Package | Added in | Why |
|---|---|---|
| `cryptography` | 1.4 | Encrypt camera source URIs |
| `celery` | 2.2 | Background jobs |
| `torch`, `transformers`, `pillow` (moved from `vlm` extra to main) | 3.2 | Local embedding models, which always run locally |

SSIM is implemented with OpenCV/numpy (no `scikit-image`). FFmpeg is an external program,
not a Python package.

### 3.6 Tests and CI

- `pytest` unit tests for logic (change detection, window splitting, merging, filters,
  trigger matching). These need no models or Docker.
- Integration tests against Postgres, MinIO and Redis. In CI they run as GitHub Actions
  service containers.
- Tests that load models or call OpenRouter are marked `models` and skipped in CI.
- CI runs `ruff` + `pytest` on every PR to `develop`. It is added in branch 1.2 (first
  code). Frontend lint + build are added in 1.5.

---

## 4. Phases

Each numbered item is one feature branch (`feature/<name>`) and one PR into `develop`.

### Phase 1: Storage foundation

**Goal:** store an uploaded video and play it back.

| # | Branch | Work |
|---|---|---|
| 1.1 | `infra-compose` | `docker-compose.yml`: `pgvector/pgvector:pg16`, MinIO (+ one-shot bucket creation), Redis, MediaMTX. Configs in `infrastructure/`. `.env.example`. `shared/config.py` (pydantic-settings) holding all §7.2 values as settings. README run instructions. |
| 1.2 | `db-schema` | SQLAlchemy models for all §5.2 tables, DB session helper, Alembic setup + first migration with the §5.3 indexes. CI workflow. Tests: migration upgrade/downgrade, basic inserts. |
| 1.3 | `storage-client` | `shared/storage/`: upload file, presigned GET URL, delete, using the §3.2 layout. Tests against MinIO. |
| 1.4 | `camera-segment-api` | FastAPI app. `POST/GET/PATCH /cameras`, `POST /cameras/{id}/test-connection` (open source with OpenCV, read one frame, 5 s timeout). `POST /videos/upload`: in this phase the **whole file** becomes one segment (camera id + start time; duration/codec/size read with `ffprobe`). `GET /segments` (camera + time filters). `GET /segments/{id}/playback` (redirect to presigned URL). Per-camera `retention_days` (default 14). Fernet encryption of source URIs. API tests. |
| 1.5 | `frontend-shell` | Next.js app in `frontend/`. Sidebar with all MVP screens (placeholders). **Cameras** page: list, add/edit, test connection, upload MP4 with start time, list segments and play them. |

**Acceptance (§15):** upload one video → it's in MinIO → its metadata comes back from
PostgreSQL → it plays through the API.

### Phase 2: Video ingestion

**Goal:** keep only footage where something happens, from files and live streams.

| # | Branch | Work |
|---|---|---|
| 2.1 | `change-detection` | `shared/video/change_detector.py`. Per sampled frame (1 fps): downscale to grayscale, blur, mask, difference against a slowly updated background, % of changed pixels, SSIM as a second check. A small state machine: **idle** → **active** when change stays above the threshold for 2 samples → **idle** again after the scene is stable for `stable_timeout` (10 s). Thresholds and mask from `cameras.detection_config`. Unit tests with synthetic frames and the static-room video. |
| 2.2 | `file-ingestion` | Celery app + `ingest_file` task. Replaces the Phase 1 whole-file upload. OpenCV scans the MP4 at 1 fps → active intervals → add pre-roll and post-roll (10 s each, clamped to the file) → merge intervals that now overlap → FFmpeg cuts each to H.264 MP4 → MinIO → `video_segments` row with `activity_score`. Gaps become `static_periods` with a reference frame. |
| 2.3 | `rtsp-ingestion` | `python -m services.ingestion`: one thread per enabled RTSP camera, camera list reloaded every 30 s. Per camera: **(a)** FFmpeg records the stream into 5 s fragments in `data/buffer/{camera_id}/`; fragments older than 60 s are deleted unless an active interval needs them. **(b)** OpenCV reads the stream at 1 fps into the change detector, so activity is detected within ~1–2 s (§17). **(c)** When an interval ends and the post-roll has passed, the fragments covering it are joined into one MP4 → MinIO → `video_segments` row. Reconnect with backoff for both FFmpeg and OpenCV, with camera status in Redis. Static periods with a health snapshot every 10 min. `snapshots/{id}/latest.jpg` updated every 5 s. Compose profile `sim` loops test MP4s as `rtsp://localhost:8554/cam-01` and `cam-02`. |
| 2.4 | `ingestion-ui` | **Live cameras** page: snapshot grid with online/offline status (`GET /cameras/{id}/snapshot`). Camera detail: timeline of stored segments and static periods. |

**Acceptance (§15):** loop a test MP4 as RTSP → only the active interval is stored →
pre-roll and post-roll are included → ingestion recovers after the stream is stopped and
restarted.

### Phase 3: VLM and vector pipeline

**Goal:** every stored segment gets descriptions and embeddings, and can be found with a
plain-English query.

| # | Branch | Work |
|---|---|---|
| 3.1 | `vlm-adapter` | `shared/model_adapters/`: a small VLM interface with `describe(frames, timestamps, camera_name)` and `ask(frames, question)`. Two implementations adapted from the reference code: **OpenAI-compatible client** (OpenRouter now, vLLM later; uses `httpx`) and **local Transformers client** (CPU, bfloat16). Backend chosen by whether `OPENROUTER_API_KEY` is set. JSON extraction and image resize helpers. Tests with mocked HTTP. |
| 3.2 | `embedding-adapters` | Local `TextEmbedder` (bge-base, CLS pooling, normalised, query prefix for searches) and `ImageEmbedder` (SigLIP image + text towers). Loaded once per process, on CPU. Moves `torch`/`transformers`/`pillow` into main dependencies, regenerates `requirements.lock`. Tests: 768-dim, normalised, a sanity similarity check. |
| 3.3 | `analysis-worker` | Observation schema + strict factual prompt (§8.1, §3.3 above). Celery task `analyze_segment`, queued by ingestion after each new segment. Splits the segment into 12 s windows starting every 9 s (a short segment is one window). Per window: 6 evenly spaced frames → saved as keyframes in MinIO → VLM `describe` → validated observation stored in `analysis_windows` → description embedded into `text_embeddings` → each keyframe embedded into `keyframes.visual_embedding`. `processing_status` per window (`pending` → `processing` → `done`/`failed`) and per segment. Celery retries up to 3 times with backoff; invalid JSON gets one retry with a stricter prompt. |
| 3.4 | `basic-retrieval` | `services/query_engine/retrieval.py`: embed the query text (bge + SigLIP text), search both indexes with camera and time filters applied in the same SQL query, return the top windows. A small CLI for manual checks (`python -m services.query_engine.search "..."`). This is the base Phase 4 builds on. |

**Acceptance (§15):** process a known test video → the stored entities/actions match its
ground-truth YAML → a natural-language query returns the right interval in the top 3.

### Phase 4: Historical queries

**Goal:** operators search footage and get verified, merged evidence playlists.

| # | Branch | Work |
|---|---|---|
| 4.1 | `query-filters` | Rule-based parsing of camera names (matched against `cameras.display_name`) and time phrases: today, yesterday, last N minutes/hours, explicit dates and times. Explicit filters from the UI win. Table-driven unit tests. |
| 4.2 | `hybrid-search` | Take 30–50 candidate windows from both indexes. Fused score = weighted sum of text similarity, visual similarity (best keyframe per window), keyword match (Postgres full-text on the description), action match (query words mapped to the action vocabulary), and existing event confidence. Weights in config. Rerank = sort by fused score. |
| 4.3 | `evidence-merging` | Expand each top candidate to its neighbouring windows. Merge overlapping or nearly touching intervals per camera into sequences. Group sequences on the same camera into one result, with explicit **gap** parts (§11). Build the playback manifest JSON (§11). Unit tests reproduce the §11 examples (merge 14:20:51–14:21:48; "5 minutes 12 seconds not shown"). |
| 4.4 | `query-api` | Celery task on the `query` queue running the §10 steps: filters → search → merge → **VLM verification** of the top 3–5 sequences (frames from the merged interval + the question → match, confidence, reason) → drop unsupported ones → manifest. Stores `query_sessions` and `query_results` (vector, rerank and verification scores, `verified`, `sequence_group_id`). Endpoints: `POST /queries`, `GET /queries/{id}`, `GET /queries/{id}/results`, `WS /queries/{id}/progress` (stages published through Redis). |
| 4.5 | `investigate-ui` | **Investigate** page: query box, camera multi-select, time range. Shows the progress stages (§10 "Searching indexed footage… / Found 8 possible matches… / Verifying 3 sequences… / 2 verified results"). Results show "possible match" until verified. An evidence player plays the parts in order and shows gap markers. |

**Acceptance (§15):** a query for an event that spans overlapping windows returns **one**
merged result, and separated results are shown with explicit gaps.

### Phase 5: Custom triggers

**Goal:** operators create rules in plain language and get live, evidence-backed alerts.

| # | Branch | Work |
|---|---|---|
| 5.1 | `trigger-api` | `POST/GET/PATCH /triggers`, `POST /triggers/{id}/enable` and `/disable`, camera assignment via `trigger_cameras`. On create or edit, the VLM (text only) compiles the rule into `compiled_rule_json` (§12.2): `required_entities`, `required_action_sequence` (vocabulary only), `maximum_sequence_gap_seconds`, `requires_vlm_verification`, and `mode`: `structured`, or `semantic` when the rule has no clear actions. Validated with Pydantic. The original rule text is kept. |
| 5.2 | `live-trigger-evaluation` | Runs at the end of `analyze_segment`, once per new window (§12.3). Loads only the enabled triggers assigned to **this** camera. **Structured:** required entities present, and actions in the required order across this window and the recent windows of the same camera within the max gap. **Semantic:** similarity between the rule text and the description embedding above a threshold. VLM verification only when the rule asks for it. Confidence must reach `minimum_confidence`. **Cooldown / duplicates:** if the same trigger fired on the same camera within `cooldown_seconds`, extend that event instead of creating a new one. Creates `events` + `event_evidence` (with pre/post roll), then publishes to Redis → `WS /live/events`. |
| 5.3 | `events-api` | `GET /events`, `GET /events/{id}` (with playback manifest), `POST /events/{id}/acknowledge` (status `new` → `confirmed` / `dismissed`). `POST /events/{id}/export`: Celery task joins the evidence parts with FFmpeg, adding a gap card (image drawn with OpenCV) between separated parts → `exports/` → download URL. |
| 5.4 | `trigger-test` | `POST /triggers/{id}/test` on a chosen test video, the previous hour, the previous 24 hours or a custom range (§12.4). Reuses Phase 4 retrieval, with the rule text as the query and the trigger's cameras as the filter, then evaluates the compiled rule on the candidates and VLM-verifies them. Returns the would-be matches. No events are created. |
| 5.5 | `triggers-events-ui` | **Triggers** page: list + 4-step builder (rule and name → cameras → confidence, cooldown and evidence seconds → test and enable). **Events** page: review list, detail with evidence player, confirm/dismiss, export. Live toast from `WS /live/events`. **Overview** page: cameras online, active triggers, events to review, latest events. **Settings** page: read-only view of the main config values. |

**Acceptance (§15):** assign a trigger to CAM-01 only → confirm CAM-02 never evaluates it
→ trigger an event on CAM-01 → receive a live notification with playable evidence.

When Phase 5 passes, the §18 milestone runs end to end, and the MVP is complete.

---

## 5. Test data (start now, needed from Phase 2)

Record short, controlled videos (§6 stage 1). A phone on a tripod is enough.

```
test-data/
  camera-01/
    person-enters.mp4        person-enters.yaml
    bag-left-behind.mp4      bag-left-behind.yaml
    static-room.mp4          static-room.yaml
  camera-02/
    vehicle-arrival.mp4      vehicle-arrival.yaml
    two-people-crossing.mp4  two-people-crossing.yaml
```

Also add one "similar-looking activity that should not trigger" clip (§6), e.g. someone
putting a bag down and picking it up again.

Ground truth format:

```yaml
video: bag-left-behind.mp4
events:
  - action: enter
    start_s: 3
    end_s: 6
  - action: place_object
    start_s: 9
    end_s: 12
  - action: leave
    start_s: 14
    end_s: 18
```

Videos are not committed (they're in `.gitignore`); the YAML files are.

---

## 6. Risks and how we handle them

| Risk | Handling |
|---|---|
| Local VLM on CPU is very slow (≈ 1 min+ per window) and needs ~7.5 GB RAM | Fallback only; daily development uses OpenRouter. Keep test videos short. |
| OpenRouter rate limits / cost | Retry with backoff and shared cooldown (from the reference client). Limit parallel requests. |
| Test footage goes to OpenRouter during development | Only our own test videos are used. The pilot switches to a local vLLM server via config (no code change). |
| 16 GB RAM with Docker + two workers + embedding models (~1 GB per worker) | Run only the needed processes. Keep Docker memory limits modest. |
| Celery doesn't officially support Windows | Run workers with `--pool=solo`. |
| RTSP reading on Windows | OpenCV's bundled FFmpeg backend, forced to TCP transport. Tested against the MediaMTX simulated cameras. |
| Change detection false alarms (lighting, screens) | Per-camera thresholds and mask polygons in `detection_config`. |
| VLM uses words outside the action vocabulary | Pydantic validation + one retry with a stricter prompt; unknown actions are dropped and noted in `uncertainties`. |

---

## 7. ADRs to write (`docs/ADRs/`)

Written as part of the branch where each decision first matters:

1. Project structure, packaging and git workflow (1.1)
2. Docker for infrastructure, app native on Windows (1.1)
3. Ingestion design: FFmpeg fragments + OpenCV change detection (2.1)
4. Celery + Redis for background jobs (2.2)
5. VLM backend: OpenAI-compatible API (OpenRouter → vLLM) with local fallback (3.1)
6. Local embedding models and vector sizes (3.2)
7. Analysis windows and the two indexes (3.3)
8. Search scoring, merging and verification (4.2)
9. Trigger compilation and evaluation (5.1)

---

## 8. Deferred (not in the MVP)

- Phase 6: real CCTV/NVR cameras, ONVIF discovery, retention cleanup job, operational
  logging, network-loss recovery on real hardware
- vLLM deployment and choosing the pilot model and machine
- Running the app services inside Docker Compose (one-machine packaging)
- Login, users and roles (Team & access screen)
- Live video in the browser (snapshots only in the MVP)
- Cross-encoder reranking, confidence calibration
- Email / messaging notifications
- Mask drawing UI
- Public evaluation datasets (§6 stage 3)
