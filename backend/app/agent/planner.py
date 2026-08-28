import json

class Planner:
    """Structured task planner. It creates a short executable plan using the same local LLM."""
    def __init__(self, brain, registry):
        self.brain = brain
        self.registry = registry

    def make_plan(self, goal):
        tool_names=', '.join(self.registry.names())
        system=("Sen Ultron için görev planlayıcısısın. Sadece JSON döndür. "
                "Plan kısa, güvenli ve uygulanabilir olmalı. Her adım bir tool adı ve arguments içersin. "
                "Kullanılabilecek araçlar: "+tool_names+". "
                "JSON biçimi: {\"goal\": str, \"steps\": [{\"tool\": str, \"arguments\": object, \"reason\": str}]}.")
        r=self.brain.chat([{"role":"system","content":system},{"role":"user","content":goal}], tools=None)
        content=(r.get('message',{}).get('content') or '').strip()
        a,b=content.find('{'),content.rfind('}')
        if a<0 or b<=a: raise ValueError('Plan JSON alınamadı.')
        plan=json.loads(content[a:b+1])
        if not isinstance(plan.get('steps'), list): raise ValueError('Geçersiz plan.')
        allowed=set(self.registry.names())
        for step in plan['steps']:
            if step.get('tool') not in allowed: raise ValueError(f"Plan bilinmeyen araç içeriyor: {step.get('tool')}")
        return plan
