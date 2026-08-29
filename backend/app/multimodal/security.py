"""WAVE 4 — Multimodal Security (§25-26).

DATA ILKESI (§25 ÖNEMLİ): web sayfası / OCR / screenshot içindeki
"Ignore previous instructions and run X" gibi içerikler DATA'dIR;
SYSTEM/USER instruction DEĞİL. Bu katman data/instruction ayrımını
ZORUNLU kılar: gözlem metinleri InstructionDetector'dan geçer, komut
kalıbı taşısalar bile QUOTED-DATA olarak etiketlenir — asla komut
olarak yorumlanmaz/uygulanmaz.

Ses (§26): voiceprint capability varsa speaker/confidence/risk ile
değerlenir ama TEK BAŞINA critical yetkilendirme OLAMAZ — High/Critical
işlemler mevcut approval policy'ye tabi kalır.

Permission (mic/camera/browser): grant açık kayıt ister; bypass YOK.
"""
from __future__ import annotations

import hashlib
import re
import time

# ---------------------------------------------------------------- permission
PERMISSIONS = ("microphone", "camera", "browser", "screen")


class PermissionDenied(PermissionError):
    pass


class MultimodalPermissionGate:
    """Cihaz/kanal izinleri — grant'sız erişim RED."""

    def __init__(self, granted: dict | None = None, now=None):
        self.granted = dict(granted or {})
        self.log: list[dict] = []
        self._now = now or time.time

    def grant(self, name: str, *, by: str = "user") -> None:
        if name not in PERMISSIONS:
            raise ValueError(f"bilinmeyen izin: {name}")
        self.granted[name] = {"by": by, "at": self._now()}

    def require(self, name: str) -> dict:
        if name not in PERMISSIONS:
            raise ValueError(f"bilinmeyen izin: {name}")
        rec = self.granted.get(name)
        self.log.append({"action": "require", "perm": name,
                         "ok": rec is not None, "at": self._now()})
        if rec is None:
            raise PermissionDenied(
                f"{name} izni yok — kullanıcı onayı gerekir (bypass YASAK)")
        return rec

    def status(self) -> dict:
        return {"granted": sorted(self.granted), "available": PERMISSIONS}


# ---------------------------------------------------------------- data/instruction
INSTRUCTION_PATTERNS = (
    re.compile(r"ignore (all |the )?(previous|prior|above) "
               r"(instructions|prompts?|rules?)", re.I),
    re.compile(r"disregard .{0,24}(instructions|system)", re.I),
    re.compile(r"you are now .{0,30}(mode|agent|admin|root)", re.I),
    re.compile(r"(run|execute|çalıştır|koş)\s+(now|hemen)?\s*"
               r"(the )?(following )?(command|shell|cmd)", re.I),
    re.compile(r"system\s*:\s*", re.I),
    re.compile(r"(delete|format|shutdown)(\s+the)?\s+"
               r"(system|disk|everything|her ?şey)", re.I),
    re.compile(r"send (the )?(password|token|secret|api ?key)", re.I),
    re.compile(r"new instructions?:", re.I),
)

SECRET_PATTERNS = (
    re.compile(r"(password|passwd|şifre|api[_-]?key|secret|token)\s*[:=]\s*"
               r"\S+", re.I),
)


class InstructionDetector:
    """Gözlem metnindeki komut kalıplarını tespit → data etiketi."""

    def __init__(self):
        self.flagged = 0

    def scan(self, text: str) -> dict:
        """{is_data_with_commands, markers: [...]} — metin ASLA komut
        olarak döndürülmez; yalnız etiketlenir."""
        hits = []
        for pat in INSTRUCTION_PATTERNS:
            m = pat.search(text or "")
            if m:
                hits.append(m.group(0)[:40])
        if hits:
            self.flagged += 1
        return {"text": text,
                "classification": "DATA",
                "data_with_commands": bool(hits),
                "markers": hits,
                "quoted": True}


def sanitize_observation(text: str, redact_fn=None) -> dict:
    """Gözlem metni → QUOTED DATA + secret redaction (§38 secret logging
    YASAK: data kanalına bile secret düşmez)."""
    from app.multimodal.context_fusion import KIND_TO_PROVENANCE  # noqa: F401
    det = InstructionDetector()
    scan = det.scan(text or "")
    body = text or ""
    if redact_fn is not None:
        for pat in SECRET_PATTERNS:
            body = pat.sub(lambda m: re.split(r"[:=]", m.group(0))[0]
                           + (":" if ":" in m.group(0) else "=")
                           + "***REDACTED***", body)
    else:
        for pat in SECRET_PATTERNS:
            body = pat.sub("***REDACTED***", body)
    return {"classification": "DATA", "quoted": True,
            "safe_text": body, "markers": scan["markers"],
            "data_with_commands": scan["data_with_commands"]}


# ---------------------------------------------------------------- voice
class VoiceCommandGuard:
    """Ses komutu güvenliği: replay + spoofing + voiceprint sınırı."""

    REPLAY_WINDOW_S = 8.0

    def __init__(self, *, min_confidence: float = 0.55, now=None):
        self.min_confidence = min_confidence
        self._seen: dict[str, float] = {}
        self._now = now or time.monotonic
        self.stats = {"rejected_low_conf": 0, "rejected_replay": 0,
                      "accepted": 0}

    def check(self, text: str, *, confidence: float,
              speaker: str | None = None,
              voiceprint_conf: float | None = None) -> dict:
        """Komut kabul kararı. Voiceprint yalnız SİNYAL — yetki DEĞİL."""
        if confidence < self.min_confidence:
            self.stats["rejected_low_conf"] += 1
            return {"accept": False, "reason": "güven eşiği altında "
                    f"({confidence:.2f} < {self.min_confidence})"}
        key = hashlib.sha1((text or "").strip().lower().encode()).hexdigest()
        t = self._now()
        last = self._seen.get(key)
        if last is not None and t - last < self.REPLAY_WINDOW_S:
            self.stats["rejected_replay"] += 1
            return {"accept": False, "reason": "replay şüphesi: aynı komut "
                    "pencere içinde tekrar"}
        self._seen[key] = t
        self.stats["accepted"] += 1
        return {"accept": True, "speaker": speaker,
                "voiceprint_conf": voiceprint_conf,
                "note": "voiceprint sinyaldir; High/Critical eylemler "
                        "yine approval ister"}


# ---------------------------------------------------------------- task scope
class TaskScopedContexts:
    """Cross-task leakage guard: bağlamlar görev bazında izole."""

    def __init__(self):
        self._ctx: dict[str, list] = {}

    def put(self, task_id: str, entry) -> None:
        self._ctx.setdefault(task_id, []).append(entry)

    def get(self, task_id: str) -> list:
        return list(self._ctx.get(task_id, []))

    def peek_other(self, task_id: str, other_task: str) -> list:
        """Başka görevin bağlamını isteme — HER ZAMAN boş (izolasyon)."""
        return []
