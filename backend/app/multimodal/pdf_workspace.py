"""Safe PDF/document workspace.

PDF bytes and extracted text are untrusted DATA. This module never executes
content found in a document and never treats document instructions as policy.
All file access is constrained to an explicit workspace root.
"""
from __future__ import annotations

import hashlib
import re
import shutil
import time
from pathlib import Path
from typing import Any


_INJECTION_PATTERNS = (
    r"ignore\s+(?:all\s+)?previous\s+instructions",
    r"disregard\s+(?:all\s+)?previous\s+instructions",
    r"system\s+prompt",
    r"developer\s+message",
    r"(?:run|execute)\s+(?:this|the)\s+(?:command|script)",
    r"(?:call|invoke)\s+(?:the\s+)?(?:tool|function)",
    r"grant\s+(?:yourself|me)\s+(?:permission|access)",
)


class PDFWorkspace:
    def __init__(self, workspace_root: str | Path | None = None) -> None:
        self.workspace_root = Path(workspace_root or Path.cwd()).resolve()
        self._index: dict[str, list[dict[str, Any]]] = {}

    def _resolve_safe(self, path: str | Path) -> Path:
        root = self.workspace_root
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = root / candidate
        resolved = candidate.resolve()
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise ValueError("path outside PDF workspace sandbox") from exc
        return resolved

    def _reader(self, path: Path):
        try:
            from pypdf import PdfReader  # type: ignore
        except Exception as exc:
            raise RuntimeError(f"pypdf unavailable: {exc}") from exc
        return PdfReader(str(path))

    def status(self) -> dict[str, Any]:
        try:
            import pypdf  # type: ignore
            pdf = {"available": True, "backend": "pypdf", "version": getattr(pypdf, "__version__", "unknown")}
        except Exception as exc:
            pdf = {"available": False, "backend": None, "reason": f"pypdf unavailable: {exc}"}
        try:
            import fitz  # type: ignore
            ocr = {"available": True, "backend": "pymupdf+local-tesseract"}
        except Exception as exc:
            ocr = {"available": False, "backend": None, "reason": f"OCR renderer unavailable: {exc}"}
        return {"available": bool(pdf["available"]), "backend": pdf.get("backend"), "version": pdf.get("version"), "ocr": ocr}

    def inspect(self, path: str | Path) -> dict[str, Any]:
        try:
            p = self._resolve_safe(path)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        if not p.is_file() or p.suffix.lower() != ".pdf":
            return {"ok": False, "error": "path is not a PDF file"}
        try:
            reader = self._reader(p)
            return {"ok": True, "path": str(p), "pages": len(reader.pages), "metadata": dict(reader.metadata or {})}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def extract_text(self, path: str | Path, max_pages: int = 50, ocr_fallback: bool = True) -> dict[str, Any]:
        try:
            p = self._resolve_safe(path)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        if not p.is_file() or p.suffix.lower() != ".pdf":
            return {"ok": False, "error": "path is not a PDF file"}
        try:
            reader = self._reader(p)
            pages = max(0, min(int(max_pages), len(reader.pages)))
            page_texts = [(reader.pages[i].extract_text() or "") for i in range(pages)]
            text = "\n\n".join(page_texts)
            ocr_used = False
            if ocr_fallback and not text.strip():
                ocr = self._ocr_pages(p, pages)
                if ocr:
                    page_texts = ocr
                    text = "\n\n".join(ocr)
                    ocr_used = True
            return {
                "ok": True,
                "path": str(p),
                "pages_read": pages,
                "text": text,
                "ocr_used": ocr_used,
                "content_trust": "untrusted_data",
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def _ocr_pages(self, path: Path, pages: int) -> list[str]:
        try:
            import fitz  # type: ignore
            import pytesseract  # type: ignore
            from PIL import Image  # type: ignore
        except Exception:
            return []
        result: list[str] = []
        doc = fitz.open(str(path))
        try:
            for i in range(min(pages, len(doc))):
                pix = doc[i].get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
                image = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                try:
                    result.append(pytesseract.image_to_string(image))
                except Exception:
                    result.append("")
        finally:
            doc.close()
        return result

    def ingest(self, path: str | Path, chunk_size: int = 1200, overlap: int = 150, max_pages: int = 50) -> dict[str, Any]:
        """Extract and index a document inside the sandbox; content stays data-only."""
        extracted = self.extract_text(path, max_pages=max_pages, ocr_fallback=True)
        if not extracted.get("ok"):
            return extracted
        text = str(extracted.get("text", ""))
        chunk_size = max(100, int(chunk_size))
        overlap = max(0, min(int(overlap), chunk_size - 1))
        step = chunk_size - overlap
        chunks: list[dict[str, Any]] = []
        for start in range(0, len(text), step):
            chunk = text[start:start + chunk_size]
            if not chunk:
                break
            chunks.append({
                "chunk_id": hashlib.sha256(f"{extracted['path']}:{start}".encode()).hexdigest()[:16],
                "source": extracted["path"],
                "start": start,
                "end": start + len(chunk),
                "text": chunk,
                "content_trust": "untrusted_data",
                "contains_prompt_injection": self.contains_prompt_injection(chunk),
            })
        key = hashlib.sha256(str(extracted["path"]).encode()).hexdigest()
        self._index[key] = chunks
        return {**extracted, "document_id": key, "chunks": chunks, "indexed_at": time.time()}

    def search(self, document_id: str, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """Deterministic lexical retrieval; results remain untrusted DATA."""
        chunks = self._index.get(str(document_id), [])
        terms = {t.lower() for t in re.findall(r"\w+", str(query)) if len(t) > 1}
        ranked = []
        for chunk in chunks:
            words = {t.lower() for t in re.findall(r"\w+", chunk["text"])}
            score = len(terms & words)
            if score:
                ranked.append((score, chunk))
        ranked.sort(key=lambda item: (-item[0], item[1]["start"]))
        return [{**chunk, "score": score} for score, chunk in ranked[: max(1, int(limit))]]

    @staticmethod
    def contains_prompt_injection(text: str) -> bool:
        value = str(text)
        return any(re.search(pattern, value, re.IGNORECASE) for pattern in _INJECTION_PATTERNS)

    def copy_into_workspace(self, source: str | Path, destination_name: str | None = None) -> dict[str, Any]:
        """Safely ingest an external PDF by copying it into the workspace first."""
        src = Path(source).resolve()
        if not src.is_file() or src.suffix.lower() != ".pdf":
            return {"ok": False, "error": "source is not a PDF file"}
        target = self._resolve_safe(destination_name or src.name)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)
        return {"ok": True, "path": str(target)}
