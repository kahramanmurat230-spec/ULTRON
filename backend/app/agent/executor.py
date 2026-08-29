class Executor:
    def __init__(self, registry, permissions, audit):
        self.registry = registry
        self.permissions = permissions
        self.audit = audit

    def execute(self, calls, approved=False):
        results = []
        for name, args in calls:
            tool = self.registry.get(name)
            if not tool:
                raise RuntimeError(f"Tool not registered: {name}")
            if tool["dangerous"]:
                self.permissions.require(name, approved=approved)
            # PHASE 2: unified risk engine + self-coding boundary
            from app.security.risk import guard as risk_guard
            decision = risk_guard(name, args, bool(tool["dangerous"]), approved)
            self.audit.write("TOOL_START", f"{name} risk={decision['level']} {args}")
            try:
                result = tool["fn"](**args)
            except Exception as exc:
                self.audit.write("TOOL_ERROR", f"{name} -> {type(exc).__name__}: {exc}")
                raise
            self.audit.write("TOOL_SUCCESS", f"{name} -> {result}")
            results.append(result)
        return results
