from pathlib import Path
from app.core.undo_journal import UndoJournal


class Executor:
    def __init__(self, registry, permissions, audit, undo_journal=None):
        self.registry = registry
        self.permissions = permissions
        self.audit = audit
        self.undo = undo_journal or getattr(registry, "undo_journal", None) or UndoJournal(Path.cwd())

    def execute(self, calls, approved=False):
        from app.security.risk import SelfCodeBoundary
        results = []
        reversible = {"write_text", "write", "edit_file"}
        for name, args in calls:
            tool = self.registry.get(name)
            if not tool:
                raise RuntimeError(f"Tool not registered: {name}")
            if name in ("write_text", "write", "edit_file", "apply_patch") and "path" in args:
                SelfCodeBoundary.check(args["path"])
            if tool["dangerous"]:
                self.permissions.require(name, approved=approved)
            from app.security.risk import guard as risk_guard
            decision = risk_guard(name, args, bool(tool["dangerous"]), approved)
            risk = decision["level"]
            self.audit.write("TOOL_START", f"{name} risk={risk} {args}")
            before = None
            undo_path = args.get("path") if name in reversible else None
            if undo_path:
                try: before = self.undo.snapshot(undo_path)
                except (OSError, ValueError, PermissionError): before = None
            try:
                result = tool["fn"](**args)
            except Exception as exc:
                self.audit.write("TOOL_ERROR", f"{name} risk={risk} -> {type(exc).__name__}: {exc}")
                raise
            if undo_path and before is not None:
                try:
                    entry_id = self.undo.record_file_change(name, undo_path, before)
                    self.audit.write("UNDO_RECORDED", f"{name} id={entry_id} path={undo_path}")
                except Exception as exc:
                    self.audit.write("UNDO_RECORD_ERROR", f"{name}: {type(exc).__name__}: {exc}")
            self.audit.write("TOOL_SUCCESS", f"{name} risk={risk} -> {result}")
            results.append(result)
        return results
