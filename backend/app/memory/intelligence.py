"""WAVE 2 — Memory intelligence: importance, dedup, conflict, decay.

Store V3 üzerinde çalışan saf servisler. Kuralılar:
- Model çıkarımı doğrulanmış olgudan AYRI ve daha düşük güvenilirliliğe sahiptir.
- Farklı zamanlı gözlemler (GPU 55 / GPU 72) çelişki DEĞİLDİR.
- Gerçek çelişki (tercih A sonra B): geçici öncelik + kaynak güvenilirliği +
  açık kullanıcı düzeltmesi + güven ile çözülür; geçmiş SÜPERSEED olarak korunur.
"""
from __future__ import annotations

import re
import time

from app.memory.layers import (
    DECAY_PROTECTED_KINDS, DECAY_PROTECTED_TYPES, IMPORTANCE_WEIGHTS,
    RETENTION_DAYS, SECURITY_TAGS, SOURCE_CONFIDENCE,
)
from app.memory.store_v3 import normalize

_TOKEN_RE = re.compile(r"[\wçğıöşü]+", re.UNICODE)

# Zaman-duyarlı türler: aynı metin farklı zamanda YENİ gözlemdür (§6)
TEMPORAL_KINDS = ("OBSERVATION", "EVENT")


def tokens(text: str) -> set:
    return set(_TOKEN_RE.findall(normalize(text)))


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


# ------------------------------------------------------------ importance
class ImportanceScorer:
    """§8 sinyalleri → 0..1 skor (toplam ağırlık normalize)."""

    def __init__(self, active_project: str | None = None, now=None):
        self.active_project = active_project
        self.now = now or time.time
        self._access_counts: dict[str, int] = {}      # id → erişim
        self._explicit_saves: set[str] = set()

    def note_access(self, rid: str):
        self._access_counts[rid] = self._access_counts.get(rid, 0) + 1

    def note_explicit_save(self, rid: str):
        self._explicit_saves.add(rid)

    def score(self, rec: dict) -> float:
        w = IMPORTANCE_WEIGHTS
        total, gained = 0.0, 0.0
        if rec.get("record_kind") == "PREFERENCE":
            total += w["user_preference"]; gained += w["user_preference"]
        if self.active_project and rec.get("project_id") == self.active_project:
            total += w["active_project"]; gained += w["active_project"]
        tags = {str(t).lower() for t in (rec.get("tags") or [])}
        if tags & set(SECURITY_TAGS):
            total += w["security"]; gained += w["security"]
        rid = rec.get("id")
        if rid in self._explicit_saves:
            total += w["explicit_save"]; gained += w["explicit_save"]
        n = self._access_counts.get(rid, rec.get("access_count") or 0)
        if n >= 5:
            total += w["frequent_access"]; gained += w["frequent_access"]
        if n >= 3:
            total += w["recurring"]; gained += w["recurring"]
        la = rec.get("last_access")
        if la and (self.now() - float(la)) < 86400:
            total += w["recent_usage"]; gained += w["recent_usage"]
        base = float(rec.get("importance") or 0.5)
        if total <= 0:
            return round(min(1.0, base), 3)
        # sinyal katkısı base ile harmanlanır (base %60 + sinyaller %40)
        return round(min(1.0, base * 0.6 + (gained / total) * 0.4 + base * 0.4 * 0), 3) \
            if False else round(min(1.0, base * 0.6 + (gained / total) * 0.4), 3)


# ------------------------------------------------------------ dedup
class DuplicateDetector:
    """exact / normalized / semantic seviyeleri (§6).

    TEMPORAL_KINDS (gözlem/olay) için zaman penceresi dışındaki aynı metin
    YENİ kayıttır — yanlış dedup engellenir.
    """

    def __init__(self, store, semantic_threshold: float = 0.85,
                 window_s: float = 300.0):
        self.store = store
        self.semantic_threshold = semantic_threshold
        self.window_s = window_s

    def check(self, content: str, memory_type: str, record_kind: str,
              project_id: str | None, created_at: float,
              subject_key: str | None = None) -> dict:
        """{duplicate: bool, level, of?}"""
        norm = normalize(content)
        cand = self.store.query(memory_type=memory_type, project_id=project_id,
                                limit=200, status="ACTIVE")
        for rec in cand:
            if rec["record_kind"] != record_kind:
                continue
            if record_kind in TEMPORAL_KINDS:
                # zaman penceresi DIŞINDA → farklı zamanlı olay, dedup DEĞİL
                if abs(float(rec.get("created_at") or 0) - created_at) > self.window_s:
                    continue
            if rec.get("subject_key") != subject_key and record_kind not in TEMPORAL_KINDS:
                continue
            if rec["normalized_content"] == norm:            # exact (normalized)
                return {"duplicate": True, "level": "normalized", "of": rec["id"]}
            sim = jaccard(tokens(content), tokens(rec["content"]))
            if sim >= self.semantic_threshold:
                return {"duplicate": True, "level": "semantic", "of": rec["id"],
                        "similarity": round(sim, 3)}
        return {"duplicate": False, "level": None}


# ------------------------------------------------------------ conflict
class ConflictResolver:
    """Aynı konu, çakışan geçerlilik, farklı değer → çözüm (§7)."""

    def __init__(self, store):
        self.store = store

    def detect(self, subject_key: str, new_rec: dict) -> list[dict]:
        """new_rec ile çakışan AKTİF kayıtları döndürür."""
        if not subject_key:
            return []
        out = []
        for rec in self.store.query(subject_key=subject_key, status="ACTIVE",
                                    limit=50):
            if rec["id"] == new_rec.get("id"):
                continue
            if rec["content"].strip() == new_rec["content"].strip():
                continue  # aynı değer → dedup konusu
            # geçerlilik çakışması mı?
            a_from = float(rec.get("valid_from") or rec["created_at"])
            a_until = float(rec["valid_until"]) if rec.get("valid_until") else None
            b_from = float(new_rec.get("valid_from") or new_rec["created_at"])
            b_until = float(new_rec["valid_until"]) if new_rec.get("valid_until") else None
            if a_until is not None and a_until < b_from:
                continue  # eskisi süresi dolmuş → geçmiş gözlem, çelişki yok
            if b_until is not None and b_until < a_from:
                continue
            # aynı an/ortak pencere + farklı değer + sabit bilgi → gerçek çelişki
            if rec["record_kind"] in ("FACT", "PREFERENCE"):
                out.append(rec)
        return out

    def resolve(self, new_rec: dict, conflicts: list[dict]) -> dict:
        """Kazananı belirle; kaybedenler SUPERSEDED (geçmiş korunur)."""
        if not conflicts:
            return {"resolved": False, "winners": [], "superseded": []}
        rank = self._rank(new_rec)
        losers = [c for c in conflicts if self._rank(c) <= rank]
        # yeni kayıt kendi konusunun en güncel beyanı: temporal precedence
        # (kullanıcı düzeltmesi) — yalnız ciddi şekilde daha güvenilir eski
        # kayıt kazanırsa o kazanır:
        for c in conflicts:
            if self._rank(c) > rank + 0.2:      # belirgin üstünlük
                losers = [x for x in conflicts if x["id"] != c["id"]]
                losers.append(new_rec)
                winners = [c]
                self._apply(winners, losers)
                return {"resolved": True, "winners": [c["id"]],
                        "superseded": [l["id"] for l in losers if l["id"] != c["id"]]}
        winners = [new_rec]
        self._apply(winners, [l for l in losers if l["id"] != new_rec.get("id")])
        return {"resolved": True, "winners": [new_rec.get("id")],
                "superseded": [l["id"] for l in losers]}

    def _rank(self, rec: dict) -> float:
        """çözüm önceliği: kullanıcı düzeltmesi > kaynak güvenilirliği >
        güven > tazelik."""
        prov = rec.get("provenance", "SYSTEM")
        score = SOURCE_CONFIDENCE.get(prov, 0.5) * 0.5
        score += float(rec.get("confidence") or 0.5) * 0.3
        age_days = max(0.0, (time.time() - float(rec.get("created_at") or 0)) / 86400)
        score += max(0.0, 1.0 - age_days / 365) * 0.2
        if prov == "USER":
            score += 0.25              # açık kullanıcı beyanı önceliği
        return score

    def _apply(self, winners, losers):
        for l in losers:
            if l.get("id"):
                # çelişkiye düşen güvenilirlik düşürülür + geçmiş korunur (§5, §7)
                self.store.update(l["id"], status="SUPERSEDED",
                                   confidence=max(0.0, float(l.get("confidence") or 0.5)
                                                  * 0.5))


# ------------------------------------------------------------ decay
class DecayManager:
    """Tip-farkında tutma; korunan türler ASLA rastgele silinmez (§9).
    Hard delete YOK — arşivleme."""

    def __init__(self, store, now=None):
        self.store = store
        self.now = now or time.time

    def _protected(self, rec: dict) -> bool:
        if rec["memory_type"] in DECAY_PROTECTED_TYPES:
            return True
        if rec["record_kind"] in DECAY_PROTECTED_KINDS:
            return True
        tags = {str(t).lower() for t in (rec.get("tags") or [])}
        return bool(tags & set(SECURITY_TAGS))

    def run(self, importance_floor: float = 0.3) -> dict:
        now = self.now()
        archived = expired = 0
        for rec in self.store.query(status="ACTIVE", limit=500):
            if self._protected(rec):
                continue
            days = RETENTION_DAYS.get(rec["memory_type"])
            if days is not None and (now - float(rec["created_at"])) > days * 86400:
                self.store.update(rec["id"], status="ARCHIVED")
                archived += 1
                continue
            if rec.get("valid_until") is not None and \
                    now > float(rec["valid_until"]):
                self.store.update(rec["id"], status="EXPIRED")
                expired += 1
                continue
            if float(rec.get("importance") or 0) < importance_floor and \
                    rec["memory_type"] in ("WORKING", "SHORT_TERM") and \
                    rec["record_kind"] in ("OBSERVATION", "EVENT"):
                self.store.update(rec["id"], status="ARCHIVED")
                archived += 1
        return {"archived": archived, "expired": expired}
