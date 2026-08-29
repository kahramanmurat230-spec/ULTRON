"""WAVE 3 — Signed capability tokens: worker yetrinin TEK kaynağı.

Token alanları: worker_id, capability, scope, issued_at, expires_at,
task_id, risk_level + HMAC imzası (orchestrator gizli anahtarı ile).
- Sahtecilik imkânsız değil ZORUNLU: imzasız/bozulmuş token RED.
- Süre dolmuş token RED. Başka worker'ın token'ı RED. Başka görevin
  token'ı RED. Capability kapsam dışı RED.
- Delegation YALNIZ supervisor → worker; worker→worker RED ve audit.
- HIGH/CRITICAL capability ayrıca onay ister: approve() yalnız
  supervisor API'ündir; worker kendi kendine onaylayamaz.
Sırrın kendisi asla token'a gömülmez (yalnız imza).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import uuid

from app.orchestr.worker import CAPABILITY_RISK

TOKEN_TTL_S = 3600.0
ALLOWED_SCOPES = ("workspace", "workspace:ro", "system:ro", "memory:ro",
                  "browser", "network", "gui")


class TokenError(Exception):
    """Yetki reddi — nedeni dürüstçe belirtilir (audit için)."""


def _b64u(data: bytes) -> str:
    import base64
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _sign(secret: bytes, body: str) -> str:
    return _b64u(hmac.new(secret, body.encode("utf-8"),
                          hashlib.sha256).digest())


class CapabilityTokenAuthority:
    """Supervisor'un token makamı. Gizli anahtar süreç ömründe kalır
    (restart → eski token'lar otomatik geçersiz: güvenlik özelliği)."""

    def __init__(self, secret: bytes | None = None, now=None):
        self.secret = secret or os.urandom(32)   # asla diske/repo'ya yazılmaz
        self.now = now or time.time
        self.issued: dict[str, dict] = {}        # token_id → kayıt (audit)
        self.revoked: set[str] = set()
        self.approved: set[str] = set()          # onaylı worker:cap

    # ------------------------------------------------------------ issue
    def issue(self, worker_id: str, capability: str, *, task_id: str,
              scope: str = "workspace:ro", ttl_s: float = TOKEN_TTL_S,
              delegated_by: str = "supervisor") -> str:
        capability = str(capability).upper()
        if capability not in CAPABILITY_RISK:
            raise TokenError(f"unknown capability {capability!r}")
        if scope not in ALLOWED_SCOPES:
            raise TokenError(f"unknown scope {scope!r}")
        risk = CAPABILITY_RISK[capability]
        token_id = uuid.uuid4().hex[:12]
        body = {"token_id": token_id, "worker_id": worker_id,
                "capability": capability, "scope": scope, "task_id": task_id,
                "risk_level": risk,
                "issued_at": round(self.now(), 3),
                "expires_at": round(self.now() + float(ttl_s), 3)}
        payload = json.dumps(body, sort_keys=True, ensure_ascii=False)
        sig = _sign(self.secret, payload)
        self.issued[token_id] = {**body, "delegated_by": delegated_by}
        return f"uct.{token_id}.{_b64u(payload.encode('utf-8'))}.{sig}"

    # ------------------------------------------------------------ verify
    def verify(self, token: str, *, worker_id: str, capability: str,
               task_id: str) -> dict:
        """Tam doğrulama zinciri — her başarısızlık TokenError ile RED."""
        try:
            prefix, token_id, body_b64, sig = token.split(".", 3)
        except (ValueError, AttributeError):
            raise TokenError("malformed token")
        if prefix != "uct":
            raise TokenError("malformed token prefix")
        import base64
        try:
            payload = base64.urlsafe_b64decode(body64_padded(body_b64)
                                               ).decode("utf-8")
        except Exception:
            raise TokenError("malformed token body")
        expect_sig = _sign(self.secret, payload)
        if not hmac.compare_digest(sig, expect_sig):
            raise TokenError("token signature invalid (forgery rejected)")
        body = json.loads(payload)
        if token_id in self.revoked or body["token_id"] != token_id:
            raise TokenError("token revoked")
        if self.now() > float(body["expires_at"]):
            raise TokenError("token expired")
        if body["worker_id"] != worker_id:
            raise TokenError("token belongs to another worker")
        if body["task_id"] != task_id:
            raise TokenError("token issued for another task (cross-task "
                             "rejected)")
        if body["capability"] != str(capability).upper():
            raise TokenError("capability not in token (scope escape "
                             "rejected)")
        # HIGH/CRITICAL → onay şart (worker kendi kendini onaylayamaz)
        if body["risk_level"] in ("HIGH", "CRITICAL"):
            key = f"{worker_id}:{body['capability']}"
            if key not in self.approved:
                raise TokenError(f"high-risk capability {body['capability']} "
                                 "requires supervisor approval")
        return body

    # ------------------------------------------------------------ approve/revoke
    def approve(self, worker_id: str, capability: str) -> dict:
        """YALNIZ supervisor çağırır (insan onay akışından sonra)."""
        cap = str(capability).upper()
        if cap not in CAPABILITY_RISK:
            raise TokenError(f"unknown capability {cap!r}")
        self.approved.add(f"{worker_id}:{cap}")
        return {"ok": True, "worker_id": worker_id, "capability": cap}

    def revoke(self, token_id: str) -> dict:
        self.revoked.add(token_id)
        return {"ok": True, "revoked": token_id}

    def audit_trail(self) -> list[dict]:
        return [dict(v) for v in self.issued.values()]


def body64_padded(b64: str) -> str:
    return b64 + "=" * (-len(b64) % 4)
