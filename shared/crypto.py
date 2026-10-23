"""Encryption of camera source URIs (they often contain passwords).

The key comes from `FERNET_KEY`. The key in `.env.example` is a shared development key
(ADR 0008): generate a private one before using real camera credentials.
"""

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from shared.config import get_settings


class CryptoError(Exception):
    """The key is missing or invalid, or a value can't be decrypted with it."""


@lru_cache
def _fernet(key: str) -> Fernet:
    if not key:
        raise CryptoError("FERNET_KEY is not set (copy it from .env.example)")
    try:
        return Fernet(key.encode())
    except ValueError as error:
        raise CryptoError("FERNET_KEY is not a valid Fernet key") from error


def check_key() -> None:
    """Raise CryptoError now instead of on the first request that needs the key."""
    _fernet(get_settings().fernet_key)


def encrypt_text(value: str) -> str:
    return _fernet(get_settings().fernet_key).encrypt(value.encode()).decode()


def decrypt_text(token: str) -> str:
    try:
        return _fernet(get_settings().fernet_key).decrypt(token.encode()).decode()
    except InvalidToken as error:
        raise CryptoError("stored value can't be decrypted with the current FERNET_KEY") from error
