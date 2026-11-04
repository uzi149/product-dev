from uuid import UUID

import redis
from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from services.api.deps import get_redis, get_session, get_storage_client
from services.cameras import delete as deletion
from shared.camera_status import ingestion_running, read_statuses
from shared.crypto import CryptoError, decrypt_text, encrypt_text
from shared.database.models import Camera, SourceType, VideoSegment
from shared.schemas.camera import (
    CameraCreate,
    CameraRead,
    CameraUpdate,
    ConnectionTestResult,
    DeletePreview,
    LiveStatus,
    mask_uri,
)
from shared.storage import StorageClient
from shared.storage.keys import snapshot_key
from shared.video.capture import check_connection

router = APIRouter(prefix="/cameras", tags=["cameras"])


def _live_statuses(client: redis.Redis, cameras: list[Camera]) -> dict[UUID, LiveStatus]:
    """Status of every RTSP camera from Redis; file cameras have none."""
    rtsp = [c for c in cameras if c.source_type == SourceType.RTSP]
    if not rtsp:
        return {}
    try:
        running = ingestion_running(client)
        statuses = read_statuses(client, [c.id for c in rtsp if c.enabled]) if running else {}
    except redis.RedisError:
        running, statuses = None, {}
    live = {}
    for camera in rtsp:
        status = statuses.get(camera.id)
        if not camera.enabled:
            live[camera.id] = LiveStatus(state="disabled")
        elif running is None:
            live[camera.id] = LiveStatus(state="unknown", error="Redis is not reachable")
        elif not running:
            live[camera.id] = LiveStatus(state="not_running")
        elif status is None:
            # Ingestion runs but hasn't picked the camera up yet (it reloads every 30 s).
            live[camera.id] = LiveStatus(state="connecting")
        else:
            live[camera.id] = LiveStatus(
                state=status.state.value,
                since=status.since,
                activity=status.activity,
                last_frame_at=status.last_frame_at,
                last_snapshot_at=status.last_snapshot_at,
                error=status.error,
            )
    return live


def _read(camera: Camera, segment_count: int, live: LiveStatus | None = None) -> CameraRead:
    masked = None
    if camera.source_uri_encrypted:
        try:
            masked = mask_uri(decrypt_text(camera.source_uri_encrypted))
        except CryptoError:
            masked = "(can't be decrypted with the current key)"
    return CameraRead(
        id=camera.id,
        display_name=camera.display_name,
        source_type=camera.source_type,
        enabled=camera.enabled,
        retention_days=camera.retention_days,
        analysis_fps=camera.analysis_fps,
        detection_config=camera.detection_config,
        created_at=camera.created_at,
        has_source_uri=camera.source_uri_encrypted is not None,
        source_uri_masked=masked,
        segment_count=segment_count,
        live=live,
    )


def _segment_count(session: Session, camera_id: UUID) -> int:
    return session.scalar(
        select(func.count()).select_from(VideoSegment).where(VideoSegment.camera_id == camera_id)
    )


def _get_camera(session: Session, camera_id: UUID) -> Camera:
    camera = session.get(Camera, camera_id)
    if camera is None:
        raise HTTPException(404, "camera not found")
    return camera


@router.post("", response_model=CameraRead, status_code=201)
def create_camera(
    body: CameraCreate,
    session: Session = Depends(get_session),
    client: redis.Redis = Depends(get_redis),
) -> CameraRead:
    camera = Camera(
        display_name=body.display_name,
        source_type=body.source_type,
        source_uri_encrypted=encrypt_text(body.source_uri) if body.source_uri else None,
        enabled=body.enabled,
        retention_days=body.retention_days,
        analysis_fps=body.analysis_fps,
        detection_config=body.detection_config,
    )
    session.add(camera)
    session.commit()
    session.refresh(camera)
    return _read(camera, 0, _live_statuses(client, [camera]).get(camera.id))


@router.get("", response_model=list[CameraRead])
def list_cameras(
    session: Session = Depends(get_session), client: redis.Redis = Depends(get_redis)
) -> list[CameraRead]:
    counts = dict(
        session.execute(
            select(VideoSegment.camera_id, func.count()).group_by(VideoSegment.camera_id)
        ).all()
    )
    cameras = list(session.scalars(select(Camera).order_by(Camera.created_at, Camera.display_name)))
    live = _live_statuses(client, cameras)
    return [_read(c, counts.get(c.id, 0), live.get(c.id)) for c in cameras]


@router.get("/{camera_id}", response_model=CameraRead)
def get_camera(
    camera_id: UUID,
    session: Session = Depends(get_session),
    client: redis.Redis = Depends(get_redis),
) -> CameraRead:
    camera = _get_camera(session, camera_id)
    live = _live_statuses(client, [camera]).get(camera_id)
    return _read(camera, _segment_count(session, camera_id), live)


@router.patch("/{camera_id}", response_model=CameraRead)
def update_camera(
    camera_id: UUID,
    body: CameraUpdate,
    session: Session = Depends(get_session),
    client: redis.Redis = Depends(get_redis),
) -> CameraRead:
    camera = _get_camera(session, camera_id)
    changes = body.model_dump(exclude_unset=True)

    if "source_uri" in changes:
        uri = changes.pop("source_uri")
        if camera.source_type == "file":
            raise HTTPException(422, "a file camera has no source_uri")
        if not uri or not uri.lower().startswith(("rtsp://", "rtsps://")):
            raise HTTPException(422, "source_uri must start with rtsp:// or rtsps://")
        camera.source_uri_encrypted = encrypt_text(uri)
    for field, value in changes.items():
        if value is None:
            raise HTTPException(422, f"{field} can't be null")
        setattr(camera, field, value)

    session.commit()
    session.refresh(camera)
    live = _live_statuses(client, [camera]).get(camera_id)
    return _read(camera, _segment_count(session, camera_id), live)


@router.get("/{camera_id}/delete-preview", response_model=DeletePreview)
def delete_preview(
    camera_id: UUID,
    session: Session = Depends(get_session),
    client: redis.Redis = Depends(get_redis),
) -> DeletePreview:
    """What deleting the camera removes, and whether it can be deleted now (ADR 0026)."""
    camera = _get_camera(session, camera_id)
    return DeletePreview.model_validate(deletion.preview(session, client, camera))


@router.delete("/{camera_id}", status_code=204)
def delete_camera(
    camera_id: UUID,
    session: Session = Depends(get_session),
    storage: StorageClient = Depends(get_storage_client),
    client: redis.Redis = Depends(get_redis),
) -> Response:
    """Delete the camera with all its footage, analysis and uploads, for good (ADR 0026)."""
    camera = _get_camera(session, camera_id)
    blocker = deletion.deletion_blocker(session, client, camera)
    if blocker is not None:
        raise HTTPException(409, blocker)
    try:
        deletion.delete_camera(session, storage, client, camera)
    except deletion.StorageUnavailable as error:
        raise HTTPException(503, str(error)) from error
    return Response(status_code=204)


@router.get("/{camera_id}/snapshot")
def snapshot(
    camera_id: UUID,
    session: Session = Depends(get_session),
    storage: StorageClient = Depends(get_storage_client),
) -> RedirectResponse:
    """Redirect to the latest snapshot (refreshed every 5 s while the camera is online)."""
    _get_camera(session, camera_id)
    key = snapshot_key(camera_id)
    if not storage.exists(key):
        raise HTTPException(404, "this camera has no snapshot yet")
    return RedirectResponse(
        storage.presigned_get_url(key), status_code=307, headers={"Cache-Control": "no-store"}
    )


@router.post("/{camera_id}/test-connection", response_model=ConnectionTestResult)
def test_camera_connection(
    camera_id: UUID, session: Session = Depends(get_session)
) -> ConnectionTestResult:
    camera = _get_camera(session, camera_id)
    if not camera.source_uri_encrypted:
        return ConnectionTestResult(ok=False, error="this camera has no live source to test")
    try:
        uri = decrypt_text(camera.source_uri_encrypted)
    except CryptoError as error:
        return ConnectionTestResult(ok=False, error=str(error))
    return ConnectionTestResult(**check_connection(uri, timeout_s=5.0).__dict__)
