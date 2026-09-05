"""WAVE 2 — Memory layer taxonomy, schemas and policies (additive).

8 katman MEMORY mimarisi (§1): her tipin amacı, retention ve retrieval
politikası, korunma kuralları. Bir tip diğerinin yerine KULLANILMAZ —
store yazımında memory_type doğrulanır.

Mevcut Foundation memory (sqlite_memory.Memory, SemanticMemory,
SemanticMemoryV2, memory_io) DOKUNULMADAN korunur; bu modül V3 katmanının
tek doğruluk kaynağıdır.
"""
from __future__ import annotations

# ------------------------------------------------------------ 8 layers
# tip → {purpose, retention} — bir tip diğerinin yerine KULLANILMAZ.
MEMORY_TYPES = {
    "WORKING":    {"purpose": "Anlık işlem belleği — mevcut görev penceresi",
                   "retention": "oturum (saatler)"},
    "SHORT_TERM": {"purpose": "Yakın geçmiş olaylar — günlük pencere",
                   "retention": "günler"},
    "EPISODIC":   {"purpose": "Yaşanmış olaylar (zamanlı, bağlamlı)",
                   "retention": "aylar (importance'a göre)"},
    "SEMANTIC":   {"purpose": "Doğrulanmış bilgi ve kavramlar",
                   "retention": "kalıcı (decay yok)"},
    "PROCEDURAL": {"purpose": "Nasıl yapılır bilgisi / alışkanlıklar",
                   "retention": "kalıcı (korunur)"},
    "LONG_TERM":  {"purpose": "Yüksek önem taşıyan kalıcı kayıtlar",
                   "retention": "kalıcı"},
    "USER":       {"purpose": "Kullanıcıya dair ifadeler/tercihler (geçmiş)",
                   "retention": "kalıcı (history korunur)"},
    "PROJECT":    {"purpose": "Proje kapsamlı bilgi",
                   "retention": "proje ömrü"},
}

# ------------------------------------------------------------ provenance
PROVENANCE_SOURCES = (
    "USER",            # kullanıcı açıkça söyledi/yazdı
    "TOOL",            # araç doğruladı (ölçüm, dosya, komut çıktısı)
    "BROWSER",         # tarayıcı gözlemi
    "VISION",          # ekran/görüntü analizi
    "SYSTEM",          # OS/süreç telemetry
    "DOCUMENT",        # dosya/belge içeriği
    "MODEL_INFERENCE", # LLM çıkarımı — doğrulanmış fact ile EŞİT SAYILMAZ
)

# ------------------------------------------------------------ record kind
RECORD_KINDS = (
    "FACT",         # doğrulanmış gerçek ("Windows 11 çalışıyor")
    "INFERENCE",    # çıkarım ("muhtemelen kod yazıyor") — kesin gerçek DEĞİL
    "PREFERENCE",   # kullanıcı tercihi
    "EVENT",        # olmuş olay
    "OBSERVATION",  # anlık gözlem ("CPU şu an %20")
    "HYPOTHESIS",   # sınanabilir önerme
)

# ------------------------------------------------------------ privacy
PRIVACY_LEVELS = ("PUBLIC", "PRIVATE", "SENSITIVE", "SECRET")
# SECRET: normal retrieval'e ASLA girmez; export dışı; yalnız açık
# secret-kanal erişimi (vault) — memory'ye yazımı reddedilir (§29).

# ------------------------------------------------------------ confidence
# Kaynak-tabanlı güven aralıkları (§5). Writable, clamp'lenir.
SOURCE_CONFIDENCE = {
    "USER": 0.95,            # açık ifade: yüksek
    "TOOL": 0.90,            # araç doğrulaması: yüksek
    "BROWSER": 0.80,
    "VISION": 0.70,          # algı: orta
    "SYSTEM": 0.90,
    "DOCUMENT": 0.80,
    "MODEL_INFERENCE": 0.50, # çıkarım: düşük — fact ile eşit sayılmaz
}
CONFIDENCE_CONTRADICTED_PENALTY = 0.5   # çelişki → yarıya düşür

# ------------------------------------------------------------ status
RECORD_STATUS = ("ACTIVE", "SUPERSEDED", "ARCHIVED", "TOMBSTED", "EXPIRED")

# ------------------------------------------------------------ retention
# Tip-farkında decay korunması (§9): bu tipler rastgele SİLİNMEZ.
DECAY_PROTECTED_TYPES = ("PROCEDURAL", "LONG_TERM", "USER", "PROJECT")
DECAY_PROTECTED_KINDS = ("FACT", "PREFERENCE")
SECURITY_TAGS = ("security", "auth", "credential", "master_rule")

# Tip başına retention (gün; None = kalıcı)
RETENTION_DAYS = {
    "WORKING": 1,
    "SHORT_TERM": 7,
    "EPISODIC": 180,
    "SEMANTIC": None,
    "PROCEDURAL": None,
    "LONG_TERM": None,
    "USER": None,
    "PROJECT": 365,
}

# ------------------------------------------------------------ importance
# Önem sinyalleri (§8) — ImportanceScorer ağırlıkları.
IMPORTANCE_WEIGHTS = {
    "user_preference": 0.30,     # PREFERENCE kind
    "active_project": 0.15,      # project_id == aktif proje
    "recurring": 0.10,           # tekrarlayan erişim/hit
    "security": 0.30,            # security etiketi
    "frequent_access": 0.10,     # access_count eşiği üstü
    "explicit_save": 0.25,       # kaynak USER + açık kayıt
    "task_dependency": 0.10,     # bir göreve bağlı
    "recent_usage": 0.05,        # son 24h erişim
}


def validate_memory_type(value: str) -> str:
    v = str(value or "").upper().strip()
    if v not in MEMORY_TYPES:
        raise ValueError(f"unknown memory_type {value!r} (known: {sorted(MEMORY_TYPES)})")
    return v


def validate_provenance(value: str) -> str:
    v = str(value or "").upper().strip()
    if v not in PROVENANCE_SOURCES:
        raise ValueError(f"unknown provenance {value!r}")
    return v


def validate_kind(value: str) -> str:
    v = str(value or "").upper().strip()
    if v not in RECORD_KINDS:
        raise ValueError(f"unknown record kind {value!r}")
    return v


def validate_privacy(value: str) -> str:
    v = str(value or "").upper().strip()
    if v not in PRIVACY_LEVELS:
        raise ValueError(f"unknown privacy level {value!r}")
    return v


def clamp_confidence(value, provenance: str) -> float:
    """0.0–1.0 normalize; kaynak tavanı aşılamaz (§5)."""
    base = SOURCE_CONFIDENCE.get(provenance, 0.5)
    try:
        c = float(value)
    except (TypeError, ValueError):
        c = base
    return round(max(0.0, min(c, base, 1.0)), 3)
