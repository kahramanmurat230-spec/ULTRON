"""Credential Vault — Fernet ile şifreli istirahat-at-rest secret deposu.

Sözleşme (test_sandbox_vault.py + connectors kullanımı):
- set(name, value, meta) → değer ASLA plaintext diskte durmaz (secrets.json
  yalnız Fernet token'ları içerir)
- get(name) → değer veya None; delete(name) → bool; list_names() → isimler
  (değerler ASLA açığa çıkmaz)
- redact(text) → depolanan DEĞERLER metinde geçiyorsa maskeler
- anahtar: ULTRON_VAULT_KEY env (öncelik) yoksa .vault.key (0o600) dosyası
"""
from __future__ import annotations

import json
import os
from pathlib import Path


class CredentialVault:
    KEY_FILE = ".vault.key"
    STORE_FILE = "secrets.json"

    def __init__(self, dir_path: str = "data/security/vault"):
        self.dir = Path(dir_path)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._fernet = self._load_or_create_key()

    # ------------------------------------------------------------ key
    def _load_or_create_key(self):
        from cryptography.fernet import Fernet
        env_key = os.environ.get("ULTRON_VAULT_KEY", "").strip()
        if env_key:
            return Fernet(env_key.encode("utf-8"))
        key_path = self.dir / self.KEY_FILE
        if key_path.exists():
            key = key_path.read_text(encoding="utf-8").strip()
        else:
            key = Fernet.generate_key().decode("utf-8")
            key_path.write_text(key, encoding="utf-8")
            try:
                os.chmod(key_path, 0o600)   # yalnız sahip okur
            except OSError:
                pass
        return Fernet(key.encode("utf-8"))

    # ------------------------------------------------------------ store
    def _store_path(self) -> Path:
        return self.dir / self.STORE_FILE

    def _read_store(self) -> dict:
        p = self._store_path()
        if not p.exists():
            return {"secrets": {}}
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — bozuk depo boş sayılır (güvenli taraf)
            return {"secrets": {}}

    def _write_store(self, data: dict) -> None:
        tmp = self._store_path().with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        os.replace(tmp, self._store_path())
        try:
            os.chmod(self._store_path(), 0o600)
        except OSError:
            pass

    # ------------------------------------------------------------ api
    def set(self, name: str, value: str, meta: str | None = None) -> None:
        if not name or value is None:
            raise ValueError("name/value zorunlu")
        data = self._read_store()
        token = self._fernet.encrypt(str(value).encode("utf-8")).decode(
            "utf-8")
        data["secrets"][name] = {"v": token, "meta": meta or ""}
        self._write_store(data)

    def get(self, name: str) -> str | None:
        entry = self._read_store()["secrets"].get(name)
        if not entry:
            return None
        try:
            return self._fernet.decrypt(entry["v"].encode("utf-8")
                                        ).decode("utf-8")
        except Exception:  # noqa: BLE001 — yanlış anahtar → yok say (RED tarafı)
            return None

    def delete(self, name: str) -> bool:
        data = self._read_store()
        if name in data["secrets"]:
            del data["secrets"][name]
            self._write_store(data)
            return True
        return False

    def list_names(self) -> list[str]:
        return sorted(self._read_store()["secrets"].keys())

    def redact(self, text: str) -> str:
        """Depolanan değerler metinde geçiyorsa maskeler (plaintext sızıntı
        önleme — secret değerler log/trace kanallarına düşmez)."""
        out = text or ""
        for name in self.list_names():
            val = self.get(name)
            if val:
                out = out.replace(val, "***REDACTED***")
        return out
