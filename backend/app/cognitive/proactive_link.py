"""Proactive Intelligence Link — Wave 5 §15.

WORLD EVENT → CONTEXT → GOAL → PREDICTION → PROACTIVE DECISION →
RISK GATE → ACTION / NOTIFY

Mevcut ProactiveMonitor'a DOKUNMAZ — onun üstünde additive bağlantı
katmanı. Spam önleme ZORUNLU ve gerçek:
- dedup: aynı olay imzası pencere içinde tekrar → sessiz
- cooldown: olay tipi başına minimum süre
- importance eşiği: önemsiz olay hiç bildirim üretmez
- user preference: bildirim tarzı (minimal) kapalıysa yalnız kritik
- risk gate: otonom aksiyon AutonomySafety'den geçer
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from pathlib import Path


def _now() -> float:
    return time.time()


_SIG = re.compile(r"[a-zçğıöşü0-9]+")


class ProactiveCortex:
    def __init__(self, db_path: str = "data/cognitive/proactive.db",
                 safety=None, goal_engine=None, predictor=None,
                 user_intel=None, cooldown_s: float = 60.0,
                 importance_threshold: float = 0.5):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(str(self.path), check_same_thread=False)
        self.db.execute("""CREATE TABLE IF NOT EXISTS events(
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL,
            signature TEXT, kind TEXT, payload TEXT,
            importance REAL, action TEXT, suppressed TEXT)""")
        self.db.execute("""CREATE TABLE IF NOT EXISTS cooldowns(
            kind TEXT PRIMARY KEY, last_ts REAL)""")
        self.db.commit()
        self.safety = safety
        self.goal_engine = goal_engine
        self.predictor = predictor
        self.user_intel = user_intel
        self.cooldown_s = float(cooldown_s)
        self.importance_threshold = float(importance_threshold)
        self.actions: list[dict] = []      # test/sunum kanalı

    # ---------------------------------------------------- importance
    def _importance(self, event: dict) -> float:
        base = float(event.get("importance", 0.5))
        kind = str(event.get("kind", "")).lower()
        if kind in ("alert", "deadline", "failure"):
            base = max(base, 0.8)
        # goal hizalanması: aktif goal başlıklarıyla kelime örtüşmesi
        if self.goal_engine is not None:
            text = " ".join(_SIG.findall(
                str(event.get("summary", "")) + " " + kind)).lower()
            for g in self.goal_engine.active_goals():
                gtoks = set(_SIG.findall(str(g.get("title", "")).lower()))
                if gtoks and gtoks & set(text.split()):
                    base = min(1.0, base + 0.25)
                    break
        return min(1.0, base)

    def _signature(self, event: dict) -> str:
        raw = f"{event.get('kind', '')}|{event.get('summary', '')}"
        return " ".join(_SIG.findall(raw.lower()))[:160]

    # ---------------------------------------------------- pipeline
    def ingest(self, event: dict, now: float | None = None) -> dict:
        """Dünya olayını değerlendir → aksiyon/notify/suppress (dürüst)."""
        now = _now() if now is None else now
        with self.lock:
            sig = self._signature(event)
            kind = str(event.get("kind", "unknown"))
            # 1) DEDUP: aynı imza pencerede
            dup = self.db.execute(
                "SELECT MAX(ts) FROM events WHERE signature=?", (sig,)).fetchone()
            if dup and dup[0] is not None and now - dup[0] < self.cooldown_s:
                return self._record(event, sig, now, importance=None,
                                    action="suppressed", suppressed="dedup")
            # 2) COOLDOWN: olay tipi bazlı
            cd = self.db.execute("SELECT last_ts FROM cooldowns WHERE kind=?",
                                 (kind,)).fetchone()
            if cd and cd[0] is not None and now - cd[0] < self.cooldown_s:
                return self._record(event, sig, now, importance=None,
                                    action="suppressed", suppressed="cooldown")
            # 3) IMPORTANCE eşiği
            imp = self._importance(event)
            if imp < self.importance_threshold:
                return self._record(event, sig, now, importance=imp,
                                    action="suppressed", suppressed="importance")
            # 4) USER PREFERENCE: minimal bildirim → yalnız yüksek önem
            minimal = False
            if self.user_intel is not None:
                st = self.user_intel.communication_style()
                minimal = (st.get("style") == "concise")
            if minimal and imp < 0.8:
                return self._record(event, sig, now, importance=imp,
                                    action="suppressed",
                                    suppressed="user-pref-minimal")
            # 5) PREDICTION zenginleştirme (opsiyonel)
            pred_note = None
            if self.predictor is not None and "value" in event:
                a = self.predictor.anomaly_risk(float(event["value"]))
                if a.get("ok") and a.get("anomaly"):
                    pred_note = f"anomaly z={a['robust_z']}"
                    imp = min(1.0, imp + 0.1)
            # 6) PROACTIVE DECISION: bildirim mi otonom aksiyon mu?
            proposed = event.get("proposed_action")
            if proposed:
                # 7) RISK GATE — otonom aksiyon safety'den geçmek ZORUNDA
                if self.safety is None:
                    return self._record(event, sig, now, imp,
                                        action="NOTIFY",
                                        extra="no-safety-engine: otonom "
                                              "aksiyon ASLA (yalnız bildirim)")
                # reversibility belirtilmemişse GÜVENLİ taraf varsayılır
                ev = self.safety.evaluate(
                    str(event.get("summary", "")), str(proposed),
                    reversibility=str(event.get("reversibility",
                                                "partially-reversible")))
                decision = ev["decision"]
                why = ev["reason"]
                if decision == "DENY":
                    return self._record(event, sig, now, imp,
                                        action="suppressed",
                                        suppressed="risk-gate-deny",
                                        extra=why)
                out = self._record(event, sig, now, imp,
                                   action=f"AUTONOMOUS:{decision}",
                                   extra=why)
                out["autonomous"] = decision
                return out
            self._record(event, sig, now, imp, action="NOTIFY")
            self.db.execute(
                """INSERT INTO cooldowns(kind,last_ts) VALUES(?,?)
                   ON CONFLICT(kind) DO UPDATE SET last_ts=excluded.last_ts""",
                (kind, now))
            self.db.commit()
            out = {"action": "NOTIFY", "importance": round(imp, 3),
                   "signature": sig}
            if pred_note:
                out["prediction"] = pred_note
            self.actions.append(out)
            return out

    def _record(self, event, sig, now, importance, action, suppressed=None,
                extra=None):
        self.db.execute(
            "INSERT INTO events(ts,signature,kind,payload,importance,action,"
            "suppressed) VALUES(?,?,?,?,?,?,?)",
            (now, sig, str(event.get("kind", "unknown")),
             json.dumps({"summary": str(event.get("summary", ""))[:200]},
                        ensure_ascii=False),
             importance, action, suppressed))
        self.db.commit()
        out = {"action": action, "signature": sig}
        if suppressed:
            out["suppressed_by"] = suppressed
        if importance is not None:
            out["importance"] = round(importance, 3)
        if extra:
            out["detail"] = str(extra)[:200]
        if action == "NOTIFY":
            self.actions.append(out)
        return out

    def stats(self) -> dict:
        with self.lock:
            total = self.db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            supp = self.db.execute(
                "SELECT suppressed, COUNT(*) FROM events"
                " WHERE suppressed IS NOT NULL GROUP BY suppressed").fetchall()
            notif = self.db.execute(
                "SELECT COUNT(*) FROM events WHERE action='NOTIFY'").fetchone()[0]
        return {"events": total, "notified": notif,
                "suppressed_by": dict(supp)}
