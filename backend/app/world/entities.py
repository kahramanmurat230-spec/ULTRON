"""WAVE 2 — World entity schema (additive over app/world/model.py).

Foundation WorldModel (sources→snapshot) sözleşmesi korunur; bu modül
kalıcı, zamansal, sürümlü world-entity şemasını tanımlar (§14-§16, §23).
"""
from __future__ import annotations

# World state minimum varlık türleri (§14)
ENTITY_TYPES = (
    "USER", "DEVICE", "OS", "ACTIVE_WINDOW", "APPLICATIONS", "PROCESSES",
    "SCREEN", "NETWORK", "PRESENCE", "TASKS", "GOALS", "IO_DEVICES",
    "BROWSER", "FILES", "SYSTEM_HEALTH", "CURRENT_TIME", "RECENT_EVENTS",
)

# Her world entity zorunlu alanları (§15)
ENTITY_FIELDS = (
    "entity_id",      # str — "SCREEN", "PROCESS:1234", "DEVICE:pc" gibi
    "entity_type",    # ENTITY_TYPES üyesi
    "state",          # dict — gözlenen durum
    "confidence",     # 0.0–1.0
    "observed_at",    # unix ts — son gözlem
    "valid_from",     # unix ts — geçerlilik başlangıcı
    "valid_until",    # unix ts | None — None=geçerli (belirsiz süre)
    "source",         # gözlem kaynağı (SYSTEM/VISION/TOOL/...)
    "version",        # int — monotonik; her güncelleme +1
)

# Varlık başına tazelik eşiği (saniye) — üstü ise stale=true (§15)
STALENESS_S = {
    "SCREEN": 120.0,
    "ACTIVE_WINDOW": 60.0,
    "PROCESSES": 120.0,
    "SYSTEM_HEALTH": 180.0,
    "PRESENCE": 300.0,
    "NETWORK": 300.0,
    "CURRENT_TIME": 5.0,
    "TASKS": 120.0,
    "GOALS": 600.0,
    "USER": 300.0,
    "DEVICE": 3600.0,
    "OS": 3600.0,
    "APPLICATIONS": 300.0,
    "IO_DEVICES": 3600.0,
    "BROWSER": 180.0,
    "FILES": 600.0,
    "RECENT_EVENTS": 120.0,
}
DEFAULT_STALENESS_S = 600.0


def validate_entity_type(value: str) -> str:
    v = str(value or "").upper().strip()
    if v not in ENTITY_TYPES:
        raise ValueError(f"unknown entity_type {value!r}")
    return v


def staleness_limit(entity_type: str) -> float:
    return STALENESS_S.get(str(entity_type or "").upper(), DEFAULT_STALENESS_S)


def is_stale(rec: dict, now: float) -> bool:
    """Kayıt bayat mı? valid_until geçtiyse veya gözlem tazelik eşiğini
    aştıysa stale=true. Sürekle 'stale' AÇIK bir bayrak olarak döner."""
    if rec.get("valid_until") is not None and now > float(rec["valid_until"]):
        return True
    limit = staleness_limit(rec.get("entity_type"))
    return (now - float(rec.get("observed_at") or 0)) > limit


# ------------------------------------------------------------ KG prep (§23)
# İleride Knowledge Graph kullanacak ilişki şeması — şimdilik ilişki
# türleri + doğrulama; saklama WorldStore/MemoryStore entity_links'te.
RELATION_TYPES = (
    "owns",          # USER → DEVICE
    "works_on",      # USER → PROJECT
    "contains",      # PROJECT → ARTIFACT
    "has_state",     # WORLD → DEVICE
    "depends_on",    # TASK → TASK/ARTIFACT
    "produced",      # TASK → ARTIFACT
    "observed",      # SOURCE → ENTITY
    "relates_to",    # genel
)


def validate_relation(value: str) -> str:
    v = str(value or "").strip().lower()
    if v not in RELATION_TYPES:
        raise ValueError(f"unknown relation {value!r}")
    return v
