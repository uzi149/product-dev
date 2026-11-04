from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from services.api.deps import get_session, get_storage_client
from shared.database.models import Keyframe
from shared.storage import StorageClient

router = APIRouter(prefix="/keyframes", tags=["keyframes"])


@router.get("/{keyframe_id}/image")
def image(
    keyframe_id: UUID,
    session: Session = Depends(get_session),
    storage: StorageClient = Depends(get_storage_client),
) -> RedirectResponse:
    """Redirect to a time-limited MinIO URL of the keyframe picture (result thumbnails)."""
    keyframe = session.get(Keyframe, keyframe_id)
    if keyframe is None:
        raise HTTPException(404, "keyframe not found")
    url = storage.presigned_get_url(storage.key_from_uri(keyframe.storage_uri))
    return RedirectResponse(url, status_code=307, headers={"Cache-Control": "no-store"})
