"""WAVE 1 / Task journal + WAL: append-only journal, crash recovery via
subprocess (REAL hard kill), SUCCESS idempotency, DB interruption honesty,
trace spans emitted by the engine."""
import asyncio
import os
import subprocess
import sys
import textwrap
from collections import Counter

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.observability.trace import Tracer  # noqa: E402
from app.tasks.engine import TaskEngine  # noqa: E402

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def make_engine(tmp, **kw):
    return TaskEngine(db_path=os.path.join(tmp, "tasks.db"),
                      tracer=Tracer(path=os.path.join(tmp, "traces.jsonl")), **kw)


# ---------------------------------------------------------------- journal
def test_step_journal_records_all_phases(tmp_path):
    e = make_engine(str(tmp_path))

    async def runner(task, step, ctx):
        return {"ok": True, "output": f"out {step['label']} password: gizli123"}

    t = e.create("g", steps=[{"label": "a", "worker": "w"},
                             {"label": "b", "worker": "gui_click"}])
    out = asyncio.run(e.execute(t["id"], runner))
    assert out["status"] == "COMPLETED"
    rows = e.journal_rows(t["id"])
    statuses = [r["status"] for r in rows]
    assert statuses[0] == "TASK_START"
    assert "STEP_START" in statuses and statuses.count("STEP_SUCCESS") == 2
    assert statuses[-1] == "TASK_COMPLETE"
    succ = [r for r in rows if r["status"] == "STEP_SUCCESS"]
    assert succ[0]["input_hash"] and len(succ[0]["input_hash"]) == 16
    assert "gizli123" not in str(rows)          # secret → journal'a asla
    assert "***REDACTED***" in str(rows)
    risk = succ[1]["risk"]
    assert '"gui_click"' in risk and '"HIGH"' in risk  # risk kararı günlükte


def test_journal_failed_step_error_class(tmp_path):
    e = make_engine(str(tmp_path))

    async def bad(task, step, ctx):
        raise RuntimeError("boom")

    t = e.create("g", steps=[{"label": "x", "worker": "w"}])
    out = asyncio.run(e.execute(t["id"], bad))
    assert out["status"] == "FAILED"
    failed = [r for r in e.journal_rows(t["id"]) if r["status"] == "STEP_FAILED"]
    assert failed and failed[0]["error_class"] == "RuntimeError"
    assert failed[0]["duration_ms"] is not None
    assert [r["status"] for r in e.journal_rows(t["id"])][-1] == "TASK_FAILED"


def test_engine_emits_task_and_step_spans(tmp_path):
    e = make_engine(str(tmp_path))

    async def runner(task, step, ctx):
        return {"ok": True, "output": "x"}

    t = e.create("g", steps=[{"label": "s1", "worker": "w"}])
    asyncio.run(e.execute(t["id"], runner))
    recs = e._get_tracer().read_all()
    kinds = Counter(r["kind"] for r in recs)
    assert kinds["task"] == 1 and kinds["step"] == 1
    root = next(r for r in recs if r["kind"] == "task")
    step = next(r for r in recs if r["kind"] == "step")
    assert step["trace_id"] == root["trace_id"]
    assert step["parent_span_id"] == root["span_id"]      # nested span
    assert root["task_id"] == t["id"] and root["status"] == "OK"
    assert root["budget"]["tools_used"] >= 1


# ---------------------------------------------------------------- WAL
def test_wal_replay_restores_success_without_rerun(tmp_path):
    e = make_engine(str(tmp_path))
    t = e.create("g", steps=[{"label": "one", "worker": "w"},
                             {"label": "two", "worker": "w"}])
    # simulate: step one journaled SUCCESS, then crash BEFORE task save
    task = e.get(t["id"])
    e.journal(t["id"], "STEP_START", step=task["steps"][0], input_hash="abc123")
    e.journal(t["id"], "STEP_SUCCESS", step=task["steps"][0],
              output_summary="done one", duration_ms=5.0)
    task["status"] = "RUNNING"  # crash sırasındaki durum
    e._save(task, "RECOVERING")

    replay = e.wal_replay(t["id"])
    assert [d["label"] for d in replay["successful_steps"]] == ["one"]
    rec = e.reconcile_from_journal(t["id"])
    assert rec["changed"] is True

    ran = []

    async def runner(task, step, ctx):
        ran.append(step["label"])
        return {"ok": True, "output": "ok"}

    out = asyncio.run(e.execute(t["id"], runner))
    assert out["status"] == "COMPLETED"
    assert ran == ["two"]  # 'one'_journal kanıtıyla ASLA yeniden koşmadı (idempotent)


def test_wal_replay_incomplete_step_detected(tmp_path):
    e = make_engine(str(tmp_path))
    t = e.create("g", steps=[{"label": "one", "worker": "w"}])
    task = e.get(t["id"])
    e.journal(t["id"], "STEP_START", step=task["steps"][0])  # yarım kalan adım
    replay = e.wal_replay(t["id"])
    assert replay["successful_steps"] == [] and replay["incomplete"] == [0]


# ------------------------------------------------- REAL crash (subprocess)
CHILD = textwrap.dedent('''
    import asyncio, os, sys
    sys.path.insert(0, sys.argv[3])
    from app.tasks.engine import TaskEngine
    e = TaskEngine(db_path=sys.argv[1])
    t = e.create("crash test", steps=[{"label": "one", "worker": "w"},
                                      {"label": "two", "worker": "w"}])
    async def runner(task, step, ctx):
        with open(sys.argv[2], "a") as f:
            f.write(step["label"] + "\\n")
        if step["label"] == "two":
            os._exit(1)  # HARD KILL: process dies mid-step, no cleanup, no save
        return {"ok": True, "output": "done " + step["label"]}
    asyncio.run(e.execute(t["id"], runner))
''')


def test_crash_recovery_subprocess_real(tmp_path):
    db = os.path.join(tmp_path, "crash.db")
    mark = os.path.join(tmp_path, "markers.txt")
    child = tmp_path / "child.py"
    child.write_text(CHILD, encoding="utf-8")
    # 1) child: step one SUCCESS, step two sırasında process ölür (gerçek kill)
    proc = subprocess.run([sys.executable, str(child), db, mark, BACKEND],
                          cwd=str(tmp_path), env={**os.environ, "PYTHONPATH": BACKEND},
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 1, proc.stderr  # gerçek çökme oldu
    assert Counter(open(mark).read().split()) == Counter({"one": 1, "two": 1})
    # 2) parent: fresh engine → boot recovery + WAL replay
    e = TaskEngine(db_path=db, tracer=Tracer(path=os.path.join(tmp_path, "t.jsonl")))
    recovered = e.recover_incomplete()
    assert len(recovered) == 1
    t = e.get(recovered[0])
    assert t["status"] == "RECOVERING"
    assert t["steps"][0]["status"] == "SUCCESS"      # journal'dan restore edildi
    assert t["current_step"] == 1                     # 'one' geçildi
    # 3) resume: 'one' ASLA yeniden koşmaz, 'two' tamamlanır
    ran = []

    async def runner(task, step, ctx):
        ran.append(step["label"])
        with open(mark, "a") as f:
            f.write(step["label"] + "\n")   # parent da işaret koyar
        return {"ok": True, "output": "retry ok"}

    out = asyncio.run(e.execute(t["id"], runner))
    assert out["status"] == "COMPLETED"
    assert ran == ["two"]
    # one=1 (asla yeniden koşulmadı), two=2 (çökme denemesi + başarılı retry)
    assert Counter(open(mark).read().split()) == Counter({"one": 1, "two": 2})


# ---------------------------------------------------------------- DB interruption
def test_journal_interruption_never_breaks_execution(tmp_path):
    e = make_engine(str(tmp_path))
    t = e.create("g", steps=[{"label": "s", "worker": "w"}])
    os.chmod(e.path, 0o444)  # OS seviyesinde yazma engeli (gerçek kesinti)
    try:
        res = e.journal(t["id"], "TASK_START")
        assert res["ok"] is False and res.get("error")  # dürüst hata
        assert e.journal_errors >= 1
    finally:
        os.chmod(e.path, 0o644)
    # engine çalışmaya devam eder (journal kapansa bile execute kırılmaz)
    async def runner(task, step, ctx):
        return {"ok": True, "output": "ok"}

    out = asyncio.run(e.execute(t["id"], runner))
    assert out["status"] == "COMPLETED"
