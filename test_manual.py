"""Manual step-by-step test of the pipeline components."""

import asyncio
import sys
import wave
import tempfile
from pathlib import Path

# Adjust these paths
YTDLP = r"C:\Users\Niahu\AppData\Local\Python\pythoncore-3.14-64\Scripts\yt-dlp.exe"
FFMPEG = r"C:\Users\Niahu\OneDrive\Desktop\Diaricat\vendor\ffmpeg\ffmpeg.exe"
URL = sys.argv[1] if len(sys.argv) > 1 else "https://www.youtube.com/watch?v=cb12KmMMDJA"


async def step1_resolve():
    """yt-dlp: resolve audio URL."""
    print(f"STEP 1: Resolving URL with yt-dlp...")
    print(f"  URL: {URL}")

    for fmt in ["bestaudio", "bestaudio/best", "best"]:
        proc = await asyncio.create_subprocess_exec(
            YTDLP, "--no-download", "-f", fmt,
            "--print", "urls", "--no-warnings", "--no-playlist", URL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode == 0:
            audio_url = stdout.decode().strip().splitlines()[0]
            print(f"  OK with fmt={fmt}")
            print(f"  Audio URL length: {len(audio_url)} chars")
            return audio_url
        else:
            print(f"  fmt={fmt} FAILED: {stderr.decode()[:200]}")

    raise RuntimeError("All formats failed")


async def step2_ffmpeg(audio_url: str):
    """ffmpeg: capture 10 seconds of PCM audio."""
    print(f"\nSTEP 2: Capturing audio with ffmpeg (10s)...")
    proc = await asyncio.create_subprocess_exec(
        FFMPEG,
        "-reconnect", "1",
        "-reconnect_streamed", "1",
        "-reconnect_delay_max", "5",
        "-i", audio_url,
        "-f", "s16le",
        "-ar", "16000",
        "-ac", "1",
        "-t", "10",  # only 10 seconds for test
        "-v", "quiet",
        "pipe:1",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
    print(f"  ffmpeg returncode: {proc.returncode}")
    if stderr:
        print(f"  stderr: {stderr.decode()[:300]}")
    print(f"  Audio bytes: {len(stdout)}")
    print(f"  Duration: {len(stdout) / (16000 * 2):.1f}s")
    return stdout


async def step3_whisper(pcm_data: bytes):
    """Whisper: transcribe the captured audio."""
    print(f"\nSTEP 3: Loading Whisper model...")
    from faster_whisper import WhisperModel

    try:
        model = WhisperModel("large-v3", device="cuda", compute_type="float16")
        print("  Loaded on CUDA")
    except Exception as e:
        print(f"  CUDA failed ({e}), trying CPU...")
        model = WhisperModel("large-v3", device="cpu", compute_type="int8")
        print("  Loaded on CPU")

    # Write to temp WAV
    tmp = Path(tempfile.mktemp(suffix=".wav"))
    with wave.open(str(tmp), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(pcm_data)

    print(f"  Transcribing {tmp} ({tmp.stat().st_size} bytes)...")
    segments, info = model.transcribe(str(tmp), language="es", vad_filter=True)

    print(f"  Language: {info.language} (prob={info.language_probability:.2f})")
    print(f"  Duration: {info.duration:.1f}s")
    print()

    for seg in segments:
        print(f"  [{seg.start:.1f}s - {seg.end:.1f}s] {seg.text.strip()}")

    tmp.unlink(missing_ok=True)


async def main():
    audio_url = await step1_resolve()
    pcm_data = await step2_ffmpeg(audio_url)

    if len(pcm_data) < 1000:
        print("\n  ERROR: Not enough audio data captured")
        return

    await step3_whisper(pcm_data)
    print("\n  ALL STEPS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
