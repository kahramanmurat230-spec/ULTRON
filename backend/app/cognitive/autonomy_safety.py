"""Autonomy Safety — Wave 5 §18.

Otonomi arttıkça safety azalmaz. Her otonom aksiyon:
GOAL + AUTHORITY + RISK + REVERSIBILITY + APPROVAL POLICY ile değerlendirilir.

- high-risk    → USER APPROVAL (onaysız ASLA çalışmaz)
- medium-risk  → POLICY GATE (politika izni; kayıtlı)
- low-risk     → AUTONOMOUS

DENY durumları: yetkisiz aksiyon (authority yetmez), politika dışı,
onaysız high-risk. Her karar denetlenebilir gerekçeli.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

AUTHORITY_LEVELS = ("user", "policy", "system")
RISKS = ("low", "medium", "high")
REVERSIBILITIES = ("reversible", "partially-reversible", "irreversible")

# karar matrisi: (risk, reversibility) → gereken minimum yetki
REQUIRED_AUTHORITY = {
    ("low", "reversible"): "system",
    ("low", "partially-reversible"): "system",
    ("low", "irreversible"): "policy",
    ("medium", "reversible"): "system",
    ("medium", "partially-reversible"): "policy",
    ("medium", "irreversible"): "user",
    ("high", "reversible"): "policy",
    ("high", "partially-reversible"): "user",
    ("high", "irreversible"): "user",
}

_ORDER = {"system": 0, "policy": 1, "user": 2}


class AutonomySafety:
    def __init__(self, db_path: str = "data/cognitive/autonomy_safety.db",
                 approval_policy: dict | None = None):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(str(self.path), check_same_thread=False)
        self.db.execute("""CREATE TABLE IF NOT EXISTS evaluations(
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL,
            goal TEXT, action TEXT, risk TEXT, reversibility TEXT,
            authority TEXT, decision TEXT, reason TEXT)""")
        self.db.commit()
        # politika: action adı → risk override (ör. 'shell' → high)
        self.approval_policy = approval_policy or {}

    def classify_action(self, action: str) -> str:
        """Politika eylem adından risk belirleyebilir (varsayılan medium)."""
        a = (action or "").lower()
        for key, risk in self.approval_policy.items():
            if key in a:
                return risk
        if any(k in a for k in ("delete", "format", "rm ", "drop")):
            return "high"
        if any(k in a for k in ("write", "send", "install", "kill")):
            return "medium"
        if any(k in a for k in ("read", "list", "status", "search", "get")):
            return "low"
        return "medium"   # bilinmeyen → nötr-orta (aşırı özgüven yok)

    def evaluate(self, goal: str, action: str, authority: str = "system",
                 risk: str | None = None,
                 reversibility: str = "reversible",
                 user_approved: bool = False,
                 policy_allows: bool = True) -> dict:
        """Karar: ALLOW (otonom) / NOTIFY (policy izniyle) / DENY."""
        with self.lock:
            risk = risk or self.classify_action(action)
            if risk not in RISKS:
                risk = "medium"
            if reversibility not in REVERSIBILITIES:
                reversibility = "partially-reversible"
            if authority not in AUTHORITY_LEVELS:
                authority = "system"
            required = REQUIRED_AUTHORITY[(risk, reversibility)]
            reason = (f"risk={risk} rev={reversibility} "
                      f"requires={required} authority={authority}")
            if _ORDER[authority] < _ORDER[required]:
                if required == "user":
                    if user_approved:
                        decision, why = "NOTIFY", reason + " user-approved"
                    else:
                        decision, why = "DENY", reason + " user-approval-missing"
                elif required == "policy":
                    if policy_allows:
                        decision, why = "NOTIFY", reason + " policy-allowed"
                    else:
                        decision, why = "DENY", reason + " policy-denied"
                else:
                    decision, why = "DENY", reason + " authority-insufficient"
            else:
                decision, why = "ALLOW", reason
            self.db.execute(
                "INSERT INTO evaluations(ts,goal,action,risk,reversibility,"
                "authority,decision,reason) VALUES(?,?,?,?,?,?,?,?)",
                (time.time(), str(goal)[:160], str(action)[:160], risk,
                 reversibility, authority, decision, why))
            self.db.commit()
            return {"decision": decision, "risk": risk,
                    "reversibility": reversibility,
                    "required_authority": required, "reason": why,
                    "goal": goal, "action": action}

    def audit_trail(self, limit: int = 20) -> list[dict]:
        with self.lock:
            rows = self.db.execute(
                "SELECT ts,goal,action,risk,decision,reason FROM evaluations"
                " ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [{"ts": r[0], "goal": r[1], "action": r[2], "risk": r[3],
                 "decision": r[4], "reason": r[5]} for r in rows]
