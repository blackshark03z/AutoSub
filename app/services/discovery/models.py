from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class DiscoveryStatus(str, Enum):
    DISCOVERABLE = "DISCOVERABLE"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    GEO_RESTRICTED = "GEO_RESTRICTED"
    DRM_SUSPECTED = "DRM_SUSPECTED"
    UNSUPPORTED = "UNSUPPORTED"
    PROBE_ERROR = "PROBE_ERROR"
    UNKNOWN = "UNKNOWN"


class DrmStatus(str, Enum):
    UNKNOWN = "UNKNOWN"
    DRM_SUSPECTED = "DRM_SUSPECTED"
    NO_DRM_APPARENT = "NO_DRM_APPARENT"


@dataclass(frozen=True)
class VideoFormatInfo:
    format_id: str
    ext: str
    resolution: str | None = None
    width: int | None = None
    height: int | None = None
    vcodec: str | None = None
    acodec: str | None = None
    tbr_kbps: float | None = None
    filesize_approx: int | None = None
    protocol: str | None = None
    note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass(frozen=True)
class DiscoveryRequest:
    video_url: str
    platform: str | None = None
    timeout_seconds: float = 15.0
    authorized_access_confirmed: bool = False


@dataclass(frozen=True)
class DiscoveryResult:
    source_url_redacted: str
    platform: str
    status: str
    recommended_engine: str | None
    title: str | None
    duration_seconds: float | None
    formats: list[dict[str, Any]] = field(default_factory=list)
    subtitle_languages: list[str] = field(default_factory=list)
    authentication_required: bool = False
    drm_status: str = "UNKNOWN"
    probe_duration_ms: int = 0
    download_tested: bool = False
    error_code: str | None = None
    error_message: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if not data.get("details"):
            data.pop("details", None)
        if not data.get("error_message"):
            data.pop("error_message", None)
        return data
