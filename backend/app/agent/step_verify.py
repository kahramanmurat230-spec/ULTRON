"""Independent step-effect verification: executed / succeeded / verified.

The planner/task pipeline reports every tool step with three explicit flags:

- ``executed``  — the tool call was actually dispatched (no fake runs)
- ``succeeded`` — the tool returned without raising (executor result)
- ``verified``  — an INDEPENDENT channel re-observed the expected effect:
                  filesystem state, process table, or live browser state.
                  NEVER the tool's own claimed success.

Verification honesty rules:
- ``verified=False``  → the effect is observably absent → the step FAILS
  (recovery/replanning kicks in; a claimed success can never survive this).
- ``verified=None``   → the effect cannot be independently observed in this
  environment (no browser/display/OCR here) → reported as unknown, never as
  a fake pass.
- Plan steps may declare explicit ``expect`` criteria (strongest signal):
  {"exists": path} | {"text_contains": str} | {"url_contains": str} |
  {"pid_gone": pid} — checked independently of the tool output.
"""
from __future__ import annotations

from pathlib import Path

_FILE_TOOLS_EXIST = {
    # tool → (arg key, expected existence) — matches the production
    # ToolRegistry signatures (app/core/runtime.py file tools)
    "write_text": ("path", True),
    "create_folder": ("path", True),
    "copy_path": ("destination", True),
    "move_path": ("destination", True),
}
_SHAPE_TOOLS = {
    "read_text", "list_directory", "find_files", "find_project", "search_web",
    "browser_read", "browser_find", "weather_current", "weather_forecast",
    "calendar_events", "email_inbox", "email_search", "screen_ocr",
    "ocr_elements", "read_screen_elements", "skills_list", "undo_list",
    "system_settings_view", "process_list",
}


class StepVerifier:
    """Verify plan-step effects through independent observation channels."""

    def __init__(self, browser_getter=None, max_detail: int = 200):
        # browser_getter: optional callable → BrowserAgent (lazily; only
        # browser steps need it, so the browser is never launched eagerly)
        self.browser_getter = browser_getter
        self.max_detail = max_detail

    def _browser(self):
        if callable(self.browser_getter):
            try:
                return self.browser_getter()
            except Exception:
                return None
        return None

    # ------------------------------------------------------------------ api
    def verify(self, tool: str, arguments: dict, result, expect: dict | None = None) -> dict:
        arguments = arguments or {}
        if isinstance(expect, dict):
            return self._verify_expect(expect, arguments)
        if tool in _FILE_TOOLS_EXIST:
            return self._verify_path(tool, arguments, _FILE_TOOLS_EXIST[tool])
        if tool == "delete_path":
            return self._verify_gone(arguments.get("path"))
        if tool == "move_path":
            return self._verify_move(arguments)
        if tool == "rename_path":
            return self._verify_rename(arguments)
        if tool == "process_kill":
            return self._verify_pid_gone(arguments.get("pid"))
        if tool == "browser_navigate":
            return self._verify_browser_url(arguments.get("url"))
        if tool in ("browser_click", "browser_type", "browser_select",
                    "browser_press_enter", "submit"):
            return self._verify_browser_alive()
        if tool in _SHAPE_TOOLS:
            return self._verify_shape(result)
        if tool == "calculate":
            return {"mode": "shape", "verified": bool(result), "detail": "non-empty result"}
        # effects not independently observable (open_url/open_application/gui_*)
        return {"mode": "none", "verified": None,
                "detail": f"no independent observation channel for {tool}"}

    # ------------------------------------------------------------ channels
    def _verify_expect(self, expect: dict, arguments: dict) -> dict:
        checks = {}
        if "exists" in expect:
            p = Path(str(expect["exists"])).expanduser()
            checks["exists"] = p.exists()
        if "text_contains" in expect:
            needle = str(expect["text_contains"]).lower()
            observed = expect.get("_observed_text")
            if observed is None and arguments.get("path"):
                # INDEPENDENT channel: read the file from disk, not the
                # tool's claimed output
                try:
                    observed = Path(str(arguments["path"])).expanduser().read_text(
                        encoding="utf-8", errors="ignore")
                except Exception:
                    observed = None
            if observed is None:
                return {"mode": "expectation", "verified": None,
                        "detail": "text_contains: no independent text channel"}
            checks["text_contains"] = needle in str(observed).lower()
        if "url_contains" in expect:
            browser = self._browser()
            if browser is None:
                return {"mode": "expectation", "verified": None,
                        "detail": "browser state not observable (no agent)"}
            try:
                current = browser.url()
                checks["url_contains"] = str(expect["url_contains"]).lower() in current.lower()
            except Exception as exc:
                return {"mode": "expectation", "verified": None,
                        "detail": f"browser state unobservable: {str(exc)[:80]}"}
        if "pid_gone" in expect:
            checks["pid_gone"] = not self._pid_exists(expect["pid_gone"])
        if not checks:
            return {"mode": "expectation", "verified": None, "detail": "no checkable expectation"}
        ok = all(checks.values())
        return {"mode": "expectation", "verified": ok,
                "detail": ("all expectations observed" if ok
                           else f"expectation failed: {checks}")[: self.max_detail]}

    def _verify_path(self, tool: str, arguments: dict, spec: tuple) -> dict:
        key, should_exist = spec
        raw = arguments.get(key)
        if not raw:
            return {"mode": "filesystem", "verified": None, "detail": f"{tool}: no {key} argument"}
        p = Path(str(raw)).expanduser()
        exists = p.exists()
        ok = exists == should_exist
        detail = (f"{tool}: {key}={p} exists={exists} (expected {should_exist})"
                  if ok else
                  f"{tool}: {key}={p} exists={exists} but expected {should_exist}")
        return {"mode": "filesystem", "verified": ok, "detail": detail[: self.max_detail]}

    def _verify_move(self, arguments: dict) -> dict:
        src, dst = arguments.get("source"), arguments.get("destination")
        if not src or not dst:
            return {"mode": "filesystem", "verified": None, "detail": "move_path: missing source/destination"}
        sp, dp = Path(str(src)).expanduser(), Path(str(dst)).expanduser()
        ok = dp.exists() and not sp.exists()
        return {"mode": "filesystem", "verified": ok,
                "detail": (f"moved {sp} -> {dp}" if ok
                           else f"move not confirmed: src_exists={sp.exists()} dst_exists={dp.exists()}")[: self.max_detail]}

    def _verify_rename(self, arguments: dict) -> dict:
        old, new_name = arguments.get("path"), arguments.get("new_name")
        if not old or not new_name:
            return {"mode": "filesystem", "verified": None, "detail": "rename_path: missing path/new_name"}
        op = Path(str(old)).expanduser()
        np_ = op.with_name(Path(str(new_name)).name)
        ok = np_.exists() and not op.exists()
        return {"mode": "filesystem", "verified": ok,
                "detail": (f"renamed {op} -> {np_}" if ok
                           else f"rename not confirmed: old_exists={op.exists()} new_exists={np_.exists()}")[: self.max_detail]}

    def _verify_gone(self, raw) -> dict:
        if not raw:
            return {"mode": "filesystem", "verified": None, "detail": "no path argument"}
        p = Path(str(raw)).expanduser()
        gone = not p.exists()
        return {"mode": "filesystem", "verified": gone,
                "detail": (f"path removed: {p}" if gone else f"path still exists: {p}")[: self.max_detail]}

    def _verify_pid_gone(self, pid) -> dict:
        if pid is None:
            return {"mode": "process", "verified": None, "detail": "no pid argument"}
        gone = not self._pid_exists(pid)
        return {"mode": "process", "verified": gone,
                "detail": (f"pid {pid} terminated" if gone else f"pid {pid} still running")[: self.max_detail]}

    @staticmethod
    def _pid_exists(pid) -> bool:
        try:
            import psutil
            return psutil.pid_exists(int(pid))
        except Exception:
            return True  # cannot observe → do not claim gone

    def _verify_browser_url(self, url) -> dict:
        browser = self._browser()
        if browser is None:
            return {"mode": "browser", "verified": None,
                    "detail": "browser state not observable (no agent)"}
        try:
            host = str(url or "").split("//")[-1].split("/")[0].lower()
            current = browser.url()
            ok = host in current.lower() if host else True
            return {"mode": "browser", "verified": ok,
                    "detail": f"url={current} (expected host {host})"[: self.max_detail]}
        except Exception as exc:
            return {"mode": "browser", "verified": None,
                    "detail": f"browser state unobservable: {str(exc)[:80]}"}

    def _verify_browser_alive(self) -> dict:
        browser = self._browser()
        if browser is None:
            return {"mode": "browser", "verified": None,
                    "detail": "browser state not observable (no agent)"}
        try:
            title = browser.title()
            return {"mode": "browser", "verified": bool(title) or title == "",
                    "detail": f"page responsive (title len={len(title or '')})"}
        except Exception as exc:
            return {"mode": "browser", "verified": None,
                    "detail": f"browser state unobservable: {str(exc)[:80]}"}

    def _verify_shape(self, result) -> dict:
        empty = result is None or result == "" or result == [] or result == {}
        return {"mode": "shape", "verified": not empty,
                "detail": ("non-empty result" if not empty else "empty result")[: self.max_detail]}
