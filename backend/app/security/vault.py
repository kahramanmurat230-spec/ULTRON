"""Credential Vault — encrypted secret storage with redaction integration.

- Secrets (API keys, tokens, passwords) are NEVER stored in plaintext:
  storage is data/vault/secrets.json with Fernet-encrypted values.
- Key material: env ULTRON_VAULT_KEY (urlsafe base64) or an auto-generated
  key file data/vault/.vault.key with 0600 permissions. No hardcoded keys.
- Secrets never enter: prompts, logs, audit, tool output. `redact()`
  combines pattern masking with concrete stored values; the server wires
  this into AuditLog, memory and agent tool-result messages.
- There is deliberately NO API to read a secret's value back over HTTP —
  connectors resolve credentials server-side at call time only.
"""
import base64
import json
import os
import time
from pathlib import Path

from app.security.redaction import redact as _pattern_redact

try:
    from cryptography.fernet import Fernet, InvalidToken
    HAVE_CRYPTO = True
except Exception:  # pragma: no cover
    HAVE_CRYPTO = False


class VaultUnavailable(RuntimeError):
    pass


class CredentialVault:
    def __init__(self, dir_path="data/vault", key_env="ULTRON_VAULT_KEY", audit=None):
        self.dir = Path(dir_path)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.store_path = self.dir / "secrets.json"
        self.key_path = self.dir / ".vault.key"
        self.audit = audit
        if not HAVE_CRYPTO:
            self._fernet = None
            return
        key = os.environ.get(key_env, "").strip()
        if key:
            self._fernet = Fernet(key.encode())
        else:
            self._fernet = Fernet(self._load_or_create_key())

    # ------------------------------------------------------------ key
    def _load_or_create_key(self):
        if self.key_path.exists():
            return self.key_path.read_text(encoding="utf-8").strip()
        key = Fernet.generate_key().decode("utf-8")
        self.key_path.write_text(key, encoding="utf-8")
        try:
            os.chmod(self.key_path, 0o600)
        except OSError:
            pass
        return key

    def _require(self):
        if not HAVE_CRYPTO or self._fernet is None:
            raise VaultUnavailable(
                "Credential vault kullanılamıyor (cryptography kurulu değil veya anahtar yok).")

    # ------------------------------------------------------------ ops
    def _load(self) -> dict:
        if not self.store_path.exists():
            return {}
        try:
            return json.loads(self.store_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save(self, data: dict) -> None:
        tmp = self.store_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        os.replace(tmp, self.store_path)
        try:
            os.chmod(self.store_path, 0o600)
        except OSError:
            pass

    def set(self, name: str, value: str, meta: str = "") -> dict:
        self._require()
        name = str(name).strip()
        if not name or not value:
            raise ValueError("name and value required")
        data = self._load()
        data[name] = {"cipher": self._fernet.encrypt(str(value).encode()).decode(),
                      "meta": str(meta)[:100], "ts": time.time()}
        self._save(data)
        if self.audit:
            self.audit.write("VAULT_SET", f"name={name} meta={meta[:40]}")  # value NEVER logged
        return {"ok": True, "name": name}

    def get(self, name: str) -> str | None:
        """Server-side only. There is no HTTP endpoint that returns values."""
        self._require()
        entry = self._load().get(str(name))
        if not entry:
            return None
        try:
            return self._fernet.decrypt(entry["cipher"].encode()).decode()
        except InvalidToken:
            return None

    def delete(self, name: str) -> bool:
        data = self._load()
        if str(name) in data:
            del data[str(name)]
            self._save(data)
            if self.audit:
                self.audit.write("VAULT_DELETE", f"name={name}")
            return True
        return False

    def list_names(self) -> list[dict]:
        return [{"name": n, "meta": e.get("meta", ""), "ts": e.get("ts")}
                for n, e in sorted(self._load().items())]

    def all_values(self) -> tuple:
        """Concrete secret values — used ONLY to seed redaction. Server-side."""
        if not HAVE_CRYPTO or self._fernet is None:
            return ()
        out = []
        for e in self._load().values():
            try:
                v = self._fernet.decrypt(e["cipher"].encode()).decode()
                if len(v) >= 4:
                    out.append(v)
            except Exception:
                continue
        return tuple(out)

    def redact(self, text: str) -> str:
        """Pattern redaction + concrete stored secret values."""
        return _pattern_redact(text, extra_values=self.all_values())

    def health(self) -> dict:
        return {"available": bool(HAVE_CRYPTO and self._fernet),
                "entries": len(self._load()),
                "key_source": "env" if os.environ.get("ULTRON_VAULT_KEY") else
                              ("keyfile" if HAVE_CRYPTO else None)}
