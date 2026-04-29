from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ManualConfig:
    autofocus_enabled: bool = False
    manual_focus_value: float | None = None
    quad_points: list[list[float]] = field(default_factory=list)
    frame_width: int | None = None
    frame_height: int | None = None
    valid: bool = False
    validation_message: str = "Manual config is not set"
    updated_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "autofocus_enabled": bool(self.autofocus_enabled),
            "manual_focus_value": self.manual_focus_value,
            "quad_points": [list(p) for p in self.quad_points],
            "frame_width": self.frame_width,
            "frame_height": self.frame_height,
            "valid": bool(self.valid),
            "validation_message": self.validation_message,
            "updated_at": self.updated_at,
        }


@dataclass
class JobRequest:
    job_id: str
    mode: str = "manual"
    readability_required: bool | None = None
    timeout_seconds: float = 15.0
    created_at: str = field(default_factory=utc_now_iso)


@dataclass
class JobRecord:
    job_id: str
    mode: str
    status: str = STATUS_QUEUED
    created_at: str = field(default_factory=utc_now_iso)
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = None
    detail: str | None = None
    image_bytes: bytes | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "mode": self.mode,
            "status": self.status,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "detail": self.detail,
            "metadata": self.metadata,
        }

