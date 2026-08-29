"""PHASE 1/10: session expiry + per-client rate limiting."""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + "/..")
from auth import Auth  # noqa: E402
from app.security.rate_limit import RateLimiter, TokenBucket  # noqa: E402


# ------------------------------------------------------------ session expiry
def test_session_expires_after_ttl(tmp_path, monkeypatch):
    monkeypatch.setenv("ULTRON_SESSION_TTL_S", "2")
    monkeypatch.setenv("ULTRON_AUTH", "1")
    monkeypatch.setenv("ULTRON_PAIRING_SECRET", "s3cret")
    a = Auth(Path(tmp_path) / "s.json", required=True)
    tok = a.handshake("phone", "s3cret")["token"]
    assert a.valid(tok) is True
    time.sleep(2.2)
    assert a.valid(tok) is False  # süresiz oturum yok


def test_session_sliding_renewal(tmp_path, monkeypatch):
    monkeypatch.setenv("ULTRON_SESSION_TTL_S", "3")
    monkeypatch.setenv("ULTRON_PAIRING_SECRET", "")
    a = Auth(Path(tmp_path) / "s.json", required=True)
    tok = a.handshake("laptop")["token"]
    for _ in range(4):
        time.sleep(1.5)          # TTL'nin yarısı kadar bekle + kullan
        assert a.valid(tok) is True  # kayan pencere: süre yenileniyor


def test_sessions_persist_across_restart(tmp_path, monkeypatch):
    monkeypatch.setenv("ULTRON_SESSION_TTL_S", "3600")
    monkeypatch.setenv("ULTRON_PAIRING_SECRET", "")
    p = Path(tmp_path) / "s.json"
    a1 = Auth(p, required=True)
    tok = a1.handshake("phone")["token"]
    a2 = Auth(p, required=True)  # restart
    assert a2.valid(tok) is True


# ------------------------------------------------------------ rate limit
def test_token_bucket_burst_then_throttle():
    b = TokenBucket(5, 1.0, now=0.0)
    allowed = [b.take(now=0.0) for _ in range(5)]
    assert all(allowed)
    assert b.take(now=0.0) is False          # burst tükendi
    assert b.take(now=1.0) is True           # 1s -> 1 token doldu
    assert b.take(now=1.0) is False


def test_rate_limiter_paths_and_auth_strictness():
    rl = RateLimiter({"security": {"rate_limit": {
        "capacity": 3, "refill_per_s": 1.0, "auth_capacity": 2,
        "auth_refill_per_s": 0.1}}})
    # normal path: 3 istek sonra 4.
    ok = [rl.check("1.2.3.4", "/api/system", now=0.0)[0] for _ in range(4)]
    assert ok == [True, True, True, False]
    # handshake daha sıkı: 2 istek sonra blok
    ok2 = [rl.check("9.9.9.9", "/api/auth/handshake", now=0.0)[0] for _ in range(3)]
    assert ok2 == [True, True, False]
    # farklı istemci etkilenmez
    assert rl.check("5.5.5.5", "/api/system", now=0.0)[0] is True
    # retry_after pozitif
    allowed, retry = rl.check("9.9.9.9", "/api/auth/handshake", now=0.0)
    assert allowed is False and retry >= 0
