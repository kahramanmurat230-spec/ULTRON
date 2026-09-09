from pathlib import Path
from app.core.plugin_manager import PluginManager


class ToolRegistry:
    def __init__(self):
        self._tools = {}
        self.plugin_manager = PluginManager(Path.cwd())
        self._plugins_loaded = False

    def register(self, name, fn, description, parameters=None, dangerous=False):
        self._tools[name] = {
            "fn": fn,
            "dangerous": dangerous,
            "schema": {
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": parameters or {
                        "type": "object",
                        "properties": {},
                        "required": []
                    }
                }
            }
        }

    def _ensure_plugins(self):
        if self._plugins_loaded:
            return
        self._plugins_loaded = True
        try:
            records = self.plugin_manager.discover(reserved=self._tools.keys())
            for record in self.plugin_manager.plugins.values():
                self.register(
                    record.name,
                    lambda parameters, _name=record.name: self.plugin_manager.call(_name, parameters),
                    record.description,
                    {"type": "object", "properties": {}, "additionalProperties": True},
                    dangerous=True,
                )
        except Exception:
            # Plugin failure must never make the core registry unavailable.
            self._plugins_loaded = True

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
