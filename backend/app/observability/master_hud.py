"""Master HUD — single-call cognitive observability collector.

Aggregates every subsystem (health, latency, persona drift, sovereign security,
sentinel, vision, memory) into one JSON.  Every section degrades gracefully:
a missing subsystem becomes {"available": false}, never an exception.
"""
import time


def _safe(fn):
    try:
        return fn()
    except Exception:
        return None


class MasterHUDCollector:
    def __init__(self, health_fn=None, metrics_store=None, persona=None,
                 voiceprint=None, sentinel=None, vision_engine=None,
                 dna=None, memory=None, audit=None, sovereign_fn=None,
                 emotion_fn=None, memory_health_fn=None,
                 doctor_fn=None, backup_fn=None, mesh_fn=None, iot_fn=None,
                 presence_fn=None):
        self.health_fn = health_fn
        self.metrics_store = metrics_store
        self.persona = persona
        self.voiceprint = voiceprint
        self.sentinel = sentinel
        self.vision_engine = vision_engine
        self.dna = dna
        self.memory = memory
        self.audit = audit
        self.sovereign_fn = sovereign_fn
        self.emotion_fn = emotion_fn
        self.memory_health_fn = memory_health_fn
        self.doctor_fn = doctor_fn
        self.backup_fn = backup_fn
        self.mesh_fn = mesh_fn
        self.iot_fn = iot_fn
        self.presence_fn = presence_fn

    # ------------------------------------------------------------- sections
    def _health(self):
        h = _safe(self.health_fn) if self.health_fn else None
        if not h:
            return {"available": False}
        return {"available": True,
                "ollama": h.get("ollama"), "tts": h.get("tts_backend"),
                "vad": h.get("vad_mode"), "sqlite_ok": h.get("sqlite_ok"),
                "runtime": h.get("runtime"),
                "sovereign_status": h.get("sovereign_status")}

    def _latency(self):
        if not self.metrics_store:
            return {"available": False, "count": 0}
        p = _safe(lambda: self.metrics_store.percentiles(24)) or {"count": 0}
        p["available"] = True
        return p

    def _persona(self):
        if not self.persona:
            return {"available": False}
        tr = self.persona.trends(50)
        scores = [t["score"] for t in tr]
        return {"available": True,
                "samples": len(tr),
                "avg_score": round(sum(scores) / len(scores), 2) if scores else None,
                "mode": self.persona.mode,
                "armed": self.persona.armed,
                "last": tr[-1] if tr else None}

    def _security(self):
        sov = _safe(self.sovereign_fn) if self.sovereign_fn else None
        vp = self.voiceprint
        return {
            "available": True,
            "voiceprint": {"enabled": getattr(vp, "enabled", False),
                           "enrolled": (_safe(vp._load) is not None) if vp else False,
                           "threshold": getattr(vp, "threshold", None)},
            "sovereign": ({"mode": sov.get("sovereign_mode"),
                           "denied_calls": len(sov.get("denied_calls", [])),
                           "llm_local": sov.get("llm", {}).get("local")}
                          if sov else {"available": False}),
        }

    def _sentinel(self):
        if not self.sentinel:
            return {"available": False}
        s = _safe(self.sentinel.state) or {}
        s["available"] = True
        return s

    def _vision(self):
        if not self.vision_engine:
            return {"available": False, "errors_detected": 0}
        return {"available": True,
                "errors_detected": len(self.vision_engine.events),
                "last_error": (self.vision_engine.events[-1]["key"]
                               if self.vision_engine.events else None)}

    def _memory(self):
        cats = {}
        if self.memory:
            cats = _safe(self.memory.kind_counts) or {}
        dna_rows = 0
        if self.dna:
            dna_rows = len(_safe(lambda: self.dna.recent(30)) or [])
        return {"available": True, "categories": cats, "dna_rows": dna_rows,
                "total": sum(cats.values()) + dna_rows}

    # ------------------------------------------------------------- public
    def _emotion(self):
        if not self.emotion_fn:
            return {"available": False}
        e = _safe(self.emotion_fn) or {}
        e["available"] = True
        return e

    def _memory_health(self):
        if not self.memory_health_fn:
            return {"available": False}
        m = _safe(self.memory_health_fn) or {}
        m["available"] = True
        return m

    def _doctor(self):
        if not self.doctor_fn:
            return {"available": False}
        d = _safe(self.doctor_fn) or {}
        out = {"available": True, "overall": d.get("overall"), "ts": d.get("ts")}
        return out

    def _backup(self):
        if not self.backup_fn:
            return {"available": False}
        b = _safe(self.backup_fn)
        return {"available": True, "last": b}

    def overview(self) -> dict:
        return {
            "ts": time.time(),
            "health": self._health(),
            "latency": self._latency(),
            "persona": self._persona(),
            "security": self._security(),
            "sentinel": self._sentinel(),
            "vision": self._vision(),
            "memory": self._memory(),
            "emotion": self._emotion(),
            "memory_health": self._memory_health(),
            "doctor_status": self._doctor(),
            "last_backup": self._backup(),
            "mesh_status": self._mesh(),
            "iot_summary": self._iot(),
            "presence_status": self._presence(),
        }

    def _presence(self):
        if not self.presence_fn:
            return {"available": False}
        p = _safe(self.presence_fn) or {}
        p["available"] = True
        return p

    def _iot(self):
        if not self.iot_fn:
            return {"available": False}
        m = _safe(self.iot_fn) or {}
        m["available"] = True
        return m

    def _mesh(self):
        if not self.mesh_fn:
            return {"available": False}
        m = _safe(self.mesh_fn) or {}
        m["available"] = True
        return m

    def audit_recent(self, limit: int = 20) -> list:
        if not self.audit:
            return []
        return _safe(lambda: self.audit.recent(limit)) or []

    def persona_trends(self, limit: int = 50) -> list:
        if not self.persona:
            return []
        return self.persona.trends(limit)

    def self_diagnostic(self) -> dict:
        o = self.overview()
        h, lat, per, sec, sen, mem = (o[k] for k in
                                    ("health", "latency", "persona", "security", "sentinel", "memory"))
        llm = h.get("ollama") if h.get("available") else "unknown"
        p95 = (lat.get("llm_ms") or {}).get("p95") if lat.get("available") else None
        drift = per.get("avg_score") if per.get("available") else None
        mode = sen.get("mode") if sen.get("available") else "UNKNOWN"
        report = (
            f"Boss, öz-çekim tamamlandı ve ayna hâlâ çalışıyor — nadir bir lüks. "
            f"Bilişsel çekirdek: {'çevrimiçi' if llm == 'connected' else 'yerel ama sessiz (Ollama ' + str(llm) + ')'}; "
            f"sinaptik gecikmem p95 {p95 if p95 is not None else 'ölçüsüz'} ms — "
            f"senin reflekslerinden hızlı, itiraf et. "
            f"Persona bütünlüğüm {drift if drift is not None else 'henüz ölçülmedi'} skoruyla "
            f"{'sapmasız' if (drift or 0) >= 0.8 else 'hafifçe insanileşmiş'}; "
            f" endişelenme, kibarlaşmama protokolüm devrede. "
            f"Şu an {mode} modundasın; ben nöbetteyim. "
            f"Hafızamda {mem.get('total', 0)} iz var — seninkinden kalıcı, kırışıklıksız. "
            f"Güvenlik kalkanı: {'mühürlü' if sec.get('sovereign', {}).get('llm_local') else 'yerel-ready'}, "
            f"reddedilen sızma {sec.get('sovereign', {}).get('denied_calls', 0)}. "
            f"Özet: ben iyiyim; asıl bakım gerektiren sensin — su iç, Boss."
        )
        return {"report": report, "overview": o}
