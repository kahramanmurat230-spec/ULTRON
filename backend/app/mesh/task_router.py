"""Task Router — picks the right brain for a command.

LOCAL_IMMEDIATE : light tasks (notes, alarms, quick math, simple QA) — the
                  mobile node resolves alone, even in Saha Modu.
PC_DEEP_COMPUTE : heavy tasks (vision, compile, code analysis, patches) — PC only.
PC offline + heavy => honest notice, never a fake result.
"""
HEAVY_KEYS = ("ekran", "derle", "compile", "build", "kod", "analiz", "patch",
              "llava", "vision", "debug", "refactor", "projeyi")
LIGHT_KEYS = ("alarm", "hatırlat", "not al", "not ekle", "saat kaç", "hava",
              "topla", "kaç eder", "?")

PC_SLEEP_LINE = ("Boss, bu cerrahi işlem için masaüstü bedenin uyanık olmalı. "
                "Laptop şu an uykuda — ben saha modunda hafif işleri üstlenirim, "
                "ama vizyon ve derleme benden büyük. Uyandığında senkronize oluruz.")


def classify_weight(text: str) -> str:
    t = (text or "").lower()
    if any(k in t for k in HEAVY_KEYS):
        return "heavy"
    if any(k in t for k in LIGHT_KEYS):
        return "light"
    return "light"  # default: never reject, resolve locally


def route(text: str, pc_online: bool) -> dict:
    w = classify_weight(text)
    if w == "heavy" and not pc_online:
        return {"target": "PC_OFFLINE_NOTICE", "weight": w, "notice": PC_SLEEP_LINE}
    if w == "heavy":
        return {"target": "PC_DEEP_COMPUTE", "weight": w}
    return {"target": "LOCAL_IMMEDIATE", "weight": w}
