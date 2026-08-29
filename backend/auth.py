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
        # PHASE 1: oturum ömrü — süresiz token yok (default 12h)
        self.ttl_s = float(os.environ.get("ULTRON_SESSION_TTL_S", 43200))
        self.sessions: dict[str, dict] = {}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._load()
        self._prune()

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

    def _prune(self) -> None:
        """Süresi dolan oturumlar silinir (kayan pencere; süresiz YOK)."""
        now = time.time()
        expired = [t for t, v in self.sessions.items()
                   if now - float(v.get("created", 0)) > self.ttl_s]
        if expired:
            for t in expired:
                del self.sessions[t]
            self._save()

    def valid(self, token: str | None) -> bool:
        if not self.required:
            return True
        if not token or token not in self.sessions:
            return False
        v = self.sessions[token]
        if time.time() - float(v.get("created", 0)) > self.ttl_s:
            del self.sessions[token]
            self._save()
            return False
        # kayan pencere: doğrulanmış oturum tazelenir
        v["created"] = time.time()
        return True

    def devices(self) -> list[dict]:
        return [{"device": v.get("device"), "created": v.get("created")} for v in self.sessions.values()]

    def enabled(self) -> bool:
        return self.required
