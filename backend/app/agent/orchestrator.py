"""Agent 2.0 — deterministic multi-step planner/executor.

USER REQUEST → INTENT → CONTEXT → PLAN → RISK ANALYSIS → [WAITING_APPROVAL]
→ TOOL SELECTION → EXECUTION (per-step VERIFY) → RECOVERY (1 safe retry /
alternative) → DONE/ERROR.  Simple questions never enter the planner.
No infinite retries. No fake success.
"""
import re

APP_MAP = {
    "chrome": "chrome", "google chrome": "chrome",
    "edge": "edge", "notepad": "notepad", "not defteri": "notepad",
    "hesap makinesi": "calculator", "calculator": "calculator",
    "discord": "discord", "spotify": "spotify",
}
DANGEROUS_TOOLS = {"gui_click", "gui_double_click", "gui_right_click", "gui_type",
                   "gui_press", "gui_hotkey", "gui_scroll", "write_text",
                   "apply_code_patch", "self_repair", "close_application"}


def _clause_split(text: str) -> list[str]:
    t = re.sub(r"\s+ve\s+(?:sonra\s+)?", " | ", text, flags=re.I)
    t = re.sub(r"\s+sonra\s+", " | ", t, flags=re.I)
    parts = [p.strip(" ,.;") for p in re.split(r"\||,", t) if p.strip(" ,.;")]
    return parts


def parse_step(clause: str) -> dict | None:
    c = clause.lower()
    m = re.search(r"(chrome|edge|notepad|not defteri|hesap makinesi|calculator|discord|spotify)", c)
    if m and re.search(r"\baç\b", c):
        app = APP_MAP.get(m.group(1), m.group(1))
        return {"label": f"{app} aç", "tool": "open_application", "args": {"name": app},
                "verify": "find_window"}
    m = re.search(r"google'?d[ae]\s+(.+?)(?:\bara\b|\baraştır\b|\bsearch\b|$)", c)
    if m:
        q = m.group(1).strip(" .")
        if q:
            from urllib.parse import quote_plus
            return {"label": f"Google: {q}", "tool": "open_url",
                    "args": {"url": "https://www.google.com/search?q=" + quote_plus(q)}, "verify": None}
    m = re.search(r"(https?://\S+)", clause)
    if m:
        return {"label": f"URL aç: {m.group(1)[:40]}", "tool": "open_url", "args": {"url": m.group(1)}, "verify": None}
    if "hava durumu" in c:
        from urllib.parse import quote_plus
        return {"label": "hava durumu", "tool": "open_url",
                "args": {"url": "https://www.google.com/search?q=" + quote_plus("hava durumu")}, "verify": None}
    if re.search(r"ekran görüntüsü|\bscreenshot\b", c):
        return {"label": "screenshot", "tool": "capture_screen", "args": {}, "verify": "png"}
    if re.search(r"\bcpu\b|\bram\b|sistem durumu|telemetry", c):
        return {"label": "system status", "tool": "system_status", "args": {}, "verify": None}
    m = re.search(r"[\"']([^\"']+)[\"']\s*yaz|([a-z0-9ğüşıöç ]{2,40})\s+yaz$", c)
    if m:
        txt = (m.group(1) or m.group(2) or "").strip()
        if txt:
            return {"label": f"yaz: {txt}", "tool": "gui_type", "args": {"text": txt}, "verify": None}
    return None


def parse_plan(text: str) -> list[dict]:
    steps = []
    for clause in _clause_split(text):
        s = parse_step(clause)
        if s:
            s["dangerous"] = s["tool"] in DANGEROUS_TOOLS
            steps.append(s)
    return steps


def risk_report(steps: list[dict]) -> list[str]:
    return [f"{s['tool']}({s['label']})" for s in steps if s["dangerous"]]


ALTERNATIVES = {"open_application": None, "open_url": "search_web", "capture_screen": None}
RETRYABLE = ("refused", "timeout", "temporarily", "10061", "econn")


def should_retry(error: str) -> bool:
    e = (error or "").lower()
    return any(k in e for k in RETRYABLE)
