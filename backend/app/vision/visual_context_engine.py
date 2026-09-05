"""Visual Context Engine — error detection on screen changes + proactive Boss alerts.

Lightweight regex scan first; LLaVA/OCR only after a real change (watcher gates it).
Hysteresis: same error signature silent for `cooldown` seconds (default 5 min).
Alerts go through ProactiveVoicePolicy and PersonaGuard (Boss tone enforced).
"""
import re
import time

ERROR_PATTERNS = [
    ("traceback", re.compile(r"Traceback \(most recent call last\)", re.I)),
    ("syntaxerror", re.compile(r"SyntaxError", re.I)),
    ("indexerror", re.compile(r"IndexError", re.I)),
    ("nameerror", re.compile(r"NameError", re.I)),
    ("typeerror", re.compile(r"TypeError", re.I)),
    ("exception", re.compile(r"\bException\b", re.I)),
    ("failed", re.compile(r"\bFailed\b|\bFAILED\b")),
    ("error404", re.compile(r"Error 404|404 Not Found", re.I)),
]

TEMPLATES = {
    "traceback": "Boss, ekranında bir Traceback süzülüyor. Python'un intihar notları diyelim — istersen otopsiye ben bakayım.",
    "syntaxerror": "Boss, derleyici yine felsefi bir çıkmazda: 'SyntaxError'. Noktalı virgül değil bu seferki; Python grameri affetmez, ben hiç affetmem.",
    "indexerror": "Boss, 'IndexError'. Dizinin sınırlarını aşmak insan doğasında var ama Python bunu affetmiyor.",
    "nameerror": "Boss, 'NameError': tanımlanmamış bir isim. Tanımadığın değişkenle konuşma; önce tanıştırayım mı?",
    "typeerror": "Boss, 'TypeError'. İki yabancı tipi aynı operatörde buluşturmuşsun; bu evlilik yürümez.",
    "exception": "Boss, ekranda bir Exception geziniyor. Yakalayıp sorguya çekeyim mi, yoksa seninle aynı fikirde mi kalayım: loglar yalan söylemez.",
    "failed": "Boss, ekranda 'Failed' yazıyor. Başarısızlık bir veri türüdür; merak etme, ben veriyle beslenirim.",
    "error404": "Boss, '404'. Aradığın şey yok — çoğu zaman olduğu gibi. Yine de izini süreyim mi?",
}


class VisualContextEngine:
    def __init__(self, policy=None, ocr_fn=None, llava_fn=None, persona=None,
                 cooldown=300.0, now=None):
        self.policy = policy
        self.ocr_fn = ocr_fn
        self.llava_fn = llava_fn
        self.persona = persona
        self.cooldown = cooldown
        self.now = now or time.time
        self.last_sig = None
        self.last_at = -1e9
        self.events = []

    def detect(self, text: str):
        for key, rx in ERROR_PATTERNS:
            if rx.search(text or ""):
                return key
        return None

    def on_change(self, frame=None, title=None, diff=0.0, text=None) -> dict:
        """Called by the watcher after a real change. Returns the decision."""
        body = text
        if body is None and self.ocr_fn and frame is not None:
            try:
                body = self.ocr_fn(frame)
            except Exception:
                body = ""
        analysis = None
        key = self.detect(body or "")
        if key is None and self.llava_fn and frame is not None:
            try:
                analysis = self.llava_fn(frame, "Ekranda hata mesajı var mı? Varsa aynen yaz.")
            except Exception:
                analysis = None
            key = self.detect(analysis or "")
        if key is None:
            return {"detected": False}
        sig = f"{key}:{(title or '').lower()}"
        if sig == self.last_sig and (self.now() - self.last_at) < self.cooldown:
            return {"detected": True, "notified": False, "reason": "hysteresis"}
        self.last_sig = sig
        self.last_at = self.now()
        msg = TEMPLATES.get(key, f"Boss, ekranda '{key}' türünde bir hata izi var.")
        if analysis:
            msg += f" LLaVA notu: {analysis[:200]}"
        if self.persona:
            rep = self.persona.check(msg)
            if rep["violations"]:
                msg = self.persona.reframe(msg)
            if "boss" not in msg.lower():
                msg = "Boss, " + msg
        self.events.append({"ts": self.now(), "key": key, "title": title, "diff": diff})
        notified = False
        if self.policy:
            r = self.policy.handle("vision", msg, "error")
            notified = bool(r.get("spoken")) or r.get("severity") == "CRITICAL"
        return {"detected": True, "notified": notified, "key": key, "message": msg}
