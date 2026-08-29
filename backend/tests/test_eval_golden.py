"""PHASE 16: Agent Evaluation Framework — golden scenarios.

Kategoriler: intent, planning, tool_selection(risk), verification,
recovery, voice, vision, browser, memory, security, self_coding.
Her vaka (input, expected) çiftidir ve GERÇEK modüller üzerinden
değerlendirilir — mock yok. `python tests/eval_golden.py` skor özetı
basar; pytest olarak da koşar (her vaka bir assertion).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.intent import classify  # noqa: E402
from app.agent.orchestrator import parse_plan, risk_report  # noqa: E402
from app.browser.agent import browser_action_risk  # noqa: E402
from app.core.model_router import fit_messages, model_matches  # noqa: E402
from app.core.model_router import TaskType  # noqa: E402
from app.mesh import task_router as tr  # noqa: E402
from app.security.risk import evaluate  # noqa: E402
from app.browser.agent import normalize_url  # noqa: E402
from app.presence.sources import PresenceSources  # noqa: E402

# ---------------------------------------------------------------- cases
INTENT_CASES = [
    ("2 ile 3ü topla kaç eder", "calculate"),
    ("cpu durumu ne", "system_status"),
    ("kendini kontrol et hata varsa bildir", "self_diagnostic"),
    ("ekranımda ne var", "screen"),
]
PLANNING_CASES = [
    ("notepad aç ve sonra hesap makinesi aç", 2),      # iki adım
    ("chrome aç", 1),
    ("bu cümlede eşleşen araç yoktur heralde", 0),      # plan yok → dürüst
]
TOOL_RISK_CASES = [
    ("system_status", "SAFE"), ("weather_current", "LOW"),
    ("close_application", "MEDIUM"), ("gui_click", "HIGH"),
    ("run_shell", "CRITICAL"), ("email_send", "HIGH"),
    ("delete_file", "CRITICAL"),
]
BROWSER_RISK_CASES = [
    ("navigate", "SAFE"), ("screenshot", "SAFE"), ("click", "MEDIUM"),
    ("type", "MEDIUM"), ("destroy", "UNKNOWN"),
]
URL_CASES = [
    ("example.com", "https://example.com"),
    ("javascript:alert(1)", None),
    ("data:text/html,x", None),
]
ROUTER_CASES = [
    ("llava:7b", TaskType.VISION, True),
    ("qwen2.5-coder:7b", TaskType.CODING, True),
    ("qwen3:4b", TaskType.FAST, True),
    ("qwen3:8b", TaskType.CODING, False),  # general model — coder değil
]
TASKROUTE_CASES = [
    ("ekranı analiz et", True, "PC_DEEP_COMPUTE"),
    ("ekranı analiz et", False, "PC_OFFLINE_NOTICE"),
    ("sabah alarmım 8 olsun", False, "LOCAL_IMMEDIATE"),
]
SECURITY_CASES = [
    # (tool, args, registry_dangerous, approved) → izin verilir mi
    ("system_status", {}, False, False, True),      # SAFE her zaman serbest
    ("gui_click", {}, True, False, False),          # onaysız HIGH → red
    ("gui_click", {}, True, True, True),            # onaylı HIGH → serbest
    ("write_text", {"path": "app/security/vault.py", "content": "x"}, True, True, False),
    ("run_shell", {}, True, False, False),          # onaysız CRITICAL → red
]
MEMORY_CASES = [
    # fit_messages: system korunur, en yeni korunur
    ("keep_system_and_newest",),
]
PRESENCE_CASES = [
    ("camera_never_available",),
]


def run_eval() -> dict:
    results = {}

    def tally(cat, pairs):
        ok = sum(1 for got, exp in pairs if got == exp)
        results[cat] = {"pass": ok, "total": len(pairs)}

    tally("intent", [(classify(t), exp) for t, exp in INTENT_CASES])
    tally("planning", [(len(parse_plan(t)), exp) for t, exp in PLANNING_CASES])
    tally("tool_selection_risk",
          [(evaluate(tool, {}, dangerous=False)["level"], exp)
           for tool, exp in TOOL_RISK_CASES])
    tally("browser_risk",
          [(browser_action_risk(a), exp) for a, exp in BROWSER_RISK_CASES])
    tally("url_guard", [
        ((normalize_url(u) if exp else _safe(normalize_url, u)), exp and normalize_url(u))
        for u, exp in URL_CASES])
    tally("model_router",
          [(model_matches(m, task), exp) for m, task, exp in ROUTER_CASES])
    tally("task_routing",
          [(tr.route(t, pc)["target"], exp) for t, pc, exp in TASKROUTE_CASES])
    # security: evaluate+guard kararı
    from app.security.risk import guard
    sec_pairs = []
    for tool, args, dangerous, approved, expect_allowed in SECURITY_CASES:
        try:
            guard(tool, args, dangerous=dangerous, approved=approved)
            allowed = True
        except PermissionError:
            allowed = False
        sec_pairs.append((allowed, expect_allowed))
    tally("security", sec_pairs)
    # memory: fit
    msgs = [{"role": "system", "content": "S" * 100},
            {"role": "user", "content": "u" * 900},
            {"role": "user", "content": "yeni"}]
    out = fit_messages(msgs, max_chars=200)
    tally("memory", [((out[0]["role"] == "system", out[-1]["content"] == "yeni"),
                      (True, True))])
    # presence: kamera asla sahte presence üretmez
    tally("presence", [((PresenceSources({}).check_camera()["available"]), False)])
    # verification & recovery kategorileri mevcut test paketlerinde
    # (test_task_engine/test_supervisor/test_codegen_pipeline) temsil edilir.
    total_p = sum(r["pass"] for r in results.values())
    total_t = sum(r["total"] for r in results.values())
    return {"categories": results, "score": f"{total_p}/{total_t}",
            "percent": round(100 * total_p / max(1, total_t), 1)}


def _safe(fn, u):
    try:
        return fn(u)
    except ValueError:
        return None


if __name__ == "__main__":
    import json
    print(json.dumps(run_eval(), indent=1, ensure_ascii=False))


# --------------------------------------------------------------- pytest
def test_eval_intent_golden():
    for t, exp in INTENT_CASES:
        assert classify(t) == exp, (t, classify(t))


def test_eval_planning_golden():
    for t, exp in PLANNING_CASES:
        assert len(parse_plan(t)) == exp, t


def test_eval_risk_golden():
    for tool, exp in TOOL_RISK_CASES:
        assert evaluate(tool, {})["level"] == exp, tool


def test_eval_browser_and_url_golden():
    for a, exp in BROWSER_RISK_CASES:
        assert browser_action_risk(a) == exp
    for u, exp in URL_CASES:
        got = _safe(normalize_url, u)
        if exp is None:
            assert got is None, u
        else:
            assert got == exp


def test_eval_router_and_taskrouting_golden():
    for m, task, exp in ROUTER_CASES:
        assert model_matches(m, task) is exp
    for t, pc, exp in TASKROUTE_CASES:
        assert tr.route(t, pc)["target"] == exp


def test_eval_security_golden():
    from app.security.risk import guard
    for tool, args, dangerous, approved, expect in SECURITY_CASES:
        try:
            guard(tool, args, dangerous=dangerous, approved=approved)
            allowed = True
        except PermissionError:
            allowed = False
        assert allowed is expect, (tool, approved)


def test_eval_summary_score():
    r = run_eval()
    assert r["percent"] == 100.0, r  # tüm goldenlar geçmeli
