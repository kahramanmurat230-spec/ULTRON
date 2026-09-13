"""Safe-by-default local plugin discovery for ULTRON.

Plugins are discovered from the project plugins directory, validated, and exposed
as metadata/callables. Plugin execution must still pass through ULTRON's executor
so existing risk/approval rules remain authoritative.
"""
from __future__ import annotations
import importlib.util, inspect, re
from dataclasses import dataclass
from pathlib import Path

_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")

@dataclass
class Plugin:
    name: str
    description: str
    run: object
    path: str
    valid: bool = True
    error: str = ""
    dangerous: bool = True

class PluginManager:
    def __init__(self, root, directory="plugins", max_plugin_bytes=1_000_000):
        self.root = Path(root).resolve()
        self.directory = (self.root / directory).resolve()
        self.max_plugin_bytes = max(1, int(max_plugin_bytes))
        self.directory.mkdir(parents=True, exist_ok=True)
        self.plugins = {}
        self.records = []

    def discover(self, reserved=()):
        self.plugins = {}; self.records = []
        reserved = set(reserved)
        for path in sorted(self.directory.glob("*.py")):
            if path.name.startswith("_"): continue
            record = self._load(path, reserved)
            self.records.append(record)
            if record.valid: self.plugins[record.name] = record
        return self.list()

    def _load(self, path, reserved):
        try:
            # Resolve before import so a symlink cannot escape the plugin directory.
            resolved = path.resolve()
            if path.is_symlink(): raise PermissionError("symlink plugin rejected")
            if resolved.parent != self.directory: raise PermissionError("plugin path escapes plugin directory")
            if resolved.stat().st_size > self.max_plugin_bytes: raise ValueError("plugin exceeds size limit")
            spec = importlib.util.spec_from_file_location(f"ultron_plugin_{path.stem}", resolved)
            if not spec or not spec.loader: raise ImportError("import spec unavailable")
            module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
            meta = getattr(module, "PLUGIN", None)
            if not isinstance(meta, dict): raise ValueError("PLUGIN dict missing")
            name, desc, run = meta.get("name"), meta.get("description", ""), getattr(module, "run", None)
            if not isinstance(name, str) or not _NAME.fullmatch(name): raise ValueError("invalid plugin name")
            if name in reserved or name in self.plugins: raise ValueError("plugin name collision")
            if not isinstance(desc, str) or not desc.strip(): raise ValueError("description missing")
            if not callable(run): raise ValueError("callable run(parameters) missing")
            return Plugin(name, desc.strip(), run, str(resolved.relative_to(self.root)))
        except Exception as exc:
            return Plugin(path.stem, "", None, str(path.relative_to(self.root)), valid=False, error=str(exc))

    def list(self):
        return [{"name": p.name, "description": p.description, "path": p.path, "valid": p.valid, "error": p.error, "dangerous": p.dangerous} for p in self.records]

    def call(self, name, parameters=None, **context):
        plugin = self.plugins.get(name)
        if plugin is None: raise KeyError(f"Plugin not available: {name}")
        parameters = parameters or {}
        fn = plugin.run
        sig = inspect.signature(fn)
        kwargs = {k:v for k,v in context.items() if k in sig.parameters or any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())}
        return fn(parameters, **kwargs)
