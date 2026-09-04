import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.browser.agent import BrowserAgent, browser_action_risk
from app.agent.browser_workflow import parse_browser_plan


def test_browser_2_risk_and_explicit_workflow():
    assert browser_action_risk("submit") == "MEDIUM"
    steps = parse_browser_plan(
        "https://example.com aç, sonra role button adı Gönder tıkla, "
        "sonra formu gönder, sonra sayfada 'Tamamlandı' metnini doğrula"
    )
    assert [s["tool"] for s in steps] == [
        "browser_navigate", "browser_click", "browser_submit", "browser_verify"
    ]
    assert steps[1]["args"]["selector"] == "role=button[name=Gönder]"


def test_browser_text_and_role_targets_use_explicit_playwright_locators():
    class Locator:
        def __init__(self, kind, value): self.kind, self.value = kind, value
        @property
        def first(self): return self
        def count(self): return 1
        def inner_text(self, **_): return self.value
        def click(self, **_): return None
        def fill(self, text, **_): self.filled = text
        def press(self, key): self.key = key
        def wait_for(self, **_): return None
        def evaluate(self, _): return "button"
        def get_attribute(self, _): return None
        def bounding_box(self): return None
        def inner_html(self, **_): return "<button>Gönder</button>"
        def select_option(self, value, **_): self.selected = value

    class Page:
        url = "https://example.com"
        def title(self): return "Test"
        def get_by_role(self, role, name=None): return Locator("role", f"{role}:{name}")
        def get_by_text(self, text): return Locator("text", text)
        def get_by_label(self, text): return Locator("label", text)
        def get_by_placeholder(self, text): return Locator("placeholder", text)
        def locator(self, selector): return Locator("css", selector)
        def inner_text(self, _): return "Tamamlandı"

    class Context:
        def new_page(self): return Page()
    class Browser:
        def new_context(self, **_): return Context()
        def close(self): pass
    class PW:
        def stop(self): pass

    agent = BrowserAgent(settings={"browser": {"timeout_seconds": 1}}, engine_factory=lambda: (Browser(), PW()))
    try:
        assert agent.click("role=button[name=Gönder]")["clicked"].startswith("role=")
        assert agent.type("label=Arama", "ultron")["typed"] == 6
        assert agent.find_elements(role="button", name="Gönder", limit=1)[0]["text"] == "button:Gönder"
        assert agent.read_text("text=Tamamlandı")["text"] == "Tamamlandı"
    finally:
        agent.close()


def test_browser_submit_requires_unambiguous_form_without_selector():
    class Locator:
        def __init__(self, count): self._count = count
        @property
        def first(self): return self
        def count(self): return self._count
        def evaluate(self, _): return "form"
    class Page:
        url = "about:blank"
        def title(self): return ""
        def locator(self, selector): return Locator(2 if selector == "form" else 0)
    class Context:
        def new_page(self): return Page()
    class Browser:
        def new_context(self, **_): return Context()
        def close(self): pass
    class PW:
        def stop(self): pass
    agent = BrowserAgent(settings={"browser": {"timeout_seconds": 1}}, engine_factory=lambda: (Browser(), PW()))
    try:
        try:
            agent.submit()
            assert False, "ambiguous submit must fail"
        except ValueError as exc:
            assert "exactly one form" in str(exc)
    finally:
        agent.close()
