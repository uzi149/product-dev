from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from services.api.deps import get_session
from shared.crypto import CryptoError, decrypt_text, encrypt_text
from shared.database.models import Camera, VideoSegment
from shared.schemas.camera import (
    CameraCreate,
    CameraRead,
    CameraUpdate,
    ConnectionTestResult,
    mask_uri,
)
from shared.video.capture import check_connection

router = APIRouter(prefix="/cameras", tags=["cameras"])


def _read(camera: Camera, segment_count: int) -> CameraRead:
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
def create_camera(body: CameraCreate, session: Session = Depends(get_session)) -> CameraRead:
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
    return _read(camera, 0)


@router.get("", response_model=list[CameraRead])
def list_cameras(session: Session = Depends(get_session)) -> list[CameraRead]:
    counts = dict(
        session.execute(
            select(VideoSegment.camera_id, func.count()).group_by(VideoSegment.camera_id)
        ).all()
    )
    cameras = session.scalars(select(Camera).order_by(Camera.created_at, Camera.display_name))
    return [_read(c, counts.get(c.id, 0)) for c in cameras]


@router.get("/{camera_id}", response_model=CameraRead)
def get_camera(camera_id: UUID, session: Session = Depends(get_session)) -> CameraRead:
    camera = _get_camera(session, camera_id)
    return _read(camera, _segment_count(session, camera_id))


@router.patch("/{camera_id}", response_model=CameraRead)
def update_camera(
    camera_id: UUID, body: CameraUpdate, session: Session = Depends(get_session)
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
    return _read(camera, _segment_count(session, camera_id))


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
