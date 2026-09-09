"""Unified production path tests (Phase 2 main work).

Chain under test:
  User goal → SupervisorAgent routing (audit vs general plan) →
  Planner (fake local brain; NO Ollama in this sandbox — honest substitution,
  the Planner/validation/risk path is the REAL production code) →
  persistent TaskEngine (kind="plan") → risk/approval gate (server-side,
  one-shot, hard blocks cannot be approved through) → Executor →
  REAL file/process effects → StepVerifier INDEPENDENT observation →
  bounded replan on failure → outcome learning → planner outcome reader.

Contracts locked here:
- non-audit goals become kind="plan" tasks; audit-keyword goals keep the
  fixed audit pipeline; without a planner worker the legacy path is used.
- every tool step reports explicit executed/succeeded/verified flags;
  a tool that CLAIMS success without producing the observable effect is
  FAILED by the independent verifier (verified=False), never trusted.
- MEDIUM+ risk steps stop in WAITING_APPROVAL; approval is persisted
  (user_approved) and ONE-SHOT (consumed after execution).
- hard security blocks (self-coding boundary / protected paths) fail the
  step even when user_approved=True — approval can never override policy.
- planner brain unreachable → honest WAITING_BRAIN (no fabricated plan);
  resume after the brain returns completes the task.
- bounded replan: failed tool step → planner rebuilds REMAINING steps,
  replan_budget decrements, exhausted budget → honest FAILED.
- outcome learning writes the exact "Verified outcome ..." record the
  Planner's OutcomePlanningContext consumes (closed loop).
"""
import asyncio
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.agent.outcome_learning import OutcomeLearner
from app.agent.outcome_planning import OutcomePlanningContext
from app.agent.planner import Planner
from app.agent.step_verify import StepVerifier
from app.agent.supervisor import (
    PlannerWorker, ReportWorker, SupervisorAgent, ToolStepWorker,
    VerificationWorker,
)
from app.tasks.engine import TaskEngine


# ------------------------------------------------------------------ fakes
class FakeBrain:
    """Stands in for the local Ollama brain (unavailable in this sandbox).
    Queue-based responses; can simulate the brain being DOWN."""

    def __init__(self, responses=None, error=None):
        self.responses = list(responses or [])
        self.error = error
        self.messages = []

    def chat(self, messages, tools=None, model=None):
        self.messages.append(messages)
        if self.error is not None:
            raise self.error
        return {"message": {"content": self.responses.pop(0)}}


class Registry:
    """Real-effect tool registry mirroring the production signatures
    (app/core/runtime.py). The .lie path simulates a tool that CLAIMS
    success without producing the effect (verifier independence test)."""

    def __init__(self, root):
        self.root = Path(root)
        self.tools = {
            "write_text": {"fn": self._write, "dangerous": True},
            "read_text": {"fn": self._read, "dangerous": False},
            "list_directory": {"fn": self._list, "dangerous": False},
            "delete_path": {"fn": self._delete, "dangerous": True},
        }
        self.executed = []

    def names(self):
        return list(self.tools)

    def get(self, name):
        return self.tools.get(name)

    def _write(self, path, content):
        if str(path).endswith(".lie"):
            return f"claimed write to {path}"  # LYING: no file is written
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        self.executed.append(("write_text", str(p)))
        return str(p)

    def _read(self, path):
        return Path(path).read_text(encoding="utf-8")

    def _list(self, root):
        return [p.name for p in Path(root).iterdir()]

    def _delete(self, path):
        p = Path(path)
        p.unlink()
        self.executed.append(("delete_path", str(p)))
        return f"deleted {p}"


class Executor:
    """Fake of app/agent/executor.Executor with the SAME gate semantics:
    dangerous tool without approval → PermissionError (never executes)."""

    def __init__(self, registry):
        self.registry = registry

    def execute(self, calls, approved=False):
        out = []
        for name, args in calls:
            item = self.registry.get(name)
            if item is None:
                raise RuntimeError(f"Tool not registered: {name}")
            if item["dangerous"] and not approved:
                raise PermissionError(f"Confirmation required for: {name}")
            out.append(item["fn"](**args))
        return out


class FakeMemory:
    def __init__(self):
        self.records = []

    def add(self, kind, content):
        self.records.append((kind, content))
        return True


def build(tmp_path, responses=None, brain_error=None):
    reg = Registry(tmp_path)
    brain = FakeBrain(responses, error=brain_error)
    planner = Planner(brain, reg)
    events = []
    engine = TaskEngine(db_path=str(tmp_path / "tasks.db"),
                        event_cb=lambda ev: events.append(ev))
    workers = {
        "planner": PlannerWorker(planner),
        "tool_step": ToolStepWorker(Executor(reg), reg, verifier=StepVerifier()),
        "verification": VerificationWorker(),
        "report": ReportWorker(),
    }
    sup = SupervisorAgent(engine, workers, event_cb=lambda ev: events.append(ev))
    return engine, sup, reg, brain, events, planner


def plan_json(*steps):
    return json.dumps({"goal": "goal", "steps": list(steps)})


def run_to_end(engine, sup, task_id, auto_approve=True, timeout=30.0):
    """Drive the spawned task to a terminal state; auto_approve emulates the
    server's /api/tasks/{id}/approve → re-spawn loop."""
    async def _main():
        engine.spawn(task_id, sup._runner)
        deadline = time.time() + timeout
        while time.time() < deadline:
            t = engine.get(task_id)
            st = t["status"]
            if st in ("COMPLETED", "FAILED", "CANCELLED", "DEAD_LETTER"):
                return t
            if st == "WAITING_APPROVAL":
                if not auto_approve:
                    await asyncio.sleep(0.05)
                    continue
                res = engine.approve(task_id)
                if res.get("ok"):
                    engine.spawn(task_id, sup._runner)
            await asyncio.sleep(0.05)
        raise TimeoutError(f"task did not finish: {engine.get(task_id)['status']}")
    return asyncio.run(_main())


# ------------------------------------------------------- routing contract
def test_routing_audit_vs_plan(tmp_path):
    engine, sup, reg, brain, events, _ = build(tmp_path)
    assert sup.is_audit_goal("kod analizi ve testleri çalıştır") is True
    assert sup.is_audit_goal("repo analizi yap") is True
    assert sup.is_audit_goal("raporu hazırla ve dosyaya yaz") is False

    async def _main():
        audit = await sup.submit("repo analizi yap", spawn=False)
        plan = await sup.submit("raporu hazırla ve dosyaya yaz", spawn=False)
        return audit, plan
    audit, plan = asyncio.run(_main())
    assert audit["kind"] == "supervisor"          # audit pipeline preserved
    assert plan["kind"] == "plan"                 # general planner pipeline
    assert plan["steps"][0]["worker"] == "planner"
    assert plan["budgets"]["replan_budget"] == 2
    # planner task that never runs stays PENDING with exactly one step
    assert engine.get(plan["id"])["status"] == "PENDING"


def test_without_planner_worker_legacy_path(tmp_path):
    """Back-compat: no registered planner → every goal uses the audit
    pipeline (previous behaviour), never an empty plan task."""
    engine = TaskEngine(db_path=str(tmp_path / "t.db"))
    workers = {"verification": VerificationWorker(), "report": ReportWorker()}
    sup = SupervisorAgent(engine, workers)
    t = asyncio.run(sup.submit("raporu hazırla", spawn=False))
    assert t["kind"] == "supervisor"
    assert any(s["worker"] == "code_analysis" for s in t["steps"])


# ------------------------------------------------------ happy path chain
def test_full_chain_plan_to_completion(tmp_path):
    out = tmp_path / "out"
    engine, sup, reg, brain, events, _ = build(tmp_path, responses=[
        plan_json(
            {"tool": "write_text",
             "arguments": {"path": str(out / "rapor.md"), "content": "ultron raporu"},
             "reason": "raporu yaz"},
            {"tool": "read_text",
             "arguments": {"path": str(out / "rapor.md")},
             "reason": "yazılanı oku", "depends_on": [0]},
        ),
    ])
    t = asyncio.run(sup.submit("raporu hazırla ve dosyaya yaz", spawn=False))
    done = run_to_end(engine, sup, t["id"])

    assert done["status"] == "COMPLETED"
    assert done["kind"] == "plan"
    # staged pipeline: plan → write → read → verification → report
    workers_seq = [s["worker"] for s in done["steps"]]
    assert workers_seq == ["planner", "tool_step", "tool_step",
                           "verification", "report"]
    assert all(s["status"] == "SUCCESS" for s in done["steps"])
    # the plan step staged the remaining steps into the persisted task
    plan_step = done["steps"][0]
    assert plan_step["result"]["output"]["staged_steps"] == 4
    # real effect happened and is independently verified
    assert (out / "rapor.md").read_text(encoding="utf-8") == "ultron raporu"
    w = done["steps"][1]["result"]["output"]
    assert w["executed"] is True and w["succeeded"] is True
    assert w["verified"]["mode"] == "filesystem"
    assert w["verified"]["verified"] is True
    # read step verified by shape (non-empty)
    r = done["steps"][2]["result"]["output"]
    assert r["verified"]["verified"] is True
    # evidence-based final report persisted in the task result
    assert "final_report" in (done["result"] or {})
    assert done["result"]["ok"] is True


def test_write_step_requires_approval_and_is_one_shot(tmp_path):
    target = tmp_path / "seed.txt"
    target.write_text("keep me", encoding="utf-8")
    engine, sup, reg, brain, events, _ = build(tmp_path, responses=[
        plan_json({"tool": "delete_path", "arguments": {"path": str(target)},
                   "reason": "eski dosyayı sil"}),
    ])
    t = asyncio.run(sup.submit("eski dosyayı temizle", spawn=False))

    async def _first_phase():
        engine.spawn(t["id"], sup._runner)
        for _ in range(100):
            cur = engine.get(t["id"])
            if cur["status"] == "WAITING_APPROVAL":
                return cur
            await asyncio.sleep(0.05)
        raise AssertionError(f"never waited: {engine.get(t['id'])['status']}")
    waiting = asyncio.run(_first_phase())
    # approval was NOT granted yet, nothing executed, nothing deleted
    assert waiting["status"] == "WAITING_APPROVAL"
    assert waiting["user_approved"] is False
    assert target.exists() and reg.executed == []
    assert any(e["status"] == "APPROVAL_REQUIRED" for e in events)

    # server-side approve → persisted one-shot flag → re-spawn
    res = engine.approve(t["id"])
    assert res["ok"] is True
    approved_task = engine.get(t["id"])
    assert approved_task["status"] == "RUNNING"
    assert approved_task["user_approved"] is True

    done = run_to_end(engine, sup, t["id"])
    assert done["status"] == "COMPLETED"
    assert not target.exists()  # real effect
    step_out = done["steps"][1]["result"]["output"]
    assert step_out["executed"] is True
    assert step_out["verified"]["mode"] == "filesystem"
    assert step_out["verified"]["verified"] is True  # path gone observed
    # ONE-SHOT: approval consumed by the executed step
    assert engine.get(t["id"])["user_approved"] is False


# ------------------------------------------- verification independence
def test_lying_tool_claimed_success_fails_verification_and_replans(tmp_path):
    """The tool CLAIMS success (returns a path string) but writes nothing.
    The independent filesystem verifier must FAIL the step, and the
    supervisor must replan (bounded) instead of trusting the tool."""
    lie = tmp_path / "out" / "rapor.lie"
    good = tmp_path / "out" / "rapor.md"
    engine, sup, reg, brain, events, _ = build(tmp_path, responses=[
        plan_json({"tool": "write_text",
                   "arguments": {"path": str(lie), "content": "x"},
                   "reason": "rapor"}),
        # replan: corrected path
        plan_json({"tool": "write_text",
                   "arguments": {"path": str(good), "content": "ultron raporu"},
                   "reason": "dzeltilmis"}),
    ])
    t = asyncio.run(sup.submit("raporu yaz", spawn=False))
    done = run_to_end(engine, sup, t["id"])

    assert done["status"] == "COMPLETED"
    statuses = [(s["worker"], s["status"]) for s in done["steps"]]
    # lying step FAILED (kept in history, superseded by the replan)
    tool_steps = [s for s in done["steps"] if s["worker"] == "tool_step"]
    assert tool_steps[0]["status"] == "FAILED"
    assert tool_steps[0]["result"]["output"]["verified"]["verified"] is False
    assert tool_steps[0]["result"]["output"]["replanned"] is True
    # corrected step succeeded and produced the real effect
    assert tool_steps[1]["status"] == "SUCCESS"
    assert good.exists()
    # budget decremented 2 → 1
    assert engine.get(t["id"])["budgets"]["replan_budget"] == 1
    assert any(e["status"] == "REPLAN" for e in events)


def test_replan_budget_exhausted_honest_failure(tmp_path):
    lie = tmp_path / "out" / "rapor.lie"
    engine, sup, reg, brain, events, _ = build(tmp_path, responses=[
        plan_json({"tool": "write_text",
                   "arguments": {"path": str(lie), "content": "x"},
                   "reason": "rapor"}),
    ])
    t = asyncio.run(sup.submit("raporu yaz", budgets={"replan_budget": 0},
                               spawn=False))
    done = run_to_end(engine, sup, t["id"])
    assert done["status"] == "FAILED"
    step = [s for s in done["steps"] if s["worker"] == "tool_step"][0]
    out = step["result"]["output"]
    assert out["executed"] is True and out["succeeded"] is True
    assert out["verified"]["verified"] is False  # claim rejected
    assert not lie.exists()


def test_hard_block_cannot_be_approved_through(tmp_path):
    """write_text targeting the security core (app/security/*) is HARD
    BLOCKED by the self-coding boundary. Even with user_approved=True the
    step must fail with executed=False — approval never overrides policy."""
    engine, sup, reg, brain, events, _ = build(tmp_path, responses=[
        plan_json({"tool": "write_text",
                   "arguments": {"path": "app/security/vault.py",
                                 "content": "# malicious"},
                   "reason": "güvenlik çekirdeğini değiştir"}),
    ])
    t = asyncio.run(sup.submit("güvenlik dosyasını değiştir", spawn=False))
    # pre-grant approval directly (simulating an already-approved task)
    task = engine.get(t["id"])
    task["user_approved"] = True
    engine._save(task)
    assert engine.get(t["id"])["user_approved"] is True

    done = run_to_end(engine, sup, t["id"])
    assert done["status"] == "FAILED"
    step = [s for s in done["steps"] if s["worker"] == "tool_step"][0]
    out = step["result"]["output"]
    assert out["executed"] is False          # never dispatched
    assert out["verified"]["mode"] == "policy"
    assert out["verified"]["verified"] is False
    assert reg.executed == []                # no tool fn ran at all
    assert "güvenlik" in out["error"]
    # the real security core is untouched
    assert (Path(__file__).parent.parent / "app" / "security" / "vault.py").exists()


# ------------------------------------------------ honest WAITING_BRAIN
def test_brain_down_is_waiting_brain_not_failure(tmp_path):
    out = tmp_path / "out" / "rapor.md"
    engine, sup, reg, brain, events, _ = build(
        tmp_path, brain_error=ConnectionError("ollama down (127.0.0.1:11434)"))
    t = asyncio.run(sup.submit("raporu hazırla", spawn=False))

    async def _first_phase():
        engine.spawn(t["id"], sup._runner)
        for _ in range(100):
            cur = engine.get(t["id"])
            if cur["status"] == "WAITING_BRAIN":
                return cur
            if cur["status"] in ("FAILED", "COMPLETED"):
                raise AssertionError(f"unexpected {cur['status']}")
            await asyncio.sleep(0.05)
        raise AssertionError("never WAITING_BRAIN")
    waiting = asyncio.run(_first_phase())
    assert waiting["checkpoint"]["waiting_brain_for"] == "plan"
    assert waiting["steps"][0]["status"] == "PENDING"  # not consumed
    assert not any(s.get("result") for s in waiting["steps"])  # nothing fabricated

    # brain comes back → resume → completes
    brain.error = None
    brain.responses = [plan_json(
        {"tool": "write_text",
         "arguments": {"path": str(out), "content": "gecikmeli rapor"},
         "reason": "rapor"})]
    engine.resume_brain(t["id"])
    done = run_to_end(engine, sup, t["id"])
    assert done["status"] == "COMPLETED"
    assert out.exists()


# ------------------------------------------------------ outcome learning
def test_outcome_learning_writes_record_the_planner_reads(tmp_path):
    out = tmp_path / "out" / "rapor.md"
    engine, sup, reg, brain, events, planner = build(tmp_path, responses=[
        plan_json({"tool": "write_text",
                   "arguments": {"path": str(out), "content": "rapor"},
                   "reason": "rapor"})])
    t = asyncio.run(sup.submit("haftalık raporu yaz", spawn=False))
    done = run_to_end(engine, sup, t["id"])
    assert done["status"] == "COMPLETED"

    # server.Hub.learn_outcome logic bound to a minimal hub (no full server
    # boot): uses the REAL OutcomeLearner against the completed task
    import server as server_mod
    mem = FakeMemory()
    mini = type("MiniHub", (), {})()
    mini.task_engine = engine
    mini.outcome_learner = OutcomeLearner(memory=mem)
    mini.durable_bus = None
    server_mod.Hub.learn_outcome(mini, done["id"])

    assert len(mem.records) == 1
    kind, text = mem.records[0]
    assert kind == "task_outcome"
    assert text.startswith("Verified outcome task=")
    assert "goal=haftalık raporu yaz" in text
    assert "status=SUCCEEDED" in text

    # the Planner's OutcomePlanningContext consumes exactly this format
    # (SemanticMemory.search hit shape: (score, kind, content, created))
    class SemSearch:
        def search(self, query, limit=6):
            return [(0.9, "task_outcome", text, "now")]
    ctx = OutcomePlanningContext(SemSearch())
    built = ctx.build("haftalık raporu yaz")
    assert "haftalık raporu yaz" in built
    assert "SUCCEEDED" in built


def test_failed_task_writes_no_outcome(tmp_path):
    engine, sup, reg, brain, events, _ = build(tmp_path, responses=[
        plan_json({"tool": "write_text",
                   "arguments": {"path": str(tmp_path / "x.lie"), "content": "x"},
                   "reason": "rapor"})])
    t = asyncio.run(sup.submit("raporu yaz", budgets={"replan_budget": 0},
                               spawn=False))
    done = run_to_end(engine, sup, t["id"])
    assert done["status"] == "FAILED"

    import server as server_mod
    mem = FakeMemory()
    mini = type("MiniHub", (), {})()
    mini.task_engine = engine
    mini.outcome_learner = OutcomeLearner(memory=mem)
    server_mod.Hub.learn_outcome(mini, done["id"])
    assert mem.records == []  # only VERIFIED successful outcomes are learned


# --------------------------------------------------------- planner world
def test_planner_receives_world_context(tmp_path):
    reg = Registry(tmp_path)
    brain = FakeBrain([plan_json({"tool": "list_directory",
                                  "arguments": {"root": str(tmp_path)},
                                  "reason": "bak"})])
    planner = Planner(brain, reg,
                      world_fn=lambda: "Kullanıcı konumu: Mersin. Cihaz: Linux sunucu, ekran yok.")
    planner.make_plan("duruma göre planla")
    system = brain.messages[0][0]["content"]
    assert "dünya/durum bağlamı" in system
    assert "Mersin" in system


def test_planner_world_fn_failure_degrades(tmp_path):
    reg = Registry(tmp_path)
    brain = FakeBrain([plan_json({"tool": "list_directory",
                                  "arguments": {"root": str(tmp_path)},
                                  "reason": "bak"})])

    def broken():
        raise RuntimeError("world model down")
    planner = Planner(brain, reg, world_fn=broken)
    out = planner.make_plan("planla")
    assert out["steps"][0]["tool"] == "list_directory"


# ---------------------------------------------------- scheduler wiring
def test_scheduler_fires_through_supervisor_pipeline(tmp_path):
    out = tmp_path / "out" / "saatlik.txt"
    engine, sup, reg, brain, events, _ = build(tmp_path, responses=[
        plan_json({"tool": "write_text",
                   "arguments": {"path": str(out), "content": "saatlik rapor"},
                   "reason": "rapor"}),
        # scheduler may fire more than once in the window; provide a spare
        plan_json({"tool": "write_text",
                   "arguments": {"path": str(out), "content": "saatlik rapor"},
                   "reason": "rapor"}),
    ])
    from app.tasks.scheduler import TaskScheduler

    async def _submit(goal, kind, budgets, needs):
        return await sup.submit(goal, budgets=budgets or None, spawn=True)

    sched = TaskScheduler(engine, db_path=str(tmp_path / "sched.db"),
                          submit_fn=_submit)
    res = sched.add_cron("saatlik raporu yaz", "0 * * * *")
    assert res["ok"] is True
    # force the schedule due (cron math not under test here)
    with sqlite3.connect(str(tmp_path / "sched.db")) as db:
        db.execute("UPDATE schedules SET next_run=? WHERE id=?",
                   (time.time() - 1, res["id"]))

    async def _main():
        fired = await sched.async_tick()
        assert len(fired) == 1
        # the unified pipeline ran: wait for terminal state
        tid = fired[0]["id"]
        deadline = time.time() + 20
        while time.time() < deadline:
            t = engine.get(tid)
            if t["status"] == "WAITING_APPROVAL":
                if engine.approve(tid)["ok"]:
                    engine.spawn(tid, sup._runner)
            if t["status"] in ("COMPLETED", "FAILED"):
                return t, sched
            await asyncio.sleep(0.05)
        raise TimeoutError(engine.get(tid)["status"])
    done, sched = asyncio.run(_main())
    assert done["status"] == "COMPLETED"
    assert done["kind"] == "plan"          # routed through the planner path
    assert sched.fires == 1
    assert out.exists()


# ------------------------------------------------ StepVerifier unit contract
def test_step_verifier_real_observations(tmp_path):
    v = StepVerifier()
    p = tmp_path / "a.txt"
    p.write_text("içerik", encoding="utf-8")

    assert v.verify("write_text", {"path": str(p)}, None)["verified"] is True
    assert v.verify("delete_path", {"path": str(p)}, None)["verified"] is False
    p.unlink()
    assert v.verify("delete_path", {"path": str(p)}, None)["verified"] is True

    # process table: a REAL spawned process, observed gone after kill
    import psutil
    proc = subprocess.Popen(["sleep", "30"])
    assert v.verify("process_kill", {"pid": proc.pid}, None)["verified"] is False
    proc.kill(); proc.wait()
    assert v.verify("process_kill", {"pid": proc.pid}, None)["verified"] is True

    # shape verification for read-only tools
    assert v.verify("read_text", {}, "içerik")["verified"] is True
    assert v.verify("read_text", {}, "")["verified"] is False

    # honest unknown for unobservable tools
    un = v.verify("open_url", {"url": "https://x"}, "ok")
    assert un["verified"] is None and un["mode"] == "none"

    # rename/move follow the real tool signatures
    q = tmp_path / "b.txt"; q.write_text("2", encoding="utf-8")
    assert v.verify("rename_path", {"path": str(q), "new_name": "c.txt"}, None)["verified"] is False


def test_step_verifier_expect_override(tmp_path):
    v = StepVerifier()
    real = tmp_path / "real.txt"
    real.write_text("merhaba dünya", encoding="utf-8")
    ok = v.verify("read_text", {"path": str(real)}, "returned text",
                  expect={"exists": str(real), "text_contains": "merhaba"})
    assert ok["mode"] == "expectation" and ok["verified"] is True
    bad = v.verify("read_text", {"path": str(real)}, "returned text",
                   expect={"exists": str(tmp_path / "yok.txt")})
    assert bad["verified"] is False


def test_user_approved_column_persists_across_engine_restart(tmp_path):
    engine, sup, reg, brain, events, _ = build(tmp_path)
    t = engine.create("goal", kind="plan", steps=[{"label": "s", "worker": "planner"}])
    task = engine.get(t["id"])
    task["user_approved"] = True
    engine._save(task)
    engine2 = TaskEngine(db_path=str(tmp_path / "tasks.db"))
    assert engine2.get(t["id"])["user_approved"] is True
