"""Simulation / What-If — Wave 5 §13.

Gerçek çalıştırmadan ÖNCE: SIMULATE → PREDICT → RISK → EXPECTED RESULT →
APPROVAL → EXECUTE.
- dry-run: aksiyon listesi etki önizlemesi (hiçbir şey çalıştırmaz)
- what-if senaryoları
- impact preview: dosya değişiklik / kaynak etkisi özetlenir
- file change preview: hedef dosya + bayt farkı tahmini (gerçek okuma ile)
- action preview: her aksiyon için risk sınıfı + beklenen etki
- rollback planı: her yazma aksiyonu için restore talimatı

Dürüstlük: simülasyon GERÇEK ÇALIŞTIRMA YAPMAZ; tahminler etiketlenir
(simulated), gerçek sonuç DEĞİL.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path


def _now() -> float:
    return time.time()


class Simulator:
    def __init__(self, db_path: str = "data/cognitive/simulations.db",
                 safety=None, root: str = "."):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(str(self.path), check_same_thread=False)
        self.db.execute("""CREATE TABLE IF NOT EXISTS simulations(
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL,
            scenario TEXT, actions_json TEXT, result_json TEXT,
            executed INTEGER DEFAULT 0)""")
        self.db.commit()
        self.safety = safety
        self.root = Path(root).resolve()

    # ---------------------------------------------------- dry-run
    def dry_run(self, scenario: str, actions: list[dict]) -> dict:
        """actions: [{'name':.., 'type':'write|shell|read|api',
        'path'?, 'command'?, 'resource'?}] — ÇALIŞTIRMADAN önizleme."""
        previews = []
        for a in actions:
            kind = a.get("type", "unknown")
            entry = {"action": a.get("name", kind), "type": kind,
                     "simulated": True}          # etiket: SAĞLAM
            if kind == "write":
                p = (self.root / str(a.get("path", ""))).resolve()
                inside = str(p).startswith(str(self.root))
                entry["file_change_preview"] = {
                    "path": str(a.get("path", "")),
                    "exists": inside and p.exists(),
                    "current_bytes": p.stat().st_size
                    if inside and p.exists() else None,
                    "new_bytes": len(str(a.get("content", "")).encode(
                        "utf-8", errors="replace")),
                    "inside_workspace": inside,
                }
                entry["rollback_plan"] = (
                    f"restore {a.get('path')} from backup/checkpoint "
                    f"(mevcut içerik yedeklenecek)")
            elif kind == "shell":
                entry["command_preview"] = str(a.get("command", ""))[:200]
                entry["risk_hint"] = ("destructive-kelime içeriyor"
                                      if any(w in str(a.get("command", "")).lower()
                                             for w in ("rm ", "del ", "format",
                                                       "drop", ">")) else
                                      "görünürde yıkıcı değil")
            elif kind == "api":
                entry["resource_impact"] = {
                    "resource": a.get("resource", "?"),
                    "method": a.get("method", "GET").upper(),
                    "side_effect": a.get("method", "GET").upper()
                    not in ("GET", "HEAD"),
                }
            elif kind == "read":
                entry["read_only"] = True
            else:
                entry["note"] = "bilinmeyen aksiyon tipi — etki tahmin edilemez"
            if self.safety is not None:
                ev = self.safety.evaluate(scenario, a.get("name", kind))
                entry["safety"] = {"decision": ev["decision"],
                                   "risk": ev["risk"],
                                   "reason": ev["reason"]}
            previews.append(entry)
        risky = [p["action"] for p in previews
                 if p.get("safety", {}).get("decision") == "DENY"]
        needs_approval = [p["action"] for p in previews
                          if p.get("safety", {}).get("decision") == "NOTIFY"]
        result = {
            "scenario": scenario, "dry_run": True,
            "action_previews": previews,
            "predicted_outcome": (f"{len(previews)} aksiyon simüle edildi; "
                                  f"{len(risky)} DENY, "
                                  f"{len(needs_approval)} onay gerektirir"),
            "risk_summary": {"deny": risky, "needs_approval": needs_approval},
            "approval_required": bool(needs_approval or risky),
            "executed": False,                    # GERÇEK ÇALIŞTIRMA YOK
        }
        with self.lock:
            self.db.execute(
                "INSERT INTO simulations(ts,scenario,actions_json,result_json)"
                " VALUES(?,?,?,?)",
                (_now(), scenario, json.dumps(actions, ensure_ascii=False)[:4000],
                 json.dumps(result, ensure_ascii=False)[:4000]))
            self.db.commit()
        return result

    # ---------------------------------------------------- what-if
    def what_if(self, scenario: str, variants: dict[str, list[dict]]) -> dict:
        """Çoklu senaryo karşılaştırması (her varyant dry-run)."""
        out = {"scenario": scenario, "variants": {}}
        for name, actions in variants.items():
            r = self.dry_run(f"{scenario}::{name}", actions)
            out["variants"][name] = {
                "approval_required": r["approval_required"],
                "actions": len(actions),
                "deny": r["risk_summary"]["deny"]}
        return out

    def history(self, executed_only: bool = False,
                limit: int = 10) -> list[dict]:
        q = ("SELECT ts,scenario,result_json,executed FROM simulations"
             " ORDER BY id DESC LIMIT ?")
        with self.lock:
            rows = self.db.execute(q, (limit,)).fetchall()
        out = []
        for ts, sc, rj, ex in rows:
            if executed_only and not ex:
                continue
            out.append({"ts": ts, "scenario": sc,
                        "result": json.loads(rj), "executed": bool(ex)})
        return out

    def mark_executed(self, scenario_like: str) -> int:
        """Onay sonrası gerçek çalıştırma işareti (dürüst kayıt)."""
        with self.lock:
            cur = self.db.execute(
                "UPDATE simulations SET executed=1 WHERE scenario LIKE ?",
                (f"%{scenario_like}%",))
            self.db.commit()
            return cur.rowcount
