"""WAVE 3 — Kanal seviyesinde ikinci geçiş maskeleme.

Security core (app/security/redaction) DEĞİŞTİRİLMEZ; ancak bazı birleşik
ifadelerde (örn. "token password: gizli") core'un bearer-pattern'i öneki
maskeler, DEĞERİ bırakır. Wave 3 kanalları (message bus, council) kendi
payload'larında bilinen secret-anahtar kelimesini takip eden değeri ikinci
geçişte maskeler — defense in depth, core'a dokunulmadan.
"""
from __future__ import annotations

import re

_TRAILING_SECRET = re.compile(
    r"(password|passwd|secret|api[_-]?key|şifre|parola|token)\s*[:=]\s*\S+",
    re.IGNORECASE)

MASK = "***REDACTED***"


def mask_trailing_secret(text: str) -> str:
    """redact() çıktısında hâlâ açık kalan 'anahtar: değer' kalıntısını
    kapatır. Core sözleşmesini genişletmez; yalnızca kendi kanalımızda
    ek güvence sağlar."""
    if not isinstance(text, str):
        return text

    def _sub(m: re.Match) -> str:
        head = re.split(r"[:=]", m.group(0), maxsplit=1)[0]
        sep = ":" if ":" in m.group(0) else "="
        return head + sep + MASK

    return _TRAILING_SECRET.sub(_sub, text)
