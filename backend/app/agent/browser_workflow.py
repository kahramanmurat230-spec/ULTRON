"""Deterministic browser workflow planner.

Turns explicit browser instructions into bounded tool steps. It intentionally
uses selectors/URLs supplied by the user rather than guessing page structure.
State-changing actions remain dangerous and are protected by the existing
approval gate.
"""
import re
from urllib.parse import quote_plus


def _split(text: str) -> list[str]:
    t = re.sub(r"\s+(?:ve\s+)?(?:sonra|ardından|ardindan)\s+", " | ", text, flags=re.I)
    return [p.strip(" ,.;") for p in t.split("|") if p.strip(" ,.;")]


def parse_browser_plan(text: str) -> list[dict]:
    """Return browser-only steps, or [] when the request is not explicit enough."""
    clauses = _split(text or "")
    steps = []
    for c in clauses:
        low = c.lower()
        m = re.search(r"(?:siteye|adrese|url'ye|url ye|sayfaya)\s+(https?://\S+|[\w.-]+\.[a-z]{2,}(?:/\S*)?)\s*(?:git|aç|ac)$", low, re.I)
        if not m:
            m = re.search(r"^(?:git|aç|ac)\s+(https?://\S+|[\w.-]+\.[a-z]{2,}(?:/\S*)?)$", low, re.I)
        if m:
            steps.append({"label": "browser navigate", "tool": "browser_navigate", "args": {"url": m.group(1)}})
            continue
        m = re.search(r"(?:google'?da|web'de|internette|internette)\s+(.+?)\s+(?:ara|araştır|arastir)$", c, re.I)
        if m:
            steps.append({"label": f"browser search: {m.group(1).strip()}", "tool": "browser_navigate", "args": {"url": "https://www.google.com/search?q=" + quote_plus(m.group(1).strip())}})
            continue
        if re.search(r"(?:sayfayı|sayfayi|sayfanın|sayfanin|sonuçları|sonuclari|sonucu)\s+(?:oku|okuy|oku ve göster|göster|goster)$", low):
            steps.append({"label": "browser read page", "tool": "browser_read", "args": {"limit": 5000}})
            continue
        m = re.search(r"(?:css|selector)\s+['\"]?([^'\"]+)['\"]?\s+(?:tıkla|tikla)$", c, re.I)
        if m:
            steps.append({"label": "browser click selector", "tool": "browser_click", "args": {"selector": m.group(1).strip()}})
            continue
        m = re.search(r"(?:selector|css)\s+['\"]?([^'\"]+)['\"]?\s+(?:alanına|alanina)\s+['\"](.+?)['\"]\s+yaz$", c, re.I)
        if m:
            steps.append({"label": "browser type selector", "tool": "browser_type", "args": {"selector": m.group(1).strip(), "text": m.group(2)}})
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
        normalized.append({
            "index": i,
            "tool": step["tool"],
            "arguments": step.get("args", {}),
            "reason": step.get("label", "browser step"),
            "depends_on": [i - 1] if i else [],
        })
    return {"goal": goal, "steps": normalized, "planner": "browser-deterministic", "bounded": True}
