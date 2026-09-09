"""ULTRON production gate: deterministic local checks for release readiness.

The gate is intentionally read-only. It never approves dangerous actions,
changes security policy, or mutates the workspace. Provider/device dependent
features must remain unavailable when their real runtime is unavailable.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def fail(message: str) -> None:
    raise SystemExit(f"PRODUCTION_GATE_FAIL: {message}")


def main() -> None:
    required = [
        ROOT / "backend" / "server.py",
        ROOT / "backend" / "app" / "core" / "capability_runtime.py",
        ROOT / "backend" / "app" / "core" / "jarvis_capabilities.py",
        ROOT / "desktop" / "main.cjs",
        ROOT / "desktop" / "package.json",
        ROOT / "frontend" / "package.json",
        ROOT / "frontend-mobile" / "package.json",
    ]
    for path in required:
        if not path.is_file():
            fail(f"missing required file: {path.relative_to(ROOT)}")

    server = (ROOT / "backend" / "server.py").read_text(encoding="utf-8")
    for marker in (
        'app.router.add_get("/api/tools", api_tools)',
        'app.router.add_post("/api/task/approve", api_task_approve)',
        'app.router.add_post("/api/codegen/apply", api_codegen_apply)',
    ):
        if marker not in server:
            fail(f"critical route missing: {marker}")

    # Syntax validation without importing optional hardware/provider modules.
    for path in (ROOT / "backend" / "server.py", ROOT / "backend" / "app" / "core" / "capability_runtime.py"):
        try:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            fail(f"syntax error in {path.relative_to(ROOT)}: {exc}")

    inventory = (ROOT / "backend" / "app" / "core" / "jarvis_capabilities.py").read_text(encoding="utf-8")
    runtime = (ROOT / "backend" / "app" / "core" / "capability_runtime.py").read_text(encoding="utf-8")
    if 'allowed = {"implemented", "adapter", "planned"}' not in inventory:
        fail("capability lifecycle validation missing")
    if 'production_ready = total > 0 and implemented == total and adapters == 0 and planned == 0' not in runtime:
        fail("production readiness must require all capabilities live")

    desktop = json.loads((ROOT / "desktop" / "package.json").read_text(encoding="utf-8"))
    if desktop.get("scripts", {}).get("dist") != "electron-builder --win":
        fail("Windows installer script missing")

    print("PRODUCTION_GATE_OK")
    print("- critical backend routes present")
    print("- backend syntax valid")
    print("- capability lifecycle guard present")
    print("- production_ready requires zero adapters/planned capabilities")
    print("- Windows installer command present")


if __name__ == "__main__":
    main()
