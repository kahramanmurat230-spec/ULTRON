"""Automatic validation suite. Real subprocess checks, streamed per-step."""
import asyncio
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

# server.py imports this module before constructing its global Hub. Install the
# runtime guard at that point so a failed optional V16 boot cannot crash V15.
from app.core.runtime_degradation import install_bridge_guard

install_bridge_guard()

BASE = Path(__file__).resolve().parent
ROOT = BASE.parent
FRONTEND = ROOT / "frontend"
BACKEND = BASE


async def _run(name: str, argv: list, cwd: Path, timeout: int, emit) -> dict:
    await emit(name, "RUNNING", "")
    try:
        p = await asyncio.to_thread(
            subprocess.run, argv, cwd=str(cwd), capture_output=True, text=True, timeout=timeout
        )
        ok = p.returncode == 0
        detail = (p.stdout or p.stderr).strip()[-300:]
    except subprocess.TimeoutExpired:
        ok, detail = False, f"timeout {timeout}s"
    except Exception as e:  # noqa: BLE001
        ok, detail = False, str(e)
    await emit(name, "SUCCESS" if ok else "ERROR", detail)
    return {"name": name, "ok": ok, "detail": detail}


async def run_all(emit, quick: bool = False) -> list:
    results = []
    results.append(await _run("TypeScript", ["npx", "tsc", "--noEmit"], FRONTEND, 240, emit))
    if not quick:
        results.append(await _run("Build", ["npm", "run", "build"], FRONTEND, 420, emit))
    results.append(await _run("PythonSyntax", [sys.executable, "-m", "compileall", "-q", "."], BACKEND, 120, emit))

    def _http(url: str, expect):
        with urllib.request.urlopen(url, timeout=5) as r:
            data = json.loads(r.read().decode())
        if not expect(data):
            raise ValueError("unexpected payload")

    async def http_check(name: str, url: str, expect) -> None:
        await emit(name, "RUNNING", "")
        try:
            # run in a thread: a blocking urlopen on the event loop would
            # deadlock the very server we are testing
            await asyncio.to_thread(_http, url, expect)
            ok, detail = True, ""
        except Exception as e:  # noqa: BLE001
            ok, detail = False, str(e)
        await emit(name, "SUCCESS" if ok else "ERROR", detail)
        results.append({"name": name, "ok": ok, "detail": detail})

    await http_check("BackendREST", "http://127.0.0.1:8000/api/system", lambda d: "cpu" in d)
    await http_check("Tools", "http://127.0.0.1:8000/api/tools", lambda d: isinstance(d, list) and len(d) >= 6)
    await http_check("Memory", "http://127.0.0.1:8000/api/memory", lambda d: "session_count" in d)
    await http_check("Telemetry", "http://127.0.0.1:8000/api/system", lambda d: d.get("ram", {}).get("percent") is not None)

    # websocket hello
    await emit("WebSocket", "RUNNING", "")
    try:
        import aiohttp
        async with aiohttp.ClientSession() as s:
            async with s.ws_connect("http://127.0.0.1:8000/ws", timeout=5) as ws:
                msg = await asyncio.wait_for(ws.receive(), 6)
                d = json.loads(msg.data)
                ok = d.get("type") == "hello"
        detail = "" if ok else "no hello"
    except Exception as e:  # noqa: BLE001
        ok, detail = False, str(e)
    await emit("WebSocket", "SUCCESS" if ok else "ERROR", detail)
    results.append({"name": "WebSocket", "ok": ok, "detail": detail})
    return results
