import json


class Planner:
    """Bounded local-LLM planner with strict structural validation.

    The deterministic Agent 2.0 planner remains the first choice for requests
    it understands. This planner is the hybrid fallback for richer natural
    language tasks. It never executes tools itself and rejects unsafe/invalid
    model output before an executor can see it.
    """

    MAX_STEPS = 12

    def __init__(self, brain, registry):
        self.brain = brain
        self.registry = registry

    def _extract_json(self, content):
        content = (content or "").strip()
        a, b = content.find("{"), content.rfind("}")
        if a < 0 or b <= a:
            raise ValueError("Plan JSON alınamadı.")
        try:
            return json.loads(content[a:b + 1])
        except json.JSONDecodeError as exc:
            raise ValueError("Plan JSON bozuk.") from exc

    def validate_plan(self, plan, goal=None):
        if not isinstance(plan, dict):
            raise ValueError("Geçersiz plan: object bekleniyor.")
        steps = plan.get("steps")
        if not isinstance(steps, list) or not steps:
            raise ValueError("Geçersiz plan: steps boş.")
        if len(steps) > self.MAX_STEPS:
            raise ValueError(f"Plan çok uzun: en fazla {self.MAX_STEPS} adım.")
        allowed = set(self.registry.names())
        normalized = []
        for i, raw in enumerate(steps):
            if not isinstance(raw, dict):
                raise ValueError(f"Geçersiz adım: {i}.")
            tool = raw.get("tool")
            if not isinstance(tool, str) or tool not in allowed:
                raise ValueError(f"Plan bilinmeyen araç içeriyor: {tool}")
            args = raw.get("arguments", raw.get("args", {}))
            if not isinstance(args, dict):
                raise ValueError(f"Plan argümanları object olmalı: {tool}")
            reason = raw.get("reason", "")
            if not isinstance(reason, str):
                reason = str(reason)
            depends = raw.get("depends_on", [])
            if depends is None:
                depends = []
            if not isinstance(depends, list):
                raise ValueError(f"depends_on liste olmalı: {tool}")
            deps = []
            for dep in depends:
                if not isinstance(dep, int) or dep < 0 or dep >= i:
                    raise ValueError(f"Geçersiz bağımlılık: step {i} -> {dep}")
                if dep not in deps:
                    deps.append(dep)
            normalized.append({
                "index": i,
                "tool": tool,
                "arguments": args,
                "reason": reason[:300],
                "depends_on": deps,
            })
        return {
            "goal": str(plan.get("goal") or goal or "")[:1000],
            "steps": normalized,
            "planner": "local-hybrid",
            "bounded": True,
        }

    def make_plan(self, goal):
        tool_names = ", ".join(self.registry.names())
        system = (
            "Sen Ultron için görev planlayıcısısın. Sadece JSON döndür. "
            "Plan kısa, güvenli ve uygulanabilir olmalı. En fazla 12 adım üret. "
            "Her adım tool adı, arguments, reason ve önceki adımlara depends_on içersin. "
            "depends_on yalnızca kendisinden önceki 0-tabanlı step index'lerini içerebilir. "
            "Tehlikeli işlemleri kullanıcı onayı olmadan çalıştırma; sadece planla. "
            "Kullanılabilecek araçlar: " + tool_names + ". "
            "JSON biçimi: {\"goal\": str, \"steps\": "
            "[{\"tool\": str, \"arguments\": object, \"reason\": str, "
            "\"depends_on\": [int]}]}."
        )
        r = self.brain.chat(
            [{"role": "system", "content": system},
             {"role": "user", "content": goal}], tools=None)
        content = (r.get("message", {}).get("content") or "").strip()
        return self.validate_plan(self._extract_json(content), goal=goal)
