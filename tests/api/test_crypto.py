import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from services.api.main import create_app
from shared.config import get_settings
from shared.crypto import CryptoError, check_key, decrypt_text, encrypt_text
from shared.schemas.camera import mask_uri


def test_round_trip_and_ciphertext_differs():
    # "!" is outside the URL-safe base64 alphabet, so the password can't appear by chance.
    token = encrypt_text("rtsp://admin:s3cret!@10.0.0.1/s")
    assert "s3cret!" not in token
    assert decrypt_text(token) == "rtsp://admin:s3cret!@10.0.0.1/s"


def test_decrypting_with_another_key_fails(monkeypatch):
    token = encrypt_text("secret")
    monkeypatch.setenv("FERNET_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    with pytest.raises(CryptoError):
        decrypt_text(token)


@pytest.mark.parametrize("key", ["", "not-a-key"])
def test_missing_or_invalid_key(monkeypatch, key):
    monkeypatch.setenv("FERNET_KEY", key)
    get_settings.cache_clear()
    with pytest.raises(CryptoError):
        check_key()


def test_api_refuses_to_start_without_a_valid_key(monkeypatch):
    monkeypatch.setenv("FERNET_KEY", "")
    get_settings.cache_clear()
    with pytest.raises(CryptoError), TestClient(create_app()):
        pass


@pytest.mark.parametrize(
    ("uri", "masked"),
    [
        ("rtsp://admin:pw@192.168.1.10:554/a?token=1", "rtsp://***@192.168.1.10:554/a"),
        ("rtsp://user@host/live", "rtsp://***@host/live"),
        ("rtsp://192.168.1.10/live", "rtsp://192.168.1.10/live"),
        ("rtsp://u:p@[::1]:8554/x", "rtsp://***@[::1]:8554/x"),
    ],
)
def test_mask_uri(uri, masked):
    assert mask_uri(uri) == masked
