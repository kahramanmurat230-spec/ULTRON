"""WAVE 4 — Browser Runtime (§14-18): DOM/AX/vision zinciri + stale DOM +
captcha/onay kapısı.

Mevcut gerçek BrowserAgent (app/browser/agent.py — Playwright) KORUNUR ve
motor olarak kullanılır. Bu katman ekler:

§15 DOM element şeması: selector/role/name/text/attributes/visibility/
   enabled/bounding_box/confidence + stale DOM fingerprint'i. DOM
   değişirse action RED → yeniden planlanır (koordinat körü değil).
§16 AX tree öncelikli hedefleme; DOM ile AX çelişirse VERIFY (belirsizse
   action verilmez).
§17 DOM yetersizse Vision fallback — element+bbox+confidence+frame_id;
   stale frame kullanılmaz (FrameStore.action_frame guard).
§18 risk: downloads/uploads/login/external navigation/dangerous risk
   değerlendirmesi; CAPTCHA tespiti → otomatik bypass YASAK, insan
   onayı/aksiyonu şart.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field

from app.multimodal.grounding import UIElement, ground
from app.multimodal.vision_runtime import StaleFrameError


class StaleDOMError(RuntimeError):
    """DOM değişti — action yeniden planlanmalı (koordinat körü değil)."""


class CaptchaDetected(RuntimeError):
    """CAPTCHA — otomatik bypass YOK; insan onayı/aksiyonu gerekir."""


class BrowserDenied(PermissionError):
    """Riskli browser eylemi onaysız — RED."""


# §18/§28 yüksek riskli eylem sınıfı (approval ZORUNLU)
HIGH_RISK_URL = re.compile(
    r"(pay|checkout|payment|billing|satınal|satinalis|purchase)", re.I)
CRITICAL_URL = re.compile(r"(account.*delete|hesabimi-sil|delete-account)",
                          re.I)
CAPTCHA_MARKERS = ("recaptcha", "hcaptcha", "captcha", "arkose",
                   "cf-challenge", "challenge-platform")
CRITICAL_ACTIONS = {"credential_submit", "payment", "purchase",
                    "account_deletion", "external_upload", "message_send",
                    "security_change"}
MEDIUM_ACTIONS = {"click", "type", "select", "scroll", "press_enter",
                  "submit", "download", "upload", "login", "navigate"}


@dataclass
class DOMElement:
    """§15 sözleşmesi."""
    selector: str
    role: str
    name: str
    text: str = ""
    attributes: dict = field(default_factory=dict)
    visibility: str = "visible"           # visible|hidden|partial
    enabled: bool = True
    bounding_box: tuple | None = None     # (x, y, w, h)
    confidence: float = 1.0
    source: str = "dom"                   # dom | ax | vision
    frame_id: str | None = None

    def to_ui_element(self) -> UIElement:
        return UIElement(
            identity=f"{self.source}:{self.selector[:48]}",
            name=self.name or self.text, role=self.role,
            bbox=self.bounding_box or (0, 0, 0, 0), source=self.source,
            confidence=self.confidence,
            visibility=self.visibility, enabled=self.enabled,
            attributes=dict(self.attributes), frame_id=self.frame_id)


@dataclass
class PageState:
    url: str
    title: str
    elements: list  # list[DOMElement]
    captured_at: float
    fingerprint: str = ""

    def dom_fingerprint(self) -> str:
        """Anlamlı DOM kimliği: selector+name+visibility sıralı özeti."""
        blob = json.dumps(sorted(
            [(e.selector, e.name, e.visibility, e.bounding_box)
             for e in self.elements]), ensure_ascii=False)
        return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------- engine
class BrowserEngine:
    """DI protokolü — gerçek uygulama PlaywrightEngine (BrowserAgent)."""

    NAME = "abstract"

    def status(self) -> dict:
        return {"engine": self.NAME, "available": False}

    def navigate(self, url: str) -> dict:  # pragma: no cover — abstract
        raise NotImplementedError

    def dom_elements(self) -> list:  # pragma: no cover
        raise NotImplementedError

    def ax_elements(self) -> list:  # pragma: no cover
        raise NotImplementedError

    def click(self, selector: str) -> dict:  # pragma: no cover
        raise NotImplementedError

    def type_text(self, selector: str, text: str) -> dict:  # pragma: no cover
        raise NotImplementedError

    def screenshot_bytes(self) -> bytes:  # pragma: no cover
        raise NotImplementedError

    def page_text(self) -> str:  # pragma: no cover
        raise NotImplementedError


class PlaywrightEngine(BrowserEngine):
    """Gerçek motor: mevcut BrowserAgent'ı sarar (DEĞİŞTİRMEDEN).

    Tarayıcı yoksa (bu konteyner) status() dürüst unavailable raporlar;
    sahte sayfa ÜRETİLMEZ.
    """

    NAME = "playwright"

    def __init__(self, agent=None, settings=None):
        if agent is None:
            from app.browser.agent import BrowserAgent
            agent = BrowserAgent(settings)
        self.agent = agent
        self._checked = False
        self._available = None

    def _ensure(self):
        if self._available is False:
            raise RuntimeError("tarayıcı bu ortamda yok (Playwright "
                               "kurulumu gerekli) — sahte sayfa YOK")
        try:
            self.agent._ensure()      # gerçek launch dener
            self._available = True
            return True
        except Exception as exc:  # noqa: BLE001
            self._available = False
            raise RuntimeError(
                f"tarayıcı başlatılamadı: {str(exc)[:120]}") from exc

    def status(self) -> dict:
        return {"engine": self.NAME, "available": self._available is True,
                "note": None if self._available else
                "launch henüz denenmedi ya da tarayıcı yok"}

    def navigate(self, url: str) -> dict:
        self._ensure()
        return self.agent.navigate(url)

    def _find_elements_as_dom(self, source: str, confidence: float) -> list:
        # BrowserAgent has no generic action dispatcher / read_ax method; use
        # its real find_elements() (actual Playwright DOM query) as the
        # concrete, honest data source for both DOM and AX-style listings.
        raw = self.agent.find_elements(selector="*", limit=200) or []
        out = []
        for el in raw:
            selector = f"#{el['id']}" if el.get("id") else f"{el.get('tag', '*')}:nth-of-type({el.get('index', 0) + 1})"
            name = el.get("name") or el.get("aria") or el.get("text", "")
            box = el.get("rect")
            out.append(DOMElement(
                selector=selector,
                role=el.get("tag", "unknown"),
                name=name,
                text=el.get("text", ""),
                attributes={k: el[k] for k in ("id", "name", "aria") if el.get(k)},
                visibility="visible" if box else "hidden",
                enabled=True,
                bounding_box=tuple(box.values()) if isinstance(box, dict) else (tuple(box) if box else None),
                confidence=confidence, source=source))
        return out

    def dom_elements(self) -> list:
        self._ensure()
        return self._find_elements_as_dom("dom", 0.9)

    def ax_elements(self) -> list:
        self._ensure()
        return self._find_elements_as_dom("ax", 0.95)

    def click(self, selector: str) -> dict:
        self._ensure()
        return self.agent.click(selector)

    def type_text(self, selector: str, text: str) -> dict:
        self._ensure()
        return self.agent.type(selector, text)

    def screenshot_bytes(self) -> bytes:
        self._ensure()
        result = self.agent.screenshot()
        from pathlib import Path
        path = (result or {}).get("path")
        return Path(path).read_bytes() if path else b""

    def page_text(self) -> str:
        self._ensure()
        return (self.agent.read_text() or {}).get("text", "")


# ---------------------------------------------------------------- runtime
class BrowserRuntime:
    """USER GOAL → NAVIGATE → DOM/AX → ACTION → OBSERVE → VERIFY."""

    def __init__(self, engine: BrowserEngine, *, frame_store=None,
                 approve_fn=None, now=None):
        self.engine = engine
        self.frame_store = frame_store      # vision fallback için (§17)
        self.approve = approve_fn
        self._now = now or time.monotonic
        self.page: PageState | None = None
        self.history: list[PageState] = []
        self.stats = {"navigations": 0, "stale_rejects": 0,
                      "captcha_hits": 0, "denied": 0}

    # ------------------------------------------------------------ state
    def refresh(self) -> PageState:
        """Sayfa durumunu gerçek motor'dan çek (DOM + AX)."""
        dom = self.engine.dom_elements()
        try:
            ax = self.engine.ax_elements()
        except Exception:  # noqa: BLE001 — AX yoksa DOM ile devam (dürüst)
            ax = []
        elements = ax + dom                  # AX öncelikli sırada tutulur
        state = PageState(url="about:blank", title="",
                          elements=elements, captured_at=time.time())
        state.fingerprint = state.dom_fingerprint()
        self.page = state
        self.history.append(state)
        if len(self.history) > 8:
            self.history.pop(0)
        return state

    def assert_fresh(self, tolerance: int = 0) -> PageState:
        """Stale DOM guard (§15): action öncesi yeniden ölç; fingerprint
        değişmişse RED — eski koordinatla tıklama YOK."""
        if self.page is None:
            raise StaleDOMError("sayfa durumu hiç ölçülmedi")
        fresh = self.refresh()
        prev = self.history[-2] if len(self.history) >= 2 else fresh
        if prev.fingerprint != fresh.fingerprint and tolerance == 0:
            self.stats["stale_rejects"] += 1
            raise StaleDOMError(
                "DOM değişti — action yeniden planlanmalı (stale DOM RED)")
        return fresh

    # ------------------------------------------------------------ risk
    def risk_for(self, action: str, params: dict | None = None) -> dict:
        params = params or {}
        url = str(params.get("url", "") or (self.page.url if self.page else ""))
        level = "SAFE"
        if action in CRITICAL_ACTIONS or CRITICAL_URL.search(url):
            level = "CRITICAL"
        elif action in MEDIUM_ACTIONS or HIGH_RISK_URL.search(url):
            level = "MEDIUM"
        return {"action": action, "level": level,
                "requires_approval": level in ("MEDIUM", "CRITICAL"),
                "critical": level == "CRITICAL"}

    def guard(self, action: str, params: dict | None = None,
              approved: bool = False) -> dict:
        """Risk + captcha + onay kapısı (§18)."""
        risk = self.risk_for(action, params)
        if self.detect_captcha():
            self.stats["captcha_hits"] += 1
            raise CaptchaDetected(
                "CAPTCHA tespit edildi — otomatik bypass YASAK; insan "
                "onayı/aksiyonu gerekir")
        if risk["requires_approval"]:
            human_ok = approved or (self.approve is not None and
                                    self.approve(action, params))
            if not human_ok:
                self.stats["denied"] += 1
                raise BrowserDenied(
                    f"onay gerekli: {action} (risk={risk['level']})")
        return risk

    def detect_captcha(self) -> bool:
        """Sayfa metni/elemanlarında captcha imzası (gerçek sinyal)."""
        try:
            text = self.engine.page_text().lower()
        except Exception:  # noqa: BLE001
            return False
        return any(m in text for m in CAPTCHA_MARKERS)

    # ------------------------------------------------------------ akış
    def navigate(self, url: str, *, approved: bool = False) -> dict:
        self.guard("navigate", {"url": url}, approved=approved)
        out = self.engine.navigate(url)
        self.stats["navigations"] += 1
        self.refresh()
        return out

    def ground_element(self, query: str) -> UIElement | None:
        """§16/§17: AX öncelikli → DOM → vision fallback."""
        if self.page is None:
            self.refresh()
        ax = [e for e in self.page.elements if e.source == "ax"]
        target = ground(query, [e.to_ui_element() for e in ax])
        if target is None:
            dom = [e for e in self.page.elements if e.source == "dom"]
            target = ground(query, [e.to_ui_element() for e in dom])
        if target is None and self.frame_store is not None:
            # §17 vision fallback — STALE frame kullanılmaz
            try:
                frame = self.frame_store.action_frame()
            except StaleFrameError:
                return None
            vis = getattr(self.engine, "vision_elements", None)
            if vis:
                els = [e.to_ui_element() for e in vis(frame)]
                target = ground(query, els)
        return target.element if target else None

    def resolve_dom_ax_conflict(self, selector: str,
                                name: str | None = None) -> dict:
        """§16: DOM ile AX çelişirse VERIFY — belirsizse action verilmez.

        AX node'ları DOM selector taşımaz; eşleştirme name üzerinden
        (name verilmişse), DOM tarafı selector üzerinden.
        """
        ax = next((e for e in self.page.elements if e.source == "ax"
                   and ((name and e.name == name)
                        or selector in e.selector)), None)
        dom = next((e for e in self.page.elements
                    if e.source == "dom" and selector in e.selector), None)
        if ax is None or dom is None:
            return {"conflict": False, "verifiable": False,
                    "reason": "tek kaynak — çapraz doğrulama mümkün değil"}
        same = (ax.bounding_box == dom.bounding_box
                and ax.enabled == dom.enabled)
        return {"conflict": not same, "verifiable": True,
                "ax_box": ax.bounding_box, "dom_box": dom.bounding_box,
                "actionable": same}

    def act(self, action: str, query_or_selector: str = "",
            params: dict | None = None, *, approved: bool = False) -> dict:
        """Action akışı: guard → stale check → grounding → eylem."""
        params = params or {}
        self.guard(action, params, approved=approved)
        selector = query_or_selector
        if action in ("click", "type") and not selector.startswith(("[", "#",
                                                                    ".")):
            el = self.ground_element(query_or_selector)
            if el is None:
                return {"state": "FAILED",
                        "error": f"hedef bulunamadı: {query_or_selector}"}
            selector = el.identity.split(":", 1)[-1]
        state = self.assert_fresh()        # stale DOM → RED (replan sinyali)
        if action == "click":
            out = self.engine.click(selector)
        elif action == "type":
            out = self.engine.type_text(selector, params.get("text", ""))
        else:
            out = {"action": action, "params": params}
        after = self.refresh()
        verified = after.fingerprint != state.fingerprint
        return {"state": "OK", "result": out,
                "dom_changed": verified}   # observe sinyali (verify commit 8)

    def status(self) -> dict:
        return {"engine": self.engine.status(), "stats": dict(self.stats),
                "page": (self.page.url if self.page else None)}
