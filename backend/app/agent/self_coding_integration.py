"""Agent integration for the bounded Self-Coding Agent 2.0."""
from __future__ import annotations

from app.code_agent.self_coding_agent import SelfCodingAgent


def _install():
    try:
        from app.agent.agent import Agent
    except Exception:
        return False
    if getattr(Agent, "_self_coding_v2_installed", False):
        return True
    original = Agent.handle

    async def handle(self, text, approved=False, *args, **kwargs):
        lowered = str(text or "").lower()
        trigger = any(key in lowered for key in (
            "kodu düzelt", "kodu düzelt", "self coding", "self-coding",
            "kendi kodunu düzelt", "kod hatasını düzelt", "testleri düzelt",
        ))
        if trigger and getattr(self, "planner", None) is not None:
            runtime_root = getattr(getattr(self, "planner", None), "root", None)
            root = runtime_root or getattr(self, "root", None)
            if root is not None:
                try:
                    agent = SelfCodingAgent(self.brain, root, audit=getattr(self, "audit", None))
                    result = agent.run(goal=str(text), approved=approved)
                    if result.get("status") == "APPROVAL_REQUIRED":
                        return {"ok": False, "result": "Kod değişikliği için açık onay gerekiyor.", "status": "WAITING_APPROVAL"}
                    return {"ok": bool(result.get("ok")), "result": result, "status": result.get("status")}
                except Exception:
                    pass
        return await original(self, text, approved=approved, *args, **kwargs)

    Agent.handle = handle
    Agent._self_coding_v2_installed = True
    return True


_install()
