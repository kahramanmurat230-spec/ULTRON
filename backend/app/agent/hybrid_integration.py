"""Live integration for the bounded local hybrid planner/executor.

The connection is installed once at import time so the existing Agent API stays
compatible. Deterministic multi-step parsing remains first; the local LLM planner
is used only when the deterministic planner cannot build a plan.
"""
import json
import re

from app.agent.hybrid_executor import HybridPlanExecutor


def _action_score(text):
    t = (text or "").lower()
    verbs = ("kontrol et", "kontrol", "bul", "listele", "say", "aç", "ac ", "kapat", "oku", "yaz", "oluştur", "olustur", "sil", "taşı", "tasi", "kopyala", "yeniden adlandır", "ara", "araştır", "arastir", "göster", "goster", "doğrula", "dogrula", "çalıştır", "calistir", "hesapla")
    return sum(1 for v in verbs if v in t)


def _looks_like_work(text):
    t = (text or "").strip().lower()
    if not t:
        return False
    if any(k in t for k in ("ekranımı analiz", "ekranimi analiz", "ekranı analiz", "ekrani analiz")):
        return False
    markers = ("önce", "once", "sonra", "ardından", "ardindan", "daha sonra", "sonucunu", "sonucunu söyle", "sonucunu soyle")
    if any(k in t for k in markers):
        return True
    if any(k in t for k in ("hesapla", "hava durumu", "hava nasıl", "hava nasil")) and _action_score(t) < 2:
        return False
    return _action_score(t) >= 2


def _deterministic_plan(text):
    try:
        from app.agent.browser_workflow import parse_browser_plan, as_hybrid_plan
        browser_plan = as_hybrid_plan(parse_browser_plan(text), text)
        if browser_plan is not None:
            return browser_plan
    except Exception:
        pass
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
        steps.append({"index": i, "tool": item["tool"], "arguments": item.get("args", item.get("arguments", {})) or {}, "reason": item.get("label", "deterministic step"), "depends_on": [i - 1] if i else []})
    return {"goal": text, "steps": steps, "planner": "deterministic-first", "bounded": True}


def _format_result(result):
    parts = []
    for step in result.get("steps", []):
        if step.get("ok"):
            value = step.get("result")
            text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
            text = re.sub(r"\s+", " ", text).strip()
            if len(text) > 500:
                text = text[:500] + "…"
            parts.append(f"Adım {step.get('index', 0) + 1}: {text}")
        else:
            parts.append(f"Adım {step.get('index', 0) + 1}: {step.get('status', 'FAILED')} — {step.get('error', 'bilinmeyen hata')}")
    return "Görev tamamlandı.\n" + "\n".join(parts)


def _validate_plan(planner, plan):
    """Use Planner validation when available; reject malformed plans otherwise."""
    if not isinstance(plan, dict):
        return None
    validator = getattr(planner, "validate_plan", None)
    if callable(validator):
        try:
            return validator(plan)
        except Exception:
            return None
    steps = plan.get("steps") or []
    if not steps or len(steps) > HybridPlanExecutor.MAX_STEPS:
        return None
    for i, step in enumerate(steps):
        if not isinstance(step, dict) or not isinstance(step.get("tool"), str):
            return None
        if not isinstance(step.get("arguments", {}), dict):
            return None
        deps = step.get("depends_on", [])
        if not isinstance(deps, list) or any(not isinstance(d, int) or d >= i or d < 0 for d in deps):
            return None
    return plan


def try_hybrid_handle(agent, text, approved=False):
    """Return an answer when hybrid execution owns the request, else None."""
    planner = getattr(agent, "planner", None)
    if planner is None or not _looks_like_work(text):
        return None
    memory_context = getattr(planner, "memory_context", None)
    semantic_memory = getattr(agent, "semantic_memory", None)
    if memory_context is not None and semantic_memory is not None:
        memory_context.semantic_memory = semantic_memory

    plan = _deterministic_plan(text)
    planner_name = "deterministic-first"
    if plan is None:
        try:
            plan = planner.make_plan(text)
            planner_name = "local-hybrid"
        except Exception:
            return None

    plan = _validate_plan(planner, plan)
    if plan is None:
        agent.audit.write("HYBRID_PLAN", f"planner={planner_name} status=INVALID_PLAN")
        return None

    executor = getattr(agent, "hybrid_executor", None)
    if executor is None:
        executor = HybridPlanExecutor(agent.executor, agent.registry, planner)
        agent.hybrid_executor = executor

    result = executor.execute(plan, approved=approved, deadline_s=60.0)
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

    answer = agent._boss_hitap(answer)
    agent._save("ULTRON", answer)
    agent.audit.write("HYBRID_PLAN", f"planner={planner_name} status={status} steps={len(result.get('steps', []))}")
    return answer


def _install():
    from app.agent.agent import Agent
    if getattr(Agent, "_hybrid_planner_connected", False):
        return
    original_handle = Agent.handle

    def hybrid_handle(self, text, approved=False):
        try:
            answer = try_hybrid_handle(self, text, approved=approved)
        except Exception as exc:
            self.audit.write("HYBRID_PLAN", f"status=INTEGRATION_ERROR error={exc}")
            answer = None
        if answer is not None:
            return answer
        return original_handle(self, text, approved=approved)

    Agent.handle = hybrid_handle
    Agent._hybrid_planner_connected = True


_install()
