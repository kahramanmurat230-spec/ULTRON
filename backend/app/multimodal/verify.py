"""WAVE 4 — Action Verification (§13): EXPECTED vs ACTUAL + kurtarma.

CLICK → beklenen durum değişimi → gözlem (screenshot/UIA/DOM/window) →
verify. Başarısızlık zinciri (sonsuz retry YASAK):
  RETRY (aynı hedef, max N) → ALTERNATIVE GROUNDING (yedek element) →
  REPLAN (üst kata bildir) → GIVE_UP (poison raporu)

Gözlem sağlayıcıları GERÇEKTİR: ekran content-hash farkı (PIL), DOM
fingerprint (browser runtime), pencere başlığı (cihaz katmanı), UIA
element durumu. Tahmin/uydurma yok — gözlem gelmediyse verify FAIL.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

MAX_RETRIES = 2                     # aynı hedefte toplam 1+2 deneme


@dataclass
class ExpectedState:
    """Action sonrası beklenen gözlemlenebilir durum."""
    dom_changed: bool | None = None          # browser: fingerprint farkı
    screen_changed: bool | None = None       # vision: content-hash farkı
    window_title_contains: str | None = None # computer: odak/pencere
    element_gone: str | None = None          # selector/name kaybolmalı
    text_present: str | None = None          # gözlem metninde geçmeli
    text_absent: str | None = None

    def criteria_count(self) -> int:
        return sum(1 for v in self.__dict__.values() if v is not None)


@dataclass
class Observation:
    """Gerçek gözlem — hangi kaynak ne gördü."""
    source: str                               # screen|dom|window|uia|text
    at: float
    data: dict = field(default_factory=dict)
    # data: {"screen_changed": bool, "dom_changed": bool,
    #        "window_title": str, "texts": [...], "elements": [...]}


@dataclass
class VerifyResult:
    ok: bool
    detail: str
    checked: list = field(default_factory=list)
    evidence: dict = field(default_factory=dict)


def verify(expected: ExpectedState, obs: Observation) -> VerifyResult:
    """EXPECTED vs ACTUAL — belirsiz gözlem FAIL sayılır (tahmin YOK)."""
    checked = []
    d = obs.data
    if expected.dom_changed is not None:
        got = d.get("dom_changed")
        if got is None:
            return VerifyResult(False, "DOM gözlemi yok (belirsiz → fail)",
                                checked + ["dom_changed"], d)
        checked.append("dom_changed")
        if bool(got) != expected.dom_changed:
            return VerifyResult(False,
                                f"dom_changed={got}, beklenen "
                                f"{expected.dom_changed}", checked, d)
    if expected.screen_changed is not None:
        got = d.get("screen_changed")
        if got is None:
            return VerifyResult(False, "ekran gözlemi yok (belirsiz → fail)",
                                checked + ["screen_changed"], d)
        checked.append("screen_changed")
        if bool(got) != expected.screen_changed:
            return VerifyResult(
                False, f"screen_changed={got}, beklenen "
                       f"{expected.screen_changed}", checked, d)
    if expected.window_title_contains is not None:
        title = str(d.get("window_title") or "")
        checked.append("window_title")
        if expected.window_title_contains.lower() not in title.lower():
            return VerifyResult(
                False, f"pencere '{expected.window_title_contains}' değil "
                       f"(aktif: '{title[:40]}')", checked, d)
    if expected.element_gone is not None:
        names = [str(e).lower() for e in d.get("elements", [])]
        checked.append("element_gone")
        if any(expected.element_gone.lower() in n for n in names):
            return VerifyResult(
                False, f"element hâlâ görünür: {expected.element_gone}",
                checked, d)
    if expected.text_present is not None:
        blob = " ".join(str(t) for t in d.get("texts", [])).lower()
        checked.append("text_present")
        if expected.text_present.lower() not in blob:
            return VerifyResult(
                False, f"metin gözlemlenmedi: {expected.text_present!r}",
                checked, d)
    if expected.text_absent is not None:
        blob = " ".join(str(t) for t in d.get("texts", [])).lower()
        checked.append("text_absent")
        if expected.text_absent.lower() in blob:
            return VerifyResult(
                False, f"yasak metin hâlâ var: {expected.text_absent!r}",
                checked, d)
    if not checked:
        return VerifyResult(False, "beklenen durum tanımsız (kriter yok)",
                            [], d)
    return VerifyResult(True, f"{len(checked)} kriter doğrulandı", checked, d)


# ---------------------------------------------------------------- gözlem
def observe_screen(before_hash: str | None, after_image: bytes) -> Observation:
    """Gerçek ekran gözlemi: content-hash farkı (PIL downsample)."""
    from app.multimodal.vision_runtime import _downsample_hash
    after = _downsample_hash(after_image)
    changed = (before_hash is not None and before_hash != after)
    return Observation("screen", time.time(),
                       {"screen_changed": changed, "hash_after": after})


def observe_dom(before_fingerprint: str | None, after_fingerprint: str | None
                ) -> Observation:
    changed = (before_fingerprint is not None
               and after_fingerprint is not None
               and before_fingerprint != after_fingerprint)
    return Observation("dom", time.time(), {"dom_changed": changed})


def observe_window(title: str | None) -> Observation:
    return Observation("window", time.time(),
                       {"window_title": title or ""})


def observe_uia(elements: list) -> Observation:
    """UIA/OCR element listesi → isim/metin envanteri."""
    names = []
    texts = []
    for e in elements:
        name = getattr(e, "name", None) or (e.get("name") if isinstance(
            e, dict) else None)
        text = getattr(e, "text", None) or (e.get("text") if isinstance(
            e, dict) else None)
        if name:
            names.append(name)
        if text:
            texts.append(text)
    return Observation("uia", time.time(),
                       {"elements": names, "texts": texts})


# ---------------------------------------------------------------- kurtarma
RETRY = "RETRY"
ALTERNATIVE = "ALTERNATIVE_GROUNDING"
REPLAN = "REPLAN"
GIVE_UP = "GIVE_UP"


class RecoveryPlanner:
    """Verify başarısızlığından sonraki adım — deterministik, limitli."""

    def __init__(self, max_retries: int = MAX_RETRIES):
        self.max_retries = max_retries

    def next(self, attempt: int, alternatives: int) -> str:
        """attempt: bu hedefte şimdiye kadarki deneme sayısı (1'den)."""
        if attempt <= self.max_retries:
            return RETRY
        if alternatives > 0:
            return ALTERNATIVE          # yedek element ile tekrar
        if attempt <= self.max_retries + 2:
            return REPLAN               # üst kat yeni plan yapar
        return GIVE_UP                  # poison — sonsuz döngü YOK


class VerifiedActionLoop:
    """ACTION → OBSERVE → VERIFY → (RETRY/ALTERNATIVE/REPLAN) döngüsü.

    executor: fn(target) -> bool (eylem başarısı)
    observer: fn() -> Observation (eylem sonrası gerçek gözlem)
    """

    def __init__(self, executor, observer, expected: ExpectedState,
                 *, targets: list = None, planner: RecoveryPlanner | None =
                 None, on_event=None, sleep=None, max_total_attempts: int =
                 12):
        self.executor = executor
        self.observer = observer
        self.expected = expected
        self.targets = list(targets or [])
        self.planner = planner or RecoveryPlanner()
        self.on_event = on_event or (lambda *a: None)
        self._sleep = sleep or (lambda: time.sleep(0))
        self.max_total_attempts = max_total_attempts   # GLOBAL tavan
        self.log: list[dict] = []

    def run(self) -> dict:
        """Hedefler üzerinde doğrulanmış eylem — sonuç raporu."""
        queue = list(self.targets) or [None]
        attempt = 0
        total = 0
        while queue:
            target = queue.pop(0)
            attempt = 0
            while True:
                if total >= self.max_total_attempts:
                    return {"state": GIVE_UP, "attempts": total,
                            "target": target, "detail": "genel deneme tavanı "
                            "aşıldı (sonsuz döngü koruması)",
                            "log": self.log}
                total += 1
                attempt += 1
                ok_exec = self.executor(target)
                obs = self.observer() if ok_exec else None
                if obs is not None:
                    res = verify(self.expected, obs)
                else:
                    res = VerifyResult(False, "eylem başarısız (exec hatası)")
                self.log.append({"target": str(target), "attempt": attempt,
                                 "ok": res.ok, "detail": res.detail})
                self.on_event("verify", res)
                if res.ok:
                    return {"state": "VERIFIED", "attempts": attempt,
                            "target": target, "detail": res.detail,
                            "log": self.log}
                remaining_alts = len(queue)
                nxt = self.planner.next(attempt, remaining_alts)
                if nxt == RETRY:
                    self._sleep()
                    continue
                if nxt == ALTERNATIVE:
                    self.on_event("alternative", queue[0])
                    break                            # sıradaki hedefe geç
                if nxt == REPLAN:
                    return {"state": "REPLAN", "attempts": attempt,
                            "target": target, "detail": res.detail,
                            "log": self.log}
                return {"state": "GIVE_UP", "attempts": attempt,
                        "target": target, "detail": res.detail,
                        "log": self.log}
        return {"state": "REPLAN", "attempts": attempt, "target": None,
                "detail": "tüm alternatifler tükendi", "log": self.log}
