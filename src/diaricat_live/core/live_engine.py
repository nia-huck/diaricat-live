"""LiveEngine — coordinates stream capture, transcription, and alert detection."""

from __future__ import annotations

import asyncio
import io
import logging
import tempfile
import wave
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import AsyncIterator

from faster_whisper import WhisperModel

from diaricat_live.models.stream import (
    AlertEvent,
    ErrorEvent,
    PartialEvent,
    SessionInfo,
    SessionState,
    StatusEvent,
    StreamEvent,
    TranscriptEvent,
)
from diaricat_live.services.alert_service import AlertService
from diaricat_live.services.stream_capture import (
    BYTES_PER_SECOND,
    SAMPLE_RATE,
    CaptureHandle,
    StreamCaptureService,
)
from diaricat_live.settings import Settings

logger = logging.getLogger(__name__)


@dataclass
class LiveSession:
    """State for a single active stream session."""

    id: str
    url: str
    state: SessionState = SessionState.starting
    started_at: datetime = field(default_factory=datetime.now)
    segments_count: int = 0
    alerts_count: int = 0
    event_bus: asyncio.Queue[StreamEvent] = field(default_factory=asyncio.Queue)
    capture_handle: CaptureHandle | None = None
    _task: asyncio.Task | None = None
    language: str = "es"

    @property
    def uptime_s(self) -> float:
        return (datetime.now() - self.started_at).total_seconds()

    def info(self) -> SessionInfo:
        return SessionInfo(
            session_id=self.id,
            url=self.url,
            state=self.state,
            started_at=self.started_at,
            uptime_s=self.uptime_s,
            segments_count=self.segments_count,
            alerts_count=self.alerts_count,
        )


class LiveEngine:
    """Manages live stream sessions: capture → transcribe → alert → emit events."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.sessions: dict[str, LiveSession] = {}

        self._capture_service = StreamCaptureService(settings)
        self._alert_service = AlertService(settings.alert_keywords_file)
        self._whisper_model: WhisperModel | None = None
        self._model_lock = asyncio.Lock()
        self._counter = 0

    # ------------------------------------------------------------------
    # Whisper model (lazy-loaded, shared across sessions)
    # ------------------------------------------------------------------

    def _load_model_sync(self) -> WhisperModel:
        """Synchronous model loading — runs in executor to avoid blocking."""
        device = self.settings.whisper_device
        compute = self.settings.whisper_compute_type

        if device == "auto":
            device = "cuda"

        logger.info(
            "Loading Whisper model=%s device=%s compute=%s",
            self.settings.whisper_model, device, compute,
        )

        loaded = False
        if device != "cpu":
            try:
                import numpy as np

                model = WhisperModel(
                    self.settings.whisper_model,
                    device=device,
                    compute_type=compute,
                )
                # Validate CUDA actually works with a tiny encode test
                test_audio = np.zeros(16000, dtype=np.float32)
                features = model.feature_extractor(test_audio)
                model.model.encode(features)
                self._whisper_model = model
                loaded = True
                logger.info("Whisper model loaded on %s", device)
            except Exception as exc:
                logger.warning("CUDA runtime failed (%s), falling back to CPU", exc)

        if not loaded:
            self._whisper_model = WhisperModel(
                self.settings.whisper_model,
                device="cpu",
                compute_type="int8",
            )
            logger.info("Whisper model loaded on CPU (int8)")

        return self._whisper_model

    async def _get_model(self) -> WhisperModel:
        if self._whisper_model is not None:
            return self._whisper_model

        async with self._model_lock:
            if self._whisper_model is not None:
                return self._whisper_model

            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, self._load_model_sync)

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    async def start_session(
        self,
        url: str,
        language: str = "es",
        extra_keywords: list[str] | None = None,
    ) -> str:
        if len(self.sessions) >= self.settings.max_sessions:
            raise RuntimeError(
                f"Max sessions ({self.settings.max_sessions}) reached. Stop one first."
            )

        self._counter += 1
        session_id = f"live-{self._counter:04d}"

        session = LiveSession(id=session_id, url=url, language=language)
        self.sessions[session_id] = session

        if extra_keywords:
            self._alert_service.add_keywords(extra_keywords)

        # Pre-load whisper model before starting the loop
        await self._get_model()

        session._task = asyncio.create_task(
            self._session_loop(session),
            name=f"live-{session_id}",
        )
        return session_id

    async def stop_session(self, session_id: str) -> None:
        session = self.sessions.get(session_id)
        if not session:
            raise KeyError(f"Session {session_id} not found")

        session.state = SessionState.stopping
        if session.capture_handle:
            await session.capture_handle.stop()
        if session._task and not session._task.done():
            session._task.cancel()
            try:
                await session._task
            except asyncio.CancelledError:
                pass

        session.state = SessionState.stopped
        logger.info("Session %s stopped", session_id)

    async def subscribe(self, session_id: str) -> AsyncIterator[StreamEvent]:
        """Yield events from a session's event bus. Used by SSE endpoint."""
        session = self.sessions.get(session_id)
        if not session:
            raise KeyError(f"Session {session_id} not found")

        while session.state not in (SessionState.stopped, SessionState.error):
            try:
                event = await asyncio.wait_for(session.event_bus.get(), timeout=1.0)
                yield event
            except asyncio.TimeoutError:
                continue

    def list_sessions(self) -> list[SessionInfo]:
        return [s.info() for s in self.sessions.values()]

    # ------------------------------------------------------------------
    # Main session loop
    # ------------------------------------------------------------------

    async def _session_loop(self, session: LiveSession) -> None:
        try:
            # Phase 1: resolve stream URL
            session.state = SessionState.resolving
            handle = await self._capture_service.start(session.url)
            session.capture_handle = handle

            # Phase 2: start capture
            session.state = SessionState.capturing

            # Phase 3: transcription loop
            session.state = SessionState.listening

            # Launch heartbeat
            heartbeat_task = asyncio.create_task(self._heartbeat_loop(session))

            # Overlap buffer for continuity between chunks
            overlap_bytes = int(self.settings.buffer_overlap_s * BYTES_PER_SECOND)
            prev_tail: bytes = b""
            stream_offset_s: float = 0.0

            async for chunk in self._capture_service.read_chunks(
                handle, self.settings.buffer_duration_s
            ):
                if session.state == SessionState.stopping:
                    break

                # Prepend overlap from previous chunk
                audio_data = prev_tail + chunk
                prev_tail = chunk[-overlap_bytes:] if len(chunk) > overlap_bytes else chunk

                # Transcribe
                segments = await self._transcribe_audio(audio_data, session.language)

                for text, seg_start, seg_end in segments:
                    abs_start = stream_offset_s + seg_start
                    abs_end = stream_offset_s + seg_end

                    # Emit transcript event
                    session.segments_count += 1
                    await session.event_bus.put(
                        TranscriptEvent(
                            session_id=session.id,
                            text=text,
                            start=round(abs_start, 2),
                            end=round(abs_end, 2),
                            language=session.language,
                        )
                    )

                    # Scan for alerts
                    alerts = self._alert_service.scan(text)
                    for alert in alerts:
                        session.alerts_count += 1
                        await session.event_bus.put(
                            AlertEvent(
                                session_id=session.id,
                                keyword=alert.keyword,
                                text=alert.context,
                                urgency=alert.urgency,
                                sector=alert.sector,
                            )
                        )

                # Advance offset (only by the new chunk, not the overlap)
                stream_offset_s += len(chunk) / BYTES_PER_SECOND

            heartbeat_task.cancel()

        except asyncio.CancelledError:
            logger.info("Session %s cancelled", session.id)
        except Exception as exc:
            logger.exception("Session %s error", session.id)
            session.state = SessionState.error
            await session.event_bus.put(
                ErrorEvent(session_id=session.id, message=str(exc))
            )
        finally:
            if session.capture_handle:
                await session.capture_handle.stop()
            if session.state not in (SessionState.stopped, SessionState.error):
                session.state = SessionState.stopped

    # ------------------------------------------------------------------
    # Transcription
    # ------------------------------------------------------------------

    async def _transcribe_audio(
        self,
        pcm_data: bytes,
        language: str,
    ) -> list[tuple[str, float, float]]:
        """Transcribe raw PCM audio and return list of (text, start_s, end_s)."""
        model = await self._get_model()

        # Write PCM to a temporary WAV file (Faster Whisper needs a file path)
        tmp = Path(tempfile.mktemp(suffix=".wav"))
        try:
            with wave.open(str(tmp), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(SAMPLE_RATE)
                wf.writeframes(pcm_data)

            # Run transcription in a thread to avoid blocking the event loop
            loop = asyncio.get_running_loop()
            segments_gen, _info = await loop.run_in_executor(
                None,
                lambda: model.transcribe(
                    str(tmp),
                    language=language,
                    vad_filter=True,
                    word_timestamps=False,
                ),
            )

            # Consume the generator in the executor too
            results = await loop.run_in_executor(
                None,
                lambda: [(s.text.strip(), s.start, s.end) for s in segments_gen if s.text.strip()],
            )
            return results

        finally:
            tmp.unlink(missing_ok=True)

    # ------------------------------------------------------------------
    # Heartbeat
    # ------------------------------------------------------------------

    async def _heartbeat_loop(self, session: LiveSession) -> None:
        try:
            while session.state == SessionState.listening:
                await asyncio.sleep(self.settings.heartbeat_interval_s)
                await session.event_bus.put(
                    StatusEvent(
                        session_id=session.id,
                        state=session.state,
                        uptime_s=round(session.uptime_s, 1),
                        segments_count=session.segments_count,
                        alerts_count=session.alerts_count,
                    )
                )
        except asyncio.CancelledError:
            pass
