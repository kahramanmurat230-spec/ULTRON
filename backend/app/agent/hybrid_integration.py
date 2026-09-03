"""Connect the bounded hybrid planner/executor to the live V16 Agent path.

The integration keeps deterministic multi-step parsing first. Only requests that
look like actionable multi-step work and are not understood by the deterministic
planner are sent to the local LLM planner. Tool execution remains behind the
validated HybridPlanExecutor safety boundary.
"""
import json
import re

from app.agent.hybrid_executor import HybridPlanExecutor


_INSTALLED = False


def _action_score(text):
    t = (text or "").lower()
    verbs = (
        "kontrol et", "kontrol", "bul", "listele", "say", "aç", "ac ",
        "kapat", "oku", "yaz", "oluştur", "olustur", "sil", "taşı", "tasi",
        "kopyala", "yeniden adlandır", "ara", "araştır", "arastir", "göster",
        "goster", "doğrula", "dogrula", "çalıştır", "calistir", "oluştur",
    )
    return sum(1 for v in verbs if v in t)


def _looks_like_work(text):
    t = (text or "").strip().lower()
    if not t:
        return False
    # Leave known deterministic/single-turn intents to Agent.handle.
    if any(k in t for k in ("ekranımı analiz", "ekranimi analiz", "ekranı analiz", "ekrani analiz")):
        return False
    if any(k in t for k in ("hesapla", "hava durumu", "hava nasıl", "hava nasil")) and _action_score(t) < 2:
        return False
    markers = ("önce", "once", "sonra", "ardından", "ardindan", "daha sonra", "sonucunu", "sonucunu söyle", "sonucunu soyle")
    return any(k in t for k in markers) or _action_score(t) >= 2


def _deterministic_plan(text):
    try:
        from app.agent.orchestrator import parse_plan
        raw = parse_plan(text)
    except Exception:
        return None
    if not isinstance(raw, list) or len(raw) < 2:
        return None
    steps = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict) or not item.get("tool"):
            return None
        steps.append({
            "index": i,
            "tool": item["tool"],
            "arguments": item.get("args", item.get("arguments", {})) or {},
            "reason": item.get("label", "deterministic step"),
            "depends_on": [i - 1] if i else [],
        })
    return {"goal": text, "steps": steps, "planner": "deterministic-first", "bounded": True}


def _format_result(result):
    parts = []
    for step in result.get("steps", []):
        if step.get("ok"):
            value = step.get("result")
            if isinstance(value, str):
                text = value
            else:
                text = json.dumps(value, ensure_ascii=False, default=str)
            text = re.sub(r"\s+", " ", text).strip()
            if len(text) > 500:
                text = text[:500] + "…"
            parts.append(f"Adım {step.get('index', 0) + 1}: {text}")
        else:
            parts.append(f"Adım {step.get('index', 0) + 1}: {step.get('status', 'FAILED')} — {step.get('error', 'bilinmeyen hata')}")
    return "Görev tamamlandı.\n" + "\n".join(parts)


def _install():
    global _INSTALLED
    if _INSTALLED:
        return
    from app.agent.agent import Agent
    if getattr(Agent, "_hybrid_planner_connected", False):
        _INSTALLED = True
        return

    original_init = Agent.__init__
    original_handle = Agent.handle

    def hybrid_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self.hybrid_executor = HybridPlanExecutor(self.executor, self.registry, self.planner)

    def hybrid_handle(self, text, approved=False):
        if self.planner is None or not _looks_like_work(text):
            return original_handle(self, text, approved=approved)

        plan = _deterministic_plan(text)
        planner_name = "deterministic-first"
        if plan is None:
            try:
                plan = self.planner.make_plan(text)
                planner_name = "local-hybrid"
            except Exception:
                # No tool execution happened, so normal conversational fallback
                # is still safe when the LLM cannot produce a valid plan.
                return original_handle(self, text, approved=approved)

        result = self.hybrid_executor.execute(plan, approved=approved)
        status = result.get("status")
        if status == "WAITING_APPROVAL":
            answer = "Onay gerekiyor: plan içinde onay gerektiren bir işlem var."
        elif not result.get("ok"):
            answer = f"Görev tamamlanamadı ({status or 'FAILED'})."
            failed = next((s for s in result.get("steps", []) if not s.get("ok")), None)
            if failed:
                answer += f" Adım {failed.get('index', 0) + 1}: {failed.get('error', 'bilinmeyen hata')}"
        else:
            answer = _format_result(result)
        answer = self._boss_hitap(answer)
        self._save("ULTRON", answer)
        self.audit.write("HYBRID_PLAN", f"planner={planner_name} status={status} steps={len(result.get('steps', []))}")
        return answer

    Agent.__init__ = hybrid_init
    Agent.handle = hybrid_handle
    Agent._hybrid_planner_connected = True
    _INSTALLED = True


# planner.py is imported after Agent by the runtime, so installation is safe here.
_install()
