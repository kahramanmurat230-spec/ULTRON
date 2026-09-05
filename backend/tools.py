"""Tool registry: real availability checks, real execution, real status transitions."""
import asyncio
import os
import shutil
import sys
import time

WIN = sys.platform.startswith("win")


class ToolRegistry:
    def __init__(self, workspace: str, broadcast, on_activity, get_ai_status) -> None:
        self.workspace = workspace
        self.broadcast = broadcast
        self.on_activity = on_activity
        self.get_ai_status = get_ai_status
        self.tools: dict[str, dict] = {
            "browser": {"label": "Browser", "detail": "", "status": "CHECKING"},
            "file_system": {"label": "File System", "detail": "", "status": "CHECKING"},
            "terminal": {"label": "Terminal", "detail": "", "status": "CHECKING"},
            "screen": {"label": "Screen", "detail": "", "status": "CHECKING"},
            "voice": {"label": "Voice", "detail": "waiting for client report", "status": "CHECKING"},
            "vision": {"label": "Vision", "detail": "", "status": "CHECKING"},
        }
        self.refresh_sync()

    # ---------- availability ----------
    def _browser_cmd(self) -> str | None:
        if WIN:
            return "start"
        for c in ("xdg-open", "google-chrome", "chromium", "microsoft-edge", "firefox"):
            if shutil.which(c):
                return c
        return None

    def _screen_cmd(self) -> str | None:
        if WIN:
            return "powershell"
        for c in ("scrot", "gnome-screenshot", "spectacle", "import"):
            if shutil.which(c):
                return c
        return None

    def refresh_sync(self) -> None:
        b = self._browser_cmd()
        self.tools["browser"].update(
            status="READY" if b else "UNAVAILABLE",
            detail=b if b else "no browser launcher found",
        )
        fs_ok = os.access(self.workspace, os.W_OK)
        self.tools["file_system"].update(
            status="READY" if fs_ok else "ERROR",
            detail=self.workspace if fs_ok else "workspace not writable",
        )
        sh = "cmd" if WIN else shutil.which("sh") or shutil.which("bash")
        self.tools["terminal"].update(
            status="READY" if sh else "UNAVAILABLE", detail=str(sh) if sh else "no shell found"
        )
        s = self._screen_cmd()
        has_display = bool(os.environ.get("DISPLAY")) or WIN
        self.tools["screen"].update(
            status="READY" if (s and has_display) else "UNAVAILABLE",
            detail=s if s else "no screenshot tool / no display",
        )
        ai = self.get_ai_status()
        vision_model = ai.get("vision")
        self.tools["vision"].update(
            status="READY" if vision_model else "UNAVAILABLE",
            detail=vision_model if vision_model else "no vision model in Ollama",
        )

    def report_voice(self, available: bool, detail: str = "") -> None:
        self.tools["voice"].update(
            status="READY" if available else "UNAVAILABLE",
            detail=detail or ("client speech API present" if available else "browser lacks speech API"),
        )

    def list_status(self) -> list[dict]:
        return [{"id": k, **v} for k, v in self.tools.items()]

    # ---------- execution ----------
    async def _set(self, name: str, status: str, detail: str | None = None) -> None:
        t = self.tools[name]
        t["status"] = status
        if detail is not None:
            t["detail"] = detail
        await self.broadcast({"type": "tools", "data": self.list_status()})

    async def execute(self, name: str, arg: str = "") -> dict:
        t = self.tools.get(name)
        if t is None:
            return {"ok": False, "error": f"unknown tool: {name}"}
        if t["status"] == "UNAVAILABLE":
            await self.on_activity(f"{t['label']} unavailable: {t['detail']}", "error")
            return {"ok": False, "error": t["detail"]}
        await self._set(name, "RUNNING")
        started = time.time()
        try:
            if name == "browser":
                result = await self._run_browser(arg)
            elif name == "screen":
                result = await self._run_screen()
            elif name == "terminal":
                result = await self._run_terminal(arg)
            elif name == "file_system":
                result = await self._run_fs(arg)
            elif name == "vision":
                result = {"ok": False, "error": "vision inference not wired (model present but no pipeline)"}
            else:
                result = {"ok": False, "error": f"{name} is client-side"}
        except Exception as exc:  # noqa: BLE001
            result = {"ok": False, "error": str(exc)}
        dt = round(time.time() - started, 2)
        if result.get("ok"):
            await self._set(name, "SUCCESS", f"ok in {dt}s")
            await self.on_activity(f"{t['label']} executed ok ({dt}s)", "success")
        else:
            await self._set(name, "ERROR", result.get("error", "failed"))
            await self.on_activity(f"{t['label']} failed: {result.get('error')}", "error")
        # decay back to READY/UNAVAILABLE so the panel settles
        asyncio.get_event_loop().call_later(4, lambda: asyncio.ensure_future(self._settle(name)))
        return result

    async def _settle(self, name: str) -> None:
        cur = self.tools[name]["status"]
        if cur in ("SUCCESS", "ERROR"):
            if name == "voice":
                await self._set(name, cur)  # voice stays as reported
            else:
                self.refresh_sync()
                await self.broadcast({"type": "tools", "data": self.list_status()})

    async def _run_browser(self, url: str) -> dict:
        target = url or "https://www.google.com"
        if WIN:
            proc = await asyncio.create_subprocess_shell(
                f'start "" "{target}"',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        else:
            launcher = self._browser_cmd()
            if not launcher:
                return {"ok": False, "error": "no browser launcher on this host"}
            proc = await asyncio.create_subprocess_exec(
                launcher, target,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        try:
            _, err = await asyncio.wait_for(proc.communicate(), timeout=10)
        except asyncio.TimeoutError:
            proc.kill()
            return {"ok": False, "error": "browser launch timed out"}
        if proc.returncode not in (0, None):
            return {"ok": False, "error": (err or b"").decode(errors="replace")[:300] or f"exit {proc.returncode}"}
        return {"ok": True, "output": f"opened {target}"}

    async def _run_screen(self) -> dict:
        out_dir = os.path.join(self.workspace, "data", "screenshots")
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, f"ultron_{int(time.time())}.png")
        cmd = self._screen_cmd()
        if not cmd:
            return {"ok": False, "error": "no screenshot tool available on this host"}
        if cmd == "scrot":
            argv = ["scrot", "-o", path]
        elif cmd == "gnome-screenshot":
            argv = ["gnome-screenshot", "-f", path]
        elif cmd == "spectacle":
            argv = ["spectacle", "-b", "-n", "-o", path]
        elif cmd == "import":
            argv = ["import", "-window", "root", path]
        else:  # powershell
            ps = (
                "Add-Type -AssemblyName System.Windows.Forms;"
                "[System.Windows.Forms.Screen]::PrimaryScreen | ForEach-Object {"
                "$b=$_.Bounds;$bmp=New-Object System.Drawing.Bitmap $b.Width,$b.Height;"
                "$g=[System.Drawing.Graphics]::FromImage($bmp);"
                "$g.CopyFromScreen($b.Location,[System.Drawing.Point]::Empty,$b.Size);"
                f"$bmp.Save('{path}')}}"
            )
            argv = ["powershell", "-Command", ps]
        proc = await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        try:
            _, err = await asyncio.wait_for(proc.communicate(), timeout=15)
        except asyncio.TimeoutError:
            proc.kill()
            return {"ok": False, "error": "screenshot timed out"}
        if os.path.exists(path) and os.path.getsize(path) > 0:
            return {"ok": True, "output": f"saved {path}"}
        return {"ok": False, "error": (err or b"").decode(errors="replace")[:300] or "no file produced"}

    async def _run_terminal(self, command: str) -> dict:
        if not command:
            return {"ok": False, "error": "no command supplied"}
        proc = await asyncio.create_subprocess_shell(
            command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
        except asyncio.TimeoutError:
            proc.kill()
            return {"ok": False, "error": "command timed out (15s)"}
        text = out.decode(errors="replace")[-2000:]
        if proc.returncode == 0:
            return {"ok": True, "output": text or "(no output)"}
        return {"ok": False, "error": f"exit {proc.returncode}: {text[:400]}"}

    async def _run_fs(self, arg: str) -> dict:
        base = os.path.abspath(arg or self.workspace)
        if not base.startswith(os.path.abspath(self.workspace)) and not arg:
            return {"ok": False, "error": "path outside workspace"}
        try:
            entries = sorted(os.listdir(base))[:50]
        except OSError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "output": ", ".join(entries) or "(empty)"}
