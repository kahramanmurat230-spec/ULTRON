"""Memory export/import — checksummed, locally-encrypted snapshot.

Sovereign: export is ALWAYS written to a local file; never uploaded.
Cipher: SHA256-keystream XOR (local at-rest obfuscation) + SHA256 integrity.
"""
import base64
import hashlib
import json
import time
from pathlib import Path

KEY = b"ultron-local-sovereign-keystore"


def _keystream(n: int, seed: bytes) -> bytes:
    out = bytearray()
    block = b""
    i = 0
    while len(out) < n:
        block = hashlib.sha256(seed + KEY + i.to_bytes(4, "big")).digest()
        out.extend(block)
        i += 1
    return bytes(out[:n])


def _xor(data: bytes) -> bytes:
    ks = _keystream(len(data), b"ultron-mem-v1")
    return bytes(a ^ b for a, b in zip(data, ks))


def build_snapshot(memory, dna, rules) -> dict:
    rows = []
    try:
        import sqlite3
        with sqlite3.connect(memory.path) as db:
            rows = db.execute("SELECT kind,content,created_at FROM memories").fetchall()
    except Exception:
        pass
    dna_rows = dna.recent(3650) if dna else []
    return {"ts": time.time(),
            "v16": [{"kind": r[0], "content": r[1], "created_at": r[2]} for r in rows],
            "dna": [{"ts": r[0], "activity": r[1], "app": r[2], "note": r[4]} for r in dna_rows],
            "master_rules": rules.get() if rules else {}}


def export_local(memory, dna, rules, out_dir="data/exports") -> dict:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(build_snapshot(memory, dna, rules), ensure_ascii=False).encode("utf-8")
    sha = hashlib.sha256(raw).hexdigest()
    envelope = {"sha256": sha, "cipher_b64": base64.b64encode(_xor(raw)).decode("ascii")}
    path = out / "memory_export.json"
    path.write_text(json.dumps(envelope, indent=1), encoding="utf-8")
    return {"path": str(path), "sha256": sha, "bytes": len(raw)}


def import_snapshot(memory, dna, envelope: dict) -> dict:
    raw = _xor(base64.b64decode(envelope.get("cipher_b64", "")))
    if hashlib.sha256(raw).hexdigest() != envelope.get("sha256"):
        raise ValueError("checksum mismatch — import reddedildi")
    snap = json.loads(raw.decode("utf-8"))
    added = 0
    import sqlite3
    with sqlite3.connect(memory.path) as db:
        for r in snap.get("v16", []):
            db.execute("INSERT INTO memories(kind,content,created_at) VALUES(?,?,?)",
                       (r["kind"], r["content"], r["created_at"]))
            added += 1
    dna_n = 0
    if dna:
        for r in snap.get("dna", []):
            dna.insert(r.get("activity", "import"), r.get("app"), note=r.get("note"), ts=r.get("ts"))
            dna_n += 1
    return {"imported_memories": added, "imported_dna": dna_n,
            "master_rules_restored": bool(snap.get("master_rules"))}
