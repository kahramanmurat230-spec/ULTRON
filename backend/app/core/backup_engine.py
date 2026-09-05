"""Backup Engine — timestamped, checksummed, rotated local backups.

Targets: memory/dna/metrics/persona DB'leri + master_rules.json + settings.json.
Rotation: son 7 yedek. Restore: SHA256 doğrulamalı, temp-çıkart + atomik taşıma.
Sovereign: yedekler asla yerel disk dışına çıkmaz.
"""
import hashlib
import json
import shutil
import tempfile
import time
import zipfile
from pathlib import Path

KEEP = 7


class BackupEngine:
    def __init__(self, root="backups", sources=None, keep=KEEP):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.sources = sources or []
        self.keep = keep
        self.index_path = self.root / "index.json"

    # ------------------------------------------------------------ helpers
    def _index(self) -> dict:
        if self.index_path.exists():
            try:
                return json.loads(self.index_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {}

    def _save_index(self, idx: dict) -> None:
        self.index_path.write_text(json.dumps(idx, indent=1), encoding="utf-8")

    # ------------------------------------------------------------ ops
    def create(self) -> dict:
        base = time.strftime("%Y%m%d-%H%M%S")
        idx = self._index()
        bid, n = base, 1
        while bid in idx:
            bid, n = f"{base}-{n}", n + 1
        zpath = self.root / f"ultron-{bid}.zip"
        h = hashlib.sha256()
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
            for name, path in self.sources:
                p = Path(path)
                if p.exists():
                    z.write(p, name)
        h.update(zpath.read_bytes())
        sha = h.hexdigest()
        idx[bid] = {"file": zpath.name, "sha256": sha, "ts": time.time(),
                    "size_kb": round(zpath.stat().st_size / 1024, 1)}
        # rotation: keep latest `keep`
        for old in sorted(idx, key=lambda k: idx[k]["ts"])[:-self.keep]:
            try:
                (self.root / idx[old]["file"]).unlink(missing_ok=True)
            except Exception:
                pass
            del idx[old]
        self._save_index(idx)
        return {"backup_id": bid, "size_kb": idx[bid]["size_kb"], "sha256": sha,
                "path": str(zpath)}

    def list(self) -> list:
        idx = self._index()
        return [{"backup_id": k, **v} for k, v in
                sorted(idx.items(), key=lambda kv: kv[1]["ts"])]

    def restore(self, backup_id: str) -> dict:
        idx = self._index()
        if backup_id not in idx:
            raise ValueError(f"unknown backup: {backup_id}")
        zpath = self.root / idx[backup_id]["file"]
        data = zpath.read_bytes()
        if hashlib.sha256(data).hexdigest() != idx[backup_id]["sha256"]:
            raise ValueError("checksum mismatch — restore reddedildi")
        restored = []
        tmpdir = Path(tempfile.mkdtemp(prefix="ultron-restore-"))
        try:
            with zipfile.ZipFile(zpath) as z:
                z.extractall(tmpdir)
            for name, path in self.sources:
                src = tmpdir / name
                if src.exists():
                    dst = Path(path)
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    tmp_out = dst.with_suffix(dst.suffix + ".restore")
                    shutil.copyfile(src, tmp_out)
                    tmp_out.replace(dst)  # atomic
                    restored.append(name)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
        return {"restored": restored, "backup_id": backup_id}
