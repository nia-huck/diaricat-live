"""Quick test: yt-dlp → ffmpeg → whisper CPU, 10s of audio."""

import asyncio
import wave
import tempfile
from pathlib import Path

YTDLP = r"C:\Users\Niahu\AppData\Local\Python\pythoncore-3.14-64\Scripts\yt-dlp.exe"
FFMPEG = r"C:\Users\Niahu\OneDrive\Desktop\Diaricat\vendor\ffmpeg\ffmpeg.exe"
URL = "https://www.youtube.com/watch?v=cb12KmMMDJA"


async def main():
    # Step 1: resolve
    print("1. yt-dlp resolving...")
    proc = await asyncio.create_subprocess_exec(
        YTDLP, "--no-download", "-f", "bestaudio/best",
        "--print", "urls", "--no-warnings", "--no-playlist", URL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    audio_url = stdout.decode().strip().splitlines()[0]
    print(f"   OK ({len(audio_url)} chars)")

    # Step 2: ffmpeg 10s
    print("2. ffmpeg capturing 10s...")
    proc = await asyncio.create_subprocess_exec(
        FFMPEG, "-reconnect", "1", "-reconnect_streamed", "1",
        "-reconnect_delay_max", "5", "-i", audio_url,
        "-f", "s16le", "-ar", "16000", "-ac", "1", "-t", "10",
        "-v", "quiet", "pipe:1",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
    print(f"   OK ({len(stdout)} bytes = {len(stdout)/32000:.1f}s)")

    # Step 3: whisper CPU
    print("3. Whisper (CPU, int8)...")
    from faster_whisper import WhisperModel
    model = WhisperModel("large-v3", device="cpu", compute_type="int8")
    print("   Model loaded")

    tmp = Path(tempfile.mktemp(suffix=".wav"))
    with wave.open(str(tmp), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(stdout)

    segments, info = model.transcribe(str(tmp), language="es", vad_filter=True)
    print(f"   Lang: {info.language} (p={info.language_probability:.2f})")

    for seg in segments:
        print(f"   [{seg.start:.1f}s-{seg.end:.1f}s] {seg.text.strip()}")

    tmp.unlink(missing_ok=True)
    print("\nDONE")


asyncio.run(main())
