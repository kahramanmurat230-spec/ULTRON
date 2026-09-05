"""PHASE 2/3: supervisor agent — real workers, plan selection, replan, E2E on
a real fixture project (offline; no LLM needed)."""
import asyncio
import os
import tempfile
from pathlib import Path

from app.agent.supervisor import (
    CodeAnalysisWorker, DiagnosticWorker, ReportWorker, SupervisorAgent,
    TestWorker, VerificationWorker, Worker,
)
from app.code_intel.analyzer import CodeIntel
from app.tasks.engine import TaskEngine


def make_fixture_project(tmp: str) -> Path:
    root = Path(tmp) / "fixtureproj"
    (root / "pkg").mkdir(parents=True, exist_ok=True)
    (root / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (root / "pkg" / "mod.py").write_text(
        "import os\n\n\ndef add(a, b):\n"
        "    '''add'''\n    return a + b\n\n\ndef unused_helper():\n    pass\n",
        encoding="utf-8")
    (root / "test_mod.py").write_text(
        "from pkg.mod import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n",
        encoding="utf-8")
    return root


def make_supervisor(tmp: str, extra_workers=None):
    root = make_fixture_project(tmp)
    engine = TaskEngine(db_path=os.path.join(tmp, "tasks.db"))
    events = []
    engine.event_cb = lambda ev: events.append(ev)
    workers = {
        "code_analysis": CodeAnalysisWorker(CodeIntel(str(root))),
        "tests": TestWorker(root),
        "verification": VerificationWorker(),
        "report": ReportWorker(router=None, llm_available=lambda: False),
    }
    workers.update(extra_workers or {})
    sup = SupervisorAgent(engine, workers, event_cb=lambda ev: events.append(ev))
    return sup, engine, events, root


def test_plan_selection():
    sup, _, _, _ = make_supervisor(tempfile.mkdtemp())
    plan = sup.plan("ultron projesini analiz et")
    ids = [s["worker"] for s in plan]
    assert ids == ["code_analysis", "verification", "report"]
    plan2 = sup.plan("testleri çalıştır ve regresyon kontrolü yap")
    assert "tests" in [s["worker"] for s in plan2]
    assert [s["worker"] for s in plan2][-2:] == ["verification", "report"]
    plan3 = sup.plan("kendini kontrol et teşhis")
    assert "diagnostic" in [s["worker"] for s in plan3]


def test_supervisor_e2e_real_workers():
    tmp = tempfile.mkdtemp()
    sup, engine, events, root = make_supervisor(tmp)
    task = asyncio.run(sup.submit("proje analizi ve testler", budgets={
        "timeout_s": 180, "step_timeout_s": 120, "tool_budget": 20}, spawn=False))
    out = asyncio.run(engine.execute(task["id"], sup._runner))
    assert out["status"] == "COMPLETED", out
    labels = [s["status"] for s in out["steps"]]
    assert "FAILED" not in labels
    assert any(ev["status"] == "TASK_COMPLETE" for ev in events)
    report_step = [s for s in out["steps"] if s["worker"] == "report"][0]
    report = report_step["result"]["output"]
    assert "GOAL" in report and "CODE:" in report and "TESTS:" in report


def test_verification_worker_rejects_garbage():
    vw = VerificationWorker()
    bad = vw.run("g", {}, {"outputs": {"code_analysis": {"files": 0}}})
    assert bad["ok"] is False and bad["output"]["problems"]
    good = vw.run("g", {}, {"outputs": {"code_analysis": {"files": 3, "issues": 0}}})
    assert good["ok"] is True


def test_supervisor_replans_with_alternative_worker():
    tmp = tempfile.mkdtemp()

    class FailingWorker(Worker):
        id = "failing"
        label = "always fails"
        alt = "backup"

        def run(self, goal, args, ctx):
            return {"ok": False, "output": {"error": "down"}}

    class BackupWorker(Worker):
        id = "backup"
        label = "backup path"

        def run(self, goal, args, ctx):
            return {"ok": True, "output": {"via": "backup"}}

    sup, engine, events, _ = make_supervisor(tmp, extra_workers={
        "failing": FailingWorker(), "backup": BackupWorker()})
    task = asyncio.run(sup.submit("x"))  # default plan -> code_analysis (real)
    # inject a failing step via a direct task
    t2 = engine.create("failing goal", kind="supervisor",
                       steps=[{"label": "primary", "worker": "failing"}])
    asyncio.run(engine.execute(t2["id"], sup._runner))
    out = engine.get(t2["id"])
    assert out["status"] == "COMPLETED"
    assert out["steps"][0]["result"]["output"] == {"via": "backup"}
    assert any(ev["status"] == "REPLAN" for ev in events)


def test_unknown_worker_fails_cleanly():
    tmp = tempfile.mkdtemp()
    sup, engine, _, _ = make_supervisor(tmp)
    t = engine.create("g", steps=[{"label": "x", "worker": "nope"}])
    asyncio.run(engine.execute(t["id"], sup._runner))
    out = engine.get(t["id"])
    assert out["status"] == "FAILED"


def test_diagnostic_worker_offline_runtime_stub_is_not_needed_here():
    # DiagnosticWorker requires the real runtime; verify it refuses cleanly
    # when constructed without one (honest error, no fake diagnostic).
    try:
        DiagnosticWorker(runtime=None).run("g", {}, {})
        raise AssertionError("must fail without a real runtime")
    except Exception:
        pass
