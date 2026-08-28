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
            self.audit.write("TOOL_START", f"{name} {args}")
            try:
                result = tool["fn"](**args)
            except Exception as exc:
                self.audit.write("TOOL_ERROR", f"{name} -> {type(exc).__name__}: {exc}")
                raise
            self.audit.write("TOOL_SUCCESS", f"{name} -> {result}")
            results.append(result)
        return results
