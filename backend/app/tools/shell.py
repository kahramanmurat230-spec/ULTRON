"""Approval-gated shell execution for the unified ULTRON runtime.

The caller (Executor) is responsible for approval. This module adds the
second boundary: deterministic ShellPolicy evaluation immediately before the
subprocess is created. It never grants approval itself.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from app.security.shell_policy import ShellPolicy


class ShellExecutor:
    def __init__(self, workspace: str | Path, timeout: int = 15, output_limit: int = 4000) -> None:
        self.workspace = Path(workspace).resolve()
        self.timeout = max(1, int(timeout))
        self.output_limit = max(256, int(output_limit))
        self.policy = ShellPolicy()

    def _cwd(self, cwd: str | None) -> str:
        if not cwd:
            return str(self.workspace)
        candidate = Path(cwd).expanduser().resolve()
        try:
            candidate.relative_to(self.workspace)
        except ValueError as exc:
            raise ValueError("shell cwd must remain inside workspace") from exc
        if not candidate.is_dir():
            raise ValueError("shell cwd is not a directory")
        return str(candidate)

    async def execute(self, command: str, cwd: str | None = None) -> dict:
        decision = self.policy.evaluate(command)
        if not decision.allowed:
            return {"ok": False, "blocked": True, "risk": decision.risk, "error": decision.reason}

        workdir = self._cwd(cwd)
        kwargs = {
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.STDOUT,
            "cwd": workdir,
        }
        if sys.platform.startswith("win"):
            proc = await asyncio.create_subprocess_shell(command, **kwargs)
        else:
            proc = await asyncio.create_subprocess_shell(command, executable=os.environ.get("SHELL", "/bin/sh"), **kwargs)
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=self.timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            return {"ok": False, "timed_out": True, "risk": decision.risk, "error": f"command timed out ({self.timeout}s)"}

        text = (out or b"").decode(errors="replace")
        text = text[-self.output_limit:]
        if proc.returncode == 0:
            return {"ok": True, "risk": decision.risk, "output": text or "(no output)"}
        return {"ok": False, "risk": decision.risk, "error": f"exit {proc.returncode}: {text[:800]}"}
