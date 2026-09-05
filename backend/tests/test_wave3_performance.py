"""WAVE 3 / Performance — gerçek ölçüm, cömüş eşikler (CI salınımı için).

İlkeler: sahte paralellik YASAK — speedup iddiası gerçek sequential baseline
ile ölçülür ve raporlanır; kazanç yoksa eşik geçilemez.
"""
import asyncio
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.observability.trace import Tracer  # noqa: E402
from app.orchestr.artifacts import ArtifactManager  # noqa: E402
from app.orchestr.budgets import BudgetPool  # noqa: E402
from app.orchestr.messages import AgentMessageBus  # noqa: E402
from app.orchestr.orchestrator import SupervisorOrchestrator  # noqa: E402
from app.orchestr.scheduler import DAGScheduler, SchedulerPolicy  # noqa: E402
from app.orchestr.tokens import CapabilityTokenAuthority  # noqa: E402
from app.orchestr.worker import WorkerRegistry  # noqa: E402
from app.security.redaction import redact  # noqa: E402


def rig(tmp, limit=8):
    reg = WorkerRegistry(db_path=os.path.join(tmp, "w.db"))
    orch = SupervisorOrchestrator(
        reg, DAGScheduler(reg, global_limit=limit),
        CapabilityTokenAuthority(),
        ArtifactManager(db_path=os.path.join(tmp, "a.db"), redact_fn=redact),
        AgentMessageBus(db_path=os.path.join(tmp, "m.db"), registry=reg,
                        redact_fn=redact),
        tracer=Tracer(os.path.join(tmp, "t.jsonl")))
    return orch


def sleeper(s=0.08):
    async def fn(worker, ctx):
        await asyncio.sleep(s)
        return {"ok": True, "output": "x", "confidence": 0.9,
                "evidence": ["e"]}
    return fn


# ------------------------------------------------------------- startup
def test_worker_startup_overhead(tmp_path):
    """Worker yaratma + kaydetme maliyeti (DB dahil) ölçülür."""
    orch = rig(str(tmp_path))
    t0 = time.perf_counter()
    ws = orch.plan("perf-startup", [{"role": "RESEARCH"} for _ in range(50)])
    for w in ws:
        orch.registry.save(w)
    dt = time.perf_counter() - t0
    per = dt / 50
    assert per < 0.05, f"worker startup {per*1000:.2f} ms/worker (>50ms)"


# ------------------------------------------------------------- speedup
def test_parallel_speedup_vs_sequential_baseline(tmp_path):
    """4 bağımsız worker × 80ms: REAL sequential baseline ile karşılaştırma."""
    orch = rig(str(tmp_path), limit=4)
    n, slice_s = 4, 0.08

    # --- sequential baseline: aynı executor'lar tek tek (paralellik YOK)
    async def sequential():
        times = []
        for i in range(n):
            ws = orch.plan(f"perf-seq-{i}", [{"role": "RESEARCH"}])
            t0 = time.perf_counter()
            await orch.execute(f"perf-seq-{i}", ws,
                               {"RESEARCH": sleeper(slice_s)})
            times.append(time.perf_counter() - t0)
        return sum(times)

    seq = asyncio.run(sequential())

    # --- parallel: DAG'de aynı iş
    ws = orch.plan("perf-par",
                   [{"role": r} for r in ("RESEARCH", "CODING", "SYSTEM",
                                          "VISION")])
    t0 = time.perf_counter()
    out = asyncio.run(orch.execute("perf-par", ws,
                                   {r: sleeper(slice_s) for r in
                                    ("RESEARCH", "CODING", "SYSTEM",
                                     "VISION")}))
    par = time.perf_counter() - t0
    speedup = seq / par
    assert all(s == "SUCCEEDED" for s in out["states"].values())
    # cömüş eşik: en az 1.6× (ideal 4×; yük dahil) — altındaysa concurrency
    # gereksizdemek eşik GEÇİLEMEZ (kazanç kanıtı şart)
    assert speedup > 1.6, (f"speedup {speedup:.2f}x < 1.6x "
                           f"(seq={seq:.3f}s par={par:.3f}s) — gereksiz "
                           f"concurrency")


# ------------------------------------------------------------- scheduler
def test_scheduler_overhead_vs_raw_asyncio(tmp_path):
    """16 no-op worker: scheduler DAG yönetimi + FSM + kayıt ek maliyeti."""
    orch = rig(str(tmp_path), limit=8)

    async def noop(worker, ctx):
        return {"ok": True, "output": 1, "confidence": 0.9,
                "evidence": ["e"]}

    async def raw():
        await asyncio.gather(*[noop(None, None) for _ in range(16)])

    t0 = time.perf_counter()
    for _ in range(3):                      # ısınma dahil ortamda ölç
        asyncio.run(raw())
    raw_dt = (time.perf_counter() - t0) / 3

    ws = orch.plan("perf-sched", [{"role": "SYSTEM"} for _ in range(16)])
    t0 = time.perf_counter()
    out = asyncio.run(orch.execute("perf-sched", ws, {"SYSTEM": noop}))
    sched_dt = time.perf_counter() - t0

    assert out["pipeline"]["results"] == 16
    overhead = sched_dt - raw_dt
    # cömüş eşik: DAG/FSM/DB/trace + pipeline yaratma < 1.2 s toplam
    assert overhead < 1.2, (f"scheduler overhead {overhead*1000:.1f} ms "
                            f"(raw={raw_dt*1000:.1f} ms, "
                            f"sched={sched_dt*1000:.1f} ms)")


# ------------------------------------------------------------- messaging
def test_message_bus_latency(tmp_path):
    orch = rig(str(tmp_path), limit=2)
    ws = orch.plan("perf-msg", [{"role": "RESEARCH"}, {"role": "CODING"}])
    for w in ws:
        orch.registry.save(w)
    a, b = ws[0].worker_id, ws[1].worker_id
    lat = []
    for i in range(100):
        t0 = time.perf_counter()
        r = orch.bus.send(a, b, f"m.{i}", {"i": i})
        orch.bus.inbox(b)
        lat.append(time.perf_counter() - t0)
        assert r["status"] == "sent"
    p50 = statistics.median(lat) * 1000
    assert p50 < 5.0, f"bus p50 {p50:.2f} ms (>5ms) — mesaj gecikmesi yüksek"


# ------------------------------------------------------------- pipeline
def test_result_pipeline_latency_100_results(tmp_path):
    """Doğrulama+çelişki+review+judge+merge hattı 100 sonuçta ölçülür."""
    from app.orchestr.results import (detect_conflicts, judge, merge,
                                      review, validate_result)
    t0 = time.perf_counter()
    results = []
    for i in range(100):
        r = {"result_id": f"r{i}", "worker_id": f"w{i}", "role": "SYSTEM",
             "task_id": "perf-merge", "trace_id": None,
             "payload": {"n": i % 7}, "confidence": 0.8 + (i % 3) * 0.05,
             "status": "OK", "artifact_refs": [], "evidence": ["e1", "e2"],
             "subject": f"s{i % 7}", "provenance": "TOOL"}
        validate_result(r)
        results.append(r)
    conflicts = detect_conflicts(results)
    note = review(results, reviewer_worker_id="rev-1")
    verdict = judge(results, note, judge_worker_id="judge-1",
                    conflicts_resolved=[])
    merged = merge(results)
    dt = time.perf_counter() - t0
    assert verdict["verdict"] == "ACCEPT" and len(merged["producers"]) == 100
    assert dt < 2.0, f"pipeline 100 sonuç {dt*1000:.1f} ms (>2000ms)"


# ------------------------------------------------------------- concurrency
def test_max_concurrency_observed_matches_limit(tmp_path):
    """global_limit=4: gözlenen eş zamanlı worker sayısı TAM 4 (sınırsız
    YASAK; limit gerçekçe tutulur) ve toplam süre sınırlı paralellikte."""
    orch = rig(str(tmp_path), limit=4)
    state = {"active": 0, "peak": 0}

    async def tracked(worker, ctx):
        state["active"] += 1
        state["peak"] = max(state["peak"], state["active"])
        await asyncio.sleep(0.06)
        state["active"] -= 1
        return {"ok": True, "output": "x", "confidence": 0.9,
                "evidence": ["e"]}

    roles = ["RESEARCH", "CODING", "SYSTEM", "VISION"] * 2 + ["SYSTEM", "COMPUTER"]
    ws = orch.plan("perf-lim", [{"role": r} for r in roles])
    t0 = time.perf_counter()
    out = asyncio.run(orch.execute("perf-lim", ws,
                                   {r: tracked for r in set(roles)}))
    dt = time.perf_counter() - t0
    assert state["peak"] == 4, f"peak {state['peak']} != 4 (limit aşıldı " \
                               f"ya da kullanılmadı)"
    assert all(s == "SUCCEEDED" for s in out["states"].values())
    # 10 iş × 60ms / 4 slot ≈ 150ms; cömüş tavan 3× (CI salınımı)
    assert dt < 0.06 * 10 / 4 * 3, f"toplam {dt:.3f}s — slot verimsiz"


# ------------------------------------------------------------- budget check
def test_budget_gate_overhead_per_pump(tmp_path):
    """resource_ok() kapısı şişirilmiş: 2000 kontrol duyarlı ölçülür."""
    calls = {"n": 0}

    def sampler():
        calls["n"] += 1
        return (10.0, 100.0)                # cpu_pct, mem_mb

    pool = BudgetPool({"tokens": 100000}, resource_sampler=sampler)
    t0 = time.perf_counter()
    for _ in range(2000):
        ok = pool.resource_ok()
        assert ok is True
    dt = time.perf_counter() - t0
    per = dt / 2000 * 1000
    assert per < 0.1, f"resource_ok {per*1000:.1f} µs/call (>100µs)"
