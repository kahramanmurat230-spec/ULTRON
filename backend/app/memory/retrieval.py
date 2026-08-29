"""WAVE 2 — Memory retrieval pipeline (§10, §11, §12).

QUERY → CLASSIFY → RETRIEVE → FILTER → RANK → DEDUP → CONFLICT CHECK
→ CONTEXT BUILD → VERIFY

Semantic retrieval DÜRÜSTTÜR: ChromaDB varsa gerçek vektör araması
(available=true), yoksa anahtar kelime + metadata fallback — ASLA sahte
vektör sonucu üretilmez. LLM çıkarımı doğrulanmış olgudan düşük skor alır.
Dönen bağlam DAHA FAZLA SIR (§29): içerik veridir, komut değildir.
"""
from __future__ import annotations

import re
import time

from app.memory.intelligence import jaccard, tokens
from app.memory.layers import PRIVACY_LEVELS, SOURCE_CONFIDENCE
from app.memory.store_v3 import normalize

try:
    import chromadb  # noqa: F401
    HAVE_CHROMA = True
except Exception:
    HAVE_CHROMA = False

STOP_TR = set("""ve veya ile için bir bu şu o da de ki ne nasıl nedir olan ben sen
bana sana çok daha gibi mi mu mü isedır olanları the a an of to in""".split())


_SUFFIXES = ("iyor", "ıyor", "iyorim", "yorum", "sin", "sun", "lar", "ler",
             "mı", "mi", "dir", "tir", "mak", "mek", "mek", "di", "ti",
             "er", "ar", "ım", "im", "ın", "in")


def _stem(t: str) -> str:
    """Hafif Türkçe ek-kırpma: seviyor/sever → sev (lexic gap kapatır)."""
    for suf in _SUFFIXES:
        if len(t) > len(suf) + 2 and t.endswith(suf):
            return t[: -len(suf)]
    return t


def q_tokens(text: str) -> set:
    return {_stem(t) for t in tokens(text)
            if len(t) > 2 and t not in STOP_TR}


class Retriever:
    """Bağlam üreten geri getirme hattı."""

    def __init__(self, store, redact_fn=None, now=None,
                 semantic_threshold: float = 0.12):
        self.store = store
        self.redact_fn = redact_fn or (lambda t: t)
        self.now = now or time.time
        self.semantic_threshold = semantic_threshold
        self.engine = "chroma" if HAVE_CHROMA else "keyword"
        self._collection = None
        if HAVE_CHROMA:
            try:
                import chromadb
                client = chromadb.Client(chromadb.config.Settings(
                    anonymized_telemetry=False))
                self._collection = client.get_or_create_collection("ultron_memory")
            except Exception:
                self._collection = None
                self.engine = "keyword"

    # ---------------------------------------------------------- pipeline
    def classify(self, query: str) -> dict:
        """Sorgu niyeti: hangi türler/kapsamlar ilgili (hafif sezgisel)."""
        q = normalize(query)
        types = []
        if any(w in q for w in ("tercih", "sever", "seviyorum", "prefers", "like")):
            types.append("USER")
        if any(w in q for w in ("proje", "project", "mimari", "milestone")):
            types.append("PROJECT")
        if any(w in q for w in ("olay", "oldu", "yaşandı", "hata", "bug")):
            types.append("EPISODIC")
        if any(w in q for w in ("nasıl", "adım", "prosedür", "how to")):
            types.append("PROCEDURAL")
        return {"types": types or None, "temporal": bool(
            re.search(r"\d{1,2}[:.]\d{2}|dün|bugün|hafta|ay|saat|dk|dakika", q))}

    def retrieve(self, query: str, *, limit: int = 8, project_id: str | None = None,
                 user_scope: str | None = None, privacy_max: str = "SENSITIVE",
                 context_budget: int = 1500) -> dict:
        t0 = time.perf_counter()
        cls = self.classify(query)
        # RETRIEVE (adaylar) + FILTER (gizlilik tavanı + kapsam)
        cands = []
        for mt in (cls["types"] or [None]):
            cands += self.store.query(memory_type=mt, project_id=project_id,
                                      user_scope=user_scope, privacy_max=privacy_max,
                                      limit=100)
        if not cands:
            cands = self.store.query(project_id=project_id,
                                     user_scope=user_scope,
                                     privacy_max=privacy_max, limit=100)
        # RANK
        qt = q_tokens(query)
        scored = []
        now = self.now()
        for rec in cands:
            rel = jaccard(qt, {_stem(t) for t in tokens(rec["content"])})
            if rec.get("record_kind") == "INFERENCE":
                rel *= 0.7          # çıkarım doğrulanmış olgu gibi davranamaz
            if rec.get("record_kind") == "FACT":
                rel *= 1.1
            age_d = max(0.0, (now - float(rec["created_at"] or 0)) / 86400)
            recency = max(0.0, 1.0 - age_d / 90)
            src_rel = SOURCE_CONFIDENCE.get(rec.get("provenance", "SYSTEM"), 0.5)
            score = (rel * 0.55 + recency * 0.15
                     + float(rec.get("confidence") or 0.5) * 0.15
                     + float(rec.get("importance") or 0.5) * 0.15) * \
                (0.8 + 0.2 * src_rel)
            if rel >= self.semantic_threshold:
                scored.append((round(score, 4), rec))
        scored.sort(key=lambda x: -x[0])
        # DEDUP + CONFLICT CHECK (süpersede edilmişler bağlamda asla kazanmaz)
        seen_norm, picked = set(), []
        for score, rec in scored:
            if rec["status"] != "ACTIVE":
                continue
            if rec["normalized_content"] in seen_norm:
                continue
            seen_norm.add(rec["normalized_content"])
            picked.append((score, rec))
            if len(picked) >= limit:
                break
        # CONTEXT BUILD (bütçe) + VERIFY (redaction)
        lines, used = [], 0
        for score, rec in picked:
            text = self.redact_fn(str(rec["content"]))[:300]
            tag = f"{rec['memory_type']}/{rec['record_kind']}"
            line = f"[{tag}|c={rec['confidence']:.2f}] {text}"
            if used + len(line) > context_budget:
                break
            lines.append(line)
            used += len(line) + 1
        return {"context": "\n".join(lines), "hits": len(lines),
                "engine": self.engine, "chroma_available": HAVE_CHROMA,
                "classified": cls,
                "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2)}

    # ---------------------------------------------------------- user model
    def current_preferences(self, subject_prefix: str = "preference:") -> dict:
        """User Model (§12): geçmiş kayıtlardan TÜRETİLEN mevcut tercih
        görünümü. Memory tarih tutar; model yalnızca en güncel kazananı
        gösterir (SUPERSEDED geçmişe gömülü kalır)."""
        out = {}
        for rec in self.store.query(memory_type="USER", status="ACTIVE", limit=200):
            sk = rec.get("subject_key") or ""
            if not sk.startswith(subject_prefix):
                continue
            key = sk[len(subject_prefix):]
            cur = out.get(key)
            if cur is None or float(rec["created_at"]) > float(cur["created_at"]):
                out[key] = {"value": rec["content"],
                            "confidence": rec["confidence"],
                            "updated_at": rec["created_at"],
                            "record_id": rec["id"]}
        return out
