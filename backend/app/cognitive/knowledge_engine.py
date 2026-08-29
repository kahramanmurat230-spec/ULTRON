"""Knowledge Engine — Wave 5 §6 (production-grade bilgi/RAG katmanı).

- document ingestion (metin; kaynağı ile)
- chunking (cümle/boyut bazlı; örtüşmeli)
- metadata + provenance (source, author, ts, scope)
- source confidence (kaynak güvenilirlik skoru — kullanıcı/Politika verir)
- retrieval: GERÇEK lexical BM25 (sahte vector search YOK — vector DB yoksa
  dürüst lexical + açıkça 'lexical' motor etiketi)
- reranking: BM25 + kaynak güveni + tazelik
- citation tracking: her sonuç köken chunk id ile döner
- contradiction detection: aynı anahtarlı çelişen iddialar
- stale knowledge detection: max_age aşan parçalar işaretlenir
- knowledge updates: yeni sürüm eklenince eski sürüm superseded olur
- deletion/tombstone: silinen bilgi geri gelmez (mezar taşı kalır)
- project-scoped knowledge: scope alanı ile izolasyon

Dürüstlük: embeddings yok → yok der; BM25 gerçek hesap.
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
import threading
import time
from pathlib import Path


def _now() -> float:
    return time.time()


_WORD = re.compile(r"[a-zçğıöşü0-9]+", re.IGNORECASE)


def _tokens(text: str) -> list[str]:
    return [w.lower() for w in _WORD.findall(text or "")]


def chunk_text(text: str, max_chars: int = 600, overlap: int = 80) -> list[str]:
    """Boyut bazlı örtüşmeli parçalama (cümle sınırlarına saygılı)."""
    text = (text or "").strip()
    if not text:
        return []
    sents = re.split(r"(?<=[.!?…])\s+", text)
    chunks, cur = [], ""
    for s in sents:
        if len(cur) + len(s) + 1 <= max_chars:
            cur = (cur + " " + s).strip()
        else:
            if cur:
                chunks.append(cur)
            # örtüşme: önceki kuyruğu taşı
            tail = cur[-overlap:] if overlap and cur else ""
            cur = (tail + " " + s).strip() if tail else s
            while len(cur) > max_chars:          # dev cümle: sert kes
                chunks.append(cur[:max_chars])
                cur = cur[max_chars - overlap:] if overlap else cur[max_chars:]
    if cur:
        chunks.append(cur)
    return chunks


class KnowledgeEngine:
    def __init__(self, db_path: str = "data/cognitive/knowledge.db",
                 default_max_age_s: float = 180 * 86400):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(str(self.path), check_same_thread=False)
        self.db.execute("""CREATE TABLE IF NOT EXISTS documents(
            doc_id TEXT PRIMARY KEY, title TEXT, source TEXT,
            author TEXT DEFAULT '', ingested REAL,
            source_confidence REAL DEFAULT 0.5, scope TEXT DEFAULT 'global',
            superseded_by TEXT, deleted REAL)""")
        self.db.execute("""CREATE TABLE IF NOT EXISTS chunks(
            chunk_id INTEGER PRIMARY KEY AUTOINCREMENT,
            doc_id TEXT, idx INTEGER, text TEXT, created REAL)""")
        self.db.execute("""CREATE TABLE IF NOT EXISTS claims(
            key TEXT, doc_id TEXT, value TEXT, ts REAL,
            PRIMARY KEY(key, doc_id))""")
        self.db.commit()
        self.default_max_age_s = float(default_max_age_s)
        self._doc_seq = 0

    # ---------------------------------------------------- ingestion
    def ingest(self, title: str, text: str, source: str = "",
               author: str = "", source_confidence: float = 0.5,
               scope: str = "global", claims: dict | None = None,
               max_chars: int = 600) -> dict:
        """Belge + chunk'ları + anahtarlı iddialar. Güncelleme semantiği:
        aynı (scope, title) varsa eski doc superseded edilir."""
        with self.lock:
            self._doc_seq += 1
            doc_id = f"doc{int(_now())}_{self._doc_seq}"
            old = self.db.execute(
                """SELECT doc_id FROM documents WHERE title=? AND scope=?
                   AND deleted IS NULL AND superseded_by IS NULL""",
                (title, scope)).fetchone()
            if old:
                self.db.execute("UPDATE documents SET superseded_by=?"
                                " WHERE doc_id=?", (doc_id, old[0]))
            self.db.execute(
                """INSERT INTO documents(doc_id,title,source,author,ingested,
                   source_confidence,scope) VALUES(?,?,?,?,?,?,?)""",
                (doc_id, title, source, author, _now(),
                 max(0.0, min(1.0, float(source_confidence))), scope))
            for i, ch in enumerate(chunk_text(text, max_chars=max_chars)):
                self.db.execute(
                    "INSERT INTO chunks(doc_id,idx,text,created) VALUES(?,?,?,?)",
                    (doc_id, i, ch, _now()))
            for k, v in (claims or {}).items():
                self.db.execute(
                    "INSERT OR REPLACE INTO claims(key,doc_id,value,ts)"
                    " VALUES(?,?,?,?)", (str(k), doc_id,
                                         json.dumps(v, ensure_ascii=False), _now()))
            self.db.commit()
            n = self.db.execute("SELECT COUNT(*) FROM chunks WHERE doc_id=?",
                                (doc_id,)).fetchone()[0]
            return {"ok": True, "doc_id": doc_id, "chunks": n,
                    "supersedes": old[0] if old else None}

    def delete(self, doc_id: str) -> dict:
        """Tombstone: içerik gider, mezar taşı kalır (sessiz geri dönüş YOK)."""
        with self.lock:
            cur = self.db.execute(
                "UPDATE documents SET deleted=? WHERE doc_id=? AND deleted IS NULL",
                (_now(), doc_id))
            self.db.commit()
            return {"ok": cur.rowcount > 0, "tombstone": bool(cur.rowcount)}

    # ---------------------------------------------------- retrieval
    def _live_docs(self, scope: str | None):
        q = ("SELECT doc_id,title,source,source_confidence,ingested FROM documents"
             " WHERE deleted IS NULL AND superseded_by IS NULL")
        args: list = []
        if scope is not None:
            q += " AND scope IN ('global',?)"
            args.append(scope)
        cur = self.db.execute(q, args)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    def search(self, query: str, scope: str | None = None,
               top_k: int = 5, max_age_s: float | None = None) -> dict:
        """BM25 lexical arama + rerank (kaynak güveni × tazelik).
        Motor dürüstçe 'lexical-bm25' etiketli — vector DEĞİL."""
        with self.lock:
            docs = self._live_docs(scope)
            if not docs:
                return {"engine": "lexical-bm25", "results": [],
                        "note": "canlı belge yok"}
            qtoks = _tokens(query)
            if not qtoks:
                return {"engine": "lexical-bm25", "results": []}
            # BM25 kümülatif: chunk-bazlı skor dokümana toplanır
            k1, b = 1.5, 0.75
            scored = []
            chunk_rows = {}
            for d in docs:
                rows = self.db.execute(
                    "SELECT chunk_id, text FROM chunks WHERE doc_id=?",
                    (d["doc_id"],)).fetchall()
                chunk_rows[d["doc_id"]] = rows
                N_all = sum(len(rows) for rows in chunk_rows.values()) or 1
                avgdl = (sum(len(_tokens(t)) for _, t in rows) / len(rows)) or 1.0
                score = 0.0
                for qt in qtoks:
                    df = sum(1 for rows in chunk_rows.values()
                             for _, t in rows if qt in _tokens(t))
                    if df == 0:
                        continue
                    idf = math.log(1 + (N_all - df + 0.5) / (df + 0.5))
                    for _, t in rows:
                        tl = _tokens(t)
                        f = tl.count(qt)
                        if not f:
                            continue
                        score += idf * (f * (k1 + 1)) / (
                            f + k1 * (1 - b + b * len(tl) / avgdl))
                if score > 0:
                    scored.append((score, d))
            max_age = self.default_max_age_s if max_age_s is None else max_age_s
            now = _now()
            results = []
            for score, d in sorted(scored, key=lambda x: -x[0])[:top_k]:
                age = now - d["ingested"]
                stale = age > max_age
                freshness = 1.0 / (1.0 + age / (7 * 86400))
                rerank = score * (0.5 + 0.5 * d["source_confidence"]) * \
                    (0.6 + 0.4 * freshness)
                # en iyi chunk: sorgu tokenlarından en çoğunu barındıran
                best = max(chunk_rows[d["doc_id"]], key=lambda r: sum(
                    1 for q in qtoks if q in _tokens(r[1])))
                results.append({
                    "doc_id": d["doc_id"], "title": d["title"],
                    "source": d["source"], "citation": best[0],
                    "snippet": best[1][:240],
                    "bm25": round(score, 3),
                    "source_confidence": d["source_confidence"],
                    "age_days": round(age / 86400, 1), "stale": stale,
                    "rerank_score": round(rerank, 3)})
            return {"engine": "lexical-bm25", "results": results}

    # ---------------------------------------------------- knowledge ops
    def contradictions(self) -> list[dict]:
        """Aynı anahtarda çelişen canlı iddialar."""
        with self.lock:
            docs = {d["doc_id"]: d for d in self._live_docs(None)}
            rows = self.db.execute(
                "SELECT key, doc_id, value FROM claims").fetchall()
        by_key: dict[str, list] = {}
        for k, doc, v in rows:
            if doc in docs:
                by_key.setdefault(k, []).append((doc, v))
        out = []
        for k, items in by_key.items():
            vals = {v for _, v in items}
            if len(vals) > 1:
                out.append({"key": k,
                            "claims": [{"doc_id": d, "value": json.loads(v)}
                                       for d, v in items],
                            "resolution": "kullanıcı kararı gerekli"})
        return out

    def stale_documents(self, max_age_s: float | None = None) -> list[dict]:
        max_age = self.default_max_age_s if max_age_s is None else max_age_s
        now = _now()
        with self.lock:
            return [{"doc_id": d["doc_id"], "title": d["title"],
                     "age_days": round((now - d["ingested"]) / 86400, 1)}
                    for d in self._live_docs(None)
                    if now - d["ingested"] > max_age]

    def claim(self, key: str) -> dict:
        """Anahtarlı iddia: en güvenilir canlı kaynak kazanır; çelişki
        varsa dürüstçe bildirilir."""
        with self.lock:
            docs = {d["doc_id"]: d for d in self._live_docs(None)}
            rows = self.db.execute(
                "SELECT doc_id, value, ts FROM claims WHERE key=?", (key,)).fetchall()
        live = [(d, v, ts) for d, v, ts in rows if d in docs]
        if not live:
            return {"key": key, "value": None, "conflict": False}
        best = max(live, key=lambda x: docs[x[0]]["source_confidence"])
        conflict = len({v for _, v, _ in live}) > 1
        return {"key": key, "value": json.loads(best[1]),
                "source": docs[best[0]]["source"],
                "source_confidence": docs[best[0]]["source_confidence"],
                "conflict": conflict}

    def stats(self) -> dict:
        with self.lock:
            total = self.db.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            live = self.db.execute(
                "SELECT COUNT(*) FROM documents WHERE deleted IS NULL"
                " AND superseded_by IS NULL").fetchone()[0]
            tombs = self.db.execute(
                "SELECT COUNT(*) FROM documents WHERE deleted IS NOT NULL"
            ).fetchone()[0]
            sup = self.db.execute(
                "SELECT COUNT(*) FROM documents WHERE superseded_by IS NOT NULL"
            ).fetchone()[0]
            chunks = self.db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        return {"documents": total, "live": live, "tombstones": tombs,
                "superseded": sup, "chunks": chunks}
