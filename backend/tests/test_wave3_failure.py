"""WAVE 3 / Failure injection — 14 arıza senaryosu, kurtarma GERÇEK.

Her enjeksiyon canlı bileşende tetiklenir (sahte crash YOK): scheduler,
bus, artifacts, orchestrator. Beklenti: sessiz başarı YOK, kurtarma
idempotent, limitler tutar.
"""
import asyncio
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.observability.trace import Tracer  # noqa: E402
from app.orchestr.artifacts import ArtifactError, ArtifactManager  # noqa: E402
from app.orchestr.messages import AgentMessageBus  # noqa: E402
from app.orchestr.orchestrator import SupervisorOrchestrator  # noqa: E402
from app.orchestr.scheduler import (  # noqa: E402
    DAGCycleError, DAGScheduler, SchedulerPolicy,
)
from app.orchestr.tokens import CapabilityTokenAuthority  # noqa: E402
from app.orchestr.worker import WorkerRegistry  # noqa: E402
from app.security.redaction import redact  # noqa: E402


def rig(tmp, task_id="fail-task", limit=4):
    reg = WorkerRegistry(db_path=os.path.join(tmp, "w.db"))
    sch = DAGScheduler(reg, global_limit=limit)
    auth = CapabilityTokenAuthority()
    arts = ArtifactManager(db_path=os.path.join(tmp, "a.db"), redact_fn=redact)
    bus = AgentMessageBus(db_path=os.path.join(tmp, "m.db"), registry=reg,
                          redact_fn=redact)
    events = []
    orch = SupervisorOrchestrator(
        reg, sch, auth, arts, bus, tracer=Tracer(os.path.join(tmp, "t.jsonl")),
        event_publisher=lambda t, p: events.append((t, p)))
    return orch, events


def ok(output="x", **kw):
    async def fn(worker, ctx):
        await asyncio.sleep(0.02)
        d = {"ok": True, "output": output, "confidence": 0.9,
             "evidence": ["e"]}
        d.update(kw)
        return d
    return fn


def boom(msg="enjeksiyon: crash"):
    async def fn(worker, ctx):
        raise RuntimeError(msg)
    return fn


# F1 — worker crash (executor istisnası)
def test_f1_worker_crash_fails_not_hangs(tmp_path):
    orch, _ = rig(str(tmp_path))
    ws = orch.plan("f1", [{"role": "RESEARCH"}, {"role": "CODING"}])
    out = asyncio.run(orch.execute(
        "f1", ws, {"RESEARCH": boom(), "CODING": ok()},
        policy=SchedulerPolicy(fail_fast=False)))
    assert out["states"][ws[0].worker_id] == "FAILED"
    assert "crash" in out["errors"][ws[0].worker_id]
    assert out["states"][ws[1].worker_id] == "SUCCEEDED"  # diğeri yaşar


# F2 — worker timeout
def test_f2_worker_timeout_isolated(tmp_path):
    orch, _ = rig(str(tmp_path))

    async def sleepy(worker, ctx):
        await asyncio.sleep(5)

    ws = orch.plan("f2", [{"role": "RESEARCH"}, {"role": "CODING"}])
    out = asyncio.run(orch.execute(
        "f2", ws, {"RESEARCH": sleepy, "CODING": ok()},
        policy=SchedulerPolicy(fail_fast=False, worker_timeout_s=0.15)))
    assert out["states"][ws[0].worker_id] == "TIMEOUT"   # yalnız o etkilenir
    assert out["states"][ws[1].worker_id] == "SUCCEEDED"


# F3 — supervisor restart (in-process DB yeniden açma + kurtarma planı)
def test_f3_supervisor_restart_recovers_from_db(tmp_path):
    tmp = str(tmp_path)
    orch, _ = rig(tmp, task_id="f3")
    ws = orch.plan("f3", [{"role": "RESEARCH"}, {"role": "CODING"}])
    asyncio.run(orch.execute("f3", ws, {"RESEARCH": ok("r"),
                                        "CODING": boom()},
                             policy=SchedulerPolicy(fail_fast=False,
                                                    retry=False)))
    # süreç "öldü": registry'yi KAPAT, yeni supervisor AYNI db ile açılır
    states = {w.worker_id: w.state for w in orch.registry.for_task("f3")}
    orch.registry.close()
    orch2, _ = rig(tmp, task_id="f3")
    plan = orch2.recover("f3")
    again = {w.worker_id: w.state for w in orch2.registry.for_task("f3")}
    assert again == states                              # durum kaybı YOK
    assert plan["keep"] == [ws[0].worker_id]            # SUCCEEDED korunur
    assert ws[1].worker_id in plan["retry"]             # FAILED yeniden denenir


# F4 — message loss (tüketici çöldü) → mesaj dayanıklı, kaybolmaz
def test_f4_message_loss_durability(tmp_path):
    tmp = str(tmp_path)
    orch, _ = rig(tmp, task_id="f4")
    ws = orch.plan("f4", [{"role": "RESEARCH"}, {"role": "CODING"}])
    for w in ws:
        orch.registry.save(w)
    orch.bus.send(ws[0].worker_id, ws[1].worker_id, "data", {"n": 1})
    # tüketici inbox'a hiç bakmadan "çöktü" → yeni süreç aynı db'yi açar
    orch.bus.close()
    reg2 = WorkerRegistry(db_path=os.path.join(tmp, "w.db"))
    bus2 = AgentMessageBus(db_path=os.path.join(tmp, "m.db"), registry=reg2,
                           redact_fn=redact)
    msgs = bus2.inbox(ws[1].worker_id)
    assert len(msgs) == 1 and msgs[0]["payload"]["n"] == 1  # kayıp YOK


# F5 — duplicate mesaj
def test_f5_duplicate_message_swallowed(tmp_path):
    orch, _ = rig(str(tmp_path), task_id="f5")
    ws = orch.plan("f5", [{"role": "RESEARCH"}, {"role": "CODING"}])
    for w in ws:
        orch.registry.save(w)
    r1 = orch.bus.send(ws[0].worker_id, ws[1].worker_id, "data", {"n": 1})
    r2 = orch.bus.send(ws[0].worker_id, ws[1].worker_id, "data", {"n": 1},
                       message_id=r1["message_id"])
    assert r2["status"] != "sent"
    assert len(orch.bus.inbox(ws[1].worker_id)) == 1


# F6 — out-of-order (yanıt istekten önce IŞLENEMEZ; inbox sıralı gelir)
def test_f6_out_of_order_inbox_is_stable(tmp_path):
    orch, _ = rig(str(tmp_path), task_id="f6")
    ws = orch.plan("f6", [{"role": "RESEARCH"}, {"role": "CODING"}])
    for w in ws:
        orch.registry.save(w)
    for n in (3, 1, 2):  # ters sırada gönder (türler benzersiz: dedup yok)
        orch.bus.send(ws[0].worker_id, ws[1].worker_id, f"data.{n}", {"n": n})
    got = [m["payload"]["n"] for m in orch.bus.inbox(ws[1].worker_id)]
    assert got == [3, 1, 2]      # teslim sırası gönderim sırası (rowid) —
    # yeniden sıralama TAHMİNİ değil, kayıt düzeni deterministik


# F7 — deadlock: DAG döngüsü reddi + wait_for timeout
def test_f7_deadlock_cycle_rejected_and_wait_times_out(tmp_path):
    orch, _ = rig(str(tmp_path), task_id="f7")
    a, b = orch.plan("f7", [
        {"role": "RESEARCH", "name": "a"},
        {"role": "CODING", "name": "b", "depends_on": ["a"]}])
    a.depends_on = list(a.depends_on) + [b.worker_id]  # a↔b döngüsü
    with pytest.raises(DAGCycleError):
        asyncio.run(orch.scheduler.run([a, b], {}))     # plan reddi
    # bekleme kilitlenmesi: timeout ile None, takılma yok
    async def waiter():
        return await orch.bus.wait_for("ghost", timeout=0.05)
    assert asyncio.run(waiter()) is None


# F8 — DB kesintisi (bağlantı kapandı) → LOUD failure
def test_f8_db_interruption_fails_loudly(tmp_path):
    tmp = str(tmp_path)
    orch, _ = rig(tmp, task_id="f8")
    ws = orch.plan("f8", [{"role": "RESEARCH"}])
    for w in ws:
        orch.registry.save(w)
    orch.registry.close()                     # DB kesintisi simülasyonu
    with pytest.raises(sqlite3.ProgrammingError):
        orch.registry.save(ws[0])             # sessiz başarı YOK


# F9 — artifact arızası (aynı id çakışması) → ikinci RED, içerik değişmez
def test_f9_artifact_failure_immutable(tmp_path):
    orch, _ = rig(str(tmp_path), task_id="f9")
    a1 = orch.artifacts.create("f9", "w1", "report", "orijinal")
    with pytest.raises(ArtifactError):
        orch.artifacts.create("f9", "w2", "report", "sanirma",
                              artifact_id=a1["artifact_id"])
    got = orch.artifacts.get(a1["artifact_id"], requester_worker="w1",
                             requester_task="f9")
    assert got["content"] == "orijinal"       # üzerine yazılmadı
    assert orch.artifacts.verify_integrity(a1["artifact_id"])["ok"] is True


# F10 — reviewer arızası → pipeline çökmez, bağımsız değerlendirme sürer
def test_f10_reviewer_failure_pipeline_survives(tmp_path):
    orch, _ = rig(str(tmp_path))
    ws = orch.plan("f10", [{"role": "RESEARCH"}, {"role": "VISION"},
                           {"role": "REVIEWER"}])
    out = asyncio.run(orch.execute(
        "f10", ws, {"RESEARCH": ok("r"), "VISION": ok("v"),
                    "REVIEWER": boom("reviewer düştü")},
        policy=SchedulerPolicy(fail_fast=False)))
    assert out["states"][ws[2].worker_id] == "FAILED"
    pipe = out["pipeline"]
    assert pipe["results"] == 2               # sonuçlar incelendi
    assert pipe["verdict"]["verdict"] in ("ACCEPT", "RETRY")
    assert out["ok"] is False                 # görev bütünlüğü raporlandı


# F11 — judge kaynağı yok → supervisor-judge devralır, self-approval YOK
def test_f11_no_judge_worker_supervisor_judges(tmp_path):
    orch, _ = rig(str(tmp_path))
    ws = orch.plan("f11", [{"role": "RESEARCH"}])
    out = asyncio.run(orch.execute(
        "f11", ws, {"RESEARCH": ok("tek sonuç")}))
    assert out["pipeline"]["verdict"]["verdict"] in ("ACCEPT", "RETRY")
    # worker'ın kendi sonucunu onaylamadığı kanıt: RESEARCH SUCCEEDED ama
    # verdict RESEARCH'in worker_id'siyle imzalanmamış (judge bağımsız)
    assert out["pipeline"]["verdict"]["judge"] != ws[0].worker_id


# F12 — kısmi DAG arızası: bağımlı dal düşer, bağımsız dal tamamlanır
def test_f12_partial_dag_failure(tmp_path):
    orch, _ = rig(str(tmp_path))
    ws = orch.plan("f12", [
        {"role": "RESEARCH", "name": "ok1"},
        {"role": "CODING", "name": "bad"},
        {"role": "SYSTEM", "name": "dep", "depends_on": ["bad"]},
        {"role": "VISION", "name": "indep"}])
    out = asyncio.run(orch.execute(
        "f12", ws, {"RESEARCH": ok("a"), "CODING": boom(),
                    "SYSTEM": ok("s"), "VISION": ok("v")},
        policy=SchedulerPolicy(fail_fast=False)))
    st = out["states"]
    assert st[ws[0].worker_id] == "SUCCEEDED"
    assert st[ws[1].worker_id] == "FAILED"
    assert st[ws[2].worker_id] in ("BLOCKED", "PENDING", "CANCELLED")
    assert st[ws[3].worker_id] == "SUCCEEDED"  # bağımsız dal etkilenmedi


# F13 — çalışma ortasında iptal: koşan durur, henüz başlamayan CANCELLED
def test_f13_cancellation_mid_run(tmp_path):
    orch, _ = rig(str(tmp_path), limit=1)     # tek slot → sırayla
    ws = orch.plan("f13", [{"role": "RESEARCH"}, {"role": "CODING"}])

    async def slow(worker, ctx):
        await asyncio.sleep(2)

    async def scenario():
        fut = asyncio.ensure_future(
            orch.execute("f13", ws, {"RESEARCH": slow, "CODING": slow}))
        await asyncio.sleep(0.1)              # ilki RUNNING'de
        await orch.scheduler.cancel("f13")
        return await fut

    out = asyncio.run(scenario())
    assert out["cancelled"] is True
    assert all(s in ("CANCELLED",) for s in out["states"].values())


# F14 — parent crash (execute iptal edildi) → RUNNING kalıntıları
# recover() yeniden koşulur, SUCCEEDED ASLA yeniden koşmaz
def test_f14_parent_crash_orphan_running_recovered(tmp_path):
    orch, _ = rig(str(tmp_path), limit=1)
    ws = orch.plan("f14", [{"role": "RESEARCH", "name": "r"},
                           {"role": "CODING", "name": "c",
                            "depends_on": ["r"]}])
    marks = []

    async def r(worker, ctx):
        marks.append("r")
        return {"ok": True, "output": "r", "confidence": 0.9,
                "evidence": ["e"]}

    async def slow_c(worker, ctx):
        await asyncio.sleep(3)

    async def scenario():
        fut = asyncio.ensure_future(orch.execute(
            "f14", ws, {"RESEARCH": r, "CODING": slow_c}))
        await asyncio.sleep(0.15)             # r bitti, c RUNNING
        fut.cancel()                          # PARENT CRASH simülasyonu
        try:
            await fut
        except (asyncio.CancelledError, Exception):
            pass

    asyncio.run(scenario())
    plan = orch.recover("f14")
    r_id = ws[0].worker_id
    assert plan["keep"] == [r_id]             # SUCCEEDED korundu
    # CANCELLED terminal: yeniden koşMAZ; kalan iş REPLAN ile yeni worker'a
    assert ws[1].worker_id not in plan["retry"]
    workers = [w for w in orch.registry.for_task("f14")
               if w.state == "SUCCEEDED"] + orch.plan(
        "f14", [{"role": "CODING", "name": "c2"}])

    async def c2(worker, ctx):
        marks.append("c")
        return {"ok": True, "output": "c", "confidence": 0.9,
                "evidence": ["e"]}

    async def r2(worker, ctx):
        marks.append("r")                     # çağrılmamalı
        return {"ok": True, "output": "r", "confidence": 0.9,
                "evidence": ["e"]}

    out = asyncio.run(orch.execute("f14", workers,
                                   {"RESEARCH": r2, "CODING": c2}))
    assert marks.count("r") == 1              # r BİR KEZ (idempotent)
    assert "c" in marks
    assert out["states"][r_id] == "SUCCEEDED"
