from pathlib import Path
import asyncio
import concurrent.futures

from app.core.plugin_manager import PluginManager
from app.core.undo_journal import UndoJournal
from app.tools.shell import ShellExecutor


class ToolRegistry:
    def __init__(self):
        self._tools = {}
        self.plugin_manager = PluginManager(Path.cwd())
        self.undo_journal = UndoJournal(Path.cwd())
        self.shell = ShellExecutor(Path.cwd())
        self._plugins_loaded = False
        self.register("undo_list", lambda: self.undo_journal.list(), "List reversible recent file actions.")
        self.register("undo_last", lambda entry_id=None: self.undo_journal.undo(entry_id), "Undo the most recent reversible file action; approval required.", {"type": "object", "properties": {"entry_id": {"type": "string"}}, "required": []}, dangerous=True)
        self.register(
            "shell_exec",
            # ShellExecutor.execute is async; the Executor/registry path is
            # sync. Bridge deterministically — returning the raw coroutine
            # here leaked an unawaited coroutine: the command NEVER ran but
            # the executor reported success (final-integration-audit defect).
            lambda command, cwd=None: self._shell_sync(command, cwd),
            "Execute a shell command inside the workspace; server-side approval and shell policy are mandatory.",
            {"type": "object", "properties": {"command": {"type": "string"}, "cwd": {"type": "string"}}, "required": ["command"]},
            dangerous=True,
        )

    def _shell_sync(self, command, cwd=None):
        coro = self.shell.execute(command, cwd)
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)
        # called from inside a running event loop: run the coroutine on a
        # dedicated thread's fresh loop (never block the server loop inline)
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()

    def register(self, name, fn, description, parameters=None, dangerous=False):
        self._tools[name] = {
            "fn": fn,
            "dangerous": dangerous,
            "schema": {
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": parameters or {"type": "object", "properties": {}, "required": []}
                }
            }
        }

    def _ensure_plugins(self):
        if self._plugins_loaded: return
        self._plugins_loaded = True
        try:
            self.plugin_manager.discover(reserved=self._tools.keys())
            for record in self.plugin_manager.plugins.values():
                self.register(record.name, lambda parameters, _name=record.name: self.plugin_manager.call(_name, parameters), record.description, {"type": "object", "properties": {}, "additionalProperties": True}, dangerous=True)
        except Exception:
            pass

    def get(self, name):
        self._ensure_plugins()
        return self._tools.get(name)

    def names(self):
        self._ensure_plugins()
        return sorted(self._tools)

    def ollama_tools(self, include_dangerous=False):
        self._ensure_plugins()
        return [v["schema"] for v in self._tools.values() if include_dangerous or not v["dangerous"]]

    def discover_plugins(self):
        self._plugins_loaded = False
        self._ensure_plugins()
        return self.plugin_manager.list()
