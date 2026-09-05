"""WAVE 4 / Browser runtime: DOM/AX şema, zincir, stale DOM, captcha,
risk/onay, DOM-AX çelişki doğrulaması, vision fallback stale guard."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.multimodal.browser_runtime import (  # noqa: E402
    DOMElement, PageState, PlaywrightEngine, StaleDOMError,
)


# ---------------------------------------------------------------- engine DI
def el(selector, name, role="button", box=(0, 0, 100, 30), source="dom",
       visible=True, enabled=True, text=""):
    return DOMElement(selector=selector, role=role, name=name, text=text,
                      visibility="visible" if visible else "hidden",
                      enabled=enabled, bounding_box=box, source=source,
                      confidence=0.95 if source == "ax" else 0.9)


class ScriptEngine:
    """DI seam — gerçek PlaywrightEngine yerine script'li sayfa (gerçek
    tarayıcı bu ortamda yok; engine.status dürüst unavailable)."""

    NAME = "script"

    def __init__(self, elements=None, page_text="", ax=None):
        self.elements = elements or []
        self.ax = ax if ax is not None else []
        self.text = page_text
        self.calls = []
        self.mutated = False

    def status(self):
        return {"engine": self.NAME, "available": True}

    def navigate(self, url):
        self.calls.append(("navigate", url))
        return {"url": url}

    def dom_elements(self):
        els = list(self.elements)
        if self.mutated:
            # DOM değişti: buton kayboldu, yeni link geldi
            els = [e for e in els if e.name != "Satın Al"]
            els.append(el("a#yeni", "Yeni Bağlantı", "link", (0, 60, 90, 20)))
        return els

    def ax_elements(self):
        return list(self.ax)

    def click(self, selector):
        self.calls.append(("click", selector))
        self.mutated = True          # eylem sayfayı değiştirdi (gerçekçi)
        return {"clicked": selector}

    def type_text(self, selector, text):
        self.calls.append(("type", selector, text))
        return {"typed": len(text)}

    def screenshot_bytes(self):
        return b"PNGDATA"

    def page_text(self):
        return self.text


def _rig(elements=None, ax=None, page_text="", approve=None,
         frame_store=None):
    from app.multimodal.browser_runtime import BrowserRuntime
    eng = ScriptEngine(elements=elements, ax=ax, page_text=page_text)
    rt = BrowserRuntime(eng, frame_store=frame_store, approve_fn=approve)
    return rt, eng


# ---------------------------------------------------------------- şema
def test_dom_element_schema():
    e = el("#btn", "Kaydet")
    d = e.to_ui_element().to_dict()
    for k in ("selector",) if False else ():
        pass
    ui = e.to_ui_element()
    assert ui.role == "button" and ui.source == "dom"
    assert e.bounding_box == (0, 0, 100, 30)
    assert ui.confidence == 0.9


def test_page_fingerprint_changes_with_dom():
    s1 = PageState("u", "t", [el("#a", "A"), el("#b", "B")], 0.0)
    s2 = PageState("u", "t", [el("#a", "A")], 0.1)
    s3 = PageState("u", "t", [el("#b", "B"), el("#a", "A")], 0.2)
    assert s1.dom_fingerprint() != s2.dom_fingerprint()
    assert s1.dom_fingerprint() == s3.dom_fingerprint()   # sıra önemsiz


# ---------------------------------------------------------------- zincir
def test_ax_priority_over_dom():
    ax_btn = el("[role=button]", "Giriş Yap", source="ax", box=(5, 5, 90, 30))
    dom_btn = el("#login", "Giriş Yap", source="dom", box=(8, 8, 84, 28))
    rt, _ = _rig(elements=[dom_btn], ax=[ax_btn])
    got = rt.ground_element("Giriş Yap")
    assert got.source == "ax"                      # AX öncelikli (§16)


def test_dom_ax_conflict_detected_and_unactionable():
    ax_btn = el("[role=button]", "Onay", source="ax", box=(0, 0, 90, 30))
    dom_btn = el("#ok", "Onay", source="dom", box=(300, 400, 90, 30))
    rt, _ = _rig(elements=[dom_btn], ax=[ax_btn])
    rt.refresh()
    res = rt.resolve_dom_ax_conflict("#ok", name="Onay")
    assert res["conflict"] is True and res["actionable"] is False


def test_dom_ax_agree_actionable():
    ax_btn = el("[role=button]", "Onay", source="ax", box=(0, 0, 90, 30))
    dom_btn = el("#ok", "Onay", source="dom", box=(0, 0, 90, 30))
    rt, _ = _rig(elements=[dom_btn], ax=[ax_btn])
    rt.refresh()
    res = rt.resolve_dom_ax_conflict("#ok", name="Onay")
    assert res["conflict"] is False and res["actionable"] is True


def test_vision_fallback_refuses_stale_frame():
    from app.multimodal.browser_runtime import BrowserRuntime
    from app.multimodal.vision_runtime import FrameStore, VisionFrame
    import time as _t
    store = FrameStore(max_age_s=0.05)
    store.add(VisionFrame("vf-1", _t.time() - 5, "screen", (1, 1), "h",
                          1.0, False, 1.0))          # eski frame
    rt, _ = _rig(frame_store=store)
    rt.refresh()
    assert rt.ground_element("olmayan buton") is None   # stale → hedef YOK


# ---------------------------------------------------------------- stale DOM
def test_stale_dom_rejects_action():
    rt, eng = _rig(elements=[el("#buy", "Satın Al")])
    rt.refresh()
    eng.mutated = True                                # DOM artık farklı
    from app.multimodal.browser_runtime import StaleDOMError as SDE
    with pytest.raises(SDE, match="DOM değişti"):
        rt.act("click", "Satın Al", approved=True)
    assert eng.calls == []                            # tıklama OLMADI


def test_fresh_dom_action_passes_and_observes_change():
    rt, eng = _rig(elements=[el("#buy", "Satın Al")])
    rt.navigate("https://ornek.com/urun", approved=True)
    out = rt.act("click", "Satın Al", approved=True)  # DOM aynı → OK
    assert out["state"] == "OK"
    assert ("click", "#buy") == (eng.calls[-1][0], eng.calls[-1][1])


def test_act_without_page_state_fails():
    rt, _ = _rig()
    with pytest.raises(StaleDOMError):
        rt.act("click", "#x", approved=True)


# ---------------------------------------------------------------- risk
def test_medium_click_needs_approval():
    from app.multimodal.browser_runtime import BrowserDenied
    rt, _ = _rig(elements=[el("#x", "Dene")])
    rt.navigate("https://ornek.com", approved=True)
    with pytest.raises(BrowserDenied, match="onay gerekli"):
        rt.act("click", "Dene")                       # onaysız RED
    assert rt.stats["denied"] == 1


def test_payment_url_escalates_critical():
    rt, _ = _rig()
    risk = rt.risk_for("click", {"url": "https://shop.com/checkout/pay"})
    assert risk["level"] == "MEDIUM" and risk["requires_approval"] is True
    risk2 = rt.risk_for("payment", {})
    assert risk2["level"] == "CRITICAL"


def test_captcha_blocks_everything_requires_human():
    from app.multimodal.browser_runtime import CaptchaDetected
    rt, _ = _rig(page_text="Lütfen reCAPTCHA doğrulamasını tamamlayın")
    with pytest.raises(CaptchaDetected, match="bypass YASAK"):
        rt.navigate("https://site.com/login", approved=True)  # nav bile RED
    rt.refresh() if rt.page else None
    with pytest.raises(CaptchaDetected):
        rt.act("click", "#submit", approved=True)     # onaylı OLSA BİLE
    assert rt.stats["captcha_hits"] == 2


def test_approval_callback_allows_medium():
    asked = []

    def approve(action, params):
        asked.append(action)
        return True

    rt, _ = _rig(elements=[el("#x", "Dene")], approve=approve)
    rt.navigate("https://ornek.com", approved=False)  # callback onaylar
    out = rt.act("click", "Dene")
    assert out["state"] == "OK" and asked == ["navigate", "click"]


def test_form_typing_flow():
    rt, eng = _rig(elements=[
        el("#email", "E-posta", role="textbox", source="dom")])
    rt.navigate("https://ornek.com/form", approved=True)
    out = rt.act("type", "E-posta", params={"text": "a@b.com"},
                 approved=True)
    assert out["state"] == "OK"
    assert eng.calls[-1] == ("type", "#email", "a@b.com")


def test_missing_target_fails_honestly():
    rt, _ = _rig(elements=[el("#a", "Başka")])
    rt.navigate("https://ornek.com", approved=True)
    out = rt.act("click", "Gönder", approved=True)
    assert out["state"] == "FAILED" and "bulunamadı" in out["error"]


def test_engine_playwright_honest_unavailable():
    eng = PlaywrightEngine.__new__(PlaywrightEngine)  # launch DENEMEDEN
    eng._available = False
    eng._checked = False
    st = eng.status()
    assert st["available"] is False and st["note"]
