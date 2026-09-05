"""Semantic Memory V2 — TF-IDF cosine (numpy) with optional ChromaDB,
silent fact extraction (AUTO_LEARNED) and organic decay.

Decay rules: 90 gün erişilmemiş + importance < eşik => archived=1.
Kritik kayıtlar (PROFILE / IMPORTANT / master_rules) ASLA decay olmaz.
Hassas veri (şifre/token/email) otomatik reddedilir.
"""
import re
import sqlite3
import time

try:
    import numpy as np
except Exception:  # pragma: no cover
    np = None

try:
    import chromadb  # noqa: F401
    HAVE_CHROMA = True
except Exception:
    HAVE_CHROMA = False

SENSITIVE = re.compile(r"(şifre|password|parola|token|api[_-]?key|secret|kredi\s*kart|"
                       r"[\w.+-]+@[\w-]+\.\w{2,})", re.I)
CRITICAL_KINDS = ("PROFILE", "IMPORTANT")

FACT_PATTERNS = [
    re.compile(r"(?:sever|seviyorum|hoşlanırım|tercihim|favorim)\s*([^.,;!]{3,60})", re.I),
    re.compile(r"(?:gece|gündüz|sabah|akşam)\s*(?:çalışırım|kod\s*yazarım|uyurum|odaklanırım)", re.I),
    re.compile(r"(\d{1,2})[.:](\d{2})\s*(?:gibi|civarında|saatinde)", re.I),
]


class SemanticMemoryV2:
    def __init__(self, memory, dna=None):
        self.memory = memory
        self.dna = dna
        self.engine = "chroma" if HAVE_CHROMA else "tfidf"

    # ------------------------------------------------------------ search
    @staticmethod
    def _tok(t):
        return re.findall(r"[\wçğıöşü]+", (t or "").lower())

    def search(self, query, limit=5):
        docs = self.memory.recent(500)
        if not docs:
            return []
        if np is None:
            q = set(self._tok(query))
            scored = []
            for row in docs:
                d = set(self._tok(row[1]))
                inter = len(q & d)
                if inter:
                    scored.append((inter / max(1, len(q | d)), row))
            scored.sort(key=lambda x: -x[0])
            return scored[:limit]
        vocab = {}
        all_rows = [self._tok(query)] + [self._tok(r[1]) for r in docs]
        for toks in all_rows:
            for t in toks:
                vocab.setdefault(t, len(vocab))
        def vec(toks):
            v = np.zeros(len(vocab))
            for t in toks:
                v[vocab[t]] += 1
            return v
        dv = [vec(t) for t in all_rows]
        n = len(docs)
        df = np.stack(dv[1:]) > 0
        idf = np.log((n + 1) / (df.sum(0) + 1)) + 1
        tfidf = [v * idf for v in dv]
        qv = tfidf[0]
        qn = np.linalg.norm(qv) or 1.0
        out = []
        for i, r in enumerate(docs):
            d = tfidf[i + 1]
            dn = np.linalg.norm(d) or 1.0
            cos = float((qv @ d) / (qn * dn))
            if cos > 0:
                out.append((round(cos, 3), r))
        out.sort(key=lambda x: -x[0])
        return out[:limit]

    # ------------------------------------------------------------ facts
    def extract_facts(self, text) -> list:
        learned = []
        if not text or SENSITIVE.search(text):
            return learned  # hassas veri asla öğrenilmez
        for rx in FACT_PATTERNS:
            m = rx.search(text)
            if m:
                fact = m.group(0).strip()[:120]
                learned.append(fact)
                if self.dna:
                    self.dna.insert("AUTO_LEARNED", note=fact)
        return learned

    # ------------------------------------------------------------ decay
    def decay(self, days=90, min_importance=0.3):
        cutoff = time.time() - days * 86400
        with sqlite3.connect(self.memory.path) as db:
            cur = db.execute(
                "UPDATE memories SET archived=1 WHERE archived=0 AND kind NOT IN (?,?)"
                " AND importance < ? AND COALESCE(last_access,0) < ? AND created_at NOT NULL",
                ("PROFILE", "IMPORTANT", min_importance, cutoff))
            return cur.rowcount

    def stats(self):
        with sqlite3.connect(self.memory.path) as db:
            total = db.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
            archived = db.execute("SELECT COUNT(*) FROM memories WHERE archived=1").fetchone()[0]
            cand = db.execute("SELECT COUNT(*) FROM memories WHERE archived=0 AND"
                              " kind NOT IN ('PROFILE','IMPORTANT') AND importance<0.3").fetchone()[0]
        auto = 0
        if self.dna:
            rows = self.dna.recent(3650)
            auto = sum(1 for r in rows if r[1] == "AUTO_LEARNED")
        return {"total": total, "archived": archived, "decay_candidates": cand,
                "auto_learned": auto, "engine": self.engine}
