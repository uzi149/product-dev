from datetime import datetime
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from shared.schemas.detection import normalise_detection_config

SourceType = Literal["rtsp", "file"]
_RTSP_SCHEMES = ("rtsp://", "rtsps://")


def mask_uri(uri: str) -> str:
    """Hide credentials and query strings: rtsp://user:pw@host:554/a?t=1 -> rtsp://***@host:554/a"""
    parts = urlsplit(uri)
    host = parts.hostname or ""
    if ":" in host:  # IPv6
        host = f"[{host}]"
    netloc = f"{host}:{parts.port}" if parts.port else host
    if parts.username or parts.password:
        netloc = f"***@{netloc}"
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


def _check_uri(source_type: str | None, source_uri: str | None) -> None:
    if source_type == "rtsp":
        if not source_uri:
            raise ValueError("source_uri is required for an rtsp camera")
        if not source_uri.lower().startswith(_RTSP_SCHEMES):
            raise ValueError("source_uri must start with rtsp:// or rtsps://")
    elif source_type == "file" and source_uri:
        raise ValueError("a file camera has no source_uri")


class CameraCreate(BaseModel):
    display_name: str = Field(min_length=1, max_length=200)
    source_type: SourceType
    source_uri: str | None = None
    enabled: bool = True
    retention_days: int = Field(default=14, ge=1, le=3650)
    analysis_fps: int = Field(default=1, ge=1, le=30)
    detection_config: dict[str, Any] = Field(default_factory=dict)

    @field_validator("detection_config")
    @classmethod
    def _check_detection_config(cls, value: dict[str, Any]) -> dict[str, Any]:
        return normalise_detection_config(value)

    @model_validator(mode="after")
    def _uri_matches_type(self) -> "CameraCreate":
        _check_uri(self.source_type, self.source_uri)
        return self


class CameraUpdate(BaseModel):
    """Every field is optional. The source type can't be changed."""

    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    source_uri: str | None = None
    enabled: bool | None = None
    retention_days: int | None = Field(default=None, ge=1, le=3650)
    analysis_fps: int | None = Field(default=None, ge=1, le=30)
    detection_config: dict[str, Any] | None = None

    @field_validator("detection_config")
    @classmethod
    def _check_detection_config(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        return None if value is None else normalise_detection_config(value)


LiveStateName = Literal["connecting", "online", "offline", "disabled", "not_running", "unknown"]


class LiveStatus(BaseModel):
    """Status of an RTSP camera, kept in Redis by live ingestion (`shared.camera_status`)."""

    state: LiveStateName
    since: datetime | None = None
    activity: bool = False
    last_frame_at: datetime | None = None
    last_snapshot_at: datetime | None = None
    error: str | None = None


class CameraRead(BaseModel):
    """The source URI is never returned, only a masked form for display."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    display_name: str
    source_type: SourceType
    enabled: bool
    retention_days: int
    analysis_fps: int
    detection_config: dict[str, Any]
    created_at: datetime
    has_source_uri: bool
    source_uri_masked: str | None = None
    segment_count: int = 0
    # RTSP cameras only; None for file cameras.
    live: LiveStatus | None = None


class ConnectionTestResult(BaseModel):
    ok: bool
    width: int | None = None
    height: int | None = None
    error: str | None = None


class DeletePreview(BaseModel):
    """What deleting a camera removes, and why it can't be deleted now (`blocker`), if so."""

    model_config = ConfigDict(from_attributes=True)

    segments: int
    static_periods: int
    analysed_windows: int
    uploads: int
    blocker: str | None
