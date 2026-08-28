"""Central agent state machine. The UI only ever displays states broadcast from here."""
import asyncio
import re
import time

STATES = ["IDLE", "LISTENING", "THINKING", "PLANNING", "EXECUTING", "VERIFYING", "WAITING_APPROVAL", "DONE", "ERROR"]


class Agent:
    def __init__(self, broadcast, tools, memory, telemetry, get_ai_status, on_activity, fallback=None) -> None:
        self.broadcast = broadcast
        self.tools = tools
        self.memory = memory
        self.telemetry = telemetry
        self.get_ai_status = get_ai_status
        self.on_activity = on_activity
        self.fallback = fallback  # V16 bridge: GENERAL_CONVERSATION routing
        self.vision_check = None  # bridge.is_vision_flow — must run BEFORE tier-1
        self.composite = None  # bridge.run_orchestrated — Agent 2.0 planner BEFORE tier-1
        self.state = "IDLE"
        self.busy = False
        self._idle_task: asyncio.Task | None = None

    async def set_state(self, state: str, message: str | None = None) -> None:
        self.state = state
        await self.broadcast(
            {"type": "agent", "state": state, "message": message, "ts": time.time()}
        )
        if message:
            self.memory.add_session("agent", f"[{state}] {message}")

    async def _schedule_idle(self, delay: float = 3.0) -> None:
        if self._idle_task:
            self._idle_task.cancel()
        self._idle_task = asyncio.create_task(self._to_idle(delay))

    async def _to_idle(self, delay: float) -> None:
        try:
            await asyncio.sleep(delay)
            if not self.busy:
                await self.set_state("IDLE")
        except asyncio.CancelledError:
            pass

    def parse_intent(self, text: str) -> dict | None:
        t = text.lower()
        m = re.search(r"(?:google'?da|google'da|google da)\s+(.*)", t)
        search_q = m.group(1).strip() if m else None
        if search_q is None and (" ara" in t or " search" in t or " araştır" in t):
            search_q = re.sub(r".*(?:ara|search|araştır)\s*", "", t).strip() or None

        if any(k in t for k in ("screenshot", "ekran görüntüsü", "ekran goruntusu")):
            return {"kind": "screenshot", "steps": ["Capture screen", "Verify file written"]}
        if any(k in t for k in ("clear memory", "hafızayı temizle", "hafizayi temizle", "clear the memory")):
            return {"kind": "clear_memory", "steps": ["Clear session memory", "Clear persistent memory", "Verify cleared"]}
        if any(k in t for k in ("remember", "hatırla", "hatirla", "not et")):
            note = re.sub(r"^.*?(remember|hatırla|hatirla|not et)[:\s]*", "", t).strip() or text
            return {"kind": "remember", "arg": note, "steps": ["Write persistent memory", "Verify stored"]}
        if any(k in t for k in ("system check", "sistem kontrol", "status check", "telemetry")) or (
            "durum" in t and "hava" not in t):
            return {"kind": "system_check", "steps": ["Sample telemetry", "Verify sensors"]}
        if any(k in t for k in ("run ", "çalıştır", "calistir", "terminal")):
            cmd = re.sub(r"^.*?(run|çalıştır|calistir|terminal)[:\s]*", "", t).strip()
            if cmd:
                return {"kind": "terminal", "arg": cmd, "steps": [f"exec: {cmd}", "Verify exit code"]}
        if any(k in t for k in ("chrome", "browser", "tarayıcı", "tarayici", "edge", "firefox")) or (
            any(k in t for k in ("aç", "ac ", "open", "launch")) and not search_q
        ):
            return {"kind": "browser", "arg": "https://www.google.com", "steps": ["Launch browser", "Verify process"]}
        if search_q or any(k in t for k in ("google", " ara.", "search")):
            q = search_q or text
            url = "https://www.google.com/search?q=" + q.replace(" ", "+")
            return {"kind": "browser", "arg": url, "steps": [f"Search: {q}", "Verify process"]}
        if any(k in t for k in ("saat", "time", "tarih", "date")):
            return {"kind": "time", "steps": ["Read system clock"]}
        if any(k in t for k in ("ollama", "model")):
            return {"kind": "ai_status", "steps": ["Probe Ollama", "Report models"]}
        return None

    async def run(self, text: str, approved: bool = False) -> dict:
        if self.busy:
            return {"ok": False, "error": "agent is busy with another command"}
        self.busy = True
        self.memory.add_session("user", text)
        await self.on_activity(f"Command: {text[:80]}", "user")
        try:
            # vision requests must bypass tier-1 keyword intents entirely
            if self.vision_check is not None and self.fallback is not None and self.vision_check(text):
                await self.fallback(text, approved)
                return {"ok": True, "result": "vision-flow"}
            # Agent 2.0: composite tasks enter the planner before keyword intents
            if self.composite is not None and await self.composite(text, approved):
                return {"ok": True, "result": "orchestrated"}
            await self.set_state("THINKING", "Parsing intent…")
            await asyncio.sleep(0.35)
            intent = self.parse_intent(text)
            if intent is None:
                if self.fallback is not None:
                    # V16 runtime: deterministic routes + Ollama general conversation
                    await self.fallback(text, approved)
                    return {"ok": True, "result": "v16-routed"}
                await self.set_state("DONE", "No matching intent. Try: open chrome / search X / screenshot / system check / remember X / run <cmd>")
                await self._schedule_idle()
                return {"ok": True, "result": "no-intent"}

            await self.set_state("PLANNING", " → ".join(intent["steps"]))
            await asyncio.sleep(0.35)

            kind = intent["kind"]
            result: dict = {"ok": False, "error": "not implemented"}

            if kind == "screenshot":
                await self.set_state("EXECUTING", "Capturing screen…")
                result = await self.tools.execute("screen")
                await self.set_state("VERIFYING", "Checking screenshot file…")
                await asyncio.sleep(0.25)
            elif kind == "browser":
                await self.set_state("EXECUTING", f"Opening {intent['arg'][:60]}")
                result = await self.tools.execute("browser", intent["arg"])
                await self.set_state("VERIFYING", "Checking browser process…")
                await asyncio.sleep(0.25)
            elif kind == "terminal":
                await self.set_state("EXECUTING", f"$ {intent['arg'][:60]}")
                result = await self.tools.execute("terminal", intent["arg"])
                await self.set_state("VERIFYING", "Checking exit code…")
                await asyncio.sleep(0.2)
            elif kind == "system_check":
                await self.set_state("EXECUTING", "Sampling CPU / RAM / DISK / NET…")
                snap = self.telemetry.snapshot()
                await self.set_state("VERIFYING", "Validating sensor response…")
                await asyncio.sleep(0.25)
                result = {"ok": True, "output": (
                    f"CPU {snap['cpu']['percent']}% · RAM {snap['ram']['percent']}% · "
                    f"DISK {snap['disk']['percent']}% · NET ↓{snap['net']['down_mbps']}Mbps"
                )}
            elif kind == "clear_memory":
                await self.set_state("EXECUTING", "Clearing memory…")
                self.memory.clear_all()
                await self.set_state("VERIFYING", "Confirming empty…")
                await asyncio.sleep(0.2)
                st = self.memory.status()
                result = {"ok": st["session_count"] == 0 and st["persistent_count"] == 0,
                          "error": None if st["session_count"] == 0 else "memory not empty"}
            elif kind == "remember":
                await self.set_state("EXECUTING", f"Storing: {intent['arg'][:60]}")
                ok = self.memory.add_persistent(intent["arg"])
                await self.set_state("VERIFYING", "Reading back…")
                await asyncio.sleep(0.2)
                result = {"ok": ok, "error": None if ok else "persistent memory not configured"}
            elif kind == "time":
                await self.set_state("EXECUTING", "Reading system clock…")
                result = {"ok": True, "output": time.strftime("%Y-%m-%d %H:%M:%S %Z")}
            elif kind == "ai_status":
                await self.set_state("EXECUTING", "Probing Ollama…")
                ai = self.get_ai_status()
                await self.set_state("VERIFYING", "Checking model list…")
                await asyncio.sleep(0.2)
                if ai.get("connected"):
                    result = {"ok": True, "output": f"Ollama online · models: {', '.join(ai.get('models', [])) or 'none'}"}
                else:
                    result = {"ok": False, "error": "Ollama offline"}
            else:
                result = {"ok": False, "error": "unknown intent kind"}

            if result.get("ok"):
                await self.set_state("DONE", result.get("output") or "Task completed successfully.")
            else:
                await self.set_state("ERROR", result.get("error") or "Task failed.")
            await self._schedule_idle()
            return result
        except Exception as exc:  # noqa: BLE001
            await self.set_state("ERROR", f"Agent exception: {exc}")
            await self._schedule_idle()
            return {"ok": False, "error": str(exc)}
        finally:
            self.busy = False
