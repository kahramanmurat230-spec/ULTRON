"""Capability Discovery + Meta-Reasoning — Wave 5 §11+§14.

§11: 'Bu görev için hangi capability gerekli?' — TASK → REQUIREMENTS →
AVAILABLE TOOLS → MISSING → SAFE ALTERNATIVE / USER REQUEST.
Eksik capability VARMIŞ GİBİ davranılmaz: missing dürüstçe listelenir.

§14: karar kalitesinin öz-değerlendirmesi — bilmediğini bilme:
- uncertainty detection
- insufficient information detection
- contradiction detection
- hallucination-risk detection
- tool-result validation (şema)
- reasoning confidence (bileşik)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# görev metni → gereken capability ailesi (anahtar sinyaller; dürüst eşleşme)
_REQUIREMENT_SIGNALS = {
    "vision": ("ekran", "görüntü", "screenshot", "resim", "ocr", "görsel"),
    "voice": ("ses", "konuş", "söyle", "mikrofon", "dinle", "wake"),
    "browser": ("web", "site", "sayfa", "tarayıcı", "url", "browser"),
    "shell": ("terminal", "komut", "çalıştır", "bash", "cmd", "shell"),
    "files": ("dosya", "oku", "yaz", "klasör", "dizin"),
    "code": ("kod", "fonksiyon", "refactor", "test yaz", "bug"),
    "memory": ("hatırla", "önceki", "geçmiş", "unut"),
    "iot": ("lamba", "klima", "priz", "cihaz", "sensör"),
    "email": ("e-posta", "mail", "posta"),
    "llm": ("özetle", "analiz et", "çevir", "yaz", "planla"),
}

# capability → örneklenmemiş karşılıklar (mevcut katmanlar; DÜRÜST envanter)
_KNOWN_CAPABILITY_TOOLS = {
    "vision": ("app.vision.analyze", "app.multimodal.vision_runtime"),
    "voice": ("app.multimodal.voice_runtime", "app.voice.voice_stack_v2"),
    "browser": ("app.multimodal.browser_runtime", "app.browser.agent"),
    "shell": ("agent.terminal", ),
    "files": ("app.security.sandbox.sandboxed_read_text",
              "app.security.sandbox.sandboxed_write_text"),
    "code": ("codegen.CodeGen", "app.code_agent.code_agent"),
    "memory": ("app.memory.sqlite_memory.Memory",
               "app.cognitive.context_engine"),
    "iot": ("app.iot.iot_nexus.IoTNexus", ),
    "email": ("app.connectors.email", ),
    "llm": ("app.core.brain.Brain", ),
}


@dataclass
class Requirement:
    capability: str
    matched_signals: list[str] = field(default_factory=list)


class CapabilityDiscovery:
    def __init__(self, available_fn=None):
        """available_fn: capability → bool (gerçek ortam sorgusu; verilmezse
        import yoklamasıyla GERÇEK kontrol yapılır)."""
        self.available_fn = available_fn or self._importable

    @staticmethod
    def _importable(capability: str) -> bool:
        import importlib
        tools = _KNOWN_CAPABILITY_TOOLS.get(capability, ())
        for t in tools:
            mod = t.rsplit(".", 1)[0]
            try:
                importlib.import_module(mod)
                return True     # modül gerçekten import edilebiliyor
            except Exception:
                continue
        return False            # yoksa yok — ASLA 'var' denmez

    def requirements(self, task: str) -> list[Requirement]:
        t = (task or "").lower()
        out = []
        for cap, signals in _REQUIREMENT_SIGNALS.items():
            hits = [s for s in signals if s in t]
            if hits:
                out.append(Requirement(capability=cap, matched_signals=hits))
        return out              # eşleşme yoksa gereksinim de YOK (dürüst)

    def discover(self, task: str) -> dict:
        reqs = self.requirements(task)
        available, missing = [], []
        for r in reqs:
            if self.available_fn(r.capability):
                available.append(r.capability)
            else:
                missing.append(r.capability)
        # güvenli alternatif önerisi (yalnız gerçekten bilinen eşlemeler)
        safe_alternatives = {}
        for cap in missing:
            if cap == "browser":
                safe_alternatives[cap] = ("http fetch (sayfa içeriği "
                                          "okunabilir, tıklama YOK)")
            elif cap == "vision":
                safe_alternatives[cap] = "metin tabanlı açıklama iste (kullanıcı)"
            elif cap == "voice":
                safe_alternatives[cap] = "metin girişi ile devam"
            else:
                safe_alternatives[cap] = "kullanıcıdan istek (dürüst eksik)"
        out = {
            "task": task, "required": [r.capability for r in reqs],
            "requirements_detail":
                [{"capability": r.capability, "signals": r.matched_signals}
                 for r in reqs],
            "available": available, "missing": missing,
            "safe_alternatives": safe_alternatives,
            "decision": ("EXECUTABLE" if not missing else
                         "NEEDS_USER_INPUT"),
        }
        return out


# ---------------------------------------------------------------- §14
class MetaReasoning:
    """Kendi muhakeme kalitesinin öz-değerlendirmesi (deterministik)."""

    # ---- belirsizlik
    @staticmethod
    def uncertainty(evidences: list[dict]) -> dict:
        """Kanıt listesinden belirsizlik: az kanıt / düşük güven dağılımı."""
        if not evidences:
            return {"level": "high", "reason": "no-evidence",
                    "confidence": 0.0}
        confs = [max(0.0, min(1.0, float(e.get("confidence", 0.5))))
                 for e in evidences]
        spread = max(confs) - min(confs)
        mean = sum(confs) / len(confs)
        if len(evidences) < 2:
            level, reason = "high", "single-evidence"
        elif mean < 0.4:
            level, reason = "high", "low-mean-confidence"
        elif spread > 0.5:
            level, reason = "medium", "conflicting-confidence"
        else:
            level, reason = "low", "consistent"
        return {"level": level, "reason": reason,
                "confidence": round(mean, 3), "spread": round(spread, 3),
                "evidence_count": len(evidences)}

    # ---- eksik bilgi
    @staticmethod
    def missing_information(payload: dict, required_fields: list[str]) -> dict:
        absent = [f for f in required_fields
                  if payload.get(f) in (None, "", [], {})]
        return {"sufficient": not absent, "missing_fields": absent}

    # ---- çelişki
    @staticmethod
    def contradictions(claims: list[dict]) -> list[dict]:
        """claims: [{'key':..,'value':..,'source':..}] — aynı anahtar çok
        farklı değer → çelişki listesi."""
        by_key: dict[str, list] = {}
        for c in claims:
            by_key.setdefault(str(c.get("key")), []).append(c)
        out = []
        for k, lst in by_key.items():
            vals = {str(c.get("value")) for c in lst}
            if len(vals) > 1:
                out.append({"key": k,
                            "values": sorted(vals),
                            "sources": [c.get("source", "?") for c in lst]})
        return out

    # ---- halüsinasyon riski
    @staticmethod
    def hallucination_risk(answer: str, citations: list[str],
                           evidences: list[dict] | None = None) -> dict:
        risks = []
        if not str(answer).strip():
            risks.append("empty-answer")
        if not citations:
            risks.append("no-citations")
        evs = evidences or []
        if evs and not any(float(e.get("confidence", 0)) > 0 for e in evs):
            risks.append("zero-confidence-evidence")
        # cevaptaki rakamlar kanıtlardaki rakamlarla örtüşmuyor mu?
        ans_nums = set(re.findall(r"\d+(?:\.\d+)?", str(answer)))
        ev_nums = set()
        for e in evs:
            ev_nums |= set(re.findall(r"\d+(?:\.\d+)?",
                                      str(e.get("text", ""))))
        if ans_nums and ev_nums and not (ans_nums & ev_nums):
            risks.append("unsupported-numbers")
        score = min(1.0, 0.25 * len(risks))
        return {"risk": round(score, 3), "signals": risks,
                "recommendation": "yanıtı kanıtsız sunma" if risks else "aç"}

    # ---- araç sonucu doğrulama
    @staticmethod
    def validate_tool_result(result, expected_fields: list[str],
                             expect_ok: bool | None = None) -> dict:
        problems = []
        if not isinstance(result, dict):
            return {"valid": False, "problems": ["result-not-dict"]}
        if expect_ok is not None and result.get("ok") is not expect_ok:
            problems.append("ok-mismatch")
        for f in expected_fields:
            if f not in result:
                problems.append(f"missing-field:{f}")
        return {"valid": not problems, "problems": problems}

    # ---- bileşik muhakeme güveni
    def reasoning_confidence(self, *, evidences: list[dict],
                             claims: list[dict] | None = None,
                             citations: list[str] | None = None,
                             answer: str = "") -> dict:
        unc = self.uncertainty(evidences)
        cons = self.contradictions(claims or [])
        hal = self.hallucination_risk(answer, citations or [], evidences)
        score = unc["confidence"]
        if cons:
            score *= 0.6
        score *= (1.0 - 0.5 * hal["risk"])
        level = ("insufficient" if unc["level"] == "high"
                 else "contradicted" if cons
                 else "risky" if hal["risk"] >= 0.5
                 else "adequate")
        return {"confidence": round(max(0.0, score), 3), "level": level,
                "uncertainty": unc, "contradictions": cons,
                "hallucination": hal,
                "knows_unknown": True}   # 'bilmediğini bilir'
