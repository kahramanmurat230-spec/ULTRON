"""WAVE 1 / Scheduler + budgets + dead-letter:
cron parse/next, cron fire → new task instances, event triggers via local
EventBus, wall-clock/token/cost/cpu/mem budget enforcement, poison-task
dead-letter after 3 consecutive failures + manual requeue.
"""
import asyncio
import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.observability.trace import Tracer  # noqa: E402
from app.tasks.engine import DEFAULT_BUDGETS, TaskEngine  # noqa: E402
from app.tasks.scheduler import (  # noqa: E402
    EventBus, TaskScheduler, cron_matches, cron_next, parse_field,
)


def make_engine(tmp, **kw):
    return TaskEngine(db_path=os.path.join(tmp, "tasks.db"),
                      tracer=Tracer(path=os.path.join(tmp, "traces.jsonl")), **kw)


def make_sched(tmp, engine, **kw):
    return TaskScheduler(engine, db_path=os.path.join(tmp, "sched.db"), **kw)


def ok(out="ok"):
    async def runner(task, step, ctx):
        return {"ok": True, "output": out}
    return runner


# ------------------------------------------------------------ cron parser
def test_parse_field_star_step_range_list():
    assert parse_field("*", 0, 59) == set(range(60))
    assert parse_field("*/15", 0, 59) == {0, 15, 30, 45}
    assert parse_field("5-7", 0, 59) == {5, 6, 7}
    assert parse_field("1,3,5", 0, 59) == {1, 3, 5}
    assert parse_field("7", 0, 6, is_dow=True) == {0}   # pazar=7 → 0
    with pytest.raises(ValueError):
        parse_field(",", 0, 59)


def test_cron_matches_and_next():
    dt = datetime(2026, 8, 29, 9, 30, tzinfo=timezone.utc)  # cumartesi
    assert cron_matches("30 9 * * *", dt)
    assert cron_matches("* * 29 8 *", dt)
    assert cron_matches("30 9 * * 6", dt)                   # cumartesi=6
    assert not cron_matches("31 9 * * *", dt)
    nxt = cron_next("30 9 * * *", dt.timestamp())
    nd = datetime.fromtimestamp(nxt, tz=timezone.utc)
    assert (nd.minute, nd.hour) == (30, 9) and nd.date() > dt.date()
    every5 = cron_next("*/5 * * * *", dt.timestamp())
    assert datetime.fromtimestamp(every5, tz=timezone.utc).minute == 35
    with pytest.raises(ValueError):
        cron_next("bad expr", dt.timestamp())


# ------------------------------------------------------------ cron fire
def test_scheduler_cron_fire_creates_instance(tmp_path):
    e = make_engine(str(tmp_path))
    sch = make_sched(str(tmp_path), e, now=lambda: 1_000_000.0)
    s = sch.add_cron("günlük rapor", "30 9 * * *")
    assert s["ok"] and s["next_run"] > 1_000_000.0
    assert sch.due() == []                       # vade henüz gelmedi
    fired = sch.tick()
    assert fired == [] and sch.list_schedules()[0]["last_run"] is None
    # vade geldi: zamancı ileri sarılır → ateşleme YENİ örnek üretir
    real_next = sch.list_schedules()[0]["next_run"]
    sch.now = lambda: real_next + 1
    fired = sch.tick()
    assert len(fired) == 1 and fired[0]["goal"] == "günlük rapor"
    assert e.get(fired[0]["id"])["status"] == "PENDING"   # örnek bağımsız
    assert sch.list_schedules()[0]["last_run"] >= real_next
    assert sch.list_schedules()[0]["next_run"] > real_next  # yeniden planlandı
    assert sch.fires == 1


def test_scheduler_persists_across_restart(tmp_path):
    e = make_engine(str(tmp_path))
    sch = make_sched(str(tmp_path), e)
    sch.add_cron("haftalık", "0 0 * * 0")
    sch2 = make_sched(str(tmp_path), e)          # restart
    assert len(sch2.list_schedules()) == 1
    assert sch2.list_schedules()[0]["cron"] == "0 0 * * 0"


def test_scheduler_remove(tmp_path):
    e = make_engine(str(tmp_path))
    sch = make_sched(str(tmp_path), e)
    s = sch.add_cron("x", "* * * * *")
    assert sch.remove_schedule(s["id"])["ok"] is True
    assert sch.list_schedules() == []


# ------------------------------------------------------------ event triggers
def test_event_bus_dispatch_and_isolation(tmp_path):
    bus = EventBus()
    got = []
    bus.subscribe("world.change.**", lambda t, p: got.append((t, p)))
    boom = []
    bus.subscribe("world.change.**", lambda t, p: (_ for _ in ()).throw(RuntimeError("x")))
    bus.subscribe("other.*", lambda t, p: got.append(("other", p)))
    n = bus.publish("world.change.screen", {"app": "terminal"})
    assert n == 2 and got == [("world.change.screen", {"app": "terminal"})]
    assert bus.dispatch_errors == 1              # abone hatası yayını bozmadı
    bus.publish("other.ping")
    assert got[-1][0] == "other"


def test_event_trigger_creates_task(tmp_path):
    e = make_engine(str(tmp_path))
    sch = make_sched(str(tmp_path), e)
    tr = sch.add_trigger("world.change.battery.low", "şarj hatırlat")
    assert tr["ok"]
    n = sch.bus.publish("world.change.battery.low", {"pct": 12})
    assert n == 1 and sch.fires == 1
    tasks = e.list()
    assert len(tasks) == 1 and tasks[0]["goal"] == "şarj hatırlat"
    assert tasks[0]["template_key"] == "event:world.change.battery.low"
    assert sch.list_triggers()[0]["fire_count"] == 0  # fire_count handle_event yolunda değil


def test_event_trigger_no_match_no_task(tmp_path):
    e = make_engine(str(tmp_path))
    sch = make_sched(str(tmp_path), e)
    sch.add_trigger("world.change.disk.full", "temizlik")
    sch.bus.publish("world.change.screen", {})
    assert e.list() == []


# ------------------------------------------------------------ budgets
def test_default_budgets_structure_additive():
    for k in ("max_iterations", "timeout_s", "tool_budget", "retry_budget",
              "step_timeout_s"):
        assert k in DEFAULT_BUDGETS                  # eski anahtarlar duruyor
    for k in ("wall_clock_s", "token_budget", "cost_budget", "cpu_max_pct",
              "mem_max_mb"):
        assert DEFAULT_BUDGETS[k] is None            # additive, varsayılan sınırsız


def test_wall_clock_budget_tighter_deadline(tmp_path):
    e = make_engine(str(tmp_path))
    t = e.create("g", steps=[{"label": "s", "worker": "w"}],
                 budgets={"timeout_s": 60, "wall_clock_s": 0.05})

    async def slow(task, step, ctx):
        await asyncio.sleep(0.15)
        return {"ok": True, "output": "x"}

    out = asyncio.run(e.execute(t["id"], slow))
    assert out["status"] == "FAILED"
    assert "timeout budget exhausted" in out["error"]   # wall_clock kazandı


def test_token_and_cost_budget_enforced(tmp_path):
    e = make_engine(str(tmp_path))
    t = e.create("g", steps=[{"label": "a", "worker": "w"},
                             {"label": "b", "worker": "w"}],
                 budgets={"token_budget": 100})

    async def runner(task, step, ctx):
        ctx["usage"]["tokens"] += 80                  # runner kullanımı raporlar
        return {"ok": True, "output": "x"}

    out = asyncio.run(e.execute(t["id"], runner))
    assert out["status"] == "FAILED"
    assert "token_budget exceeded" in out["error"]

    t2 = e.create("g2", steps=[{"label": "a", "worker": "w"}],
                  budgets={"cost_budget": 1.0})

    async def costly(task, step, ctx):
        ctx["usage"]["cost"] += 2.0
        return {"ok": True, "output": "x"}

    out2 = asyncio.run(e.execute(t2["id"], costly))
    assert out2["status"] == "FAILED" and "cost_budget exceeded" in out2["error"]


def test_mem_budget_enforced(tmp_path):
    calls = []

    def sampler():                       # gerçek örnekleyici yerine enjekte
        calls.append(1)
        return (5.0, 9999.0)             # 9999MB — limit üstü

    e = make_engine(str(tmp_path), resource_sampler=sampler)
    t = e.create("g", steps=[{"label": "s", "worker": "w"}],
                 budgets={"mem_max_mb": 100})
    out = asyncio.run(e.execute(t["id"], ok()))
    assert out["status"] == "FAILED"
    assert "memory_budget exceeded" in out["error"] and calls


def test_cpu_budget_enforced(tmp_path):
    def sampler():
        return (95.0, 10.0)              # %95 CPU — limit üstü

    e = make_engine(str(tmp_path), resource_sampler=sampler)
    t = e.create("g", steps=[{"label": "s", "worker": "w"}],
                 budgets={"cpu_max_pct": 50})
    out = asyncio.run(e.execute(t["id"], ok()))
    assert out["status"] == "FAILED" and "cpu_budget exceeded" in out["error"]


def test_budgets_none_means_unlimited(tmp_path):
    e = make_engine(str(tmp_path))       # sampler gerçek psutil; limitler None
    t = e.create("g", steps=[{"label": "s", "worker": "w"}])
    out = asyncio.run(e.execute(t["id"], ok()))
    assert out["status"] == "COMPLETED"


# ------------------------------------------------------------ dead-letter
def test_poison_task_dead_letter_after_3_failures(tmp_path):
    notified = []
    e = make_engine(str(tmp_path), on_dead_letter=lambda t: notified.append(t["id"]))

    async def boom(task, step, ctx):
        raise RuntimeError("poison")

    t = e.create("g", steps=[{"label": "s", "worker": "w"}])
    # deneme 1-2: FAILED (streak 1,2)
    assert asyncio.run(e.execute(t["id"], boom))["status"] == "FAILED"
    t2 = e.get(t["id"])
    e._save(t2, "RECOVERING")                    # FAILED→RECOVERING (izinli)
    assert asyncio.run(e.execute(t["id"], boom))["status"] == "FAILED"
    assert e.get(t["id"])["failure_streak"] == 2
    t3 = e.get(t["id"])
    e._save(t3, "RECOVERING")
    # deneme 3: DEAD_LETTER — OTOMATİK RETRY BİTER
    out = asyncio.run(e.execute(t["id"], boom))
    assert out["status"] == "DEAD_LETTER"
    assert out["result"]["reason"] == "dead_letter"
    assert out["result"]["failure_streak"] == 3
    assert notified == [t["id"]]                    # insan bildirimi tetiklendi
    assert e.dead_letter_count == 1
    rows = [r["status"] for r in e.journal_rows(t["id"])]
    assert "TASK_DEAD_LETTER" in rows
    # dead-lettered görev execute REDDERDER
    with pytest.raises(RuntimeError, match="dead-lettered"):
        asyncio.run(e.execute(t["id"], boom))
    # cancel da terminal der
    assert e.cancel(t["id"])["ok"] is False


def test_dead_letter_manual_requeue(tmp_path):
    e = make_engine(str(tmp_path))
    t = e.create("g", steps=[{"label": "s", "worker": "w"}])
    task = e.get(t["id"])
    task["failure_streak"] = 3
    e._save(task, "RUNNING")
    asyncio.run(e.execute(t["id"], _boom()))
    assert e.get(t["id"])["status"] == "DEAD_LETTER"
    out = e.requeue(t["id"])                        # İNSAN kararı
    assert out["ok"] if isinstance(out, dict) and "ok" in out else True
    got = e.get(t["id"])
    assert got["status"] == "RECOVERING"
    assert got["failure_streak"] == 0
    assert any(r["status"] == "TASK_REQUEUE" for r in e.journal_rows(t["id"]))
    # requeue sonrası başarı normal COMPLETED
    out2 = asyncio.run(e.execute(t["id"], ok()))
    assert out2["status"] == "COMPLETED"


def _boom():
    async def runner(task, step, ctx):
        raise RuntimeError("poison")
    return runner


def test_success_resets_failure_streak(tmp_path):
    e = make_engine(str(tmp_path))
    t = e.create("g", steps=[{"label": "s", "worker": "w"}])
    asyncio.run(e.execute(t["id"], _boom()))        # streak 1
    assert e.get(t["id"])["failure_streak"] == 1
    task = e.get(t["id"])
    e._save(task, "RECOVERING")
    out = asyncio.run(e.execute(t["id"], ok()))     # başarı
    assert out["status"] == "COMPLETED"
    assert e.get(t["id"])["failure_streak"] == 0


def test_boot_crash_streak_dead_letters(tmp_path):
    """3 kez çökme (boot'ta RUNNING bulunan) → 3.süde poison → dead-letter."""
    e = make_engine(str(tmp_path))
    t = e.create("g", steps=[{"label": "s", "worker": "w"}])
    for i in range(2):
        task = e.get(t["id"])
        task["status"] = "RUNNING"                  # çökmüş gibi
        task["failure_streak"] = i
        e._save(task, "RUNNING")
        e.recover_incomplete()
    assert e.get(t["id"])["status"] == "RECOVERING"
    assert e.get(t["id"])["failure_streak"] == 2
    task = e.get(t["id"])
    e._save(task, "RUNNING")
    rec = e.recover_incomplete()                    # 3. çökme
    assert t["id"] not in rec                       # respawn listesinde YOK
    assert e.get(t["id"])["status"] == "DEAD_LETTER"
