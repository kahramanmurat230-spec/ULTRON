import re
import math
from collections import Counter

class SemanticMemory:
    """Lightweight local semantic retrieval without an external vector DB.
    Uses normalized token overlap, phrase matches and recency. It can later be
    swapped for embeddings without changing the Memory interface.
    """
    STOP = set('ve veya ile için bir bu şu o da de ki ne nasıl nedir olan olanı ben sen bana sana çok daha gibi mi mu mü'.split())

    def __init__(self, memory, max_candidates=5000):
        self.memory = memory
        self.max_candidates = max_candidates

    def _tokens(self, text):
        return [t for t in re.findall(r"[\wçğıöşüÇĞİÖŞÜ]+", text.lower()) if len(t) > 2 and t not in self.STOP]

    def _score(self, query, content):
        q = self._tokens(query); c = self._tokens(content)
        if not q or not c: return 0.0
        qc, cc = Counter(q), Counter(c)
        inter = sum(min(qc[k], cc[k]) for k in qc.keys() & cc.keys())
        union = len(set(q) | set(c)) or 1
        score = inter / union
        if query.lower() in content.lower(): score += 0.45
        return min(score, 1.0)

    def search(self, query, limit=5, kinds=None):
        rows = self.memory.recent(self.max_candidates)
        scored=[]
        for kind, content, created in rows:
            if kinds and kind not in kinds: continue
            s=self._score(query, content)
            if s > 0: scored.append((s, kind, content, created))
        scored.sort(key=lambda x: (x[0], x[3]), reverse=True)
        return scored[:limit]

    def context(self, query, limit=5):
        hits=self.search(query, limit)
        if not hits: return ''
        return '\n'.join(f'[{kind}] {content}' for _,kind,content,_ in hits)
