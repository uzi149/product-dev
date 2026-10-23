from shared.database.session import get_session
from shared.storage import StorageClient, get_storage


def get_storage_client() -> StorageClient:
    return get_storage()


__all__ = ["get_session", "get_storage_client"]
