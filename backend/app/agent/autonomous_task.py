"""Bounded autonomous task coordinator.

It keeps progressing through read-only/planning/verification work automatically.
Any mutating step is delegated to CodeAgent with an explicit approval flag; this
module never manufactures approval on its own.
"""
from __future__ import annotations
import time

class AutonomousTask:
    MAX_STEPS = 8

    def __init__(self, code_agent, audit=None):
        self.code_agent = code_agent
        self.audit = audit

    def _audit(self, event, text):
        if self.audit:
            try: self.audit.write(event, text)
            except Exception: pass

    def run(self, goal, paths=None, approved=False, max_iterations=3, timeout=180):
        started = time.monotonic()
        self._audit("AUTONOMOUS_TASK_START", f"goal={goal}")
        if not goal or not isinstance(goal, str):
            return {"ok": False, "status": "INVALID_GOAL", "steps": []}
        # Planning/review is always automatic and non-mutating.
        try:
            analysis = self.code_agent.analyze(paths=paths, goal=goal)
        except Exception as exc:
            return {"ok": False, "status": "ANALYSIS_FAILED", "error": str(exc), "steps": []}
        steps = [{"stage": "ANALYZE", "ok": True, "result": analysis}]
        if not approved:
            steps.append({"stage": "MUTATION_GATE", "ok": False, "status": "APPROVAL_REQUIRED"})
            return {"ok": False, "status": "APPROVAL_REQUIRED", "steps": steps}
        remaining = max(1.0, float(timeout) - (time.monotonic() - started))
        result = self.code_agent.self_coding_loop(paths=paths, goal=goal, approved=True, max_iterations=min(int(max_iterations), 3), timeout=remaining)
        steps.append({"stage": "SELF_CODING", "ok": bool(result.get("ok")), "result": result})
        final_ok = bool(result.get("ok"))
        self._audit("AUTONOMOUS_TASK_DONE", f"goal={goal} ok={final_ok}")
        return {"ok": final_ok, "status": result.get("status", "UNKNOWN"), "steps": steps}
