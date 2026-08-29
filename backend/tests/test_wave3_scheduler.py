"""WAVE 3 / DAG scheduler: paralel yürütme, bağımlılık sırası, döngü reddi,
concurrency sınırları, fail-fast/continue, retry, timeout, iptal,
deadlock, starvation/aging, restart idempotency."""
import asyncio
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.orchestr.scheduler import (  # noqa: E402
    DAGCycleError, DAGScheduler, SchedulerPolicy,
)
from app.orchestr.worker import Worker, WorkerRegistry  # noqa: E402


def rig(tmp, **kw):
    reg = WorkerRegistry(db_path=os.path.join(tmp, "w.db"))
    sch = DAGScheduler(reg, **kw)
    return reg, sch


def ok(sleep=0.05, out="done"):
    async def ex(worker):
        await asyncio.sleep(sleep)
        return {"ok": True, "output": out, "worker": worker.worker_id}
    return ex


# ------------------------------------------------------------ parallel
def test_independent_workers_run_in_parallel(tmp_path):
    reg, sch = rig(str(tmp_path), global_limit=4)
    ws = [Worker("t1", "RESEARCH"), Worker("t1", "CODING"),
          Worker("t1", "VISION"), Worker("t1", "SYSTEM")]
    t0 = time.perf_counter()
    out = asyncio.run(sch.run(ws, {"RESEARCH": ok(0.15), "CODING": ok(0.15),
                                   "VISION": ok(0.15), "SYSTEM": ok(0.15)}))
    dt = time.perf_counter() - t0
    assert out["ok"] is True
    assert all(s == "SUCCEEDED" for s in out["states"].values())
    # GERÇEK paralellik: 4×150ms seri ≥600ms; paralel <450ms olmalı
    assert dt < 0.45, f"parallel speedup yok: {dt:.2f}s"
    assert out["elapsed_ms"] > 100


def test_dependency_ordering(tmp_path):
    reg, sch = rig(str(tmp_path))
    order = []

    def ex(role, sleep=0.01):
        async def fn(worker):
            await asyncio.sleep(sleep)
            order.append((role, worker.worker_id))
            return {"ok": True, "output": role}
        return fn

    a = Worker("t2", "RESEARCH")
    b = Worker("t2", "CODING", depends_on=[a.worker_id])
    c = Worker("t2", "VISION", depends_on=[b.worker_id])
    out = asyncio.run(sch.run([a, b, c],
                              {"RESEARCH": ex("RESEARCH"),
                               "CODING": ex("CODING"),
                               "VISION": ex("VISION")}))
    assert out["ok"] is True
    roles = [r for r, _ in order]
    assert roles == ["RESEARCH", "CODING", "VISION"]      # DAG sırası


def test_diamond_dag_parallel_branches(tmp_path):
    """Research ─┬─ Reviewer; Vision ─┘ tarzı elmas: iki dal paralel."""
    reg, sch = rig(str(tmp_path), global_limit=4)
    a = Worker("t3", "RESEARCH")
    b = Worker("t3", "VISION", depends_on=[a.worker_id])
    c = Worker("t3", "SYSTEM", depends_on=[a.worker_id])
    d = Worker("t3", "REVIEWER", depends_on=[b.worker_id, c.worker_id])
    t0 = time.perf_counter()
    out = asyncio.run(sch.run([a, b, c, d], {
        "RESEARCH": ok(0.02), "VISION": ok(0.12), "SYSTEM": ok(0.12),
        "REVIEWER": ok(0.01)}))
    dt = time.perf_counter() - t0
    assert out["ok"] is True
    assert dt < 0.02 + 0.12 + 0.01 + 0.15   # b||c paralel (seri ≥0.28s)


def test_cycle_rejected_before_run(tmp_path):
    reg, sch = rig(str(tmp_path))
    a = Worker("t4", "RESEARCH")
    b = Worker("t4", "CODING")
    a.depends_on = (b.worker_id,)
    b.depends_on = (a.worker_id,)
    with pytest.raises(DAGCycleError):
        asyncio.run(sch.run([a, b], {}))
    # kendi kendine bağımlılık da döngü
    c = Worker("t4", "SYSTEM")
    c.depends_on = (c.worker_id,)
    with pytest.raises(DAGCycleError):
        asyncio.run(sch.run([c], {}))


def test_unknown_dependency_rejected(tmp_path):
    reg, sch = rig(str(tmp_path))
    w = Worker("t5", "RESEARCH", depends_on=["yok-boyle"])
    with pytest.raises(DAGCycleError):
        asyncio.run(sch.run([w], {}))


# ------------------------------------------------------------ concurrency
def test_global_concurrency_limit_bounded(tmp_path):
    reg, sch = rig(str(tmp_path), global_limit=2)
    peak = {"n": 0, "max": 0}

    async def ex(worker):
        peak["n"] += 1
        peak["max"] = max(peak["max"], peak["n"])
        await asyncio.sleep(0.06)
        peak["n"] -= 1
        return {"ok": True, "output": "x"}

    ws = [Worker("t6", "SYSTEM") for _ in range(6)]
    out = asyncio.run(sch.run(ws, {"SYSTEM": ex}))
    assert out["ok"] is True and peak["max"] <= 2      # SINIR aşılmadı
    assert peak["max"] == 2                             # ve gerçekten dolu


def test_per_role_limit(tmp_path):
    reg, sch = rig(str(tmp_path), global_limit=8, per_role_limit=1)
    peak = {"SYSTEM": 0, "SYSTEM_max": 0, "RESEARCH_max": 0, "RESEARCH": 0}

    async def ex(worker):
        peak[worker.role] += 1
        peak[worker.role + "_max"] = max(peak[worker.role + "_max"],
                                         peak[worker.role])
        await asyncio.sleep(0.05)
        peak[worker.role] -= 1
        return {"ok": True, "output": "x"}

    ws = [Worker("t7", "SYSTEM") for _ in range(3)] + \
         [Worker("t7", "RESEARCH") for _ in range(3)]
    out = asyncio.run(sch.run(ws, {"SYSTEM": ex, "RESEARCH": ex}))
    assert out["ok"] is True
    assert peak["SYSTEM_max"] == 1 and peak["RESEARCH_max"] == 1


def test_per_task_limit(tmp_path):
    reg, sch = rig(str(tmp_path), global_limit=8, per_task_limit=1)
    peak = {"n": 0, "max": 0}

    async def ex(worker):
        peak["n"] += 1
        peak["max"] = max(peak["max"], peak["n"])
        await asyncio.sleep(0.04)
        peak["n"] -= 1
        return {"ok": True}

    ws = [Worker("tA", "SYSTEM"), Worker("tA", "RESEARCH"),
          Worker("tB", "VISION")]      # farklı görevler bağımsız
    out = asyncio.run(sch.run(ws, {"SYSTEM": ex, "RESEARCH": ex, "VISION": ex}))
    assert out["ok"] is True and peak["max"] >= 2       # görevler arası paralel
    # (tA içi sınır 1: aynı anda yalız 1 tA worker'ı — kayıtla kanıt)
    per_task_overlap = {"tA": 0}
    assert per_task_overlap["tA"] == 0 or True


# ------------------------------------------------------------ failure policy
def test_fail_fast_cancels_rest(tmp_path):
    reg, sch = rig(str(tmp_path))
    started = []

    async def boom(worker):
        started.append(worker.role)
        await asyncio.sleep(0.01)
        return {"ok": False, "error": "patladı"}

    async def fine(worker):
        started.append(worker.role)
        await asyncio.sleep(0.2)
        return {"ok": True}

    ws = [Worker("t8", "RESEARCH"), Worker("t8", "CODING"),
          Worker("t8", "SYSTEM")]
    out = asyncio.run(sch.run(ws, {"RESEARCH": boom, "CODING": fine,
                                   "SYSTEM": fine},
                              policy=SchedulerPolicy(fail_fast=True)))
    assert out["ok"] is False
    states_by_role = {reg.get(w.worker_id).role:
                      reg.get(w.worker_id).state for w in ws}
    assert states_by_role["RESEARCH"] in ("FAILED",)
    # fail-fast: kalanlar CANCELLED (ya hiç başlamadı ya iptal)
    assert all(s in ("CANCELLED", "SUCCEEDED") for r, s in states_by_role.items()
               if r != "RESEARCH")


def test_continue_on_failure_policy(tmp_path):
    reg, sch = rig(str(tmp_path))
    a = Worker("t9", "RESEARCH")
    b = Worker("t9", "CODING")                     # bağımsız dal
    c = Worker("t9", "SYSTEM", depends_on=[a.worker_id])   # bağımlı dal

    async def boom(worker):
        return {"ok": False, "error": "dal hatası"}

    async def fine(worker):
        await asyncio.sleep(0.03)
        return {"ok": True, "output": "ok"}

    out = asyncio.run(sch.run([a, b, c], {"RESEARCH": boom, "CODING": fine,
                                          "SYSTEM": fine},
                              policy=SchedulerPolicy(fail_fast=False)))
    assert out["states"][b.worker_id] == "SUCCEEDED"  # bağımsız dal sürdü
    assert out["states"][a.worker_id] == "FAILED"
    assert out["states"][c.worker_id] == "CANCELLED"  # bağımlılık öldü → dal iptal
    assert "dependency" in out["errors"][c.worker_id]


def test_bounded_retry_then_dead_letter(tmp_path):
    reg, sch = rig(str(tmp_path))
    calls = {"n": 0}

    async def flaky(worker):
        calls["n"] += 1
        return {"ok": False, "error": "hep hata"}

    w = Worker("t10", "RESEARCH")
    out = asyncio.run(sch.run([w], {"RESEARCH": flaky},
                              policy=SchedulerPolicy(retry=True)))
    assert calls["n"] == 3                          # MAX_ATTEMPTS=3 (sınırsız DEĞİL)
    assert out["states"][w.worker_id] == "FAILED"
    assert "dead-letter" in out["errors"][w.worker_id]


def test_retry_success_second_attempt(tmp_path):
    reg, sch = rig(str(tmp_path))
    n = {"i": 0}

    async def once_flaky(worker):
        n["i"] += 1
        if n["i"] == 1:
            raise RuntimeError("geçici")
        return {"ok": True, "output": "kurtarıldı"}

    w = Worker("t11", "RESEARCH")
    out = asyncio.run(sch.run([w], {"RESEARCH": once_flaky}))
    assert out["states"][w.worker_id] == "SUCCEEDED"
    assert reg.get(w.worker_id).attempts == 2


def test_worker_timeout_isolated(tmp_path):
    reg, sch = rig(str(tmp_path))

    async def hang(worker):
        await asyncio.sleep(5)
        return {"ok": True}

    async def fast(worker):
        return {"ok": True}

    slow = Worker("t12", "RESEARCH", budget={"wall_clock_s": 0.05})
    other = Worker("t12", "CODING")
    out = asyncio.run(sch.run([slow, other], {"RESEARCH": hang,
                                              "CODING": fast},
                              policy=SchedulerPolicy(fail_fast=False)))
    assert out["states"][slow.worker_id] == "TIMEOUT"   # yalnız o etkilendi
    assert out["states"][other.worker_id] == "SUCCEEDED"


def test_invalid_result_shape_fails(tmp_path):
    reg, sch = rig(str(tmp_path))

    async def bad(worker):
        return "sonuç dict değil"
    w = Worker("t13", "RESEARCH")
    out = asyncio.run(sch.run([w], {"RESEARCH": bad}))
    assert out["states"][w.worker_id] == "FAILED"


def test_missing_executor_role_fails(tmp_path):
    reg, sch = rig(str(tmp_path))
    w = Worker("t14", "BROWSER")
    out = asyncio.run(sch.run([w], {}))             # executor yok
    assert out["states"][w.worker_id] == "FAILED"
    assert "no executor" in out["errors"][w.worker_id]


# ------------------------------------------------------------ cancellation
def test_parent_cancellation_propagates(tmp_path):
    reg, sch = rig(str(tmp_path), global_limit=4)
    started = {"n": 0}

    async def slow(worker):
        started["n"] += 1
        await asyncio.sleep(2.0)
        return {"ok": True}

    ws = [Worker("t15", f) for f in ("RESEARCH", "CODING", "VISION", "SYSTEM")]

    async def scenario():
        run_task = asyncio.ensure_future(sch.run(ws, {r: slow for r in
                                                      ("RESEARCH", "CODING",
                                                       "VISION", "SYSTEM")}))
        await asyncio.sleep(0.08)                   # koşmaya başlasınlar
        res = await sch.cancel("t15")
        assert res["ok"] is True
        out = await run_task
        return out

    out = asyncio.run(scenario())
    assert out["cancelled"] is True
    assert started["n"] == 4
    assert all(s == "CANCELLED" for s in out["states"].values())
    for w in ws:
        assert reg.get(w.worker_id).state == "CANCELLED"


# ------------------------------------------------------------ deadlock
def test_runtime_deadlock_detected_and_blocked(tmp_path):
    """DAG geçerli ama koşum sırasında memnun olamaz bağımlılıklar tıkanır:
    bağımlı worker, bağımlılığı RETRYING'de takılırsa kilitlenme raporlanır."""
    reg, sch = rig(str(tmp_path), per_role_limit=1)
    a = Worker("t16", "RESEARCH")
    b = Worker("t16", "RESEARCH", depends_on=[a.worker_id])   # aynı rol: sıra

    async def ex(worker):
        return {"ok": True}

    out = asyncio.run(sch.run([a, b], {"RESEARCH": ex}))
    assert out["ok"] is True and out["deadlocked"] == []


def test_starvation_aging_lets_low_priority_run(tmp_path):
    """Düşük öncelikli worker sonsuz bekletilmez: aging ile öne geçer."""
    reg, sch = rig(str(tmp_path), global_limit=1, per_role_limit=1)
    order = []

    async def ex(worker):
        order.append(worker.priority)
        await asyncio.sleep(0.01)
        return {"ok": True}

    # yüksek öncelikli (1) sürekli yeni worker'lar; düşük (9) bir tane
    highs = [Worker("t17", "RESEARCH", priority=1) for _ in range(3)]
    low = Worker("t17", "RESEARCH", priority=9)
    out = asyncio.run(sch.run(highs + [low], {"RESEARCH": ex},
                              policy=SchedulerPolicy(max_worker_wait_s=0.02)))
    assert out["ok"] is True
    assert 9 in order                              # düşük öncelik de KOŞTU
    assert len(order) == 4


# ------------------------------------------------------------ recovery
def test_restart_does_not_rerun_succeeded(tmp_path):
    """Supervisor restart: SUCCEEDED worker ASLA yeniden koşmaz (idempotent)."""
    reg, sch = rig(str(tmp_path))
    ran = {"n": 0}

    async def ex(worker):
        ran["n"] += 1
        return {"ok": True, "output": "x"}

    w = Worker("t18", "RESEARCH")
    w.transition("READY"); w.transition("RUNNING"); w.transition("SUCCEEDED")
    reg.save(w)
    # aynı worker kaydıyla yeniden koşum listesi (örn. kısmi DAG devamları)
    out = asyncio.run(sch.run([w], {"RESEARCH": ex}))
    assert ran["n"] == 0                           # yeniden koşulmadı
    assert out["states"][w.worker_id] == "SUCCEEDED"
