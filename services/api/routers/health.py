from urllib.parse import urlsplit

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from services.api.deps import get_session, get_storage_client
from shared.config import get_settings
from shared.model_adapters import resolve_vlm_backend
from shared.storage import StorageClient

router = APIRouter(tags=["health"])


def vlm_backend() -> dict:
    """Where analysis sends the frames, for the UI's confirmation text (ADR 0025). Never a key."""
    settings = get_settings()
    if resolve_vlm_backend(settings) == "local":
        return {"kind": "local", "host": None}
    return {"kind": "api", "host": urlsplit(settings.vlm_base_url).netloc}


@router.get("/health")
def health(
    session: Session = Depends(get_session),
    storage: StorageClient = Depends(get_storage_client),
) -> dict:
    try:
        session.execute(text("SELECT 1"))
        database = True
    except Exception:  # noqa: BLE001 - any failure means "unhealthy"
        database = False
    storage_ok = storage.is_healthy()
    return {
        "status": "ok" if database and storage_ok else "degraded",
        "database": database,
        "storage": storage_ok,
        "vlm_backend": vlm_backend(),
    }
