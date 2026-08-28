"""Workspace Sentinel — mode detection from the active window (platform-aware).

CODING_MODE / BROWSER_RESEARCH / IDLE_MODE / DEEP_FOCUS with per-mode actions.
No deps required: win32 -> pygetwindow/win32gui, linux -> xdotool, mac -> osascript.
Headless/missing tooling => title None => UNKNOWN/IDLE (honest).
"""
import re
import subprocess
import sys
import time

CODING_RE = re.compile(r"code|vscode|jetbrains|idea|pycharm|webstorm|terminal|powershell|cmd|vim|nvim", re.I)
RESEARCH_RE = re.compile(r"stack overflow|github|gitlab|docs|readthedocs|mdn|wikipedia", re.I)
BROWSER_RE = re.compile(r"chrome|firefox|edge|brave|safari", re.I)

DEEP_FOCUS_MIN = 45
BREAK_MIN = 90
IDLE_MIN = 5

BREAK_LINE = ("Boss, {min} dakikadır aynı ekrana bakıyorsun. Beyin dediğin şey, dinlenmeyi "
              "hak eden bir organ değil — ama seninkinin şu an ihtiyacı var.")


def get_window_title() -> str | None:
    try:
        if sys.platform == "win32":
            try:
                import pygetwindow as gw
                w = gw.getActiveWindow()
                return w.title if w else None
            except Exception:
                import win32gui
                return win32gui.GetWindowText(win32gui.GetForegroundWindow())
        if sys.platform == "darwin":
            out = subprocess.run(["osascript", "-e",
                                  'tell app "System Events" to get name of first process whose frontmost is true'],
                                 capture_output=True, text=True, timeout=3)
            return out.stdout.strip() or None
        out = subprocess.run(["xdotool", "getactivewindow", "getwindowname"],
                             capture_output=True, text=True, timeout=3)
        return out.stdout.strip() or None
    except Exception:
        return None


class WorkspaceSentinel:
    def __init__(self, now=None):
        self.now = now or time.time
        self.mode = "UNKNOWN"
        self.title = None
        self.mode_since = self.now()
        self.focus_title = None
        self.focus_since = self.now()
        self.last_active = self.now()
        self.break_sent = False
        self.manual_mode = None  # scenes engine override (CODING_FOCUS vb.)
        self.on_break = None  # callback(min)

    def classify(self, title: str | None, idle_min: float) -> str:
        if title is None or idle_min >= IDLE_MIN:
            return "IDLE_MODE"
        if self.focus_title == title and (self.now() - self.focus_since) / 60 >= DEEP_FOCUS_MIN:
            return "DEEP_FOCUS"
        if title and CODING_RE.search(title):
            return "CODING_MODE"
        if title and BROWSER_RE.search(title) and RESEARCH_RE.search(title):
            return "BROWSER_RESEARCH"
        if title and BROWSER_RE.search(title):
            return "BROWSER_RESEARCH"
        return "ACTIVE_OTHER"

    def sample(self, title: str | None = None, ts: float | None = None,
               idle_min: float = 0.0) -> str:
        t = ts if ts is not None else self.now()
        title = title if title is not None else get_window_title()
        if title != self.focus_title:
            self.focus_title = title
            self.focus_since = t
            self.break_sent = False
        if title:
            self.last_active = t
        new_mode = self.classify(title, idle_min)
        if new_mode != self.mode:
            self.mode = new_mode
            self.mode_since = t
        self.title = title
        focused_min = (t - self.focus_since) / 60
        if self.mode == "DEEP_FOCUS" and focused_min >= BREAK_MIN and not self.break_sent:
            self.break_sent = True
            if self.on_break:
                self.on_break(int(focused_min))
        return self.mode

    @property
    def quiet(self) -> bool:
        """CODING/DEEP_FOCUS (auto or scene-override) => do not interrupt the Boss."""
        return (self.manual_mode or self.mode) in ("CODING_MODE", "DEEP_FOCUS")

    def state(self) -> dict:
        return {
            "mode": self.manual_mode or self.mode,
            "window": self.title,
            "mode_min": round((self.now() - self.mode_since) / 60, 1),
            "focus_min": round((self.now() - self.focus_since) / 60, 1),
            "quiet": (self.manual_mode or self.mode) in ("CODING_MODE", "DEEP_FOCUS"),
        }
