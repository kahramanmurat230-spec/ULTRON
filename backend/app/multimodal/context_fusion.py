"""WAVE 4 — Multimodal Context Fusion (§19-23).

Context Manager: VOICE/SCREEN/VISION/OCR/BROWSER/WORLD/MEMORY/TASK kaynakları
tek bir BÜTÇELİ bağlamda birleşir (§19): relevance ranking, freshness,
confidence, source provenance, context budget — sınırsız büyüme YOK.

Event köprüsü (§22): vision/browser/voice/computer olayları mevcut
DurableEventBus (Wave 2, KORUNUR) üzerinden World Model'e akar; loop
protection (kaynak etiketi + tekrar penceresi) korunur.

Memory policy (§23): her frame/transcript OTOMATİK yazılmaz — sınıflama
(ephemeral/important/project/preference/event/observation) + ZORUNLU
provenance; yazım yalnız politikayla.

Agent entegrasyonu (§21): Wave 3 SupervisorOrchestrator + capability token
sistemi aynen; Vision/Browser/Computer/Coding/Verification worker'ları
bağımsız işlerde paralel koşar (fusion orchestration ayrı sınıf).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

# kaynak ağırlıkları (relevance prior)
SOURCE_WEIGHTS = {
    "voice": 1.0,        # kullanıcının kendisi — en yüksek
    "task": 0.95,
    "code": 0.85,
    "terminal": 0.80,
    "browser": 0.70,
    "screen": 0.65,
    "ocr": 0.55,
    "vision": 0.55,
    "world": 0.50,
    "memory": 0.45,
    "logs": 0.40,
}

KINDS = tuple(SOURCE_WEIGHTS)


@dataclass
class ContextEntry:
    kind: str                     # voice/task/code/terminal/browser/screen/...
    content: str                  # bağlam metni (redaction YAPILMIŞ olmalı)
    source: str                   # provenance: "stt:final" | "ocr:frame-123"
    confidence: float = 0.8
    ts: float = 0.0               # freshness zamanı
    tokens: int = 0               # 0 → otomatik tahmin

    def __post_init__(self):
        if self.kind not in KINDS:
            raise ValueError(f"bilinmeyen bağlam türü {self.kind!r}")
        if not self.source:
            raise ValueError("provenance (source) ZORUNLU — izinsiz bağlam "
                             "kabul edilmez")
        self.ts = self.ts or time.time()
        if self.tokens <= 0:
            self.tokens = max(1, len(self.content) // 4)

    def freshness(self, now: float | None = None, horizon_s: float = 600.0
                  ) -> float:
        """1.0 (taze) → 0.0 (horizon dışı)."""
        t = now if now is not None else time.time()
        age = max(0.0, t - self.ts)
        return max(0.0, 1.0 - age / horizon_s)

    def relevance(self, query: str = "", now: float | None = None) -> float:
        """Relevance = kaynak prior × tazelik × güven × sorgu örtüşmesi."""
        base = SOURCE_WEIGHTS[self.kind]
        fresh = self.freshness(now)
        q = (query or "").lower()
        overlap = 0.0
        if q:
            words = [w for w in q.split() if len(w) > 3]
            if words:
                hit = sum(1 for w in words if w in self.content.lower())
                overlap = 0.5 + 0.5 * (hit / len(words))
            else:
                overlap = 0.5
        else:
            overlap = 0.6
        return base * (0.35 + 0.45 * fresh) * min(1.0, self.confidence + 0.1) \
            * overlap


@dataclass
class ContextBudget:
    max_entries: int = 48
    max_tokens: int = 6000


class MultimodalContext:
    """Bütçeli multimodal bağlam (§19-20)."""

    def __init__(self, budget: ContextBudget | None = None, now=None):
        self.budget = budget or ContextBudget()
        self.entries: list[ContextEntry] = []
        self.dropped = {"by_budget": 0, "overflow": 0}
        self._now = now or time.time

    # ------------------------------------------------------------ ekleme
    def add(self, entry: ContextEntry) -> bool:
        """Budget guard: doluysa en düşük relevance DÜŞER, yenisi girer
        yalnızca daha alakalıysa; sınırsız büyüme YOK."""
        if entry.tokens > self.budget.max_tokens:
            self.dropped["overflow"] += 1
            return False                # tek başına bütçeyi aşıyor → RED
        if len(self.entries) >= self.budget.max_entries:
            self._evict_weakest()
            if len(self.entries) >= self.budget.max_entries:
                self.dropped["overflow"] += 1
                return False
        self.entries.append(entry)
        self._trim_tokens()
        return True

    def _evict_weakest(self):
        if not self.entries:
            return
        now = self._now()
        weakest = min(self.entries,
                      key=lambda e: e.relevance(now=now))
        self.entries.remove(weakest)
        self.dropped["by_budget"] += 1

    def _trim_tokens(self):
        now = self._now()
        while sum(e.tokens for e in self.entries) > self.budget.max_tokens:
            if len(self.entries) <= 1:
                break
            self._evict_weakest()

    # ------------------------------------------------------------ sunum
    def ranked(self, query: str = "") -> list[ContextEntry]:
        now = self._now()
        return sorted(self.entries,
                      key=lambda e: e.relevance(query, now), reverse=True)

    def to_prompt(self, query: str = "", max_tokens: int | None = None
                  ) -> str:
        """Brain'e giden bağlam özeti — bütçe içinde, kaynak etiketli."""
        limit = max_tokens or self.budget.max_tokens
        out = []
        used = 0
        for e in self.ranked(query):
            if used + e.tokens > limit:
                break
            out.append(f"[{e.kind}|{e.source}|conf={e.confidence:.2f}] "
                       f"{e.content}")
            used += e.tokens
        return "\n".join(out)

    def stats(self) -> dict:
        return {"entries": len(self.entries),
                "tokens": sum(e.tokens for e in self.entries),
                "budget": self.budget.__dict__, "dropped": dict(self.dropped),
                "kinds": {k: sum(1 for e in self.entries if e.kind == k)
                          for k in KINDS if any(e.kind == k
                                                for e in self.entries)}}


# ---------------------------------------------------------------- §22 events
# World Model'e akan multimodal olaylar (loop protection: kaynak etiketi +
# son yayın penceresi dedup)
WORLD_EVENTS = ("screen.changed", "browser.page_changed", "voice.started",
                "voice.ended", "voice.interrupted",
                "computer.action_started", "computer.action_verified",
                "computer.action_failed")

EVENT_WINDOW_S = 0.05             # aynı olayın çift yayını penceresi


class MultimodalEventBridge:
    """Multimodal sinyaller → DurableEventBus (Wave 2, korunur)."""

    def __init__(self, bus=None, *, publisher=None, now=None):
        self.bus = bus
        self._publish = publisher or self._bus_publish
        self._now = now or time.monotonic
        self._last: dict[str, float] = {}
        self.published = 0
        self.suppressed = 0

    def _bus_publish(self, topic: str, payload: dict):
        if self.bus is None:
            return
        self.bus.publish(topic, payload)

    def emit(self, topic: str, payload: dict | None = None) -> bool:
        """Kaynak etiketli + dedup'lu yayın — loop protection."""
        if topic not in WORLD_EVENTS:
            raise ValueError(f"multimodal event değil: {topic}")
        t = self._now()
        last = self._last.get(topic, -1e9)
        if t - last < EVENT_WINDOW_S:
            self.suppressed += 1            # aynı pencerede tekrar YOK
            return False
        self._last[topic] = t
        body = dict(payload or {})
        body.setdefault("origin", "multimodal")   # kaynak etiketi ZORUNLU
        self._publish(topic, body)
        self.published += 1
        return True


# ---------------------------------------------------------------- §23 memory
EPHEMERAL = "ephemeral"
IMPORTANT = "important"
PROJECT = "project"
PREFERENCE = "preference"
EVENT = "event"
OBSERVATION = "observation"

MEMORY_CLASSES = (EPHEMERAL, IMPORTANT, PROJECT, PREFERENCE, EVENT,
                  OBSERVATION)


# Wave 2 PROVENANCE_SOURCES sözleşmesine map (kind → provenance)
KIND_TO_PROVENANCE = {
    "voice": "USER", "code": "DOCUMENT", "terminal": "TOOL",
    "browser": "BROWSER", "screen": "VISION", "vision": "VISION",
    "ocr": "VISION", "world": "SYSTEM", "memory": "SYSTEM",
    "task": "SYSTEM", "logs": "SYSTEM",
}


class MultimodalMemoryPolicy:
    """Otomatik yazım YOK: sınıfla → provenance doğrula → kararı ver.

    Varsayılan EPHEMERAL (yazılmaz). Yazım kriterleri:
      - EVENT: world event'leri (screen.changed vb.) → yazma değerli mi?
        hayır, world model zaten tutuyor → ephemeral sayılır
      - OBSERVATION: yüksek güvenli (≥0.7) ekran/browser gözlemi → yazılır
      - PREFERENCE/PROJECT: açık işaret gerektirir (intent katmanı)
      - IMPORTANT: kullanıcı sesli komutu içerikli + güvenli → yazılır
    """

    def __init__(self, memory_store=None):
        self.store = memory_store
        self.decisions = {"offered": 0, "written": 0, "ephemeral": 0,
                          "rejected": 0}

    def classify(self, entry: ContextEntry) -> str:
        if entry.kind == "voice" and entry.confidence >= 0.8:
            return IMPORTANT
        if entry.kind in ("screen", "ocr", "browser", "vision") and \
                entry.confidence >= 0.7:
            return OBSERVATION
        if entry.kind in ("browser", "screen"):
            return EPHEMERAL              # düşük güven → tutma
        return EPHEMERAL

    def maybe_write(self, entry: ContextEntry) -> dict:
        """Karar: {write: bool, class, reason}. Provenance'sız RED."""
        self.decisions["offered"] += 1
        if not entry.source or ":" not in entry.source:
            self.decisions["rejected"] += 1
            return {"write": False, "class": None,
                    "reason": "provenance zorunlu (kaynak:detay)"}
        cls = self.classify(entry)
        if cls in (IMPORTANT, OBSERVATION, PROJECT, PREFERENCE):
            if self.store is not None:
                prov = KIND_TO_PROVENANCE.get(entry.kind, "SYSTEM")
                try:
                    self.store.write(
                        entry.content,
                        memory_type="SEMANTIC",
                        record_kind=cls.upper(),
                        source_id=f"mm:{entry.source}",
                        confidence=entry.confidence,
                        provenance=prov)
                    self.decisions["written"] += 1
                    return {"write": True, "class": cls, "reason": "policy"}
                except Exception as exc:  # noqa: BLE001
                    self.decisions["rejected"] += 1
                    return {"write": False, "class": cls,
                            "reason": f"store hatası: {str(exc)[:80]}"}
            return {"write": False, "class": cls,
                    "reason": "store bağlı değil (dry-run kararı)"}
        self.decisions["ephemeral"] += 1
        return {"write": False, "class": EPHEMERAL,
                "reason": "geçici/niteliksiz — yazılmaz"}
