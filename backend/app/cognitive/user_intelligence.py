"""User Intelligence — Wave 5 §2.

Mevcut user modelini güçlendiren additive katman:
- preference learning: explicit (kullanıcı sözlü) + inferred (davranıştan)
- behavior patterns: tekrarlanan eylem dizileri + saat-bazlı sıklık
- communication style: concise/deep, dil, ort. yanıt uzunluğu (gerçek ölçüm)
- recurring workflows: adım dizisi frekansı
- confidence: explicit=1.0; inferred = n_obs bazlı üst sınır (asla 1.0 değil)
- explicit vs inferred daima ayrı etiketli
- user-controlled correction: correct() inferred'i geçersiz kılar
- privacy boundaries: kara listedeki alanlar ASLA otomatik öğrenilmez;
  hassas kişisel özellikler "gerçek" olarak kabul edilmez (inferred kalır)

Dürüstlük: deterministik istatistik; LLM tahmini YOK; veri yoksa
confidence düşük raporlanır.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections import Counter
from pathlib import Path

# Otomatik öğrenmeye KAPALI alanlar (privacy boundary — additive sabit)
PRIVACY_BOUNDARY_FIELDS = frozenset({
    "health", "political_view", "religious_view", "sexual_orientation",
    "biometric", "financial_account", "exact_location_home", "national_id",
})


def _confidence(n_obs: int, agreements: int) -> float:
    """Inferred güven: hiçbir zaman 1.0 olamaz (gerçek kabul edilmez)."""
    if n_obs <= 0:
        return 0.0
    base = min(0.9, agreements / max(1.0, float(n_obs)))
    # az gözlem → üst sınırla ceza (tek örnekten yüksek güven çıkmaz)
    cap = min(0.9, 0.35 + 0.1 * n_obs)
    return round(min(base, cap), 3)


class UserIntelligence:
    def __init__(self, db_path: str = "data/cognitive/user_model.db"):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(str(self.path), check_same_thread=False)
        self.db.execute("""CREATE TABLE IF NOT EXISTS preferences(
            domain TEXT, name TEXT, value TEXT, kind TEXT,
            confidence REAL, n_obs INTEGER, agreements INTEGER,
            updated REAL, source TEXT,
            PRIMARY KEY(domain, name, kind))""")
        self.db.execute("""CREATE TABLE IF NOT EXISTS behavior_events(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL, hour INTEGER, action TEXT, context TEXT)""")
        self.db.execute("""CREATE TABLE IF NOT EXISTS corrections(
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL,
            domain TEXT, name TEXT, old TEXT, new TEXT)""")
        self.db.commit()

    # ---------------------------------------------------- preferences
    def set_explicit(self, domain: str, name: str, value,
                     source: str = "user") -> dict:
        """Kullanıcı beyanı: confidence=1.0, inferred'i ezer."""
        if name in PRIVACY_BOUNDARY_FIELDS:
            return {"ok": False,
                    "error": f"'{name}' privacy boundary içinde — profil alanı değil"}
        with self.lock:
            self.db.execute(
                """INSERT INTO preferences
                   (domain,name,value,kind,confidence,n_obs,agreements,updated,source)
                   VALUES(?,?,?, 'explicit', 1.0, 1, 1, ?, ?)
                   ON CONFLICT(domain,name,kind) DO UPDATE SET
                     value=excluded.value, updated=excluded.updated,
                     source=excluded.source, confidence=1.0""",
                (domain, name, json.dumps(value, ensure_ascii=False),
                 time.time(), str(source)[:80]))
            self.db.execute(
                "DELETE FROM preferences WHERE domain=? AND name=? AND kind='inferred'",
                (domain, name))
            self.db.commit()
            return {"ok": True, "kind": "explicit", "confidence": 1.0}

    def observe(self, domain: str, name: str, value,
                context: str = "", now: float | None = None) -> dict:
        """Davranış gözlemi → inferred preference (explicit VARSA sayılmaz:
        kullanıcı beyanı davranışla çelişse bile inferred yazılmaz)."""
        if name in PRIVACY_BOUNDARY_FIELDS:
            return {"ok": False, "error":
                    f"'{name}' otomatik öğrenime kapalı (privacy boundary)"}
        now = time.time() if now is None else now
        with self.lock:
            has_explicit = self.db.execute(
                "SELECT 1 FROM preferences WHERE domain=? AND name=? AND kind='explicit'",
                (domain, name)).fetchone()
            if has_explicit:
                return {"ok": True, "kind": "explicit-protected",
                        "note": "explicit preference mevcut — gözlem yutuldu"}
            row = self.db.execute(
                """SELECT value, n_obs, agreements FROM preferences
                   WHERE domain=? AND name=? AND kind='inferred'""",
                (domain, name)).fetchone()
            val = json.dumps(value, ensure_ascii=False)
            if row is None:
                self.db.execute(
                    """INSERT INTO preferences
                       (domain,name,value,kind,confidence,n_obs,agreements,updated,source)
                       VALUES(?,?,?, 'inferred', ?, 1, 1, ?, 'behavior')""",
                    (domain, name, val, _confidence(1, 1), now))
                n, a = 1, 1
            else:
                old_val, n, a = row
                n += 1
                if old_val == val:
                    a += 1
                else:  # değer değişti: yeni aday, anlaşma sayacı sıfırdan
                    n, a = 1, 1
                    self.db.execute(
                        "DELETE FROM preferences WHERE domain=? AND name=? AND kind='inferred'",
                        (domain, name))
                    self.db.execute(
                        """INSERT INTO preferences
                           (domain,name,value,kind,confidence,n_obs,agreements,updated,source)
                           VALUES(?,?,?, 'inferred', ?, 1, 1, ?, 'behavior')""",
                        (domain, name, val, _confidence(1, 1), now))
                self.db.execute(
                    """UPDATE preferences SET confidence=?, n_obs=?, agreements=?,
                       updated=? WHERE domain=? AND name=? AND kind='inferred'""",
                    (_confidence(n, a), n, a, now, domain, name))
            self.db.commit()
            return {"ok": True, "kind": "inferred",
                    "confidence": _confidence(n, a), "n_obs": n,
                    "agreements": a}

    def correct(self, domain: str, name: str, new_value) -> dict:
        """Kullanıcı düzeltmesi: inferred → explicit (kayıt tutulur)."""
        with self.lock:
            row = self.db.execute(
                """SELECT value FROM preferences WHERE domain=? AND name=?
                   AND kind='inferred'""", (domain, name)).fetchone()
            old = json.loads(row[0]) if row else None
            out = self.set_explicit(domain, name, new_value, source="correction")
            if out.get("ok"):
                self.db.execute(
                    "INSERT INTO corrections(ts,domain,name,old,new) VALUES(?,?,?,?,?)",
                    (time.time(), domain, name,
                     json.dumps(old, ensure_ascii=False) if old is not None else None,
                     json.dumps(new_value, ensure_ascii=False)))
                self.db.commit()
            return out

    def preferences(self, domain: str | None = None,
                    min_confidence: float = 0.0) -> list[dict]:
        q = ("SELECT domain,name,value,kind,confidence,n_obs,agreements,updated,source"
             " FROM preferences WHERE confidence>=?")
        args: list = [min_confidence]
        if domain:
            q += " AND domain=?"
            args.append(domain)
        with self.lock:
            rows = self.db.execute(q + " ORDER BY domain,name", args).fetchall()
        return [{"domain": r[0], "name": r[1], "value": json.loads(r[2]),
                 "kind": r[3], "confidence": r[4], "n_obs": r[5],
                 "agreements": r[6], "updated": r[7], "source": r[8]}
                for r in rows]

    # ---------------------------------------------------- behavior
    def record_event(self, action: str, context: str = "",
                     now: float | None = None) -> dict:
        now = time.time() if now is None else now
        with self.lock:
            self.db.execute(
                "INSERT INTO behavior_events(ts,hour,action,context) VALUES(?,?,?,?)",
                (now, time.localtime(now).tm_hour, str(action)[:80],
                 str(context)[:120]))
            self.db.commit()
            return {"ok": True}

    def behavior_patterns(self, min_count: int = 2) -> list[dict]:
        """Tekrarlanan eylemler (sıklık + saat dağılımı)."""
        with self.lock:
            rows = self.db.execute(
                "SELECT action, COUNT(*), AVG(hour) FROM behavior_events"
                " GROUP BY action HAVING COUNT(?)<=COUNT(*)", (min_count,)).fetchall()
            top_hour = dict(self.db.execute(
                "SELECT action, MAX(cnt) FROM (SELECT action, hour, COUNT(*) cnt"
                " FROM behavior_events GROUP BY action, hour) GROUP BY action"
            ).fetchall())
        return [{"action": r[0], "count": r[1],
                 "avg_hour": round(r[2] or 0, 1),
                 "peak_hour": top_hour.get(r[0])}
                for r in rows]

    def recurring_workflows(self, min_len: int = 2, min_count: int = 2,
                            limit: int = 20) -> list[dict]:
        """Ardışık eylem dizisi (bigram..) tekrarları."""
        with self.lock:
            acts = [r[0] for r in self.db.execute(
                "SELECT action FROM behavior_events ORDER BY ts").fetchall()]
        seqs = Counter(tuple(acts[i:i + min_len])
                       for i in range(len(acts) - min_len + 1))
        return [{"workflow": list(seq), "count": c}
                for seq, c in seqs.most_common(limit) if c >= min_count]

    # ---------------------------------------------------- communication
    def note_response_length(self, tokens: int, now: float | None = None):
        """Yanıt uzunluğu ölçümü → communication style önerisi (inferred)."""
        style = "concise" if tokens <= 80 else "deep"
        return self.observe("communication", "style", style,
                            context=f"resp_len={tokens}", now=now)

    def communication_style(self) -> dict:
        prefs = [p for p in self.preferences("communication")
                 if p["name"] == "style"]
        if not prefs:
            return {"style": None, "confidence": 0.0,
                    "note": "yetersiz gözlem — varsayılan nötr"}
        p = max(prefs, key=lambda x: x["confidence"])
        return {"style": p["value"], "confidence": p["confidence"],
                "kind": p["kind"], "n_obs": p["n_obs"]}

    def stats(self) -> dict:
        with self.lock:
            ev = self.db.execute("SELECT COUNT(*) FROM behavior_events").fetchone()[0]
            pf = self.db.execute("SELECT kind, COUNT(*) FROM preferences GROUP BY kind"
                                 ).fetchall()
            cr = self.db.execute("SELECT COUNT(*) FROM corrections").fetchone()[0]
        return {"events": ev, "preferences": dict(pf), "corrections": cr,
                "privacy_boundary_fields": sorted(PRIVACY_BOUNDARY_FIELDS)}
