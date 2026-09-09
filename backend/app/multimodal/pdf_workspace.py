"""Safe PDF workspace adapter.

Provides real metadata/text extraction when pypdf is installed. It never
pretends a PDF backend exists when the dependency is missing.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


class PDFWorkspace:
    def _reader(self, path: str):
        try:
            from pypdf import PdfReader  # type: ignore
        except Exception as exc:
            raise RuntimeError(f"pypdf unavailable: {exc}") from exc
        return PdfReader(str(Path(path).resolve()))

    def status(self) -> dict[str, Any]:
        try:
            import pypdf  # type: ignore
            return {"available": True, "backend": "pypdf", "version": getattr(pypdf, "__version__", "unknown")}
        except Exception as exc:
            return {"available": False, "backend": None, "reason": f"pypdf unavailable: {exc}"}

    def inspect(self, path: str) -> dict[str, Any]:
        p = Path(path).resolve()
        if not p.is_file() or p.suffix.lower() != ".pdf":
            return {"ok": False, "error": "path is not a PDF file"}
        try:
            reader = self._reader(str(p))
            return {"ok": True, "path": str(p), "pages": len(reader.pages), "metadata": dict(reader.metadata or {})}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def extract_text(self, path: str, max_pages: int = 50) -> dict[str, Any]:
        p = Path(path).resolve()
        if not p.is_file() or p.suffix.lower() != ".pdf":
            return {"ok": False, "error": "path is not a PDF file"}
        try:
            reader = self._reader(str(p))
            pages = max(0, min(int(max_pages), len(reader.pages)))
            text = "\n\n".join((reader.pages[i].extract_text() or "") for i in range(pages))
            return {"ok": True, "path": str(p), "pages_read": pages, "text": text}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
