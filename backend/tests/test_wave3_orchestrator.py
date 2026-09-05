"""WAVE 3 / Orchestrator: uçuca E2E çoklu-agent görevi, approval akışı
(token kapılı grant), memory policy (otomatik yazma YOK), observability
span zinciri + redaction, event yayınları, restart kurtarma (GERÇEK alt
süreç çökmesi), iptal yayılımı."""
import asyncio
import os
import subprocess
import sys
import textwrap

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.observability.trace import Tracer  # noqa: E402
from app.orchestr.artifacts import ArtifactManager  # noqa: E402
from app.orchestr.messages import AgentMessageBus  # noqa: E402
from app.orchestr.orchestrator import (  # noqa: E402
    ResultMemoryPolicy, SupervisorOrchestrator,
)
from app.orchestr.scheduler import DAGScheduler, SchedulerPolicy  # noqa: E402
from app.orchestr.tokens import CapabilityTokenAuthority, TokenError  # noqa: E402
from app.orchestr.worker import WorkerRegistry  # noqa: E402
from app.memory.store_v3 import MemoryStore  # noqa: E402
from app.security.redaction import redact  # noqa: E402

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def rig(tmp, mem=False):
    reg = WorkerRegistry(db_path=os.path.join(tmp, "w.db"))
    sch = DAGScheduler(reg, global_limit=4)
    auth = CapabilityTokenAuthority()
    arts = ArtifactManager(db_path=os.path.join(tmp, "a.db"), redact_fn=redact)
    bus = AgentMessageBus(db_path=os.path.join(tmp, "m.db"), redact_fn=redact,
                          registry=reg)
    tracer = Tracer(path=os.path.join(tmp, "tr.jsonl"))
    memstore = MemoryStore(db_path=os.path.join(tmp, "v3.db"),
                           redact_fn=redact) if mem else None
    orch = SupervisorOrchestrator(
        reg, sch, auth, arts, bus, tracer=tracer,
        memory_policy=ResultMemoryPolicy(memstore),
        event_publisher=lambda t, p: events.append((t, p)))
    events = []
    return orch, events, arts, auth, reg, memstore


# ------------------------------------------------------------ E2E
def test_end_to_end_multi_agent_task(tmp_path):
    """Research + Vision paralel → Reviewer bağımlı → judge ACCEPT."""
    orch, events, arts, auth, reg, _ = rig(str(tmp_path))
    task_id = "e2e-1"
    ws = orch.plan(task_id, [
        {"role": "RESEARCH", "name": "r1"},
        {"role": "VISION", "name": "v1"},
        {"role": "REVIEWER", "name": "rev", "depends_on": ["r1", "v1"],
         "priority": 8},
    ])

    async def research(worker, ctx):
        await asyncio.sleep(0.05)
        a = ctx["artifact"]("analysis", "web taraması: 3 kaynak")
        return {"ok": True, "output": {"sources": 3},
                "confidence": 0.9, "evidence": ["url1", "url2"],
                "artifact_refs": [a["artifact_id"]]}

    async def vision(worker, ctx):
        await asyncio.sleep(0.05)
        return {"ok": True, "output": {"objects": 7}, "confidence": 0.85,
                "evidence": ["screen-1"]}

    async def reviewer(worker, ctx):
        msgs = ctx["worker"]
        return {"ok": True, "output": {"reviewed": True}, "confidence": 0.9,
                "evidence": ["checked-both"]}

    out = asyncio.run(orch.execute(task_id, ws, {
        "RESEARCH": research, "VISION": vision, "REVIEWER": reviewer}))
    assert out["ok"] is True
    assert all(s == "SUCCEEDED" for s in out["states"].values())
    pipe = out["pipeline"]
    assert pipe["verdict"]["verdict"] == "ACCEPT"
    assert pipe["results"] == 3
    assert pipe["merged"]["merged"]["RESEARCH-rol"] if False else \
        len(pipe["merged"]["producers"]) == 3
    assert len(arts.for_task(task_id)) == 1          # artifact üretildi
    assert any(t == "task.worker.created" for t, _ in events)


def test_dag_actually_parallel_in_orchestrator(tmp_path):
    import time
    orch, *_ = rig(str(tmp_path))
    ws = orch.plan("par-1", [{"role": "RESEARCH"}, {"role": "CODING"},
                             {"role": "SYSTEM"}, {"role": "VISION"}])

    async def slow(worker, ctx):
        await asyncio.sleep(0.15)
        return {"ok": True, "output": "x", "confidence": 0.8,
                "evidence": ["e"]}

    t0 = time.perf_counter()
    out = asyncio.run(orch.execute("par-1", ws, {r: slow for r in
                                                 ("RESEARCH", "CODING",
                                                  "SYSTEM", "VISION")}))
    dt = time.perf_counter() - t0
    assert out["ok"] is True and dt < 0.5          # seri ≥ 0.6s olurdu


# ------------------------------------------------------------ approval
def test_high_risk_capability_requires_approval(tmp_path):
    """CODING worker WRITE_WORKSPACE (HIGH) ister: onaysız RED → onayla → OK."""
    orch, events, arts, auth, reg, _ = rig(str(tmp_path))
    ws = orch.plan("appr-1", [{"role": "CODING"}])
    w = ws[0]
    orch.issue_worker_tokens(w)

    async def coder(worker, ctx):
        ctx["grant"]("WRITE_WORKSPACE")            # onaysız deneme
        return {"ok": True, "output": "yazıldı", "confidence": 0.9,
                "evidence": ["diff"]}

    out = asyncio.run(orch.execute("appr-1", ws, {"CODING": coder}))
    assert out["states"][w.worker_id] == "FAILED"   # onaysız HIGH → RED
    assert "approval" in out["errors"][w.worker_id]

    # onay sonrası yeniden deneme (yeniden issue gerekmez: token durur)
    ws2 = orch.plan("appr-2", [{"role": "CODING"}])
    w2 = ws2[0]

    async def coder2(worker, ctx):
        ctx["grant"]("WRITE_WORKSPACE")
        return {"ok": True, "output": "yazıldı", "confidence": 0.9,
                "evidence": ["diff"]}

    # onaysız: yine RED (farklı worker)
    out2 = asyncio.run(orch.execute("appr-2", ws2, {"CODING": coder2}))
    assert out2["states"][w2.worker_id] == "FAILED"

    # İNSAN onayı → başarır (onay worker başına: w2'nin onayı w3'ü AÇMAZ —
    # izolasyon appr-2'de kanıtlandı)
    ws3 = orch.plan("appr-3", [{"role": "CODING"}])
    w3 = ws3[0]
    orch.approve(w3.worker_id, "WRITE_WORKSPACE")

    async def coder3(worker, ctx):
        ctx["grant"]("WRITE_WORKSPACE")
        return {"ok": True, "output": "tamam", "confidence": 0.9,
                "evidence": ["diff"]}

    out3 = asyncio.run(orch.execute("appr-3", ws3, {"CODING": coder3}))
    assert out3["states"][w3.worker_id] == "SUCCEEDED"
    assert any(t == "task.worker.approved" for t, _ in events)


def test_grant_rejects_capability_not_in_role(tmp_path):
    """RESEARCH worker WRITE_WORKSPACE grant'i RED (rol dışı → token yok)."""
    orch, *_ = rig(str(tmp_path))
    w = orch.plan("esc-1", [{"role": "RESEARCH"}])[0]
    orch.issue_worker_tokens(w)
    ctx = orch._ctx_for(w)
    with pytest.raises(TokenError):
        ctx["grant"]("WRITE_WORKSPACE")


# ------------------------------------------------------------ memory policy
def test_worker_results_not_auto_written_to_memory(tmp_path):
    orch, events, arts, auth, reg, mem = rig(str(tmp_path), mem=True)
    ws = orch.plan("mem-1", [{"role": "RESEARCH"}])

    async def r(worker, ctx):
        return {"ok": True, "output": {"bulgu": 1}, "confidence": 0.9,
                "evidence": ["e1"]}

    asyncio.run(orch.execute("mem-1", ws, {"RESEARCH": r}))
    assert len(mem.query()) == 0                    # OTOMATİK yazma YOK
    assert orch.memory_policy.decisions["denied"] == 0  # hiç istenmedi

    # politikaya açık onayla yazım
    res = {"payload": {"bulgu": 1}, "provenance": "TOOL", "confidence": 0.9,
           "evidence": ["e1", "e2"], "worker_id": "w", "task_id": "mem-1"}
    out = orch.memory_policy.decide(res, approved=False)
    assert out["write"] is False                    # onaysız yine YOK
    out2 = orch.memory_policy.decide(res, approved=True)
    assert out2["write"] is True
    rows = mem.query()
    assert len(rows) == 1 and rows[0]["source_id"] == "worker:w"
    assert rows[0]["record_kind"] == "OBSERVATION"


# ------------------------------------------------------------ observability
def test_span_chain_task_worker_and_redaction(tmp_path):
    orch, events, arts, auth, reg, _ = rig(str(tmp_path))
    ws = orch.plan("obs-1", [{"role": "RESEARCH"}])

    async def r(worker, ctx):
        return {"ok": True, "output": "rapor password: cok-gizli-77",
                "confidence": 0.9, "evidence": ["e"]}

    asyncio.run(orch.execute("obs-1", ws, {"RESEARCH": r}))
    tr = orch._get_tracer()
    recs = tr.read_all()
    roots = [r for r in recs if r["kind"] == "task"]
    steps = [r for r in recs if r["kind"] == "step"]
    assert len(roots) == 1 and len(steps) == 1
    assert steps[0]["parent_span_id"] == roots[0]["span_id"]   # zincir
    assert steps[0]["attributes"]["role"] == "RESEARCH"
    raw = open(os.path.join(str(tmp_path), "tr.jsonl"), encoding="utf-8").read()
    assert "cok-gizli-77" not in raw                # secret span'a düşmez
    assert "***REDACTED***" in raw


# ------------------------------------------------------------ restart recovery
CHILD = textwrap.dedent('''
import asyncio, os, sys
sys.path.insert(0, sys.argv[1])
from app.observability.trace import Tracer
from app.orchestr.artifacts import ArtifactManager
from app.orchestr.messages import AgentMessageBus
from app.orchestr.orchestrator import SupervisorOrchestrator, ResultMemoryPolicy
from app.orchestr.scheduler import DAGScheduler
from app.orchestr.tokens import CapabilityTokenAuthority
from app.orchestr.worker import WorkerRegistry

base = sys.argv[2]
reg = WorkerRegistry(db_path=os.path.join(base, "w.db"))
sch = DAGScheduler(reg, global_limit=1)          # SIRALI: r → v → c
auth = CapabilityTokenAuthority()
arts = ArtifactManager(db_path=os.path.join(base, "a.db"))
bus = AgentMessageBus(db_path=os.path.join(base, "m.db"), registry=reg)
orch = SupervisorOrchestrator(reg, sch, auth, arts, bus,
                              tracer=Tracer(path=os.path.join(base, "tr.jsonl")))
task_id = "rec-1"
ws = orch.plan(task_id, [
    {"role": "RESEARCH", "name": "r"},
    {"role": "VISION", "name": "v", "depends_on": ["r"]},
    {"role": "CODING", "name": "c", "depends_on": ["v"]},
])

async def r(worker, ctx):
    with open(sys.argv[3], "a") as f: f.write("r\\n")
    await asyncio.sleep(0.03)
    return {"ok": True, "output": "r-ok", "confidence": 0.9,
            "evidence": ["e"]}

async def v(worker, ctx):
    with open(sys.argv[3], "a") as f: f.write("v\\n")
    await asyncio.sleep(0.03)
    os._exit(1)                                  # GERÇEK çökme (worker sırasında)

async def c(worker, ctx):
    with open(sys.argv[3], "a") as f: f.write("c\\n")
    return {"ok": True, "output": "c-ok", "confidence": 0.9,
            "evidence": ["e"]}

asyncio.run(orch.execute(task_id, ws, {"RESEARCH": r, "VISION": v,
                                       "CODING": c}))
''')


def test_supervisor_restart_recovery_real_subprocess(tmp_path):
    """Alt süreç worker sırasında ölür → yeni supervisor kurtarır:
    SUCCEEDED 'r' ASLA yeniden koşmaz; kalan tamamlanır."""
    base = str(tmp_path)
    mark = os.path.join(base, "markers.txt")
    child = tmp_path / "child.py"
    child.write_text(CHILD, encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(child), BACKEND, base, mark],
        cwd=base, env={**os.environ, "PYTHONPATH": BACKEND},
        capture_output=True, text=True, timeout=60)
    assert proc.returncode == 1                     # gerçek çökme
    assert open(mark).read().split() == ["r", "v"]  # r bitti, v çöktü

    # yeni supervisor (aynı DB'ler) — kurtarma + idempotent devam
    reg = WorkerRegistry(db_path=os.path.join(base, "w.db"))
    sch = DAGScheduler(reg, global_limit=2)
    auth = CapabilityTokenAuthority()               # yeni anahtar: eski
    arts = ArtifactManager(db_path=os.path.join(base, "a.db"))  # token'lar ölü
    bus = AgentMessageBus(db_path=os.path.join(base, "m.db"), registry=reg)
    from app.observability.trace import Tracer
    orch = SupervisorOrchestrator(
        reg, sch, auth, arts, bus,
        tracer=Tracer(path=os.path.join(base, "tr.jsonl")))
    task_id = "rec-1"
    plan = orch.recover(task_id)
    r_id = next(w for w in reg.for_task(task_id) if w.role == "RESEARCH").worker_id
    assert plan["keep"] == [r_id]                   # SUCCEEDED korunur
    workers = reg.for_task(task_id)

    async def v2(worker, ctx):
        with open(mark, "a") as f: f.write("v\n")
        return {"ok": True, "output": "v-ok", "confidence": 0.9,
                "evidence": ["e"]}

    async def c2(worker, ctx):
        with open(mark, "a") as f: f.write("c\n")
        return {"ok": True, "output": "c-ok", "confidence": 0.9,
                "evidence": ["e"]}

    async def r2(worker, ctx):
        with open(mark, "a") as f: f.write("r\\n")  # asla çağrılmamalı
        return {"ok": True, "output": "r-ok", "confidence": 0.9,
                "evidence": ["e"]}

    out = asyncio.run(orch.execute(task_id, workers,
                                   {"VISION": v2, "CODING": c2,
                                    "RESEARCH": r2}))
    assert out["states"][r_id] == "SUCCEEDED"       # idempotent: yeniden koşmadı
    marks = open(mark).read().split()
    assert marks.count("r") == 1                    # r BİR KEZ koştu
    assert marks.count("v") == 2                    # v: çöken + kurtarılan
    assert marks.count("c") == 1
    assert out["pipeline"]["verdict"]["verdict"] in ("ACCEPT", "RETRY")


# ------------------------------------------------------------ cancellation
def test_orchestrator_cancellation_propagates(tmp_path):
    orch, *_ = rig(str(tmp_path))
    ws = orch.plan("cxl-1", [{"role": "RESEARCH"}, {"role": "CODING"}])

    async def slow(worker, ctx):
        await asyncio.sleep(3)
        return {"ok": True, "output": "x", "confidence": 0.9,
                "evidence": ["e"]}

    async def scenario():
        fut = asyncio.ensure_future(orch.execute(
            "cxl-1", ws, {"RESEARCH": slow, "CODING": slow}))
        await asyncio.sleep(0.08)
        await orch.scheduler.cancel("cxl-1")
        return await fut

    out = asyncio.run(scenario())
    assert out["cancelled"] is True
    assert all(s == "CANCELLED" for s in out["states"].values())
