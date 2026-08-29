"""Cognitive Context Engine — Wave 5 §1.

Katmanlar (hepsi SQLite-backed, dış bağımlılık YOK):
- working context: kısa ömürlü anahtar/değer parçaları (öncelik + TTL ile)
- long-term projection: kalıcı parçaların bağlama yansıması (decay + salience)
- cross-modal: parça kaynağı etiketi (text/voice/vision/browser/system) —
  farklı modalitelerden gelen bağlam ayrı kökenle izlenir
- task/project context: isim-uzaylı kapsam (namespace=task:<id> | project:<ad>)
- temporal context: geçerlilik penceresi (valid_from/valid_to)
- compression: bütçeyi aşan bağlam öncelik sırasıyla sıkıştırılır;
  atılan parçalar dürüstçe "dropped" olarak raporlanır (sessiz kayıp YOK)
- prioritization: score = w_i*importance + w_r*recency + w_e*explicit
- conflict resolution: aynı anahtar farklı değer → yeni kazanır, çakışma
  kaydı tutulur; explicit parça inferred'i ezer
- expiration: TTL dolan parçalar bağlamdan düşer (loglanır)
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

MODALITIES = ("text", "voice", "vision", "browser", "system")

_W_IMPORTANCE = 0.5
_W_RECENCY = 0.3
_W_EXPLICIT = 0.2


def _now() -> float:
    return time.time()


class ContextEngine:
    """Merkezi bağlam yöneticisi. Deterministik — LLM GEREKMEZ."""

    def __init__(self, db_path: str = "data/cognitive/context.db",
                 token_budget: int = 2000):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(str(self.path), check_same_thread=False)
        self.db.execute("""CREATE TABLE IF NOT EXISTS context_items(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            namespace TEXT DEFAULT 'global',
            modality TEXT DEFAULT 'system',
            importance REAL DEFAULT 0.5,
            explicit INTEGER DEFAULT 0,
            created REAL, last_touched REAL,
            ttl_s REAL, valid_from REAL, valid_to REAL,
            source TEXT DEFAULT '')""")
        self.db.execute("CREATE INDEX IF NOT EXISTS ix_ctx_ns ON context_items(namespace)")
        self.db.commit()
        self.token_budget = int(token_budget)
        self.conflicts: list[dict] = []
        self.expired: list[dict] = []
        self.last_compression: dict = {}

    # ---------------------------------------------------------- helpers
    def _tokens(self, text: str) -> int:
        # dürüst yaklaşım: boşluk-bazlı üst sınır (model tokenizer'ı yok)
        return max(1, len(str(text).split()))

    def _expired(self, row, now: float) -> bool:
        """Kalıcı düşme: TTL doldu VEYA geçerlilik penceresi KAPANDI.
        Gelecekte başlayacak parça (valid_from ileride) silinmez — yalnızca
        henüz görünmez."""
        if row["ttl_s"] is not None and now - row["created"] > row["ttl_s"]:
            return True
        if row["valid_to"] is not None and now > row["valid_to"]:
            return True
        return False

    def _visible(self, row, now: float) -> bool:
        if row["valid_from"] is not None and now < row["valid_from"]:
            return False
        return True

    # ---------------------------------------------------------- write
    def add(self, key: str, value, namespace: str = "global",
            modality: str = "system", importance: float = 0.5,
            explicit: bool = False, ttl_s: float | None = None,
            valid_from: float | None = None, valid_to: float | None = None,
            source: str = "", now: float | None = None) -> dict:
        if modality not in MODALITIES:
            raise ValueError(f"unknown modality: {modality}")
        now = _now() if now is None else now
        with self.lock:
            # conflict detection: aynı namespace+key canlı parça
            for row in self._rows(namespace, now):
                if row["key"] == key:
                    old = json.loads(row["value"])
                    new_explicit = bool(explicit)
                    old_explicit = bool(row["explicit"])
                    if old != value:
                        if new_explicit or not old_explicit:
                            self.conflicts.append({
                                "key": key, "namespace": namespace,
                                "old": old, "new": value,
                                "resolution": "new-wins"
                                if new_explicit or not old_explicit else "kept-old",
                                "ts": now})
                            if not (old_explicit and not new_explicit):
                                self.db.execute("DELETE FROM context_items WHERE id=?",
                                                (row["id"],))
                        else:  # explicit eski → korunur, yenisi reddedilir
                            self.conflicts.append({
                                "key": key, "namespace": namespace,
                                "old": old, "new": value,
                                "resolution": "kept-old-explicit", "ts": now})
                            return {"ok": False, "error":
                                    "explicit item wins over inferred update",
                                    "conflict": True}
            self.db.execute(
                """INSERT INTO context_items
                   (key,value,namespace,modality,importance,explicit,created,
                    last_touched,ttl_s,valid_from,valid_to,source)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (key, json.dumps(value, ensure_ascii=False), namespace,
                 modality, float(importance), int(bool(explicit)), now, now,
                 ttl_s, valid_from, valid_to, str(source)[:120]))
            self.db.commit()
            return {"ok": True, "key": key, "namespace": namespace}

    def touch(self, key: str, namespace: str = "global",
              now: float | None = None) -> bool:
        now = _now() if now is None else now
        with self.lock:
            cur = self.db.execute(
                "UPDATE context_items SET last_touched=? WHERE key=? AND namespace=?",
                (now, key, namespace))
            self.db.commit()
            return cur.rowcount > 0

    def remove(self, key: str, namespace: str = "global") -> bool:
        with self.lock:
            cur = self.db.execute(
                "DELETE FROM context_items WHERE key=? AND namespace=?",
                (key, namespace))
            self.db.commit()
            return cur.rowcount > 0

    # ---------------------------------------------------------- read
    def _rows(self, namespace: str, now: float):
        cur = self.db.execute(
            "SELECT * FROM context_items WHERE namespace=?", (namespace,))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    def _score(self, row, now: float) -> float:
        age = max(0.0, now - (row["last_touched"] or row["created"] or now))
        recency = 1.0 / (1.0 + age / 3600.0)   # 1sa → 0.5
        return (_W_IMPORTANCE * float(row["importance"] or 0.5)
                + _W_RECENCY * recency
                + _W_EXPLICIT * float(bool(row["explicit"])))

    def snapshot(self, namespace: str = "global", now: float | None = None,
                 token_budget: int | None = None) -> dict:
        """Önceliklendirilmiş + sıkıştırılmış bağlam görünümü.

        Dürüstlük: bütçe aşılırsa düşük skorlu parçalar düşürülür ve
        last_compression'da 'dropped' listelenir — sessiz kayıp YOK."""
        now = _now() if now is None else now
        budget = self.token_budget if token_budget is None else int(token_budget)
        with self.lock:
            # expiration (önce): TTL/pencere dolanlar düşer + kayıt
            rows = self._rows(namespace, now)
            alive, expired_now = [], []
            for r in rows:
                if self._expired(r, now):
                    expired_now.append(r)
                else:
                    alive.append(r)
            for r in expired_now:
                self.db.execute("DELETE FROM context_items WHERE id=?", (r["id"],))
                self.expired.append({"key": r["key"], "namespace": namespace,
                                     "reason": "ttl-or-window", "ts": now})
            if expired_now:
                self.db.commit()
            scored = sorted(((self._score(r, now), r) for r in alive),
                            key=lambda x: -x[0])
            kept, dropped, used = [], [], 0
            for sc, r in scored:
                if not self._visible(r, now):
                    continue   # henüz başlamamış parça: listelenmez, silinmez
                t = self._tokens(r["value"])
                if used + t <= budget:
                    used += t
                    kept.append({"key": r["key"], "value": json.loads(r["value"]),
                                 "modality": r["modality"],
                                 "importance": r["importance"],
                                 "explicit": bool(r["explicit"]),
                                 "source": r["source"], "score": round(sc, 4)})
                else:
                    dropped.append(r["key"])
            self.last_compression = {
                "namespace": namespace, "budget": budget, "used": used,
                "kept": len(kept), "dropped": dropped, "ts": now}
            return {"namespace": namespace, "items": kept,
                    "tokens_used": used, "token_budget": budget,
                    "dropped": dropped, "expired_now":
                        [e["key"] for e in expired_now]}

    # ---------------------------------------------------------- projection
    def project(self, namespaces: list[str], now: float | None = None,
                token_budget: int | None = None) -> dict:
        """Çok-isim-uzaylı yansıtma: task/project/global birleşik görünüm."""
        now = _now() if now is None else now
        merged, dropped = {}, []
        for ns in namespaces:
            snap = self.snapshot(ns, now=now, token_budget=token_budget)
            for it in snap["items"]:
                k = (ns, it["key"])
                if k in merged and merged[k]["value"] != it["value"]:
                    self.conflicts.append({"key": it["key"], "namespace": ns,
                                           "old": merged[k]["value"],
                                           "new": it["value"],
                                           "resolution": "later-namespace-wins",
                                           "ts": now})
                merged[k] = it
            dropped += snap["dropped"]
        return {"namespaces": namespaces, "items": list(merged.values()),
                "dropped": dropped}

    def stats(self) -> dict:
        with self.lock:
            n = self.db.execute("SELECT COUNT(*) FROM context_items").fetchone()[0]
            by_ns = self.db.execute(
                "SELECT namespace, COUNT(*) FROM context_items GROUP BY namespace"
            ).fetchall()
            return {"items": n, "by_namespace": dict(by_ns),
                    "conflicts": len(self.conflicts),
                    "expired_total": len(self.expired)}
