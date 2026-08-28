"""Shared-brain session auth (phone + laptop clients).

Disabled by default for local use; enable with ULTRON_AUTH=1 when exposing
the backend over a network. Tokens are random, stored server-side only.
No secrets live in source code.
"""
import json
import os
import secrets
import time
from pathlib import Path


class Auth:
    def __init__(self, path: Path, required: bool):
        self.path = path
        self.required = required
        self.pairing_secret = os.environ.get("ULTRON_PAIRING_SECRET", "")
        self.sessions: dict[str, dict] = {}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._load()

    def _load(self) -> None:
        try:
            if self.path.exists():
                self.sessions = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            self.sessions = {}

    def _save(self) -> None:
        self.path.write_text(json.dumps(self.sessions, indent=1), encoding="utf-8")

    def handshake(self, device: str, pairing_secret: str = "") -> dict:
        if self.required and self.pairing_secret and not secrets.compare_digest(pairing_secret, self.pairing_secret):
            raise PermissionError("Pairing secret required")
        token = secrets.token_urlsafe(24)
        self.sessions[token] = {"device": device or "unknown", "created": time.time()}
        self._save()
        return {"token": token, "device": device}

    def valid(self, token: str | None) -> bool:
        if not self.required:
            return True
        return bool(token) and token in self.sessions

    def devices(self) -> list[dict]:
        return [{"device": v.get("device"), "created": v.get("created")} for v in self.sessions.values()]

    def enabled(self) -> bool:
        return self.required
