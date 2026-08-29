"""PHASE 1 security: audit-log secret redaction + sovereign TTS locality."""
from app.security.audit import AuditLog
from app.security.redaction import redact, SECRET_VALUE_PATTERNS
from app.security import sovereign_privacy as sov
from health import tts_backend_name


def test_redact_masks_password_pairs():
    line = "user said password=hunter2 please"
    out = redact(line)
    assert "hunter2" not in out
    assert "password=" in out or "password" in out


def test_redact_masks_tokens_and_api_keys():
    for raw in (
        "api_key: sk-1234567890abcdef",
        "token=abc123def456ghi789",
        "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig",
        "secret 'supersecretvalue99'",
    ):
        out = redact(raw)
        for frag in ("1234567890abcdef", "abc123def456ghi789", "eyJhbGciOiJIUzI1NiJ9", "supersecretvalue99"):
            assert frag not in out, f"leaked {frag} from {raw!r}"


def test_redact_masks_extra_values():
    out = redact("connect with MYVaultValue123", extra_values=("MYVaultValue123",))
    assert "MYVaultValue123" not in out


def test_redact_keeps_normal_text():
    assert redact("chrome aç ve sonra sistem durumu") == "chrome aç ve sonra sistem durumu"


def test_audit_log_writes_redacted(tmp_path):
    log = AuditLog(path=tmp_path / "a.log")
    log.write("USER", "not al: şifrem gizli-sifre-42 sakla")
    content = (tmp_path / "a.log").read_text(encoding="utf-8")
    assert "gizli-sifre-42" not in content
    assert "USER" in content


def test_sovereign_tts_locality_is_honest():
    # edge-tts is a MICROSOFT CLOUD service -> must NEVER be reported local.
    a = sov.audit("http://127.0.0.1:11434", "edge-tts-neural", False, True)
    assert a["tts"]["local"] is False
    # a truly local engine is reported local
    b = sov.audit("http://127.0.0.1:11434", "kokoro", False, True)
    assert b["tts"]["local"] is True
    # none installed -> not local, honest
    c = sov.audit("http://127.0.0.1:11434", None, False, False)
    assert c["tts"]["local"] is False


def test_sovereign_status_partial_with_cloud_tts():
    a = sov.audit("http://127.0.0.1:11434", "edge-tts-neural", False, True)
    assert sov.sovereign_status(a) == "PARTIAL"


def test_secret_patterns_not_empty():
    assert len(SECRET_VALUE_PATTERNS) >= 3
