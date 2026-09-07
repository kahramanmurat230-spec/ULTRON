from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "backend" / "server.py"

text = SERVER.read_text(encoding="utf-8")

if 'app.router.add_post("/api/voice/ptt", api_voice_ptt)' in text:
    print("PTT endpoint already installed.")
    raise SystemExit(0)

marker = 'async def api_voice_live(req: web.Request) -> web.Response:\n'
if marker not in text:
    raise SystemExit("Could not find api_voice_live marker; server.py was not changed.")

handler = '''async def api_voice_ptt(req: web.Request) -> web.Response:\n    try:\n        body = await req.json()\n    except Exception:\n        body = {}\n    try:\n        seconds = max(1.0, min(float(body.get("seconds", 6.0)), 15.0))\n    except (TypeError, ValueError):\n        seconds = 6.0\n    if not (hub.bridge and hub.bridge.available):\n        return web.json_response({"ok": False, "error": "V16 runtime unavailable"}, status=503)\n    import importlib.util as _iu\n    if not (_iu.find_spec("sounddevice") and _iu.find_spec("faster_whisper")):\n        return web.json_response({"ok": False, "error": "sounddevice + faster-whisper not installed"}, status=503)\n    if getattr(hub, "ptt_voice", None) is None:\n        from app.voice.push_to_talk import PushToTalkVoice\n        hub.ptt_voice = PushToTalkVoice(\n            hub.bridge.runtime.agent,\n            hub.bridge.runtime.tts,\n            hub.bridge.runtime.settings,\n            stack=hub.bridge.voice_stack,\n        )\n    result = await asyncio.to_thread(hub.ptt_voice.listen_once, seconds)\n    if result.get("status") == "busy":\n        return web.json_response({"ok": False, **result}, status=409)\n    if result.get("status") != "ok":\n        return web.json_response({"ok": False, **result}, status=422)\n    await hub.on_activity(f"PTT: {result.get('text', '')[:100]}", "success")\n    return web.json_response({"ok": True, **result})\n\n\n'''

text = text.replace(marker, handler + marker, 1)

route_marker = '    app.router.add_post("/api/voice/live", api_voice_live)\n'
if route_marker not in text:
    raise SystemExit("Could not find voice/live route marker; server.py was not changed.")
text = text.replace(
    route_marker,
    '    app.router.add_post("/api/voice/ptt", api_voice_ptt)\n' + route_marker,
    1,
)

SERVER.write_text(text, encoding="utf-8")
print("PTT endpoint installed in backend/server.py")
print("Route: POST /api/voice/ptt")
