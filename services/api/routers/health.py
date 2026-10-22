from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from services.api.deps import get_session, get_storage_client
from shared.storage import StorageClient

router = APIRouter(tags=["health"])


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
    }
