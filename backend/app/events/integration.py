"""WAVE 2 — Integration core: Event ↔ World ↔ Memory ↔ Proactive (§19-§22).

Köprüler DurableEventBus aboneleri olarak çalışır:
- EventToWorldBridge : world.change.*  → WorldStore.upsert (sürüm+tarih)
- EventToMemoryBridge: sınıflandırma   → MemoryStore (EPHEMERAL asla yazılmaz)
- EventToProactive   : olay → politika → karar (cooldown+dedup+önem; spam yok)
- capture_world()    : Foundation WorldModel anlık görüntüsünü WorldStore'a
                       ve değişimde world.change.* olaylarına çevirir
- snapshot_world_to_memory(): dünya durumu periyodik EPISODIC gözlem olarak
                       belleğe işlenir (world→memory)

Hepsi additive; Foundation WorldModel ve Wave 1 motorları değişmez.
"""
from __future__ import annotations

import time

from app.events.schema import classify_event
from app.memory.intelligence import ConflictResolver

# olay → varlık eşlemesi (topic kısmi adı → entity_type)
_TOPIC_ENTITY = {
    "screen": "SCREEN", "process": "PROCESSES", "presence": "PRESENCE",
    "network": "NETWORK", "task": "TASKS", "goal": "GOALS",
    "device": "DEVICE", "browser": "BROWSER", "files": "FILES",
    "system": "SYSTEM_HEALTH", "time": "CURRENT_TIME", "user": "USER",
    "application": "APPLICATIONS", "io": "IO_DEVICES", "os": "OS",
    "window": "ACTIVE_WINDOW", "events": "RECENT_EVENTS",
}


class EventToWorldBridge:
    """world.change.* olaylarını WorldStore'a uygular (§19)."""

    def __init__(self, bus, world_store):
        self.bus = bus
        self.world = world_store
        self.applied = 0
        self.errors = 0
        self.sub_id = bus.subscribe("world.change.*", self.handle)

    def handle(self, topic: str, payload: dict):
        parts = topic.split(".")
        key = parts[2] if len(parts) > 2 else ""
        etype = _TOPIC_ENTITY.get(key)
        entity_id = payload.get("entity_id") or (etype or "WORLD")
        if etype is None:
            return                       # bilinmeyen alt konu — sessizce yoksay
        self.world.upsert(
            entity_id, etype, payload.get("state") or payload,
            source=str(payload.get("source") or "SYSTEM"),
            confidence=float(payload.get("confidence", 0.8)),
            observed_at=payload.get("observed_at"),
            record_history=True)
        self.applied += 1


class EventToMemoryBridge:
    """Sınıflandırma filtresiyle olay→bellek (§20): EPHEMERAL yazılmaz."""

    WRITE_CLASSES = ("IMPORTANT", "MEMORABLE", "SECURITY", "PROJECT")

    def __init__(self, bus, memory_store, now=None):
        self.bus = bus
        self.memory = memory_store
        self.now = now or time.time
        self.written = 0
        self.dropped_ephemeral = 0
        self.resolver = ConflictResolver(memory_store)
        self.sub_id = bus.subscribe("*", self.handle)

    _TYPE_BY_CLASS = {"SECURITY": "LONG_TERM", "PROJECT": "PROJECT",
                      "MEMORABLE": "SEMANTIC", "IMPORTANT": "EPISODIC"}

    def handle(self, topic: str, payload: dict):
        cls = classify_event(topic)
        if cls not in self.WRITE_CLASSES:
            self.dropped_ephemeral += 1
            return
        content = str(payload.get("summary") or topic)[:500]
        mtype = self._TYPE_BY_CLASS.get(cls, "EPISODIC")
        res = self.memory.write(
            content, memory_type=mtype,
            provenance=str(payload.get("source") or "SYSTEM"),
            record_kind="EVENT",
            source_id=f"event:{topic}",
            subject_key=f"event:{topic}",
            importance=0.7 if cls in ("SECURITY", "PROJECT") else 0.4,
            tags=["event", cls.lower()], dedup=False)
        if res.get("ok"):
            self.written += 1


class EventToProactiveBridge:
    """Olay → bağlam → politika → proaktif karar (§21). Spam yok:
    cooldown + (type,entity) dedup + önem eşiği."""

    def __init__(self, bus, decision_cb, cooldown_s: float = 300.0,
                 min_importance: float = 0.5, now=None):
        self.bus = bus
        self.decide = decision_cb          # (topic, payload) → None | action
        self.cooldown_s = cooldown_s
        self.min_importance = min_importance
        self.now = now or time.time
        self._last: dict[tuple, float] = {}
        self.fired = 0
        self.suppressed = 0
        self.sub_id = bus.subscribe("world.change.*", self.handle)

    def handle(self, topic: str, payload: dict):
        importance = float(payload.get("importance", 0.5))
        if importance < self.min_importance:
            self.suppressed += 1
            return
        key = (topic, str(payload.get("entity_id") or ""))
        now = self.now()
        last = self._last.get(key)
        if last is not None and (now - last) < self.cooldown_s:
            self.suppressed += 1           # dedup + cooldown: spam engeli
            return
        action = self.decide(topic, payload)
        if action is None:
            return
        self._last[key] = now
        self.fired += 1
        return action


def capture_world(world_model, world_store, bus, *, correlation_id=None,
                  source="SYSTEM") -> dict:
    """Foundation WorldModel anlık görüntüsü → WorldStore varlıkları +
    değişen varlıklar için world.change.* olayları (§19)."""
    snap = world_model.snapshot()
    published, updated = [], 0
    corr = correlation_id or f"capture-{int(time.time())}"
    # bölüm → (entity_id, entity_type)
    sections = {
        "screen": ("SCREEN", "SCREEN"),
        "presence": ("PRESENCE", "PRESENCE"),
        "task": ("TASKS", "TASKS"),
        "system": ("SYSTEM_HEALTH", "SYSTEM_HEALTH"),
        "workspace": ("ACTIVE_WINDOW", "ACTIVE_WINDOW"),
        "apps": ("APPLICATIONS", "APPLICATIONS"),
        "network": ("NETWORK", "NETWORK"),
        "iot": ("IO_DEVICES", "IO_DEVICES"),
        "events": ("RECENT_EVENTS", "RECENT_EVENTS"),
        "os": ("OS", "OS"),
    }
    for section, (eid, etype) in sections.items():
        data = snap.get(section)
        if not isinstance(data, dict) or data.get("available") is False:
            continue                       # eksik kaynak sessizce atlanır
        res = world_store.upsert(eid, etype, {"section": section, "data": data},
                                 source=source, confidence=0.8)
        updated += 1
        if res["changed"]:
            topic = f"world.change.{ {'ACTIVE_WINDOW': 'window'}.get(etype, etype.lower()) }"
            out = bus.publish(topic, {
                "entity_id": eid, "state": {"section": section, "data": data},
                "observed_at": snap["ts"], "source": source,
            }, entity_id=eid, correlation_id=corr)
            published.append(topic if out.get("ok") else f"{topic}:blocked")
    return {"updated": updated, "published": published,
            "entities": world_store.snapshot()["count"]}


def snapshot_world_to_memory(world_store, memory_store, *, now=None) -> dict:
    """Dünya durumunun özetini EPISODIC gözlem olarak belleğe işle
    (world→memory §30 entegrasyonu; IMPORTANCE orta, retention tip-farkında)."""
    now = now or time.time
    snap = world_store.snapshot(include_stale=False)
    fresh = [e for e in snap["entities"] if not e["stale"]]
    if not fresh:
        return {"ok": False, "error": "no fresh entities"}
    parts = []
    for e in fresh[:10]:
        key = f"{e['entity_id']}@v{e['version']}"
        parts.append(key)
    content = "Dünya gözlemi: " + ", ".join(parts)
    res = memory_store.write(content, memory_type="EPISODIC", provenance="SYSTEM",
                             record_kind="OBSERVATION",
                             source_id="world:snapshot",
                             subject_key="world:snapshot",
                             importance=0.3, tags=["world", "observation"])
    return {"ok": bool(res.get("ok")), "id": res.get("id"),
            "entities": len(fresh)}
