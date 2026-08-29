"""Learning Loop + Self-Evaluation — Wave 5 §9+§17.

§9 OBSERVE → EXECUTE → RESULT → EVALUATE → LESSON → STORE → FUTURE DECISION
Production kodu OTOMATİK DEĞİŞTİRMEZ — öğrenme GÜVENLİ BİLGİ katmanında:
- memory update (lesson → KnowledgeEngine claim'leri)
- preference update (→ UserIntelligence.observe)
- strategy update (görev tipi için strateji notu: başarılı/tekrar-dene/kaçın)
- failure pattern update (hata imzası frekansı)

§17 EXPECTED vs ACTUAL → EVALUATE → LESSON → MEMORY:
success/quality/latency/cost/errors/recovery/user_correction ölçülür.

Kapanış dürüstlüğü: ders yalnız GERÇEK sonuçtan üretilir; expected verilmedi
alse 'unknown-expected' dürüstçe işaretlenir ve quality skoru hesaplanmaz.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path


def _now() -> float:
    return time.time()


class LearningLoop:
    def __init__(self, db_path: str = "data/cognitive/learning.db",
                 knowledge=None, user_intel=None):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(str(self.path), check_same_thread=False)
        self.db.execute("""CREATE TABLE IF NOT EXISTS outcomes(
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL,
            task_type TEXT, goal TEXT, expected TEXT, actual TEXT,
            success INTEGER, quality REAL, latency_s REAL, cost REAL,
            errors INTEGER, recovery TEXT, user_correction TEXT,
            lesson TEXT, strategy TEXT)""")
        self.db.execute("""CREATE TABLE IF NOT EXISTS failure_patterns(
            signature TEXT PRIMARY KEY, count INTEGER,
            last_seen REAL, sample TEXT)""")
        self.db.execute("""CREATE TABLE IF NOT EXISTS strategies(
            task_type TEXT PRIMARY KEY, strategy TEXT,
            wins INTEGER, losses INTEGER, updated REAL)""")
        self.db.commit()
        self.knowledge = knowledge      # KnowledgeEngine (opsiyonel enjeksiyon)
        self.user_intel = user_intel    # UserIntelligence (opsiyonel)

    # ---------------------------------------------------- observe
    def record_outcome(self, task_type: str, *, goal: str = "",
                       expected: str = "", actual: str = "",
                       success: bool | None = None,
                       latency_s: float = 0.0, cost: float = 0.0,
                       errors: int = 0, recovery: str = "",
                       user_correction: str = "",
                       failure_signature: str = "") -> dict:
        """Gerçek sonucu kaydet + değerlendir (quality) + ders üret."""
        with self.lock:
            # §17 quality: expected biliniyorsa metin örtüşmesi; bilinmiyorsa
            # dürüstçe None (uydurma kalite YOK)
            quality = None
            if expected and actual:
                e = set(expected.lower().split())
                a = set(actual.lower().split())
                quality = round(len(e & a) / max(1, len(e)), 3)
            if success is None:
                success = (errors == 0)
            lesson = self._derive_lesson(task_type, success, quality,
                                         errors, user_correction,
                                         failure_signature)
            strategy = ("repeat" if success else
                        "retry-with-changes" if errors and recovery else "avoid")
            self.db.execute(
                """INSERT INTO outcomes(ts,task_type,goal,expected,actual,
                   success,quality,latency_s,cost,errors,recovery,
                   user_correction,lesson,strategy)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (_now(), task_type, goal[:200], expected[:400],
                 actual[:400], int(bool(success)), quality,
                 float(latency_s), float(cost), int(errors),
                 recovery[:200], user_correction[:200],
                 lesson, strategy))
            if failure_signature:
                row = self.db.execute(
                    "SELECT count FROM failure_patterns WHERE signature=?",
                    (failure_signature,)).fetchone()
                self.db.execute(
                    """INSERT INTO failure_patterns(signature,count,last_seen,sample)
                       VALUES(?,?,?,?) ON CONFLICT(signature) DO UPDATE SET
                       count=excluded.count, last_seen=excluded.last_seen""",
                    (failure_signature, (row[0] if row else 0) + 1, _now(),
                     actual[:200]))
            st = self.db.execute(
                "SELECT wins,losses FROM strategies WHERE task_type=?",
                (task_type,)).fetchone()
            wins, losses = st if st else (0, 0)
            wins, losses = (wins + 1, losses) if success else (wins, losses + 1)
            self.db.execute(
                """INSERT INTO strategies(task_type,strategy,wins,losses,updated)
                   VALUES(?,?,?,?,?) ON CONFLICT(task_type) DO UPDATE SET
                   strategy=excluded.strategy, wins=excluded.wins,
                   losses=excluded.losses, updated=excluded.updated""",
                (task_type, strategy, wins, losses, _now()))
            self.db.commit()
            oid = self.db.execute("SELECT last_insert_rowid()").fetchone()[0]
            # güvenli bilgi katmanına yansıtma (production kodu DEĞİL)
            if self.knowledge is not None and lesson:
                self.knowledge.ingest(
                    title=f"lesson:{task_type}:{oid}",
                    text=lesson, source="learning-loop",
                    source_confidence=0.6 if success else 0.4,
                    scope="global",
                    claims={f"lesson_{task_type}": lesson})
            if self.user_intel is not None and user_correction:
                self.user_intel.observe("corrections", task_type,
                                        user_correction)
            return {"ok": True, "outcome_id": oid, "quality": quality,
                    "lesson": lesson, "strategy": strategy}

    @staticmethod
    def _derive_lesson(task_type, success, quality, errors,
                       user_correction, failure_signature) -> str:
        if user_correction:
            return (f"[{task_type}] kullanıcı düzeltmesi: "
                    f"{user_correction[:120]} — tercih güncellenmeli")
        if success and (quality is None or quality >= 0.5):
            return f"[{task_type}] yaklaşım işe yaradı — tekrar için sakla"
        if failure_signature:
            return (f"[{task_type}] hata imzası '{failure_signature}' — "
                    f"tekrardan kaçın")
        if errors:
            return f"[{task_type}] {errors} hata ile tamamlandı — izole et"
        if quality is not None and quality < 0.5:
            return (f"[{task_type}] düşük kalite ({quality}) — "
                    f"beklenti/sonuç uyumsuz")
        return f"[{task_type}] sonuç kaydedildi"

    # ---------------------------------------------------- karar desteği
    def strategy_for(self, task_type: str) -> dict:
        with self.lock:
            row = self.db.execute(
                "SELECT strategy,wins,losses FROM strategies WHERE task_type=?",
                (task_type,)).fetchone()
        if not row:
            return {"task_type": task_type, "strategy": "no-history",
                    "confidence": 0.0}
        strat, wins, losses = row
        n = wins + losses
        conf = min(0.9, 0.5 + 0.1 * n)
        if losses > wins * 2:
            return {"task_type": task_type, "strategy": "consider-avoid",
                    "wins": wins, "losses": losses,
                    "confidence": round(conf, 3)}
        return {"task_type": task_type, "strategy": strat,
                "wins": wins, "losses": losses,
                "confidence": round(conf, 3)}

    def failure_frequency(self, signature: str) -> int:
        with self.lock:
            row = self.db.execute(
                "SELECT count FROM failure_patterns WHERE signature=?",
                (signature,)).fetchone()
        return row[0] if row else 0

    def metrics(self, task_type: str | None = None) -> dict:
        q = ("SELECT COUNT(*), SUM(success), AVG(latency_s), SUM(cost),"
             " SUM(errors) FROM outcomes")
        args: list = []
        if task_type:
            q += " WHERE task_type=?"
            args.append(task_type)
        with self.lock:
            r = self.db.execute(q, args).fetchone()
        n = r[0] or 0
        return {"outcomes": n,
                "success_rate": round((r[1] or 0) / n, 3) if n else None,
                "avg_latency_s": round(r[2] or 0, 3) if n else None,
                "total_cost": round(r[3] or 0, 4),
                "total_errors": int(r[4] or 0)}

    def recent_lessons(self, limit: int = 10) -> list[dict]:
        with self.lock:
            rows = self.db.execute(
                "SELECT ts,task_type,lesson,strategy FROM outcomes"
                " ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [{"ts": r[0], "task_type": r[1], "lesson": r[2],
                 "strategy": r[3]} for r in rows]
