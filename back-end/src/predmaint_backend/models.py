from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_AUDIO_BYTES = 12_000_000
MAX_METADATA_BYTES = 64 * 1024
MAX_FRAMES = 48_000 * 120
NO_MODEL_REASON = "No approved analysis model; local foundation does not perform analysis."


class APIError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status = status
        self.code = code
        self.message = message
        super().__init__(message)


class RecordingRegistration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    client_recording_id: UUID
    synthetic: Literal[True]
    captured_at: datetime
    site_id: str = Field(min_length=1, max_length=100)
    track: str = Field(min_length=1, max_length=100)
    direction: str = Field(min_length=1, max_length=100)
    train_number: str | None = Field(default=None, max_length=100)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(gt=0, le=MAX_AUDIO_BYTES, strict=True)
    frame_count: int = Field(gt=0, le=MAX_FRAMES, strict=True)
    sample_rate: Literal[48000] = 48000
    channels: Literal[1] = 1
    sample_width_bits: Literal[16] = 16

    @field_validator("synthetic", mode="before")
    @classmethod
    def synthetic_only(cls, value: object) -> bool:
        if value is not True:
            raise ValueError("Only explicitly synthetic recordings are permitted")
        return True

    @field_validator("captured_at")
    @classmethod
    def timezone_required(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("captured_at must include a timezone")
        return value


class Recording(BaseModel):
    id: UUID
    metadata: RecordingRegistration
    created_at: datetime
    status: Literal["registered", "uploaded", "completed"]
    analysis_status: Literal["not_requested", "unavailable"]
    analysis_reason: str | None


class RecordingPage(BaseModel):
    items: list[Recording]
    limit: int
    offset: int
    total: int
