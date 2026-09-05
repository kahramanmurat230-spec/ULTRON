"""Knowledge Graph — Wave 5 §8.

ENTITY — RELATION — TIMESTAMP — SOURCE — CONFIDENCE — SCOPE modeli:
- entity creation (scoped; user/project izolasyonu)
- relationship creation (geçici ilişkiler: from_ts/to_ts)
- temporal relationships (zaman aralıklı; 'X artık Y değil' durumları)
- source provenance (her kenar kökenli)
- conflict detection (aynı ilişkide çelişen canlı kenarlar)
- graph traversal (BFS, derinlik limitli; döngü güvenli)
- stale relationship handling (to_ts geçti → tarihe düşer)
- confidence bazlı kenar filtresi

SQLite-backed; additive; mevcut world/entities modülünden BAĞIMSIZ katman
(onun yerine geçmez).
"""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path


def _now() -> float:
    return time.time()


class KnowledgeGraph:
    def __init__(self, db_path: str = "data/cognitive/kgraph.db"):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(str(self.path), check_same_thread=False)
        self.db.execute("""CREATE TABLE IF NOT EXISTS entities(
            id TEXT, scope TEXT, type TEXT, name TEXT,
            created REAL, meta TEXT,
            PRIMARY KEY(id, scope))""")
        self.db.execute("""CREATE TABLE IF NOT EXISTS edges(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            src TEXT, dst TEXT, relation TEXT, scope TEXT,
            confidence REAL DEFAULT 0.5, source TEXT,
            from_ts REAL, to_ts REAL, created REAL, retired REAL)""")
        self.db.commit()
        self._seq = 0

    # ---------------------------------------------------- entities
    def add_entity(self, entity_id: str, name: str, etype: str = "thing",
                   scope: str = "global", meta: dict | None = None) -> dict:
        import json
        with self.lock:
            self.db.execute(
                """INSERT OR REPLACE INTO entities(id,scope,type,name,created,meta)
                   VALUES(?,?,?,?,?,?)""",
                (entity_id, scope, etype, name, _now(),
                 json.dumps(meta or {}, ensure_ascii=False)))
            self.db.commit()
            return {"ok": True, "id": entity_id, "scope": scope}

    def entity(self, entity_id: str, scope: str = "global") -> dict | None:
        with self.lock:
            r = self.db.execute(
                "SELECT id,scope,type,name,created FROM entities"
                " WHERE id=? AND scope=?", (entity_id, scope)).fetchone()
        if not r:
            return None
        return {"id": r[0], "scope": r[1], "type": r[2], "name": r[3],
                "created": r[4]}

    # ---------------------------------------------------- relations
    def relate(self, src: str, dst: str, relation: str,
               scope: str = "global", confidence: float = 0.5,
               source: str = "", from_ts: float | None = None,
               to_ts: float | None = None, supersede: bool = True) -> dict:
        """Kenar ekle. supersede=True ise aynı (src,relation) için eski
        sonsuz kenar emekli edilir (çelişki yerine sürüm geçmişi)."""
        with self.lock:
            for e in (src, dst):
                if self.entity(e, scope) is None:
                    return {"ok": False, "error": f"unknown entity: {e}"}
            now = _now()
            if supersede:
                self.db.execute(
                    """UPDATE edges SET retired=? WHERE src=? AND relation=?
                       AND scope=? AND retired IS NULL""",
                    (now, src, relation, scope))
            cur = self.db.execute(
                """INSERT INTO edges(src,dst,relation,scope,confidence,source,
                   from_ts,to_ts,created) VALUES(?,?,?,?,?,?,?,?,?)""",
                (src, dst, relation, scope,
                 max(0.0, min(1.0, float(confidence))), str(source)[:120],
                 from_ts if from_ts is not None else now, to_ts, now))
            self.db.commit()
            return {"ok": True, "edge_id": cur.lastrowid}

    def retire(self, edge_id: int, at: float | None = None) -> dict:
        with self.lock:
            cur = self.db.execute(
                "UPDATE edges SET retired=?, to_ts=COALESCE(to_ts,?)"
                " WHERE id=? AND retired IS NULL",
                (at or _now(), at or _now(), edge_id))
            self.db.commit()
            return {"ok": cur.rowcount > 0}

    def live_edges(self, src: str, relation: str | None = None,
                   scope: str = "global", now: float | None = None,
                   min_confidence: float = 0.0) -> list[dict]:
        now = _now() if now is None else now
        q = ("SELECT id,src,dst,relation,confidence,source,from_ts,to_ts"
             " FROM edges WHERE src=? AND scope=? AND retired IS NULL"
             " AND confidence>=?")
        args: list = [src, scope, min_confidence]
        if relation:
            q += " AND relation=?"
            args.append(relation)
        with self.lock:
            rows = self.db.execute(q, args).fetchall()
        # sütun sırası: id,src,dst,relation,confidence,source,from_ts,to_ts
        out = []
        for r in rows:
            live = ((r[7] is None or now <= r[7])
                    and (r[6] is None or now >= r[6]))
            out.append({"edge_id": r[0], "src": r[1], "dst": r[2],
                        "relation": r[3], "confidence": r[4], "source": r[5],
                        "from_ts": r[6], "to_ts": r[7], "live": live})
        return out

    # ---------------------------------------------------- conflicts
    def conflicts(self, scope: str = "global") -> list[dict]:
        """Aynı (src, relation) için birden çok canlı dst → çelişki."""
        now = _now()
        seen: dict[tuple, list] = {}
        with self.lock:
            rows = self.db.execute(
                "SELECT src,dst,relation,confidence FROM edges"
                " WHERE scope=? AND retired IS NULL", (scope,)).fetchall()
        for src, dst, rel, conf in rows:
            seen.setdefault((src, rel), []).append((dst, conf))
        out = []
        for (src, rel), lst in seen.items():
            dsts = {d for d, _ in lst}
            if len(dsts) > 1:
                out.append({"src": src, "relation": rel,
                            "candidates": lst,
                            "resolution": "en yüksek güvenli kenar + kullanıcı"})
        return out

    # ---------------------------------------------------- traversal
    def traverse(self, start: str, max_depth: int = 3,
                 scope: str = "global", min_confidence: float = 0.0,
                 now: float | None = None) -> dict:
        """BFS; döngü güvenli; derinlik limitli; yalnız canlı kenarlar."""
        now = _now() if now is None else now
        visited = {start: 0}
        frontier = [start]
        paths: dict[str, list[str]] = {start: [start]}
        depth_reached = 0
        for depth in range(1, max_depth + 1):
            nxt = []
            for node in frontier:
                for e in self.live_edges(node, scope=scope, now=now,
                                         min_confidence=min_confidence):
                    if not e["live"]:
                        continue
                    dst = e["dst"]
                    if dst not in visited:
                        visited[dst] = depth
                        paths[dst] = paths[node] + [f"-[{e['relation']}]->", dst]
                        nxt.append(dst)
                        depth_reached = max(depth_reached, depth)
            frontier = nxt
            if not frontier:
                break
        return {"start": start, "max_depth": max_depth,
                "reached_depth": depth_reached,
                "nodes": {n: {"depth": d, "path": paths[n]}
                          for n, d in visited.items() if n != start}}

    def stale_edges(self, now: float | None = None) -> list[dict]:
        """to_ts'i geçmiş ama retire edilmemiş kenarlar (temizlik önerisi)."""
        now = _now() if now is None else now
        with self.lock:
            rows = self.db.execute(
                "SELECT id,src,dst,relation,to_ts FROM edges"
                " WHERE to_ts IS NOT NULL AND to_ts<? AND retired IS NULL",
                (now,)).fetchall()
        return [{"edge_id": r[0], "src": r[1], "dst": r[2],
                 "relation": r[3], "to_ts": r[4]} for r in rows]

    def stats(self) -> dict:
        with self.lock:
            ents = self.db.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
            edges = self.db.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
            retired = self.db.execute(
                "SELECT COUNT(*) FROM edges WHERE retired IS NOT NULL"
            ).fetchone()[0]
        return {"entities": ents, "edges": edges, "retired": retired}
