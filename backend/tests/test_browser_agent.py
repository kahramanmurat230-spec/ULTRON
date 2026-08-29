"""Browser Agent foundation tests.

Layer 1 (always runs): risk classification, URL normalization, verify
predicates, worker-thread dispatch, timeout enforcement, lifecycle — with an
in-process test engine (dependency-injection seam; production default is
real Playwright Chromium).

Layer 2 (skipped without a browser): real Chromium navigation/click/type/
screenshot/verify against a local file:// page — no network needed.
"""
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.browser.agent import (  # noqa: E402
    BrowserAgent, browser_action_risk, normalize_url, BROWSER_ACTIONS,
)


# ------------------------------------------------------------ pure logic
def test_risk_classification():
    assert browser_action_risk("navigate") == "SAFE"
    assert browser_action_risk("screenshot") == "SAFE"
    assert browser_action_risk("read_text") == "SAFE"
    assert browser_action_risk("verify") == "SAFE"
    assert browser_action_risk("click") == "MEDIUM"
    assert browser_action_risk("type") == "MEDIUM"
    assert browser_action_risk("select") == "MEDIUM"
    assert browser_action_risk("hack_the_planet") == "UNKNOWN"
    # read-only actions never count as state-changing
    for a in ("navigate", "read_text", "read_dom", "find_elements"):
        assert a not in STATE_CHANGING if (STATE_CHANGING := __import__(
            "app.browser.agent", fromlist=["STATE_CHANGING"]).STATE_CHANGING) else True


def test_normalize_url():
    assert normalize_url("example.com") == "https://example.com"
    assert normalize_url("https://a.b/c?d=1") == "https://a.b/c?d=1"
    assert normalize_url("http://x.y") == "http://x.y"
    assert normalize_url("file:///tmp/page.html") == "file:///tmp/page.html"
    assert normalize_url("about:blank") == "about:blank"
    for bad in ("", "  ", "javascript:alert(1)", "data:text/html,x",
                "ftp://evil/x", "c:\\windows\\evil"):
        with pytest.raises(ValueError):
            normalize_url(bad)


# ------------------------------------------------------------ test engine
class FakePage:
    def __init__(self, engine):
        self.engine = engine
        self.url = engine.home
        self.title_text = "Test"
        self.body = "hello world"
        self.selectors = {"#btn": 1, ".item": 3, "#missing": 0}
        self.filled = {}
        self.clicked = []
        self.deadline_hit = False

    def title(self):
        if self.engine.slow:
            time.sleep(5)
        return self.title_text

    def inner_text(self, sel, timeout=None):
        if sel == "body":
            return self.body
        return "elem-text"

    def locator(self, sel):
        return FakeLocator(self, sel)

    def click(self, sel, timeout=None):
        if self.engine.slow:
            time.sleep(5)
        if self.selectors.get(sel, 0) == 0:
            raise RuntimeError(f"locator: element not found {sel}")
        self.clicked.append(sel)
        self.body += " clicked"

    def fill(self, sel, text, timeout=None):
        if self.engine.slow:
            time.sleep(5)
        if self.selectors.get(sel, 0) == 0:
            raise RuntimeError(f"element not found {sel}")
        self.filled[sel] = text

    def press(self, sel, key):
        self.body += " enter"

    def goto(self, url, wait_until=None, timeout=None):
        self.url = url
        self.title_text = "Page: " + url
        return None

    def screenshot(self, path, timeout=None):
        Path(path).write_bytes(b"PNGDATA")
        return path


class FakeLocator:
    def __init__(self, page, sel):
        self.page, self.sel = page, sel

    def count(self):
        return self.page.selectors.get(self.sel, 0)

    def first(self):
        return self

    def nth(self, i):
        return self

    def inner_text(self, timeout=None):
        return "text-of-" + self.sel

    def inner_html(self, timeout=None):
        return f"<div id='{self.sel}'/>"

    def evaluate(self, expr):
        return "div"

    def get_attribute(self, name):
        return None

    def bounding_box(self):
        return {"x": 0, "y": 0, "width": 10, "height": 10}


class FakeContext:
    def __init__(self, engine):
        self.engine = engine

    def new_page(self):
        return FakePage(self.engine)


class FakeBrowser:
    def __init__(self, engine):
        self.engine = engine
        self.contexts = [FakeContext(engine)]

    def new_context(self, **_kw):
        return self.contexts[0]

    def close(self):
        self.engine.closed = True


class FakeEngineHandle:
    def __init__(self, engine):
        self.engine = engine

    def stop(self):
        self.engine.stopped = True


def make_test_agent(tmp_path, slow=False):
    engine = type("Engine", (), {})()
    engine.slow = slow
    engine.closed = False
    engine.stopped = False
    engine.home = "https://home.test/"
    agent = BrowserAgent(
        settings={"browser": {"headless": True, "timeout_seconds": 1.0}},
        engine_factory=lambda: (FakeBrowser(engine), FakeEngineHandle(engine)))
    agent._fake_engine = engine
    return agent


def test_browser_agent_lifecycle_navigate_click_type_verify(tmp_path):
    agent = make_test_agent(tmp_path)
    try:
        res = agent.navigate("example.com")
        assert res["url"] == "https://example.com"
        assert res["title"] == "Page: https://example.com"
        page = agent._pages[0]
        assert agent.read_text()["text"] == "hello world"
        assert agent.click("#btn")["clicked"] == "#btn"
        assert page.clicked == ["#btn"]
        assert agent.type("#btn", "merhaba", press_enter=True)["typed"] == 7
        assert page.filled["#btn"] == "merhaba"
        assert len(agent.find_elements(".item", limit=2)) == 2
        assert agent.find_elements(".item")[0]["tag"] == "div"
        shot = agent.screenshot(str(tmp_path / "s.png"))
        assert shot["bytes"] > 0
        v = agent.verify(url_contains="example", text_contains="clicked")
        assert v["ok"] and all(v["checks"].values())
        v2 = agent.verify(text_contains="asla-yok")
        assert v2["ok"] is False
    finally:
        agent.close()


def test_browser_agent_timeout_enforced(tmp_path):
    agent = make_test_agent(tmp_path, slow=True)
    try:
        with pytest.raises(TimeoutError):
            agent.title()
    finally:
        agent.close()


def test_browser_agent_unknown_action_rejected(tmp_path):
    agent = make_test_agent(tmp_path)
    try:
        with pytest.raises(ValueError):
            agent._act("explode", lambda page: None)
    finally:
        agent.close()


def test_browser_agent_click_missing_element_audited(tmp_path):
    agent = make_test_agent(tmp_path)
    logs = []
    agent.audit = type("A", (), {"write": lambda self, e, d="": logs.append((e, d))})()
    try:
        agent.navigate("example.com")
        with pytest.raises(Exception):
            agent.click("#missing")
        assert any("ACTION_ERROR" in e for e, _ in logs), logs
    finally:
        agent.close()


# ------------------------------------------------------------ real browser
def _real_browser_available():
    try:
        import playwright  # noqa: F401
        from playwright.sync_api import sync_playwright
        import os as _os
        exe = _os.environ.get("ULTRON_BROWSER_EXECUTABLE")
        with sync_playwright() as p:
            try:
                kw = {"headless": True}
                if exe:
                    kw["executable_path"] = exe
                    kw["args"] = ["--no-sandbox"]
                b = p.chromium.launch(**kw)
                b.close()
                return True
            except Exception:
                # try system channels
                for ch in ("chrome", "msedge"):
                    try:
                        b = p.chromium.launch(headless=True, channel=ch)
                        b.close()
                        return True
                    except Exception:
                        continue
        return False
    except Exception:
        return False


@pytest.mark.skipif(not _real_browser_available(),
                    reason="gerçek tarayıcı bu ortamda yok (CDN engelli) — PHASE 8 PARTIAL")
def test_real_chromium_e2e_local_page(tmp_path):
    html = tmp_path / "page.html"
    html.write_text(
        "<html><head><title>ULTRON Test Sayfası</title></head><body>"
        "<h1 id='hdr'>Merhaba Tarayıcı</h1>"
        "<input id='q' name='q'>"
        "<button id='go'>Git</button>"
        "</body></html>", encoding="utf-8")
    agent = BrowserAgent(settings={"browser": {"headless": True, "timeout_seconds": 10}})
    try:
        res = agent.navigate(html.as_uri())
        assert "ULTRON Test" in res["title"]
        assert agent.verify(title_contains="ULTRON", text_contains="Merhaba")["ok"]
        assert agent.type("#q", "ultron sorgusu")["typed"] > 0
        assert agent.click("#go")["clicked"] == "#go"
        shot = agent.screenshot()
        assert Path(shot["path"]).stat().st_size > 0
        # DOM inspection + element detection + tabs + history
        dom = agent.read_dom("#hdr")
        assert "Merhaba" in dom["html"]
        els = agent.find_elements("button")
        assert any(e["text"] == "Git" for e in els)
        t2 = agent.new_tab()
        assert t2["tab"] == 1
        agent.navigate(html.as_uri())
        agent.switch_tab(0)
        assert "ULTRON Test" in agent.title()
        agent.close_tab(1)
        v = agent.verify(selector_exists="#q")
        assert v["ok"] is True
    finally:
        agent.close()
