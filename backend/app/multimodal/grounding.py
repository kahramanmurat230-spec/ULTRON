"""WAVE 4 — OCR + UI anlama + görsel grounding (§9-11).

Zincir (§10,coordinate SON fallback):
  UIA → Window metadata → DOM → Vision → OCR → Coordinate

Grounding (§11): "mavi butona tıkla" → TEXT → UI ELEMENT → BOUNDING BOX →
CONFIDENCE → ACTION. Coordinate körü körüne KULLANILMAZ; her hedef
element identity (kaynak + id) + confidence taşır. Eşleşme yoksa None —
TAHMİN YOK.

OCR (§9): mevcut app/vision/analyze.ocr_elements (pytesseract) yeniden
kullanılır; binary yoksa dürüst unavailable. OCR metni DATA'dır — asla
instruction değil (bkz. security katmanı).
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

# kaynak güven sıralaması (§10 zinciri)
SOURCE_RANK = {
    "uia": 0.95, "window": 0.85, "dom": 0.90, "vision": 0.75,
    "ocr": 0.65, "coordinate": 0.20,
}

_TR_MAP = str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU")


def norm_text(s: str) -> str:
    """TR-normalize eşleşme: accentsiz, küçük harf, boşluk düzelt."""
    return " ".join((s or "").translate(_TR_MAP).lower().split())


# renk adları → RGB yaklaşık aralıkları (grounding için)
_COLOR_RGB = {
    "mavi": (40, 90, 220), "blue": (40, 90, 220),
    "kırmızı": (220, 50, 50), "red": (220, 50, 50),
    "yeşil": (50, 180, 80), "green": (50, 180, 80),
    "sarı": (230, 220, 60), "yellow": (230, 220, 60),
    "turuncu": (240, 150, 40), "orange": (240, 150, 40),
    "mor": (150, 60, 200), "purple": (150, 60, 200),
    "siyah": (30, 30, 30), "black": (30, 30, 30),
    "beyaz": (240, 240, 240), "white": (240, 240, 240),
    "gri": (140, 140, 140), "gray": (140, 140, 140),
}


@dataclass
class UIElement:
    """Birleştirilmiş UI hedefi — her kaynak aynı şemaya düşer."""
    identity: str                       # kaynak-içi benzersiz kimlik
    name: str                           # görünür metin/ad
    role: str                           # button/link/input/text/...
    bbox: tuple[int, int, int, int]     # x, y, w, h
    source: str                         # uia/window/dom/vision/ocr/coordinate
    confidence: float                   # kaynağın güveni × eşleşme gücü
    visibility: str = "visible"         # visible|partial|hidden
    enabled: bool = True
    attributes: dict = field(default_factory=dict)
    frame_id: str | None = None         # stale denetimi için
    captured_at: float = 0.0

    def center(self) -> tuple[int, int]:
        x, y, w, h = self.bbox
        return (x + w // 2, y + h // 2)

    def to_dict(self) -> dict:
        return {"identity": self.identity, "name": self.name,
                "role": self.role, "bbox": list(self.bbox),
                "source": self.source, "confidence": round(self.confidence, 3),
                "visibility": self.visibility, "enabled": self.enabled,
                "frame_id": self.frame_id}


class ProviderUnavailable(RuntimeError):
    """Katman gerçek capability gerektiriyor ve bu ortamda yok."""


# ---------------------------------------------------------------- providers
class UIAProvider:
    """Windows UI Automation (§10 birincil) — comtypes gerçek UIA.

    Linux/headless'ta dürüst unavailable. elemanlar: role/name/bbox/enabled.
    """

    NAME = "uia"

    def __init__(self):
        self.error = None
        try:
            import comtypes  # noqa: F401
            import sys
            if not sys.platform.startswith("win"):
                raise OSError("UIA yalnız Windows")
        except Exception as exc:  # noqa: BLE001
            self.error = f"UIA yok ({exc})"
            self.available = False
        else:
            self.available = True

    def status(self) -> dict:
        return {"provider": self.NAME, "available": self.available,
                "error": self.error}

    def elements(self) -> list[UIElement]:
        raise ProviderUnavailable(
            f"UIA bu ortamda yok: {self.error}")  # gerçek Windows'ta doldurulur


class WindowMetadataProvider:
    """Pencere başlıkları (pygetwindow) — ikinci katman."""

    NAME = "window"

    def __init__(self):
        self.error = None
        try:
            import pygetwindow  # noqa: F401
        except Exception as exc:  # noqa: BLE001
            self.error = f"pygetwindow yok ({exc})"
            self.available = False
        else:
            self.available = True

    def status(self) -> dict:
        return {"provider": self.NAME, "available": self.available,
                "error": self.error}

    def elements(self) -> list[UIElement]:
        raise ProviderUnavailable(f"pencere API yok: {self.error}")


class OCRProvider:
    """Mevcut analyze.ocr_elements köprüsü (§9) — pytesseract gerçek."""

    NAME = "ocr"

    def __init__(self, image_path=None):
        self.image_path = image_path
        try:
            import pytesseract
            pytesseract.get_tesseract_version()   # binary de gerçek olsun
        except Exception as exc:  # noqa: BLE001
            self.available = False
            self.error = f"tesseract yok ({str(exc)[:80]})"
        else:
            self.available = True
            self.error = None

    def status(self) -> dict:
        return {"provider": self.NAME, "available": self.available,
                "error": self.error}

    def elements(self, image_bytes: bytes | None = None,
                 min_conf: int = 40) -> list[UIElement]:
        """Gerçek OCR: görüntüyü (path/bytes) analiz eder → UIElement."""
        import io
        import tempfile
        from pathlib import Path
        from app.vision.analyze import ocr_elements, VisionFoundationError
        if image_bytes is not None:
            tmp = Path(tempfile.mkstemp(suffix=".png")[1])
            tmp.write_bytes(image_bytes)
            path = str(tmp)
        else:
            path = self.image_path
        if not path:
            raise ProviderUnavailable("OCR görüntüsü verilmedi")
        try:
            els = ocr_elements(path, min_conf=min_conf)
        except (VisionFoundationError, Exception) as exc:  # noqa: BLE001
            raise ProviderUnavailable(f"OCR başarısız: {exc}") from exc
        out = []
        for e in els:
            out.append(UIElement(
                identity=f"ocr:{e['text'][:32]}@{e['box']}",
                name=e["text"], role="text",
                bbox=tuple(e["box"]), source="ocr",
                confidence=float(e.get("confidence", 0.5)) / 100.0
                * SOURCE_RANK["ocr"],
                attributes={"ocr_conf": e.get("confidence")},
                captured_at=time.time()))
        return out


# ---------------------------------------------------------------- hierarchy
class UIHierarchy:
    """Katman zinciri: ilk gerçek veren kaynak kullanılır (§10 sırası)."""

    def __init__(self, providers: list | None = None):
        self.providers = providers if providers is not None else [
            UIAProvider(), WindowMetadataProvider()]
        self.last_source: str | None = None

    def status(self) -> dict:
        return {"providers": [p.status() for p in self.providers],
                "available": any(getattr(p, "available", False)
                                 for p in self.providers)}

    def elements(self, **kw) -> list[UIElement]:
        errors = []
        for p in self.providers:
            if not getattr(p, "available", False):
                continue
            try:
                els = p.elements(**kw)
                self.last_source = getattr(p, "NAME", type(p).__name__)
                return els
            except ProviderUnavailable as exc:
                errors.append(str(exc))
        raise ProviderUnavailable(
            "UI anlama katmanlarının hiçbiri kullanılamıyor: "
            + " | ".join(errors[:3]))


# ---------------------------------------------------------------- grounding
@dataclass
class GroundedTarget:
    element: UIElement
    query: str
    match_kind: str                     # exact | partial | color | coordinate
    alternatives: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"element": self.element.to_dict(), "query": self.query,
                "match_kind": self.match_kind,
                "bbox": list(self.element.bbox),
                "center": list(self.element.center()),
                "confidence": round(self.element.confidence, 3),
                "action_hint": f"click@{self.element.center()}",
                "alternatives": [a.to_dict() for a in self.alternatives]}


# sıfat→tür çıkarımı (basit TR/EN)
_TYPE_HINTS = {"buton": "button", "button": "button", "link": "link",
               "bağlantı": "link", "alan": "input", "kutusu": "input",
               "input": "input", "sekme": "tab", "tab": "tab",
               "menü": "menu", "menu": "menu"}


def _parse_query(query: str) -> dict:
    """'mavi butona tıkla' → {color, type, text} — saf dil bilgisiz ayrım."""
    q = norm_text(query)
    color = next((c for c in _COLOR_RGB if c in q), None)
    etype = next((t for h, t in _TYPE_HINTS.items() if h in q), None)
    return {"raw": query, "norm": q, "color": color, "type": etype}


def ground(query: str, elements: list[UIElement],
           *, min_confidence: float = 0.25) -> GroundedTarget | None:
    """Metin/renk/tür sorgusu → element + bbox + confidence.

    Coordinate körü körüne KULLANILMAZ: coordinate kaynaklı elementler
    yalnız açıkça verilmişse ve düşük güvenle eşleşir.
    Eşleşme yoksa None — tahmin YOK.
    """
    if not elements:
        return None
    parsed = _parse_query(query)
    qtext = parsed["norm"]
    # sorgudan tür/sıfat kelimelerini çıkar → kalan = aranan metin
    leftover = qtext
    for w in list(_TYPE_HINTS) + list(_COLOR_RGB):
        leftover = leftover.replace(w, " ")
    leftover = norm_text(re.sub(r"\b(tikla|tıkla|click|ac|aç|kapat|bas)\b",
                                " ", leftover))
    scored: list[tuple[float, UIElement, str]] = []
    for el in elements:
        name = norm_text(el.name)
        if not name:
            continue
        score = 0.0
        kind = ""
        if len(leftover) >= 2 and (leftover == name or leftover in name
                                   or name in leftover):
            score = 1.0 if leftover == name else 0.7
            kind = "exact" if score == 1.0 else "partial"
        elif parsed["color"]:
            # renk grounding: element bbox'ının görüntüdeki baskın rengi
            dom = el.attributes.get("dominant_rgb")
            if dom and _color_close(dom, _COLOR_RGB[parsed["color"]]):
                score = 0.6
                kind = "color"
        if score <= 0:
            continue
        if parsed["type"] and parsed["type"] == el.role:
            score = min(1.0, score + 0.1)
        total = score * SOURCE_RANK.get(el.source, 0.5)
        scored.append((total, el, kind))
    if not scored:
        return None
    scored.sort(key=lambda t: t[0], reverse=True)
    best_score, best, kind = scored[0]
    if best_score < min_confidence:
        return None                     # düşük güven → hedefleme RED
    return GroundedTarget(element=best, query=query, match_kind=kind,
                          alternatives=[el for _, el, _ in scored[1:3]])


def _color_close(a: tuple, b: tuple, tol: int = 90) -> bool:
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5 <= tol


def dominant_rgb(image_bytes: bytes, bbox=None) -> tuple | None:
    """Görüntü (veya bbox bölgesi) baskın rengi — grounding için gerçek
    görüntü analizi (PIL); hata → None (tahmin yok)."""
    import io
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        if bbox:
            x, y, w, h = bbox
            img = img.crop((x, y, x + w, y + h))
        img = img.resize((8, 8))
        px = list(img.getdata())
        r = sum(p[0] for p in px) // len(px)
        g = sum(p[1] for p in px) // len(px)
        b = sum(p[2] for p in px) // len(px)
        return (r, g, b)
    except Exception:  # noqa: BLE001
        return None
