"""WAVE 4 / OCR+UI grounding: zincir, element şeması, metin/renk hedefleme,
coordinate son fallback, dürüst unavailable."""
import io
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.multimodal.grounding import (  # noqa: E402
    OCRProvider, UIAProvider, UIElement, UIHierarchy, WindowMetadataProvider,
    _parse_query, dominant_rgb, ground, norm_text,
)
from app.multimodal.vision_runtime import CaptureUnavailable  # noqa: E402


def jpeg_with(color, size=(320, 180)):
    from PIL import Image
    img = Image.new("RGB", size, color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


class FakeProvider:
    """DI seam — gerçek UIA/DOM yerine sabit eleman listesi (zincir ve
    grounding mantığı test edilir; providers gerçek sistemde connect)."""

    def __init__(self, name, elements, available=True):
        self.NAME = name
        self._elements = elements
        self.available = available

    def status(self):
        return {"provider": self.NAME, "available": self.available}

    def elements(self, **kw):
        if not self.available:
            from app.multimodal.grounding import ProviderUnavailable
            raise ProviderUnavailable(f"{self.NAME} unavailable")
        return self._elements


# ---------------------------------------------------------------- providers
def test_uia_unavailable_on_linux_honest():
    p = UIAProvider()
    st = p.status()
    assert st["available"] is False and st["error"]
    with pytest.raises(Exception):
        p.elements()


def test_window_provider_honest_without_pygetwindow():
    p = WindowMetadataProvider()
    assert p.status()["available"] is False   # pygetwindow kurulu değil


def test_ocr_provider_honest_without_tesseract():
    p = OCRProvider()
    assert p.status()["available"] is False
    from app.multimodal.grounding import ProviderUnavailable
    with pytest.raises(ProviderUnavailable):
        p.elements(image_bytes=b"not-an-image")


def test_hierarchy_falls_through_chain_in_order():
    """UIA → window → dom: ilk ÇALIŞAN kazanır (sıra §10)."""
    dom = FakeProvider("dom", [UIElement("d1", "Kaydet", "button",
                                          (10, 10, 80, 30), "dom", 0.9)])
    h = UIHierarchy([UIAProvider(), WindowMetadataProvider(), dom])
    els = h.elements()
    assert els[0].name == "Kaydet" and h.last_source == "dom"


def test_hierarchy_all_unavailable_raises():
    h = UIHierarchy([FakeProvider("a", [], available=False),
                     FakeProvider("b", [], available=False)])
    from app.multimodal.grounding import ProviderUnavailable
    with pytest.raises(ProviderUnavailable):
        h.elements()
    assert h.status()["available"] is False


# ---------------------------------------------------------------- elements
def test_element_schema_and_center():
    el = UIElement("x1", "Tamam", "button", (100, 50, 40, 20), "uia", 0.95)
    d = el.to_dict()
    for k in ("identity", "name", "role", "bbox", "source", "confidence"):
        assert k in d
    assert el.center() == (120, 60)


# ---------------------------------------------------------------- grounding
def test_ground_exact_text_with_confidence():
    els = [UIElement("b1", "Kaydet", "button", (10, 10, 80, 30), "uia", 1.0),
           UIElement("b2", "İptal", "button", (10, 50, 80, 30), "uia", 1.0)]
    t = ground("Kaydet'e tıkla", els)
    assert t is not None
    assert t.element.identity == "b1"
    assert t.match_kind in ("exact", "partial")
    assert t.element.confidence > 0.3
    assert t.to_dict()["action_hint"].startswith("click@(")


def test_ground_turkish_normalized():
    els = [UIElement("b1", "ÇIKIŞ", "button", (0, 0, 60, 30), "uia", 1.0)]
    t = ground("çıkış butonuna bas", els)     # aksan/büyük-küçük farkı
    assert t is not None and t.element.identity == "b1"


def test_ground_color_uses_real_image():
    img = jpeg_with((30, 80, 230))            # gerçek mavi görüntü
    dom = dominant_rgb(img, (0, 0, 320, 180))
    assert dom is not None and dom[2] > dom[0]   # baskın mavi (gerçek analiz)
    els = [UIElement("c1", "Onay", "button", (0, 0, 320, 180), "vision",
                     0.9, attributes={"dominant_rgb": dom}),
           UIElement("c2", "Onay", "button", (0, 0, 10, 10), "vision", 0.9,
                     attributes={"dominant_rgb": (230, 40, 40)})]
    t = ground("mavi butona tıkla", els)
    assert t is not None and t.element.identity == "c1"
    assert t.match_kind == "color"


def test_ground_no_match_returns_none_no_guess():
    els = [UIElement("b1", "Kaydet", "button", (0, 0, 1, 1), "uia", 1.0)]
    assert ground("sil butonuna bas", els) is None
    assert ground("x", []) is None


def test_ground_low_confidence_rejected():
    # coordinate kaynak → rank 0.2 × kısmi 0.7 = 0.14 < 0.25 eşik
    els = [UIElement("p1", "Herhangi", "unknown", (5, 5, 5, 5),
                     "coordinate", 0.5)]
    assert ground("herhangi", els) is None   # kör coordinate RED


def test_ground_alternatives_reported():
    els = [UIElement("a", "Dosya", "button", (0, 0, 1, 1), "uia", 1.0),
           UIElement("b", "Dosya", "menu", (0, 30, 1, 1), "uia", 1.0),
           UIElement("c", "Dosyalar", "link", (0, 60, 1, 1), "uia", 1.0)]
    t = ground("Dosya", els)
    assert t.element.identity == "a"
    assert len(t.alternatives) == 2          # belirsizlik görünür


def test_parse_query_extracts_color_and_type():
    p = _parse_query("Mavi BUTONA tıkla")
    assert p["color"] == "mavi"
    assert p["type"] == "button"
    assert norm_text("  ÇIKIŞ  ") == "cikis"


def test_dominant_rgb_fails_honest():
    assert dominant_rgb(b"garbage") is None  # hata → tahmin yok
