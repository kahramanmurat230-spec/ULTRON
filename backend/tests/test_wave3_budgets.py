"""WAVE 3 / Budget isolation: parent üst sınır, worker cap'leri, tüketim
denetimi, plan reddi, kaynak kapısı (cpu/mem), bütçe hatası retry'sız."""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.orchestr.budgets import BudgetExceeded, BudgetPool  # noqa: E402
from app.orchestr.scheduler import DAGScheduler, SchedulerPolicy  # noqa: E402
from app.orchestr.worker import Worker, WorkerRegistry  # noqa: E402


def rig(tmp, **kw):
    reg = WorkerRegistry(db_path=os.path.join(tmp, "w.db"))
    return reg, DAGScheduler(reg, **kw)


# ------------------------------------------------------------ plan check
def test_plan_rejects_workers_exceeding_parent(tmp_path):
    pool = BudgetPool({"token_budget": 100})
    ws = [Worker("t", "RESEARCH", budget={"token_budget": 60}),
          Worker("t", "CODING", budget={"token_budget": 60})]
    with pytest.raises(BudgetExceeded, match="parent"):
        pool.plan_check(ws)


def test_plan_accepts_within_parent(tmp_path):
    pool = BudgetPool({"token_budget": 100, "cost_budget": 2.0})
    ws = [Worker("t", "RESEARCH", budget={"token_budget": 60}),
          Worker("t", "CODING", budget={"token_budget": 40,
                                        "cost_budget": 2.0})]
    pool.plan_check(ws)                      # 60+40=100 ≤ 100


# ------------------------------------------------------------ consume
def test_consume_enforces_worker_and_parent_caps(tmp_path):
    pool = BudgetPool({"token_budget": 100, "tool_budget": 10})
    pool.consume("w1", tokens=30, tools=3, worker_caps={"token_budget": 50})
    with pytest.raises(BudgetExceeded, match="worker"):
        pool.consume("w1", tokens=30, tools=0, worker_caps={"token_budget": 50})
    # worker cap'i olmayan ikinci worker parent sınırını aşar
    with pytest.raises(BudgetExceeded, match="parent"):
        pool.consume("w2", tokens=80, tools=0)
    u = pool.usage()
    assert u["consumed"]["token_budget"] == 30
    assert u["per_worker"]["w1"]["token_budget"] == 30


def test_budget_axes_independent(tmp_path):
    pool = BudgetPool({"cost_budget": 1.0})
    pool.consume("w", cost=0.6)
    with pytest.raises(BudgetExceeded):
        pool.consume("w", cost=0.6)          # 1.2 > 1.0
    pool.consume("w", tokens=999)            # token sınırı yok → serbest


# ------------------------------------------------------------ integration
def test_scheduler_rejects_over_budget_plan(tmp_path):
    reg, sch = rig(str(tmp_path))
    pool = BudgetPool({"token_budget": 10})
    ws = [Worker("t", "RESEARCH", budget={"token_budget": 8}),
          Worker("t", "CODING", budget={"token_budget": 8})]

    async def ex(worker):
        return {"ok": True}
    with pytest.raises(BudgetExceeded):
        asyncio.run(sch.run(ws, {"RESEARCH": ex, "CODING": ex},
                            budget_pool=pool))


def test_scheduler_usage_consumption_parent_cap(tmp_path):
    """Worker'lar başarı raporladı ama tüketim toplamı parent'ı aşarsa:
    aşan worker FAILED (retry yok), dürüst bütçe hatası."""
    reg, sch = rig(str(tmp_path))
    pool = BudgetPool({"token_budget": 100})
    w1 = Worker("t", "RESEARCH")
    w2 = Worker("t", "CODING")

    async def big(worker):
        return {"ok": True, "output": "x", "usage": {"tokens": 80, "tools": 1}}

    out = asyncio.run(sch.run([w1, w2], {"RESEARCH": big, "CODING": big},
                              policy=SchedulerPolicy(fail_fast=False),
                              budget_pool=pool))
    states = [out["states"][w1.worker_id], out["states"][w2.worker_id]]
    assert states.count("SUCCEEDED") == 1        # ilk 80 token kabul
    assert states.count("FAILED") == 1           # ikinci 80 → 160 > 100 RED
    failed = w1.worker_id if out["states"][w1.worker_id] == "FAILED" else w2.worker_id
    assert "parent budget exceeded" in out["errors"][failed]
    assert pool.usage()["consumed"]["token_budget"] == 80.0


def test_scheduler_usage_within_budget_ok(tmp_path):
    reg, sch = rig(str(tmp_path))
    pool = BudgetPool({"token_budget": 200, "tool_budget": 10})

    async def ex(worker):
        return {"ok": True, "output": "x",
                "usage": {"tokens": 50, "tools": 2, "cost": 0.1}}

    ws = [Worker("t", "RESEARCH"), Worker("t", "CODING")]
    out = asyncio.run(sch.run(ws, {"RESEARCH": ex, "CODING": ex},
                              budget_pool=pool))
    assert out["ok"] is True
    u = pool.usage()
    assert u["consumed"]["token_budget"] == 100
    assert u["consumed"]["tool_budget"] == 4


def test_resource_gate_stops_launch_under_pressure(tmp_path):
    """Parent cpu/mem sınırı aşılırsa yeni worker başlamaz; rahatlayınca sürer."""
    pressure = {"on": True}

    def sampler():
        return (95.0, 50.0) if pressure["on"] else (5.0, 50.0)

    reg, sch = rig(str(tmp_path), global_limit=2)
    pool = BudgetPool({"cpu_max_pct": 80}, resource_sampler=sampler)
    ran = []

    async def ex(worker):
        ran.append(worker.worker_id)
        return {"ok": True}

    ws = [Worker("t", "RESEARCH"), Worker("t", "CODING")]

    async def scenario():
        task = asyncio.ensure_future(sch.run(ws, {"RESEARCH": ex,
                                                  "CODING": ex},
                                             budget_pool=pool))
        await asyncio.sleep(0.05)
        assert ran == []                       # baskı altında launch YOK
        pressure["on"] = False                 # rahatladı
        return await task

    out = asyncio.run(scenario())
    assert out["ok"] is True and len(ran) == 2


def test_budget_exceeded_is_not_retried(tmp_path):
    """Bütçe hatası kalıcıdır: retry döngüsüne girmez."""
    reg, sch = rig(str(tmp_path))
    pool = BudgetPool({"tool_budget": 1})
    calls = {"n": 0}

    async def ex(worker):
        calls["n"] += 1
        return {"ok": True, "usage": {"tools": 5}}   # parent'ı eziyor

    w = Worker("t", "RESEARCH")
    out = asyncio.run(sch.run([w], {"RESEARCH": ex}, budget_pool=pool))
    assert calls["n"] == 1                          # retry YOK
    assert out["states"][w.worker_id] == "FAILED"
    assert "parent budget exceeded" in out["errors"][w.worker_id]
