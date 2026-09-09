from app.core.plugin_manager import PluginManager


class ToolRegistry:
    def __init__(self):
        self._tools = {}
        self.plugin_manager = PluginManager(__import__('pathlib').Path.cwd())

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

    def get(self, name):
        return self._tools.get(name)

    def names(self):
        return sorted(self._tools)

    def ollama_tools(self, include_dangerous=False):
        return [v["schema"] for v in self._tools.values() if include_dangerous or not v["dangerous"]]

    def discover_plugins(self):
        """Load local plugins without allowing them to bypass the executor."""
        records = self.plugin_manager.discover(reserved=self._tools.keys())
        for record in self.plugin_manager.plugins.values():
            self.register(
                record.name,
                lambda parameters, _name=record.name: self.plugin_manager.call(_name, parameters),
                record.description,
                {"type": "object", "properties": {}, "additionalProperties": True},
                dangerous=True,
            )
        return records
