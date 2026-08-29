"""WAVE 3 / Capability tokens: sahtecilik, süre, başka worker/görev
token'ı, kapsam kaçışı, onay kapısı, iptal, delegasyon denetimi."""
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.orchestr.tokens import (  # noqa: E402
    CapabilityTokenAuthority, TokenError,
)


def auth(clock=None):
    return CapabilityTokenAuthority(now=clock or time.time)


# ------------------------------------------------------------ issue/verify
def test_issue_and_verify_ok(tmp_path):
    a = auth()
    tok = a.issue("w1", "WEB_SEARCH", task_id="t1", scope="network")
    body = a.verify(tok, worker_id="w1", capability="WEB_SEARCH", task_id="t1")
    for k in ("worker_id", "capability", "scope", "issued_at", "expires_at",
              "task_id", "risk_level"):
        assert k in body
    assert body["risk_level"] == "LOW"


def test_unknown_capability_and_scope_rejected(tmp_path):
    a = auth()
    with pytest.raises(TokenError, match="unknown capability"):
        a.issue("w1", "MIND_READ", task_id="t1")
    with pytest.raises(TokenError, match="unknown scope"):
        a.issue("w1", "WEB_SEARCH", task_id="t1", scope="宇宙")


# ------------------------------------------------------------ forgery
def test_forged_token_rejected(tmp_path):
    a = auth()
    tok = a.issue("w1", "FETCH", task_id="t1", scope="network")
    parts = tok.split(".")
    # imzayı boz
    forged = f"{parts[0]}.{parts[1]}.{parts[2]}.AAAAFORGED"
    with pytest.raises(TokenError, match="forgery"):
        a.verify(forged, worker_id="w1", capability="FETCH", task_id="t1")
    # gövdeyi değiştir (başka capability'ye)
    import base64, json
    body = json.loads(base64.urlsafe_b64decode(
        parts[2] + "=" * (-len(parts[2]) % 4)).decode())
    body["capability"] = "WRITE_WORKSPACE"
    new_body = _b64e(json.dumps(body, sort_keys=True).encode())
    with pytest.raises(TokenError):
        a.verify(f"uct.{parts[1]}.{new_body}.{parts[3]}",
                 worker_id="w1", capability="WRITE_WORKSPACE", task_id="t1")
    with pytest.raises(TokenError, match="malformed"):
        a.verify("şüpheli-token", worker_id="w1", capability="FETCH",
                 task_id="t1")


def _b64e(data: bytes) -> str:
    import base64
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def test_foreign_authority_token_rejected(tmp_path):
    """Başka anahtarla üretilen token RED (authority izolasyonu)."""
    a1, a2 = auth(), auth()
    tok = a2.issue("w1", "FETCH", task_id="t1", scope="network")
    with pytest.raises(TokenError):
        a1.verify(tok, worker_id="w1", capability="FETCH", task_id="t1")


# ------------------------------------------------------------ expiry
def test_expired_token_rejected(tmp_path):
    clock = {"t": 1000.0}
    a = auth(clock=lambda: clock["t"])
    tok = a.issue("w1", "WEB_SEARCH", task_id="t1", scope="network",
                  ttl_s=10)
    a.verify(tok, worker_id="w1", capability="WEB_SEARCH", task_id="t1")
    clock["t"] += 11
    with pytest.raises(TokenError, match="expired"):
        a.verify(tok, worker_id="w1", capability="WEB_SEARCH", task_id="t1")


# ------------------------------------------------------------ ownership
def test_wrong_worker_token_rejected(tmp_path):
    a = auth()
    tok = a.issue("w1", "OCR", task_id="t1")
    with pytest.raises(TokenError, match="another worker"):
        a.verify(tok, worker_id="w2", capability="OCR", task_id="t1")


def test_cross_task_token_rejected(tmp_path):
    a = auth()
    tok = a.issue("w1", "OCR", task_id="t1")
    with pytest.raises(TokenError, match="another task"):
        a.verify(tok, worker_id="w1", capability="OCR", task_id="t2")


def test_scope_escape_rejected(tmp_path):
    a = auth()
    tok = a.issue("w1", "READ_WORKSPACE", task_id="t1", scope="workspace:ro")
    a.verify(tok, worker_id="w1", capability="READ_WORKSPACE", task_id="t1")
    with pytest.raises(TokenError, match="scope escape"):
        a.verify(tok, worker_id="w1", capability="WRITE_WORKSPACE",
                 task_id="t1")            # token başka capability taşıyor


# ------------------------------------------------------------ approval
def test_high_risk_requires_supervisor_approval(tmp_path):
    a = auth()
    tok = a.issue("w1", "WRITE_WORKSPACE", task_id="t1", scope="workspace")
    with pytest.raises(TokenError, match="approval"):
        a.verify(tok, worker_id="w1", capability="WRITE_WORKSPACE",
                 task_id="t1")            # onaysız RED
    a.approve("w1", "WRITE_WORKSPACE")    # yalnız supervisor API
    body = a.verify(tok, worker_id="w1", capability="WRITE_WORKSPACE",
                    task_id="t1")
    assert body["risk_level"] == "HIGH"


def test_worker_cannot_self_approve(tmp_path):
    """approve() authority nesnesinde; worker erişimi YOKTUR (API yüzeyi
    yok). Onaysız HIGH capability her zaman RED — kanıt:"""
    a = auth()
    tok = a.issue("w1", "WRITE_WORKSPACE", task_id="t1", scope="workspace")
    # worker tarafında approve çağrısı bulunmaz; onaysız deneme:
    with pytest.raises(TokenError):
        a.verify(tok, worker_id="w1", capability="WRITE_WORKSPACE",
                 task_id="t1")


def test_revoked_token_rejected(tmp_path):
    a = auth()
    tok = a.issue("w1", "FETCH", task_id="t1", scope="network")
    token_id = tok.split(".")[1]
    a.revoke(token_id)
    with pytest.raises(TokenError, match="revoked"):
        a.verify(tok, worker_id="w1", capability="FETCH", task_id="t1")


def test_delegation_audit_trail(tmp_path):
    a = auth()
    a.issue("w1", "WEB_SEARCH", task_id="t1", scope="network")
    trail = a.audit_trail()
    assert len(trail) == 1
    assert trail[0]["delegated_by"] == "supervisor"
    assert trail[0]["worker_id"] == "w1"


def test_secret_never_embedded_in_token(tmp_path):
    a = auth()
    tok = a.issue("w1", "WEB_SEARCH", task_id="t1", scope="network")
    # anahtar token metninde görünmez
    assert a.secret.hex() not in tok
    assert a.secret not in tok.encode()
