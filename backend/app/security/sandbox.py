"""Filesystem sandbox — allowed-roots enforcement for agent file access.

Blocks, for both read and write:
  - path traversal via ../ (resolved before checking)
  - absolute escape outside allowed roots
  - UNC paths (\\\\server\\share) — always rejected
  - symlink escape (final realpath must stay inside a root)

System directories are never writable. Roots come from
settings.security.filesystem (allowed_read_roots / allowed_write_roots);
defaults: read = workspace + home, write = workspace only.
"""
import os
from pathlib import Path


class SandboxViolation(PermissionError):
    pass


class FilesystemSandbox:
    def __init__(self, settings=None, workspace_root=None):
        settings = settings or {}
        cfg = settings.get("security", {}).get("filesystem", {})
        home = Path.home()
        ws = Path(workspace_root or cfg.get("workspace_root") or Path.cwd()).resolve()
        self.read_roots = self._roots(cfg.get("allowed_read_roots"), [ws, home])
        self.write_roots = self._roots(cfg.get("allowed_write_roots"), [ws])

    @staticmethod
    def _roots(configured, defaults):
        if configured:
            return [Path(p).expanduser().resolve() for p in configured]
        return [Path(p).expanduser().resolve() for p in defaults]

    # ------------------------------------------------------------ checks
    @staticmethod
    def _is_unc(path: str) -> bool:
        return path.startswith("\\\\") or path.startswith("//")

    def _real(self, p: Path) -> Path:
        try:
            return Path(os.path.realpath(p))
        except Exception:
            return p.resolve(strict=False)

    def check(self, path, write: bool = False) -> Path:
        """Validate a path; returns the resolved path or raises SandboxViolation."""
        raw = str(path)
        if self._is_unc(raw):
            raise SandboxViolation(f"UNC path reddedildi: {raw[:80]}")
        p = Path(raw).expanduser()
        # traversal / absolute escape: resolve fully BEFORE the roots check
        rp = self._real(p if p.is_absolute() else (Path.cwd() / p))
        roots = self.write_roots if write else self.read_roots
        for root in roots:
            try:
                rp.relative_to(root)
                if write:
                    self._check_not_system(rp)
                return rp
            except ValueError:
                continue
        kind = "write" if write else "read"
        raise SandboxViolation(
            f"path {kind} outside allowed roots: {raw[:80]} (allowed: "
            f"{', '.join(str(r) for r in roots)})")

    def _check_not_system(self, rp: Path):
        system_dirs = ("/Windows", "/Program Files", "/Program Files (x86)",
                       "/ProgramData", "/System Volume Information")
        parts = [pt for pt in rp.parts]
        joined = "/".join(parts[:3]) if len(parts) >= 3 else str(rp)
        for sd in system_dirs:
            if joined.lower().startswith(sd.lower()):
                raise SandboxViolation(f"system directory write rejected: {rp}")

    def validate_read(self, path) -> Path:
        return self.check(path, write=False)

    def validate_write(self, path) -> Path:
        return self.check(path, write=True)


def sandboxed_read_text(sandbox, path):
    rp = sandbox.validate_read(path)
    return rp.read_text(encoding="utf-8", errors="replace")[:50000]


def sandboxed_write_text(sandbox, path, content):
    rp = sandbox.validate_write(path)
    rp.parent.mkdir(parents=True, exist_ok=True)
    rp.write_text(content, encoding="utf-8")
    return str(rp)


def sandboxed_list_directory(sandbox, root):
    rp = sandbox.validate_read(root)
    if not rp.exists():
        raise FileNotFoundError(str(rp))
    items = []
    for x in sorted(rp.iterdir(), key=lambda q: (not q.is_dir(), q.name.lower()))[:300]:
        items.append({"name": x.name, "type": "folder" if x.is_dir() else "file", "path": str(x)})
    return items


def sandboxed_find_files(sandbox, root, pattern):
    rp = sandbox.validate_read(root)
    if not rp.exists():
        raise FileNotFoundError(str(rp))
    out = []
    for p in rp.rglob(pattern):
        if not p.is_file():
            continue
        try:  # every hit must itself stay inside read roots (symlink escapes)
            sandbox.validate_read(p)
        except SandboxViolation:
            continue
        out.append(str(p))
        if len(out) >= 300:
            break
    return out
