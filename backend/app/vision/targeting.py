"""Vision 2.0 targeting helper.

LLaVA describes the screen; coordinate extraction is treated as UNRELIABLE.
If no confident numeric target can be parsed, the caller must use
WAITING_APPROVAL / confirmation instead of clicking blindly.
"""
import re

TARGET_PROMPT = ("Ekranda şu hedefi bul: '{target}'. "
                 "Önce hedefi Türkçe kısaca tanımla, sonra tahmini konumunu "
                 "'KONUM: x, y' formatında 0-100 normalize koordinat olarak yaz. "
                 "Emin değilsen 'KONUM: YOK' yaz.")


def build_prompt(target: str) -> str:
    return TARGET_PROMPT.format(target=target)


def parse_coords(analysis: str) -> tuple[float, float] | None:
    m = re.search(r"KONUM:\s*(\d{1,3})\s*,\s*(\d{1,3})", analysis or "", re.I)
    if not m:
        return None
    x, y = float(m.group(1)), float(m.group(2))
    if not (0 <= x <= 100 and 0 <= y <= 100):
        return None
    return x, y


def click_target(text: str) -> str | None:
    """'Şu ekrandaki kırmızı butona tıkla' → 'kırmızı buton' etc."""
    m = re.search(r"(?:ekrandaki|ekranda|şu)\s+([^|]+?)\s+(?:butonuna|butona|bağlantıya|link?e|öğeye|yere)?\s*(?:tıkla|bas|tıklar mısın)", text, re.I)
    if m:
        return m.group(1).strip()[:80]
    if re.search(r"\btıkla\b|\bbas\b", text, re.I) and re.search(r"ekran|buton|link|kırmızı|yeşil|mavi", text, re.I):
        return text.strip()[:80]
    return None
