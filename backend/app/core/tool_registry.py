class ToolRegistry:
    def __init__(self):
        self._tools = {}

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
