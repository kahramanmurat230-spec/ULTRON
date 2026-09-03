"""Safe execution layer for validated local-LLM plans."""
import time


class HybridPlanExecutor:
    MAX_STEPS = 12
    MAX_RETRIES = 1

    def __init__(self, executor, registry, planner=None):
        self.executor = executor
        self.registry = registry
        self.planner = planner

    @staticmethod
    def _resolve(value, results):
        if not isinstance(value, str):
            return value
        prefix = "{{step."
        if not value.startswith(prefix) or not value.endswith("}}"):
            return value
        try:
            index, field = value[len(prefix):-2].split(".", 1)
            item = results[int(index)]
            if field == "result":
                return item.get("result")
            return item.get(field)
        except (ValueError, IndexError, KeyError):
            raise ValueError(f"Geçersiz step context: {value}")

    @classmethod
    def _resolve_args(cls, args, results):
        if isinstance(args, dict):
            return {k: cls._resolve_args(v, results) for k, v in args.items()}
        if isinstance(args, list):
            return [cls._resolve_args(v, results) for v in args]
        return cls._resolve(args, results)

    def execute(self, plan, approved=False, deadline_s=None):
        if not isinstance(plan, dict):
            return {"ok": False, "error": "Plan object bekleniyor.", "steps": []}
        steps = plan.get("steps") or []
        if not steps or len(steps) > self.MAX_STEPS:
            return {"ok": False, "error": "Plan adım sınırı geçersiz.", "steps": []}
        results = []
        started = time.monotonic()
        for i, step in enumerate(steps):
            if deadline_s is not None and time.monotonic() - started >= deadline_s:
                return {"ok": False, "status": "CANCELLED", "reason": "hard_deadline", "steps": results}
            tool = step.get("tool")
            reg = self.registry.get(tool)
            if reg is None:
                results.append({"index": i, "tool": tool, "ok": False, "error": "Tool not registered"})
                return {"ok": False, "status": "FAILED", "steps": results}
            deps = step.get("depends_on") or []
            if any(d >= len(results) or not results[d].get("ok") for d in deps):
                results.append({"index": i, "tool": tool, "ok": False, "status": "SKIPPED", "error": "Dependency failed"})
                return {"ok": False, "status": "FAILED", "steps": results}
            if reg.get("dangerous") and not approved:
                results.append({"index": i, "tool": tool, "ok": False, "status": "WAITING_APPROVAL", "error": f"Confirmation required for: {tool}"})
                return {"ok": False, "status": "WAITING_APPROVAL", "steps": results}
            try:
                args = self._resolve_args(step.get("arguments", {}), results)
                last_error = None
                output = None
                for attempt in range(self.MAX_RETRIES + 1):
                    try:
                        output = self.executor.execute([(tool, args)], approved=approved)[0]
                        break
                    except Exception as exc:
                        last_error = exc
                        if attempt >= self.MAX_RETRIES:
                            raise
                results.append({"index": i, "tool": tool, "ok": True, "result": output, "attempts": attempt + 1})
            except Exception as exc:
                results.append({"index": i, "tool": tool, "ok": False, "error": str(last_error or exc), "attempts": attempt + 1})
                return {"ok": False, "status": "FAILED", "steps": results}
        return {"ok": True, "status": "DONE", "steps": results, "goal": plan.get("goal", "")}
