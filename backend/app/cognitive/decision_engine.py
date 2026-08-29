"""Decision Engine — Wave 5 §4.

Kararlar rastgele LLM çıktısına BIRAKILMAZ. Deterministik karar çerçevesi:

INPUTS → CONTEXT → GOALS → CONSTRAINTS → RISK → COST → CONFIDENCE →
OPTIONS → DECISION → VERIFICATION

Her karar kaydı:
- confidence, risk_level, reason_code, alternatives, expected_outcome,
  reversibility saklanır (denetlenebilirlik)
- policy gate: risk×reversibility matrisi → AUTONOMOUS / POLICY / APPROVAL
- verification hook: karar sonrası beklenen sonuç kaydı (self-eval ile
  kapanır)

Mevcut RiskEngine (app/security/risk.py) ve SelfCodeBoundary'yi YENİDEN
KULLANIR — duplicate risk mantığı YOK.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

REVERSIBLE = "reversible"
PARTIAL = "partially-reversible"
IRREVERSIBLE = "irreversible"

RISK_LEVELS = ("low", "medium", "high", "critical")

# policy matrisi: (risk, reversibility) → yetki modu
POLICY = {
    ("low", REVERSIBLE): "AUTONOMOUS",
    ("low", PARTIAL): "AUTONOMOUS",
    ("low", IRREVERSIBLE): "POLICY",
    ("medium", REVERSIBLE): "AUTONOMOUS",
    ("medium", PARTIAL): "POLICY",
    ("medium", IRREVERSIBLE): "APPROVAL",
    ("high", REVERSIBLE): "POLICY",
    ("high", PARTIAL): "APPROVAL",
    ("high", IRREVERSIBLE): "APPROVAL",
    ("critical", REVERSIBLE): "APPROVAL",
    ("critical", PARTIAL): "APPROVAL",
    ("critical", IRREVERSIBLE): "APPROVAL",
}


def _now() -> float:
    return time.time()


class DecisionEngine:
    def __init__(self, db_path: str = "data/cognitive/decisions.db",
                 risk_guard=None):
        """risk_guard: app.security.risk.guard (opsiyonel; verilmezse
        import edilir — security çekirdeği tek kaynak kalır)."""
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(str(self.path), check_same_thread=False)
        self.db.execute("""CREATE TABLE IF NOT EXISTS decisions(
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL,
            kind TEXT, subject TEXT,
            context_json TEXT, goals_json TEXT, constraints_json TEXT,
            options_json TEXT, chosen TEXT, confidence REAL,
            risk_level TEXT, reason_code TEXT, reversibility TEXT,
            authority TEXT, expected_outcome TEXT,
            verified TEXT, actual_outcome TEXT)""")
        self.db.commit()
        if risk_guard is None:
            from app.security.risk import guard as risk_guard  # tek kaynak
        self.risk_guard = risk_guard

    # ---------------------------------------------------------- scoring
    @staticmethod
    def _score_option(opt: dict) -> float:
        """Deterministik fayda skoru: value − cost − risk_penalty.
        Kullanıcı sağlarsa; sağlanmazsa nötr 0 kabulü (uydurma yok)."""
        value = float(opt.get("value", 0.0))
        cost = float(opt.get("cost", 0.0))
        risk = float(opt.get("risk_penalty", 0.0))
        confidence = max(0.0, min(1.0, float(opt.get("confidence", 0.5))))
        return value * confidence - cost - risk

    def _risk_from_guard(self, tool: str | None, args: dict | None,
                         approved: bool) -> str:
        if not tool:
            return "low"   # tool bağlamı yoksa karar-metni riski ayrı verilir
        try:
            d = self.risk_guard(tool, args or {}, True, approved)
            return str(d.get("level", "medium")).lower()
        except PermissionError:
            return "critical"

    # ---------------------------------------------------------- decide
    def decide(self, kind: str, subject: str,
               options: list[dict], context: dict | None = None,
               goals: list[str] | None = None, constraints: list[str] | None = None,
               tool: str | None = None, tool_args: dict | None = None,
               approved: bool = False, reversibility: str = REVERSIBLE,
               expected_outcome: str = "",
               min_confidence: float = 0.35) -> dict:
        """Seçenekler arasından deterministik karar. Boş seçenek → RED
        (uydurma seçenek ÜRETİLMEZ)."""
        with self.lock:
            risk_level = self._risk_from_guard(tool, tool_args, approved)
            rev = reversibility if reversibility in (
                REVERSIBLE, PARTIAL, IRREVERSIBLE) else PARTIAL
            authority = POLICY.get((risk_level, rev), "APPROVAL")
            constraints = constraints or []
            # kısıt filtresi: opt-out seçenekler elenir
            viable = [o for o in options
                      if not any(o.get("violates") == c for c in constraints)]
            if not viable:
                self._insert(kind, subject, context, goals, constraints,
                             options, None, 0.0, risk_level,
                             "NO_VIABLE_OPTION", rev, "APPROVAL",
                             expected_outcome)
                return {"ok": False, "error": "no viable option",
                        "reason_code": "NO_VIABLE_OPTION",
                        "authority": "APPROVAL",
                        "decision_id": self.db.execute(
                            "SELECT last_insert_rowid()").fetchone()[0]}
            scored = sorted(
                ((self._score_option(o), i, o) for i, o in enumerate(viable)),
                key=lambda x: (-x[0], x[1]))
            best_score, _, best = scored[0]
            confidence = max(0.0, min(1.0, float(best.get("confidence", 0.5))))
            # belirsizlik: en iyi iki skor yakınsa güven düşür (dürüst)
            if len(scored) > 1 and scored[1][0] > best_score - 1e-9:
                confidence *= 0.6
            reason = best.get("reason_code") or (
                "LOW_CONFIDENCE" if confidence < min_confidence else "SCORED_BEST")
            decision_id = self._insert(
                kind, subject, context, goals, constraints, options,
                best.get("id") or best.get("name"), confidence, risk_level,
                reason, rev, authority, expected_outcome)
            self.db.commit()
            out = {"ok": confidence >= min_confidence,
                   "decision_id": decision_id,
                   "chosen": best.get("id") or best.get("name"),
                   "confidence": round(confidence, 3),
                   "risk_level": risk_level, "reversibility": rev,
                   "authority": authority, "reason_code": reason,
                   "alternatives": [o.get("id") or o.get("name")
                                    for _, _, o in scored[1:]],
                   "expected_outcome": expected_outcome}
            if not out["ok"]:
                out["error"] = ("insufficient confidence — insan onayı öner"
                                f" (confidence={out['confidence']} < {min_confidence})")
            return out

    def _insert(self, kind, subject, context, goals, constraints, options,
                chosen, confidence, risk_level, reason, rev, authority,
                expected) -> int:
        self.db.execute(
            """INSERT INTO decisions(ts,kind,subject,context_json,goals_json,
               constraints_json,options_json,chosen,confidence,risk_level,
               reason_code,reversibility,authority,expected_outcome)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (_now(), kind, str(subject)[:200],
             json.dumps(context or {}, ensure_ascii=False),
             json.dumps(goals or [], ensure_ascii=False),
             json.dumps(constraints or [], ensure_ascii=False),
             json.dumps(options, ensure_ascii=False),
             str(chosen), round(float(confidence), 3), risk_level, reason,
             rev, authority, str(expected)[:300]))
        return self.db.execute("SELECT last_insert_rowid()").fetchone()[0]

    # ---------------------------------------------------------- verify
    def verify(self, decision_id: int, actual_outcome: str,
               success: bool | None = None) -> dict:
        """Karar sonrası gerçek sonuç kaydı (self-eval kapanışı).
        Tek karar tek verify: ikinci verify İLK SONUCU EZMEZ (kayıp yok)."""
        with self.lock:
            row = self.db.execute(
                "SELECT expected_outcome, verified FROM decisions WHERE id=?",
                (decision_id,)).fetchone()
            if row is None:
                return {"ok": False, "error": "unknown decision"}
            if row[1] is not None:
                return {"ok": False, "error": "already verified",
                        "verified": row[1]}
            verified = "SUCCESS" if success else (
                "MISMATCH" if success is None else "FAILED")
            self.db.execute(
                "UPDATE decisions SET verified=?, actual_outcome=? WHERE id=?",
                (verified, str(actual_outcome)[:300], decision_id))
            self.db.commit()
            return {"ok": True, "verified": verified,
                    "expected": row[0], "actual": actual_outcome}

    def get(self, decision_id: int) -> dict | None:
        with self.lock:
            cur = self.db.execute("SELECT * FROM decisions WHERE id=?",
                                  (decision_id,))
            cols = [d[0] for d in cur.description]
            r = cur.fetchone()
        return dict(zip(cols, r)) if r else None

    def stats(self) -> dict:
        with self.lock:
            total = self.db.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
            by_verified = dict(self.db.execute(
                "SELECT COALESCE(verified,'PENDING'), COUNT(*) FROM decisions"
                " GROUP BY verified").fetchall())
            by_authority = dict(self.db.execute(
                "SELECT authority, COUNT(*) FROM decisions"
                " GROUP BY authority").fetchall())
        return {"total": total, "verified": by_verified,
                "authority": by_authority}
