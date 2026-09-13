"""Regression tests for intent/tool routing.

Real-Windows bug report: "kamerayı aç" and "37×24 kaçar? Sadece sonuç ve işlem
yaz." both opened https://www.google.com because the V15 router treated ANY
"…aç/open…" text as a browser command (substring match: "aç" ⊂ "k-a-ç-ar").

Routing rules enforced here:
- Camera requests        -> camera capability (never browser).
- Math / chat / dates    -> deterministic calculator or LLM (never browser).
- Browser / web search   -> ONLY explicit browser names or explicit search intent.
- System telemetry       -> system status path (preserved).
- Approval gate / shell security untouched (camera is a non-dangerous read-only
  capture, same policy as V15's existing camera tool).
"""
import asyncio
from pathlib import Path

import pytest

from agent import Agent as V15Agent
from tools import ToolRegistry as V15ToolRegistry
from app.agent.agent import Agent as V16Agent
from app.core.intent import classify, _pure_math
from app.tools.calculator import calculate


def _v15():
    # parse_intent only uses class-level regexes — no I/O attributes needed.
    return object.__new__(V15Agent)


# ------------------------------------------------------------------ V15 router

@pytest.mark.parametrize("text", [
    "kamerayı aç", "kamera aç", "webcam aç", "webcam'ı aç",
    "kamera ile fotoğraf çek", "fotoğraf çek", "fotograf cek",
    "open the camera", "take a photo",
])
def test_v15_camera_requests_route_to_camera_not_browser(text):
    intent = _v15().parse_intent(text)
    assert intent is not None, text
    assert intent["kind"] == "camera", text


@pytest.mark.parametrize("text", [
    "kamerayı aç", "not defterini aç", "spotify'ı aç", "araba almak istiyorum",
    "37×24 kaçar? Sadece sonuç ve işlem yaz.", "2+2", "selam", "12.03.2026",
    "bu konuda karar verdim", "defteri aç",
])
def test_v15_never_opens_google_for_non_web_requests(text):
    intent = _v15().parse_intent(text)
    if intent is not None:
        assert intent["kind"] != "browser", text
        assert "google.com" not in str(intent.get("arg", "")), text


@pytest.mark.parametrize("text", ["chrome'u aç", "tarayıcıyı aç", "firefox aç", "edge'i aç", "open browser"])
def test_v15_explicit_browser_names_still_open_browser(text):
    intent = _v15().parse_intent(text)
    assert intent is not None and intent["kind"] == "browser", text


def test_v15_explicit_search_still_builds_search_url():
    intent = _v15().parse_intent("Google'da bugün yapay zeka haberlerini ara")
    assert intent is not None and intent["kind"] == "browser"
    assert intent["arg"].startswith("https://www.google.com/search?q=")
    assert "bugün" in intent["arg"]


def test_v15_search_verb_is_word_bounded():
    # "araba"/"karar"/"kaçar" contain "ara"/"aç" as substrings — never web search.
    for text in ("araba almak istiyorum", "37×24 kaçar?", "bu konuda karar verdim"):
        intent = _v15().parse_intent(text)
        assert intent is None or intent["kind"] != "browser", text


def test_v15_other_kinds_preserved():
    assert _v15().parse_intent("CPU durumum nedir?")["kind"] == "system_check"
    assert _v15().parse_intent("ekran görüntüsü al")["kind"] == "screenshot"
    assert _v15().parse_intent("selam") is None
    assert _v15().parse_intent("2+2") is None  # math falls through to V16/LLM


def test_v15_tool_registry_has_real_camera_tool(tmp_path):
    async def _noop(*args, **kwargs):
        return None

    reg = V15ToolRegistry(str(tmp_path), _noop, _noop, lambda: {"vision": None})
    assert "camera" in reg.tools
    result = asyncio.run(reg.execute("camera"))
    assert isinstance(result, dict)
    # Sandbox/CI has no camera: the tool must fail HONESTLY (ok=False + error),
    # never pretend and never fall back to a browser.
    if not result.get("ok"):
        assert result.get("error")


# ------------------------------------------------------------------ V16 router

@pytest.mark.parametrize("text", ["kamerayı aç", "webcam'ı aç", "fotoğraf çek", "open the camera"])
def test_v16_classify_camera(text):
    assert classify(text) == "camera", text


@pytest.mark.parametrize("text", [
    "37×24 kaçar? Sadece sonuç ve işlem yaz.", "37×24", "2+2", "2x3", "8÷2",
])
def test_v16_classify_math_goes_to_calculate(text):
    assert classify(text) == "calculate", text


def test_v16_classify_chat_system_and_search():
    assert classify("selam") == "conversation"
    assert classify("CPU durumum nedir?") == "system_status"
    assert classify("sistem durumu") == "system_status"
    assert classify("Google'da bugün yapay zeka haberlerini ara") == "web_search"
    assert classify("araba almak istiyorum") == "conversation"


def test_v16_classify_dates_are_not_math():
    assert classify("12.03.2026") == "conversation"


def test_pure_math_helper():
    assert _pure_math("2+2")
    assert _pure_math("37×24")
    assert _pure_math(" 2 + 2 = ")
    assert not _pure_math("12.03.2026")
    assert not _pure_math("selam")
    assert not _pure_math("5")
    assert not _pure_math("")


# -------------------------------------------------------------- V16 calculator

@pytest.mark.parametrize("text,expected", [
    ("37×24 kaçar? Sadece sonuç ve işlem yaz.", "888"),
    ("2+2", "4"),
    ("(2+3)*4", "20"),
    ("37x24", "888"),
    ("8÷2", "4"),
    ("100/4", "25"),
    ("2+3*4", "14"),
    ("5 ile 3 topla", "8"),
    ("3 elma + 2 armut kaç eder", "5"),
    ("10'un yüzde 20'si", "2"),
    ("16'nın karekökü", "4"),
])
def test_calculate_deterministic_results(text, expected):
    assert calculate(text) == expected


@pytest.mark.parametrize("text", ["selam", "12.03.2026"])
def test_calculate_refuses_non_math(text):
    with pytest.raises(ValueError):
        calculate(text)


# ------------------------------------------------------- V16 Agent._direct()

class _FakeMemory:
    def __init__(self):
        self.rows = []

    def add(self, kind, text):
        self.rows.append((kind, text))

    def recent(self, limit=3):
        return []


class _FakeExecutor:
    def __init__(self, result=None, error=None):
        self.calls = []
        self._result = result
        self._error = error

    def execute(self, calls, approved=False):
        self.calls.append((calls, approved))
        if self._error is not None:
            raise self._error
        return [self._result]


def _v16(executor):
    agent = object.__new__(V16Agent)
    agent.adaptive = None
    agent.memory = _FakeMemory()
    agent.executor = executor
    return agent


def test_v16_direct_camera_success_reports_frame():
    ex = _FakeExecutor(result={"ok": True, "mime": "image/jpeg", "image_b64": "A" * 1365})
    out = _v16(ex)._direct("kamerayı aç")
    assert out and out.startswith("Kamera karesi alındı")
    assert ex.calls[0][0][0][0] == "camera_capture"  # camera tool — NOT open_url/browser


def test_v16_direct_camera_unavailable_is_honest():
    ex = _FakeExecutor(result={"ok": False, "error": "camera unavailable or permission denied"})
    out = _v16(ex)._direct("kamerayı aç")
    assert "Kamera kullanılamadı" in out and "permission denied" in out


def test_v16_direct_camera_tool_crash_is_honest():
    ex = _FakeExecutor(error=RuntimeError("device busy"))
    out = _v16(ex)._direct("kamerayı aç")
    assert "Kamera kullanılamadı" in out


def test_v16_direct_math_is_deterministic_without_llm():
    ex = _FakeExecutor(result="888")
    out = _v16(ex)._direct("37×24 kaçar? Sadece sonuç ve işlem yaz.")
    assert out == "Sonuç: 888"
    assert ex.calls[0][0][0][0] == "calculate"  # calculator tool — NOT a browser


def test_v16_direct_math_parser_failure_falls_through_to_llm():
    # If the deterministic calculator cannot parse the expression, _direct must
    # return None so handle() continues to the LLM — never to a browser.
    ex = _FakeExecutor(error=ValueError("Hesaplama ifadesi anlaşılamadı"))
    assert _v16(ex)._direct("kaçkaç 9?#") is None


# ------------------------------------------------------------ V16 runtime reg

def test_v16_runtime_registers_camera_capture(monkeypatch):
    from app.core.runtime import UltronRuntime
    from app.security import sovereign_privacy as sov

    # Building the runtime calls sovereign_privacy.configure(settings) with the
    # repo settings (sovereign_mode: true). Isolate that module-global state so
    # it cannot leak into later tests (e.g. test_redaction_sovereign).
    monkeypatch.setattr(sov, "state", dict(sov.state))

    settings = Path(__file__).resolve().parents[1] / "config" / "settings.json"
    rt = UltronRuntime(str(settings))
    tool = rt.registry.get("camera_capture")
    assert tool is not None, "camera_capture must be registered in the V16 runtime"
    assert tool["dangerous"] is False  # read-only capture, consistent with V15
    result = tool["fn"]()
    assert isinstance(result, dict) and "ok" in result
    if not result["ok"]:
        assert result.get("error")  # honest unavailability — never faked
