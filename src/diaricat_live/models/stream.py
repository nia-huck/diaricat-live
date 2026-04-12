"""Pydantic schemas for the live streaming API."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------

class StartStreamRequest(BaseModel):
    """Body for POST /v1/stream/start."""

    url: str = Field(..., description="YouTube or internet stream URL")
    language: str = Field("es", description="Whisper language hint (ISO 639-1)")
    keywords: list[str] | None = Field(
        None,
        description="Extra keywords to watch for (merged with default set)",
    )


class StopStreamRequest(BaseModel):
    """Body for POST /v1/stream/stop."""

    session_id: str


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------

class SessionState(str, Enum):
    starting = "starting"
    resolving = "resolving"      # yt-dlp extracting audio URL
    capturing = "capturing"      # ffmpeg streaming audio
    listening = "listening"      # whisper active, producing transcripts
    stopping = "stopping"
    stopped = "stopped"
    error = "error"


class SessionInfo(BaseModel):
    session_id: str
    url: str
    state: SessionState
    started_at: datetime
    uptime_s: float = 0.0
    segments_count: int = 0
    alerts_count: int = 0


# ---------------------------------------------------------------------------
# SSE Events
# ---------------------------------------------------------------------------

class TranscriptEvent(BaseModel):
    """A finalized transcript segment."""

    type: Literal["transcript"] = "transcript"
    session_id: str
    text: str
    start: float = Field(..., description="Offset in seconds from stream start")
    end: float
    language: str = "es"
    ts: datetime = Field(default_factory=datetime.now)


class PartialEvent(BaseModel):
    """Partial (in-progress) transcript, updated as audio comes in."""

    type: Literal["partial"] = "partial"
    session_id: str
    text: str
    ts: datetime = Field(default_factory=datetime.now)


class AlertEvent(BaseModel):
    """Alert triggered by keyword detection in transcript."""

    type: Literal["alert"] = "alert"
    session_id: str
    keyword: str
    text: str = Field(..., description="Surrounding context where keyword was found")
    urgency: int = Field(..., ge=1, le=10)
    sector: str
    ts: datetime = Field(default_factory=datetime.now)


class StatusEvent(BaseModel):
    """Periodic heartbeat with session stats."""

    type: Literal["status"] = "status"
    session_id: str
    state: SessionState
    uptime_s: float
    segments_count: int
    alerts_count: int
    ts: datetime = Field(default_factory=datetime.now)


class ErrorEvent(BaseModel):
    """Error notification."""

    type: Literal["error"] = "error"
    session_id: str
    message: str
    ts: datetime = Field(default_factory=datetime.now)


# Union type for all events emitted via SSE
StreamEvent = TranscriptEvent | PartialEvent | AlertEvent | StatusEvent | ErrorEvent


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------

class StartStreamResponse(BaseModel):
    session_id: str
    state: SessionState = SessionState.starting


class StreamStatusResponse(BaseModel):
    sessions: list[SessionInfo]
