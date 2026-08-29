class Executor:
    def __init__(self, registry, permissions, audit):
        self.registry = registry
        self.permissions = permissions
        self.audit = audit

    def execute(self, calls, approved=False):
        from app.security.risk import SelfCodeBoundary
        results = []
        for name, args in calls:
            tool = self.registry.get(name)
            if not tool:
                raise RuntimeError(f"Tool not registered: {name}")
            # PHASE: güvenlik çekirdeğine yazma — onay olsa bile RED
            # (boundary, approval'dan ÜSTTÜR; aksi halde onay = core bypass)
            if name in ("write_text", "write", "edit_file", "apply_patch") \
                    and "path" in args:
                SelfCodeBoundary.check(args["path"])
            if tool["dangerous"]:
                self.permissions.require(name, approved=approved)
            # MERGE UNION — iki güvenlik katmani birlikte (talimat: R risk_guard
            # + L path-boundary ASLA kaybolmez):
            #   1) L: güvenlik çekirdeğine yazma yolu — onay olsa bile RED
            #      (boundary, approval'dan ÜSTTÜR; onay = core bypass olamaz)
            #   2) R: birleşik 5-seviye risk motoru (risk.guard) audit'e taşır
            from app.security.risk import guard as risk_guard
            decision = risk_guard(name, args, bool(tool["dangerous"]), approved)
            risk = decision["level"]
            self.audit.write("TOOL_START", f"{name} risk={risk} {args}")
            try:
                result = tool["fn"](**args)
            except Exception as exc:
                self.audit.write("TOOL_ERROR", f"{name} risk={risk} -> {type(exc).__name__}: {exc}")
                raise
            self.audit.write("TOOL_SUCCESS", f"{name} risk={risk} -> {result}")
            results.append(result)
        return results
