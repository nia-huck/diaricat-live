"""Quick integration test for Diaricat Live.

Tests:
1. Health endpoint
2. Start a stream session (uses a short YouTube video)
3. Check status
4. Listen for a few events via SSE
5. Stop session

Usage:
    # First start the server: diaricat-live
    # Then run: python test_flow.py
    # Or with a specific URL: python test_flow.py "https://youtube.com/watch?v=..."
"""

from __future__ import annotations

import json
import sys
import time

import httpx

BASE = "http://127.0.0.1:8766"


def test_health():
    print("1. Health check...")
    r = httpx.get(f"{BASE}/health", timeout=5)
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    print("   OK\n")


def test_start(url: str) -> str:
    print(f"2. Starting stream: {url[:80]}...")
    r = httpx.post(
        f"{BASE}/v1/stream/start",
        json={"url": url, "language": "es"},
        timeout=60,
    )
    assert r.status_code == 200, f"Failed: {r.status_code} {r.text}"
    data = r.json()
    session_id = data["session_id"]
    print(f"   Session: {session_id}")
    print(f"   State: {data['state']}\n")
    return session_id


def test_status(expected_session: str):
    print("3. Checking status...")
    r = httpx.get(f"{BASE}/v1/stream/status", timeout=5)
    assert r.status_code == 200
    sessions = r.json()["sessions"]
    print(f"   Active sessions: {len(sessions)}")
    for s in sessions:
        print(f"   - {s['session_id']}: {s['state']} (uptime={s['uptime_s']:.0f}s)")
    assert any(s["session_id"] == expected_session for s in sessions)
    print()


def test_sse(session_id: str, max_events: int = 10, timeout_s: int = 60):
    print(f"4. Listening for SSE events (max {max_events}, timeout {timeout_s}s)...")
    url = f"{BASE}/v1/stream/events/{session_id}"
    count = 0

    try:
        with httpx.stream("GET", url, timeout=httpx.Timeout(timeout_s, connect=10)) as r:
            event_type = ""
            for line in r.iter_lines():
                if line.startswith("event:"):
                    event_type = line[6:].strip()
                elif line.startswith("data:"):
                    data = json.loads(line[5:].strip())
                    count += 1
                    etype = data.get("type", event_type)

                    if etype == "transcript":
                        print(f"   [{count}] TRANSCRIPT: {data.get('text', '')[:100]}")
                    elif etype == "alert":
                        print(
                            f"   [{count}] ALERT! [{data.get('sector')}] "
                            f"u={data.get('urgency')} {data.get('keyword')}: "
                            f"{data.get('text', '')[:80]}"
                        )
                    elif etype == "status":
                        print(f"   [{count}] STATUS: segments={data.get('segments_count')}")
                    elif etype == "error":
                        print(f"   [{count}] ERROR: {data.get('message')}")

                    if count >= max_events:
                        break
    except httpx.ReadTimeout:
        print(f"   Timeout after {timeout_s}s")

    print(f"   Received {count} events\n")
    return count


def test_stop(session_id: str):
    print(f"5. Stopping session {session_id}...")
    r = httpx.post(
        f"{BASE}/v1/stream/stop",
        json={"session_id": session_id},
        timeout=10,
    )
    assert r.status_code == 200
    print(f"   {r.json()}\n")


def main():
    # Default test URL — a 24/7 news stream (C5N)
    url = sys.argv[1] if len(sys.argv) > 1 else "https://www.youtube.com/watch?v=jD1YCfKsJUQ"

    print("=" * 50)
    print("  Diaricat Live — Integration Test")
    print("=" * 50 + "\n")

    test_health()
    session_id = test_start(url)

    # Give it a few seconds to start capturing
    print("   Esperando 10s para que arranque la captura...\n")
    time.sleep(10)

    test_status(session_id)
    events = test_sse(session_id, max_events=5, timeout_s=120)
    test_stop(session_id)

    print("=" * 50)
    if events > 0:
        print("  PASSED — Pipeline funciona end-to-end")
    else:
        print("  PARTIAL — Server OK pero no se recibieron eventos")
        print("  (puede ser que el video no tenga audio o Whisper tarde en cargar)")
    print("=" * 50)


if __name__ == "__main__":
    main()
