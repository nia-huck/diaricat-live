"""FastAPI application for Diaricat Live."""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from sse_starlette.sse import EventSourceResponse

from diaricat_live.core.live_engine import LiveEngine
from diaricat_live.models.stream import (
    SessionState,
    StartStreamRequest,
    StartStreamResponse,
    StopStreamRequest,
    StreamStatusResponse,
)
from diaricat_live.settings import Settings

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    if settings is None:
        settings = Settings()

    engine = LiveEngine(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logger.info("Diaricat Live starting on %s:%s", settings.host, settings.port)
        yield
        # Shutdown: stop all active sessions
        for sid in list(engine.sessions):
            try:
                await engine.stop_session(sid)
            except Exception:
                logger.exception("Error stopping session %s on shutdown", sid)
        logger.info("Diaricat Live stopped")

    app = FastAPI(
        title="Diaricat Live",
        version="0.1.0",
        description="Real-time audio stream transcription and alert service",
        lifespan=lifespan,
    )

    # -- Middleware --
    app.add_middleware(GZipMiddleware, minimum_size=1000)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:*",
            "http://localhost:*",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # -- Routes --

    @app.get("/health")
    async def health():
        return {"status": "ok", "service": "diaricat-live"}

    @app.post("/v1/stream/start", response_model=StartStreamResponse)
    async def start_stream(req: StartStreamRequest):
        try:
            session_id = await engine.start_session(
                url=req.url,
                language=req.language,
                extra_keywords=req.keywords,
            )
            return StartStreamResponse(
                session_id=session_id,
                state=SessionState.starting,
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=429, detail=str(exc))

    @app.post("/v1/stream/stop")
    async def stop_stream(req: StopStreamRequest):
        try:
            await engine.stop_session(req.session_id)
            return {"status": "stopped", "session_id": req.session_id}
        except KeyError:
            raise HTTPException(status_code=404, detail="Session not found")

    @app.get("/v1/stream/status", response_model=StreamStatusResponse)
    async def stream_status():
        return StreamStatusResponse(sessions=engine.list_sessions())

    @app.get("/v1/stream/events/{session_id}")
    async def stream_events(session_id: str, request: Request):
        """SSE endpoint — Nyx Terminal connects here to receive live events."""
        if session_id not in engine.sessions:
            raise HTTPException(status_code=404, detail="Session not found")

        async def event_generator():
            try:
                async for event in engine.subscribe(session_id):
                    if await request.is_disconnected():
                        break
                    yield {
                        "event": event.type,
                        "data": event.model_dump_json(),
                    }
            except asyncio.CancelledError:
                pass

        return EventSourceResponse(event_generator())

    @app.get("/v1/stream/events")
    async def stream_all_events(request: Request):
        """SSE endpoint that merges events from ALL active sessions."""

        async def merged_generator():
            queues: dict[str, asyncio.Queue] = {}

            # Fan-in: create a shared queue
            merged = asyncio.Queue()

            async def forward(sid: str):
                try:
                    async for event in engine.subscribe(sid):
                        await merged.put(event)
                except Exception:
                    pass

            tasks = []
            for sid in engine.sessions:
                tasks.append(asyncio.create_task(forward(sid)))

            try:
                while not await request.is_disconnected():
                    try:
                        event = await asyncio.wait_for(merged.get(), timeout=1.0)
                        yield {
                            "event": event.type,
                            "data": event.model_dump_json(),
                        }
                    except asyncio.TimeoutError:
                        continue
            finally:
                for t in tasks:
                    t.cancel()

        return EventSourceResponse(merged_generator())

    return app
