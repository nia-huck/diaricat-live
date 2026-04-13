# Diaricat Live

Real-time audio stream transcription service. Captures audio from YouTube and internet streams, transcribes with Faster Whisper, and emits events via SSE.

Built as a companion to [Diaricat](https://github.com/nia-huck/Diaricat) — extending local transcription to live streams.

## How it works

```
YouTube/Stream URL
       │
   yt-dlp (resolves direct audio URL)
       │
   ffmpeg (stream → PCM 16kHz mono)
       │
   Faster Whisper (speech-to-text, chunked)
       │
   SSE events → your app
```

## Features

- **Live transcription** from any YouTube video or internet audio stream
- **Keyword detection** with configurable alert rules
- **SSE (Server-Sent Events)** for real-time consumption from any client
- **Multiple simultaneous streams** (configurable, default 3)
- **Automatic format fallback** for HLS streams (YouTube Live, etc.)
- **CUDA with CPU fallback** — auto-detects GPU availability

## Quick start

```bash
# Clone
git clone https://github.com/nia-huck/diaricat-live.git
cd diaricat-live

# Install
pip install -e .

# Configure (optional — defaults work out of the box)
cp .env.example .env
# Edit .env to set ffmpeg/yt-dlp paths if not in PATH

# Run
diaricat-live
# → Uvicorn running on http://127.0.0.1:8766
```

## Requirements

- Python 3.11+
- ffmpeg
- yt-dlp
- A Faster Whisper model (downloads automatically on first run)

## API

Base URL: `http://127.0.0.1:8766`

### Start a stream

```bash
curl -X POST http://127.0.0.1:8766/v1/stream/start \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.youtube.com/watch?v=VIDEO_ID", "language": "es"}'

# → {"session_id": "live-0001", "state": "starting"}
```

### Listen to events (SSE)

```bash
curl http://127.0.0.1:8766/v1/stream/events/live-0001
```

Events emitted:

| Event | Description |
|---|---|
| `transcript` | Finalized transcript segment with text, timestamps |
| `alert` | Keyword detected — includes keyword, context, urgency (1-10), category |
| `status` | Periodic heartbeat with session stats |
| `error` | Error notification |

Example SSE output:

```
event: transcript
data: {"type":"transcript","session_id":"live-0001","text":"The president announced new measures today","start":125.3,"end":130.1,"language":"es"}

event: alert
data: {"type":"alert","session_id":"live-0001","keyword":"measures","text":"...announced new measures today...","urgency":7,"sector":"custom"}
```

### Stop a stream

```bash
curl -X POST http://127.0.0.1:8766/v1/stream/stop \
  -H "Content-Type: application/json" \
  -d '{"session_id": "live-0001"}'
```

### Check active sessions

```bash
curl http://127.0.0.1:8766/v1/stream/status
```

### Health check

```bash
curl http://127.0.0.1:8766/health
```

## Frontend integration

```js
// Start a stream
const res = await fetch("http://127.0.0.1:8766/v1/stream/start", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ url: youtubeUrl, language: "es" }),
});
const { session_id } = await res.json();

// Listen to events
const es = new EventSource(
  `http://127.0.0.1:8766/v1/stream/events/${session_id}`
);

es.addEventListener("transcript", (e) => {
  const data = JSON.parse(e.data);
  console.log(data.text);
});

es.addEventListener("alert", (e) => {
  const data = JSON.parse(e.data);
  console.log(`ALERT: ${data.keyword} — ${data.text}`);
});
```

## Custom keywords

Keywords can be added per-request:

```json
{
  "url": "https://www.youtube.com/watch?v=...",
  "language": "es",
  "keywords": ["keyword1", "keyword2"]
}
```

Or via a `keywords.yaml` file (see `keywords.yaml.example`):

```yaml
keywords:
  - pattern: "breaking news"
    sector: "media"
    urgency: 8
```

## Configuration

All settings via environment variables (prefix `DIARICAT_LIVE_`):

| Variable | Default | Description |
|---|---|---|
| `HOST` | `127.0.0.1` | Server bind address |
| `PORT` | `8766` | Server port |
| `WHISPER_MODEL` | `large-v3` | Faster Whisper model |
| `WHISPER_DEVICE` | `auto` | `auto`, `cpu`, or `cuda` |
| `WHISPER_COMPUTE_TYPE` | `float16` | `float16`, `int8_float16`, `int8` |
| `LANGUAGE` | `es` | Default language hint |
| `BUFFER_DURATION_S` | `5.0` | Audio buffer window (seconds) |
| `BUFFER_OVERLAP_S` | `1.0` | Overlap between buffers |
| `MAX_SESSIONS` | `3` | Max simultaneous streams |
| `FFMPEG_PATH` | `ffmpeg` | Path to ffmpeg binary |
| `YTDLP_PATH` | `yt-dlp` | Path to yt-dlp binary |

## Project structure

```
src/diaricat_live/
├── api/app.py                  # FastAPI endpoints + SSE
├── core/live_engine.py         # Session manager, transcription loop
├── services/
│   ├── stream_capture.py       # yt-dlp + ffmpeg audio pipe
│   └── alert_service.py        # Configurable keyword scanner
├── models/stream.py            # Pydantic schemas
├── settings.py                 # Configuration via env vars
└── run.py                      # Entry point
```

## License

MIT
