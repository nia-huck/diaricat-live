"""Configuration for Diaricat Live."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DIARICAT_LIVE_",
        env_file=".env",
        env_file_encoding="utf-8",
    )

    # --- Server ---
    host: str = "127.0.0.1"
    port: int = 8766

    # --- Whisper ---
    whisper_model: str = Field("large-v3", description="Faster Whisper model name")
    whisper_device: str = Field("auto", description="auto | cpu | cuda")
    whisper_compute_type: str = Field("float16", description="float16 | int8_float16 | int8")
    language: str = Field("es", description="Default language hint for Whisper")

    # --- Stream capture ---
    buffer_duration_s: float = Field(5.0, description="Audio buffer window in seconds")
    buffer_overlap_s: float = Field(1.0, description="Overlap between consecutive buffers")
    ffmpeg_path: str = Field("ffmpeg", description="Path to ffmpeg binary")
    ytdlp_path: str = Field("yt-dlp", description="Path to yt-dlp binary")

    # --- Alerts ---
    alert_keywords_file: Path | None = Field(
        None,
        description="YAML file with extra keyword definitions",
    )

    # --- Misc ---
    max_sessions: int = Field(3, description="Max simultaneous stream sessions")
    heartbeat_interval_s: float = Field(30.0, description="Seconds between status heartbeats")
