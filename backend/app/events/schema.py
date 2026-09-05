"""WAVE 2 — Durable event schema + validation + classification (§17-§20).

Ortak sinir sistemi: her olay şu alanları taşır —
  event_id (uuid, benzersiz — dedup anahtarı)
  event_type (ör. world.change.screen, memory.created, goal.changed)
  timestamp (unix)
  source (provenance kaynağı)
  entity_id (ilgili varlık; yoksa None)
  payload (dict — redacted, boyut sınırlı)
  correlation_id (aynı mantıksal akış)
  causation_id (bu olayı doğrudan tetikleyen olay)
  confidence (0-1)
  schema_version (int — uyumsuz reddedilir)

Event ADI'DIR: payload içindeki metin asla otomatik komut sayılmaz (§29).
"""
from __future__ import annotations

import re
import uuid

SCHEMA_VERSION = 1
MAX_PAYLOAD_BYTES = 64 * 1024          # oversized payload reddi (§29)
MAX_EVENT_TYPE_LEN = 120

# Olay tipi ad alanları
KNOWN_NAMESPACES = ("world.change.", "memory.", "goal.", "device.", "task.",
                    "system.", "user.", "project.")
EVENT_TYPE_RE = re.compile(r"^[a-z0-9_.:-]+$")

# Olay → sınıflandırma (§20): hangi olaylar memory'ye YAZILIR.
EVENT_CLASSES = ("EPHEMERAL", "IMPORTANT", "MEMORABLE", "SECURITY", "PROJECT")
# Varsayılan sınıflandırma tablosu (prefix → class); yoksa EPHEMERAL.
DEFAULT_CLASSIFICATION = {
    "world.change.screen": "EPHEMERAL",
    "world.change.process": "EPHEMERAL",
    "world.change.system": "EPHEMERAL",
    "world.change.files": "EPHEMERAL",
    "world.change.network": "EPHEMERAL",
    "world.change.time": "EPHEMERAL",
    "world.change.presence": "IMPORTANT",
    "world.change.device": "IMPORTANT",
    "world.change.browser": "EPHEMERAL",
    "world.change.task": "IMPORTANT",
    "world.change.goal": "IMPORTANT",
    "memory.created": "MEMORABLE",
    "memory.updated": "MEMORABLE",
    "goal.changed": "IMPORTANT",
    "device.changed": "IMPORTANT",
    "task.dead_letter": "SECURITY",
    "task.recovered": "IMPORTANT",
    "security.alert": "SECURITY",
    "project.changed": "PROJECT",
}
# EPHEMERAL dışındakiler event→memory köprüsünde memory'ye yazılır.


def new_event_id() -> str:
    return uuid.uuid4().hex


def classify_event(event_type: str) -> str:
    """Olay tipini sınıfa çevirir (§20) — memory kirliliği filtresi."""
    t = str(event_type or "")
    if t in DEFAULT_CLASSIFICATION:
        return DEFAULT_CLASSIFICATION[t]
    for prefix in ("world.change.", "task.", "system.", "user.", "project.",
                   "memory.", "goal.", "device."):
        if t.startswith(prefix) and f"{prefix.rstrip('.')}" in DEFAULT_CLASSIFICATION:
            continue
    # en uzun önek eşleşmesi
    best = None
    for key, cls in DEFAULT_CLASSIFICATION.items():
        if t.startswith(key) and (best is None or len(key) > len(best[0])):
            best = (key, cls)
    return best[1] if best else "EPHEMERAL"


def validate_event(evt: dict) -> dict:
    """Şema doğrulama — bozuk/uyumsuz olay HONEST ValueError ile reddedilir.
    Dönen dict normalize edilmiş olaydır (orijinal değişmez)."""
    if not isinstance(evt, dict):
        raise ValueError("event must be a dict")
    et = str(evt.get("event_type") or "")
    if not et or len(et) > MAX_EVENT_TYPE_LEN or not EVENT_TYPE_RE.match(et):
        raise ValueError(f"invalid event_type {et!r}")
    eid = str(evt.get("event_id") or "") or new_event_id()
    ts = evt.get("timestamp")
    if ts is None:
        raise ValueError("event.timestamp required")
    ts = float(ts)
    payload = evt.get("payload")
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise ValueError("event.payload must be a dict")
    import json
    size = len(json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8"))
    if size > MAX_PAYLOAD_BYTES:
        raise ValueError(f"payload too large: {size} > {MAX_PAYLOAD_BYTES} bytes")
    version = int(evt.get("schema_version") or SCHEMA_VERSION)
    if version != SCHEMA_VERSION:
        raise ValueError(f"unsupported schema_version {version} "
                         f"(expected {SCHEMA_VERSION})")
    conf = evt.get("confidence")
    conf = 1.0 if conf is None else max(0.0, min(1.0, float(conf)))
    return {
        "event_id": eid,
        "event_type": et,
        "timestamp": ts,
        "source": str(evt.get("source") or "SYSTEM"),
        "entity_id": (str(evt["entity_id"])[:160] if evt.get("entity_id")
                      else None),
        "payload": payload,
        "correlation_id": (str(evt["correlation_id"])[:64]
                           if evt.get("correlation_id") else None),
        "causation_id": (str(evt["causation_id"])[:64]
                         if evt.get("causation_id") else None),
        "confidence": conf,
        "schema_version": version,
    }
