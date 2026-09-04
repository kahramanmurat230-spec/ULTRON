"""Deterministic browser workflow planner.

Only explicit user-supplied targets are emitted; the planner never invents
selectors or page structure. State-changing steps remain approval-protected.
"""
import re
from urllib.parse import quote_plus


def _split(text: str) -> list[str]:
    t = re.sub(r"\s+(?:ve\s+)?(?:sonra|ardından|ardindan)\s+", " | ", text, flags=re.I)
    return [p.strip(" ,.;") for p in t.split("|") if p.strip(" ,.;")]


def parse_browser_plan(text: str) -> list[dict]:
    """Return deterministic browser steps; ambiguous actions are omitted."""
    clauses = _split(text or "")
    steps = []
    for c in clauses:
        low = c.lower()
        m = re.search(r"(?:siteye|adrese|url'ye|url ye|sayfaya)\s+(https?://\S+|[\w.-]+\.[a-z]{2,}(?:/\S*)?)\s*(?:git|aç|ac)$", low, re.I)
        if not m:
            m = re.search(r"^(?:git|aç|ac)\s+(https?://\S+|[\w.-]+\.[a-z]{2,}(?:/\S*)?)$", low, re.I)
        if not m:
            m = re.search(r"^(https?://\S+|[\w.-]+\.[a-z]{2,}(?:/\S*)?)\s*(?:git|aç|ac)$", low, re.I)
        if m:
            steps.append({"label": "browser navigate", "tool": "browser_navigate", "args": {"url": m.group(1)}})
            continue
        m = re.search(r"(?:google'?da|web'de|internette)\s+(.+?)\s+(?:ara|araştır|arastir)$", c, re.I)
        if m:
            q = m.group(1).strip()
            steps.append({"label": f"browser search: {q}", "tool": "browser_navigate", "args": {"url": "https://www.google.com/search?q=" + quote_plus(q)}})
            continue
        if re.search(r"(?:sayfayı|sayfayi|sayfanın|sayfanin|sonuçları|sonuclari|sonucu)\s+(?:oku|okuy|oku ve göster|göster|goster)$", low):
            steps.append({"label": "browser read page", "tool": "browser_read", "args": {"limit": 5000}})
            continue
        m = re.search(r"(?:css|selector)\s+['\"]?([^'\"]+)['\"]?\s+(?:tıkla|tikla)$", c, re.I)
        if m:
            steps.append({"label": "browser click selector", "tool": "browser_click", "args": {"selector": m.group(1).strip()}})
            continue
        m = re.search(r"(?:role|rol)\s+['\"]?([\w-]+)['\"]?(?:\s+(?:adı|adi|name)\s+['\"](.+?)['\"])?\s+(?:tıkla|tikla)$", c, re.I)
        if m:
            target = "role=" + m.group(1).strip()
            if m.group(2): target += "[name=" + m.group(2).strip() + "]"
            steps.append({"label": "browser click role", "tool": "browser_click", "args": {"selector": target}})
            continue
        m = re.search(r"(?:text|metin)\s+['\"](.+?)['\"]\s+(?:tıkla|tikla)$", c, re.I)
        if m:
            steps.append({"label": "browser click text", "tool": "browser_click", "args": {"selector": "text=" + m.group(1)}})
            continue
        m = re.search(r"(?:selector|css|role|rol|text|metin)\s+(.+?)\s+(?:alanına|alanina)\s+['\"](.+?)['\"]\s+yaz$", c, re.I)
        if m:
            target = m.group(1).strip()
            if target.lower().startswith(("role ", "rol ")):
                target = "role=" + target.split(None, 1)[1]
            elif target.lower().startswith("text ") or target.lower().startswith("metin "):
                target = "text=" + target.split(None, 1)[1].strip("'\"")
            steps.append({"label": "browser type target", "tool": "browser_type", "args": {"selector": target, "text": m.group(2)}})
            continue
        if re.search(r"(?:formu|form)\s+(?:gönder|gonder|submit)$", low):
            steps.append({"label": "browser submit form", "tool": "browser_submit", "args": {}})
            continue
        m = re.search(r"(?:sayfada|browser'da|browserda)\s+['\"](.+?)['\"]\s+(?:metnini|yazısını|yazisini)\s+doğrula$", c, re.I)
        if m:
            steps.append({"label": "browser verify text", "tool": "browser_verify", "args": {"text_contains": m.group(1)}})
            continue
    return steps


def as_hybrid_plan(steps: list[dict], goal: str) -> dict | None:
    if len(steps) < 2:
        return None
    normalized = []
    for i, step in enumerate(steps):
        normalized.append({"index": i, "tool": step["tool"], "arguments": step.get("args", {}), "reason": step.get("label", "browser step"), "depends_on": [i - 1] if i else []})
    return {"goal": goal, "steps": normalized, "planner": "browser-deterministic", "bounded": True}
