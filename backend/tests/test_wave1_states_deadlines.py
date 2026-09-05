"""WAVE 1 / States + deadlines + dependencies:
SCHEDULED & WAITING_BRAIN (additive statuses), soft/hard deadlines
(graceful cancel + partial result), needs:[task|artifact] DAG with
circular rejection, artifact registry (path traversal rejected).
"""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.observability.trace import Tracer  # noqa: E402
from app.tasks.engine import BrainUnavailable, STATUSES, TaskEngine  # noqa: E402


def make_engine(tmp):
    return TaskEngine(db_path=os.path.join(tmp, "tasks.db"),
                      tracer=Tracer(path=os.path.join(tmp, "traces.jsonl")))


def ok(label):
    async def runner(task, step, ctx):
        return {"ok": True, "output": f"done {label}"}
    return runner


# ---------------------------------------------------------- SCHEDULED
def test_scheduled_status_and_transitions(tmp_path):
    e = make_engine(str(tmp_path))
    t = e.create("g", steps=[{"label": "s", "worker": "w"}], scheduled=True)
    assert t["status"] == "SCHEDULED"
    assert "SCHEDULED" in STATUSES
    out = asyncio.run(e.execute(t["id"], ok("x")))   # SCHEDULED→RUNNING izinli
    assert out["status"] == "COMPLETED"
    # PENDING→SCHEDULED manual; SCHEDULED→COMPLETED geçişi YOK
    t2 = e.create("g2", steps=[{"label": "s", "worker": "w"}])
    assert e.schedule(t2["id"])["status"] == "SCHEDULED"
    with pytest.raises(Exception):
        e._save(e.get(t2["id"]), "COMPLETED")


# ---------------------------------------------------------- WAITING_BRAIN
def test_waiting_brain_auto_park_and_resume(tmp_path):
    e = make_engine(str(tmp_path))
    t = e.create("g", steps=[{"label": "s", "worker": "w"}])
    calls = {"n": 0}

    async def brain_down(task, step, ctx):
        calls["n"] += 1
        if calls["n"] == 1:
            raise BrainUnavailable("ollama down")
        return {"ok": True, "output": "finally"}

    out = asyncio.run(e.execute(t["id"], brain_down))
    assert out["status"] == "WAITING_BRAIN"           # fail DEĞİL — park
    assert any(r["status"] == "TASK_WAITING_BRAIN" for r in e.journal_rows(t["id"]))
    out2 = asyncio.run(e.execute(t["id"], brain_down))  # resume: RUNNING'a döner
    assert out2["status"] == "COMPLETED" and calls["n"] == 2


def test_waiting_brain_manual(tmp_path):
    e = make_engine(str(tmp_path))
    t = e.create("g", steps=[{"label": "s", "worker": "w"}])
    task = e.get(t["id"])
    e._save(task, "RUNNING")                          # manuel RUNNING
    assert e.set_brain_unavailable(t["id"])["status"] == "WAITING_BRAIN"
    assert e.resume_brain(t["id"])["status"] == "RUNNING"


# ---------------------------------------------------------- deadlines
def test_soft_deadline_warns_but_continues(tmp_path):
    e = make_engine(str(tmp_path))
    events = []
    e.event_cb = lambda ev: events.append(ev["status"])

    async def slow(task, step, ctx):
        await asyncio.sleep(0.08)
        return {"ok": True, "output": "ok"}

    t = e.create("g", steps=[{"label": "s", "worker": "w"}],
                 deadline_soft_s=0.02, budgets={"timeout_s": 30})
    out = asyncio.run(e.execute(t["id"], slow))
    assert out["status"] == "COMPLETED"               # soft: yalnız uyarı
    assert "TASK_SOFT_DEADLINE" in events
    assert any(r["status"] == "TASK_SOFT_DEADLINE" for r in e.journal_rows(t["id"]))


def test_hard_deadline_graceful_cancel_partial_result(tmp_path):
    e = make_engine(str(tmp_path))
    events = []
    e.event_cb = lambda ev: events.append(ev["status"])
    t = e.create("g", steps=[{"label": "one", "worker": "w"},
                             {"label": "two", "worker": "w"}],
                 deadline_hard_s=0.05, budgets={"timeout_s": 30})
    out = asyncio.run(e.execute(t["id"], _slow_runner()))
    assert out["status"] == "CANCELLED"               # graceful, FAILED değil
    assert out["result"]["partial"] is True
    assert out["result"]["reason"] == "hard_deadline"
    assert out["result"]["completed_steps"] == ["one"]  # kısmi rapor dürüst
    assert "TASK_HARD_DEADLINE" in events
    rows = [r["status"] for r in e.journal_rows(t["id"])]
    assert "TASK_HARD_DEADLINE" in rows


def _slow_runner():
    async def runner(task, step, ctx):
        await asyncio.sleep(0.07)
        return {"ok": True, "output": "ok"}
    return runner


# ---------------------------------------------------------- dependencies
def test_dependency_task_ready_after_completion(tmp_path):
    e = make_engine(str(tmp_path))
    a = e.create("A", steps=[{"label": "s", "worker": "w"}])
    asyncio.run(e.execute(a["id"], ok("a")))
    b = e.create("B", steps=[{"label": "s", "worker": "w"}], needs=[f"task:{a['id']}"])
    assert e.dependency_state(b["id"])["state"] == "ready"
    assert asyncio.run(e.execute(b["id"], ok("b")))["status"] == "COMPLETED"


def test_dependency_unknown_rejected(tmp_path):
    e = make_engine(str(tmp_path))
    with pytest.raises(ValueError):
        e.create("X", steps=[{"label": "s", "worker": "w"}], needs=["task:deadbeef"])


def test_dependency_blocked_then_ready(tmp_path):
    e = make_engine(str(tmp_path))
    a = e.create("A", steps=[{"label": "s", "worker": "w"}])  # PENDING
    b = e.create("B", steps=[{"label": "s", "worker": "w"}], needs=[f"task:{a['id']}"])
    assert e.dependency_state(b["id"])["state"] == "blocked"
    out = asyncio.run(e.execute(b["id"], ok("b")))      # blocked → koşmaz
    assert out["status"] == "PENDING"
    asyncio.run(e.execute(a["id"], ok("a")))
    assert e.dependency_state(b["id"])["state"] == "ready"
    assert asyncio.run(e.execute(b["id"], ok("b")))["status"] == "COMPLETED"


def test_dependency_broken_fails_honestly(tmp_path):
    e = make_engine(str(tmp_path))
    a = e.create("A", steps=[{"label": "s", "worker": "w"}],
                 budgets={"retry_budget": 0})

    async def boom(task, step, ctx):
        raise RuntimeError("kaboom")

    asyncio.run(e.execute(a["id"], boom))              # A FAILED
    b = e.create("B", steps=[{"label": "s", "worker": "w"}], needs=[f"task:{a['id']}"])
    out = asyncio.run(e.execute(b["id"], ok("b")))
    assert out["status"] == "CANCELLED"          # terminal, sonsuz retry YOK
    assert out["result"]["reason"] == "dependency_broken"
    assert "dependency broken" in out["error"]
    assert any(r["error_class"] == "DependencyBroken" for r in e.journal_rows(b["id"]))


def test_dependency_artifact_gate(tmp_path):
    e = make_engine(str(tmp_path))
    b = e.create("B", steps=[{"label": "s", "worker": "w"}],
                 needs=["artifact:report-q3"])
    assert e.dependency_state(b["id"])["state"] == "blocked"
    e.mark_artifact(b["id"], "report-q3", path="data/reports/q3.md")
    assert e.dependency_state(b["id"])["state"] == "ready"
    assert asyncio.run(e.execute(b["id"], ok("b")))["status"] == "COMPLETED"


def test_artifact_path_traversal_rejected(tmp_path):
    e = make_engine(str(tmp_path))
    with pytest.raises(ValueError):
        e.mark_artifact("x", "../etc/passwd")
    with pytest.raises(ValueError):
        e.mark_artifact("x", "a/b")


def test_needs_persisted_in_get(tmp_path):
    e = make_engine(str(tmp_path))
    a = e.create("A", steps=[{"label": "s", "worker": "w"}])
    b = e.create("B", steps=[{"label": "s", "worker": "w"}],
                 needs=[f"task:{a['id']}", "artifact:rep"])
    got = e.get(b["id"])
    assert got["needs"] == [f"task:{a['id']}", "artifact:rep"]
    assert got["deadline_soft"] is None and got["deadline_hard"] is None
    assert got["template_key"] is None


# ---------------------------------------------------------- cycles
def test_update_needs_circular_rejected(tmp_path):
    e = make_engine(str(tmp_path))
    a = e.create("A", steps=[{"label": "s", "worker": "w"}])
    b = e.create("B", steps=[{"label": "s", "worker": "w"}])
    e.update_needs(a["id"], [f"task:{b['id']}"])       # A→B ok
    with pytest.raises(ValueError, match="circular"):
        e.update_needs(b["id"], [f"task:{a['id']}"])   # B→A döngü RED
    with pytest.raises(ValueError, match="circular"):
        e.update_needs(a["id"], [f"task:{a['id']}"])   # kendine bağımlılık RED


def test_cycle_rejected_three_node(tmp_path):
    e = make_engine(str(tmp_path))
    a = e.create("A", steps=[{"label": "s", "worker": "w"}])
    b = e.create("B", steps=[{"label": "s", "worker": "w"}])
    c = e.create("C", steps=[{"label": "s", "worker": "w"}])
    e.update_needs(a["id"], [f"task:{b['id']}"])
    e.update_needs(b["id"], [f"task:{c['id']}"])
    with pytest.raises(ValueError, match="circular"):
        e.update_needs(c["id"], [f"task:{a['id']}"])   # A→B→C→A döngü RED


def test_old_db_rows_still_readable(tmp_path):
    """Eski şemalı (needs kolonu yok) satırlar migration sonrası okunur."""
    import sqlite3
    e = make_engine(str(tmp_path))
    t = e.create("g", steps=[{"label": "s", "worker": "w"}])
    # kolonu geriye uyumlu test et: get needs=[] döndürmeli
    assert e.get(t["id"])["needs"] == []
    conn = sqlite3.connect(e.path)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(tasks)")]
    assert {"needs", "deadline_soft", "deadline_hard", "template_key"} <= set(cols)
    conn.close()
