"""Capture audio from YouTube / internet streams via yt-dlp + ffmpeg."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field

from diaricat_live.settings import Settings

logger = logging.getLogger(__name__)

# PCM 16-bit mono @ 16 kHz  →  32 000 bytes per second
SAMPLE_RATE = 16_000
BYTES_PER_SAMPLE = 2
BYTES_PER_SECOND = SAMPLE_RATE * BYTES_PER_SAMPLE


@dataclass
class CaptureHandle:
    """Holds references to the running yt-dlp and ffmpeg subprocesses."""

    url: str
    ytdlp_proc: asyncio.subprocess.Process | None = None
    ffmpeg_proc: asyncio.subprocess.Process | None = None
    audio_stream_url: str = ""
    _stopped: bool = False

    async def stop(self) -> None:
        self._stopped = True
        for proc in (self.ffmpeg_proc, self.ytdlp_proc):
            if proc and proc.returncode is None:
                try:
                    proc.terminate()
                    await asyncio.wait_for(proc.wait(), timeout=5)
                except (ProcessLookupError, asyncio.TimeoutError):
                    proc.kill()

    @property
    def stopped(self) -> bool:
        return self._stopped


class StreamCaptureService:
    """Resolves a stream URL via yt-dlp and pipes audio through ffmpeg as PCM chunks."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def start(self, url: str) -> CaptureHandle:
        """Resolve the audio URL and prepare ffmpeg for streaming."""
        handle = CaptureHandle(url=url)

        # Step 1: yt-dlp extracts the direct audio URL
        handle.audio_stream_url = await self._resolve_audio_url(url, handle)

        # Step 2: launch ffmpeg reading from that URL → PCM stdout
        handle.ffmpeg_proc = await self._start_ffmpeg(handle.audio_stream_url)

        logger.info("Capture started for %s", url)
        return handle

    async def read_chunks(
        self,
        handle: CaptureHandle,
        chunk_duration_s: float = 5.0,
    ):
        """Async generator that yields PCM audio chunks from the stream.

        Each chunk is `chunk_duration_s` seconds of raw PCM s16le @ 16kHz mono.
        """
        chunk_size = int(chunk_duration_s * BYTES_PER_SECOND)
        stdout = handle.ffmpeg_proc.stdout
        assert stdout is not None

        buffer = bytearray()
        while not handle.stopped:
            try:
                data = await asyncio.wait_for(stdout.read(4096), timeout=30)
            except asyncio.TimeoutError:
                # Stream may have stalled — yield what we have
                if buffer:
                    yield bytes(buffer)
                    buffer.clear()
                continue

            if not data:
                # Stream ended
                break

            buffer.extend(data)

            while len(buffer) >= chunk_size:
                yield bytes(buffer[:chunk_size])
                del buffer[:chunk_size]

        # Flush remaining audio
        if buffer:
            yield bytes(buffer)

        logger.info("Capture stream ended for %s", handle.url)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _resolve_audio_url(self, url: str, handle: CaptureHandle) -> str:
        """Use yt-dlp to extract the best audio stream URL.

        Tries 'bestaudio' first, falls back to 'bestaudio/best' for HLS
        streams that only have combined video+audio formats.
        """
        format_specs = ["bestaudio", "bestaudio/best", "best"]

        for fmt in format_specs:
            cmd = [
                self.settings.ytdlp_path,
                "--no-download",
                "-f", fmt,
                "--print", "urls",
                "--no-warnings",
                "--no-playlist",
                url,
            ]
            logger.debug("Resolving audio URL (fmt=%s): %s", fmt, " ".join(cmd[:6]))

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            handle.ytdlp_proc = proc
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)

            if proc.returncode == 0:
                audio_url = stdout.decode().strip().splitlines()[0]
                logger.info("Resolved audio URL with fmt=%s (%d chars)", fmt, len(audio_url))
                return audio_url

            logger.debug("fmt=%s failed: %s", fmt, stderr.decode(errors="replace").strip()[:200])

        raise RuntimeError(
            f"yt-dlp could not resolve any audio format for: {url}"
        )

    async def _start_ffmpeg(self, audio_url: str) -> asyncio.subprocess.Process:
        """Launch ffmpeg to read the audio stream and output raw PCM to stdout."""
        cmd = [
            self.settings.ffmpeg_path,
            "-reconnect", "1",
            "-reconnect_streamed", "1",
            "-reconnect_delay_max", "5",
            "-i", audio_url,
            "-f", "s16le",
            "-ar", str(SAMPLE_RATE),
            "-ac", "1",
            "-v", "quiet",
            "pipe:1",
        ]
        logger.debug("Starting ffmpeg: %s", " ".join(cmd[:6]) + " ...")

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        return proc
