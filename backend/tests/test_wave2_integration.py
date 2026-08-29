"""WAVE 2 / Integration: event→world, event→memory (EPHEMERAL dam),
event→proactive (cooldown+dedup+importance), event→task (Wave 1 scheduler
durable bus ile), world→memory, memory→context, capture_world."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.events.bus import DurableEventBus  # noqa: E402
from app.events.integration import (  # noqa: E402
    EventToMemoryBridge, EventToProactiveBridge, EventToWorldBridge,
    capture_world, snapshot_world_to_memory,
)
from app.memory.store_v3 import MemoryStore  # noqa: E402
from app.security.redaction import redact  # noqa: E402
from app.tasks.engine import TaskEngine  # noqa: E402
from app.tasks.scheduler import TaskScheduler  # noqa: E402
from app.world.model import WorldModel  # noqa: E402
from app.world.store import WorldStore  # noqa: E402


def rig(tmp):
    bus = DurableEventBus(db_path=os.path.join(tmp, "bus.db"), redact_fn=redact)
    ws = WorldStore(db_path=os.path.join(tmp, "world.db"))
    ms = MemoryStore(db_path=os.path.join(tmp, "v3.db"), redact_fn=redact)
    return bus, ws, ms


# ------------------------------------------------------------ event → world
def test_event_updates_world_state(tmp_path):
    bus, ws, _ = rig(str(tmp_path))
    EventToWorldBridge(bus, ws)
    bus.publish("world.change.screen",
                {"entity_id": "SCREEN", "state": {"app": "editor"},
                 "observed_at": 1000.0, "source": "VISION"})
    rec = ws.get("SCREEN")
    assert rec is not None and rec["state"]["app"] == "editor"
    assert rec["entity_type"] == "SCREEN" and rec["source"] == "VISION"
    assert rec["version"] == 1
    bus.publish("world.change.screen",
                {"entity_id": "SCREEN", "state": {"app": "browser"},
                 "observed_at": 1010.0, "source": "VISION"})
    assert ws.get("SCREEN")["version"] == 2          # geçiş tarihi tutuldu
    assert len(ws.history("SCREEN")) == 2


def test_process_exit_event_updates_world(tmp_path):
    """§19: PROCESS_EXIT → process.changed → World güncelleme."""
    bus, ws, _ = rig(str(tmp_path))
    EventToWorldBridge(bus, ws)
    bus.publish("world.change.process",
                {"entity_id": "PROCESS:9999", "state": {"pid": 9999, "status": "exited"},
                 "source": "SYSTEM"})
    rec = ws.get("PROCESS:9999")
    assert rec["entity_type"] == "PROCESSES"
    assert rec["state"]["status"] == "exited"


# ------------------------------------------------------------ event → memory
def test_ephemeral_events_never_reach_memory(tmp_path):
    bus, _, ms = rig(str(tmp_path))
    EventToMemoryBridge(bus, ms)
    for _ in range(50):                               # ekran spam'i
        bus.publish("world.change.screen", {"entity_id": "SCREEN",
                                            "state": {"app": "x"}})
    assert len(ms.query()) == 0                       # EPHEMERAL → bellek TEMİZ


def test_important_events_reach_memory(tmp_path):
    bus, _, ms = rig(str(tmp_path))
    EventToMemoryBridge(bus, ms)
    bus.publish("world.change.presence",
                {"summary": "kullanıcı odadan ayrıldı", "source": "TOOL"},
                entity_id="PRESENCE")
    rows = ms.query()
    assert len(rows) == 1
    assert rows[0]["content"] == "kullanıcı odadan ayrıldı"
    assert rows[0]["memory_type"] == "EPISODIC"       # IMPORTANT → EPISODIC
    assert rows[0]["record_kind"] == "EVENT"
    assert rows[0]["source_id"] == "event:world.change.presence"


def test_security_event_to_long_term_memory(tmp_path):
    bus, _, ms = rig(str(tmp_path))
    EventToMemoryBridge(bus, ms)
    bus.publish("security.alert", {"summary": "3 başarısız girişim", "source": "SYSTEM"})
    rows = ms.query()
    assert rows[0]["memory_type"] == "LONG_TERM" and rows[0]["importance"] == 0.7


# ------------------------------------------------------------ event → proactive
def test_proactive_cooldown_dedup_no_spam(tmp_path):
    bus, ws, _ = rig(str(tmp_path))
    decisions = []
    clock = {"t": 1000.0}
    br = EventToProactiveBridge(
        bus, lambda t, p: decisions.append(t) or {"action": "notify"},
        cooldown_s=300.0, min_importance=0.5, now=lambda: clock["t"])
    ev = {"entity_id": "SYSTEM_HEALTH", "importance": 0.9, "cpu": 96}
    bus.publish("world.change.system", ev)            # 1. → ateşlenir
    bus.publish("world.change.system", ev)            # cooldown → bastırılır
    clock["t"] += 100
    bus.publish("world.change.system", ev)            # hâlâ cooldown
    assert br.fired == 1 and br.suppressed == 2 and len(decisions) == 1
    clock["t"] += 400                                 # pencere geçti
    bus.publish("world.change.system", ev)
    assert br.fired == 2


def test_proactive_low_importance_suppressed(tmp_path):
    bus, ws, _ = rig(str(tmp_path))
    br = EventToProactiveBridge(bus, lambda t, p: {"a": 1},
                                min_importance=0.8)
    bus.publish("world.change.files", {"entity_id": "FILES", "importance": 0.3})
    assert br.fired == 0 and br.suppressed == 1


# ------------------------------------------------------------ event → task (Wave 1)
def test_durable_bus_drop_in_with_wave1_scheduler(tmp_path):
    """Wave 1 TaskScheduler, DurableEventBus ile birebir çalışır (§22)."""
    engine = TaskEngine(db_path=os.path.join(tmp_path, "tasks.db"))
    bus, _, _ = rig(str(tmp_path))
    sched = TaskScheduler(engine, db_path=os.path.join(tmp_path, "sched.db"),
                          bus=bus)                    # DURABLE bus enjekte
    sched.add_trigger("world.change.battery.low", "şarj hatırlat")
    before = len(engine.list())
    bus.publish("world.change.battery.low", {"pct": 10})
    assert len(engine.list()) == before + 1           # tetikleyici görev üretti
    assert engine.list()[-1]["goal"] == "şarj hatırlat"
    # döngü koruması: aynı olay tekrar (loop window) → engellenmez çünkü
    # correlation farklı; ama kanıt: görev yalnız olay başına üretilir
    bus.publish("world.change.battery.low", {"pct": 9})
    assert len(engine.list()) == before + 2           # gerçek ikinci olay → ikinci görev


# ------------------------------------------------------------ world → memory
def test_world_snapshot_to_memory(tmp_path):
    bus, ws, ms = rig(str(tmp_path))
    ws.upsert("SCREEN", "SCREEN", {"app": "x"})
    ws.upsert("TASKS", "TASKS", {"active": 1})
    out = snapshot_world_to_memory(ws, ms)
    assert out["ok"] and out["entities"] >= 1
    rows = ms.query(memory_type="EPISODIC")
    assert any("Dünya gözlemi" in r["content"] for r in rows)
    assert rows[0]["record_kind"] == "OBSERVATION"


# ------------------------------------------------------------ capture (WorldModel → WorldStore → events)
def test_capture_world_from_foundation_model(tmp_path):
    bus, ws, _ = rig(str(tmp_path))
    applied = []
    EventToWorldBridge(bus, ws)                       # olayları geri uygular
    wm = WorldModel(sources={
        "screen": lambda: {"available": True, "status": "idle"},
        "presence": lambda: {"available": True, "boss_in_room": True},
        "missing": lambda: {"available": False},      # eksik kaynak atlanır
    })
    out = capture_world(wm, ws, bus, correlation_id="cap-1")
    assert out["updated"] == 2                        # yalnız mevcut bölümler
    assert ws.get("SCREEN")["state"]["data"]["status"] == "idle"
    assert ws.get("PRESENCE")["state"]["data"]["boss_in_room"] is True
    assert "world.change.screen" in out["published"]  # değişim → olay
    # ikinci yakalama: değer aynı → olay yok (spam yok)
    out2 = capture_world(wm, ws, bus, correlation_id="cap-2")
    assert out2["published"] == [] or all(b.endswith(":blocked") for b in out2["published"]) \
        or out2["published"] == []                    # değişmedi → yayın yok


def test_capture_world_change_publishes_once(tmp_path):
    bus, ws, _ = rig(str(tmp_path))
    seen = []
    bus.subscribe("world.change.screen", lambda t, p: seen.append(t))
    state = {"app": "a"}
    wm = WorldModel(sources={"screen": lambda: dict(state)})
    capture_world(wm, ws, bus, correlation_id="c1")   # oluşturma → 1 olay
    state["app"] = "b"
    capture_world(wm, ws, bus, correlation_id="c2")   # değişim → 1 olay
    assert seen.count("world.change.screen") == 2
    capture_world(wm, ws, bus, correlation_id="c3")   # değişiklik yok → yayın yok
    assert seen.count("world.change.screen") == 2     # spam yok


# ------------------------------------------------------------ memory → context
def test_memory_context_for_query(tmp_path):
    from app.memory.retrieval import Retriever
    bus, _, ms = rig(str(tmp_path))
    ms.write("Kullanıcı VS Code seviyor", memory_type="USER", provenance="USER",
             record_kind="PREFERENCE", subject_key="preference:editor",
             confidence=0.95)
    out = Retriever(ms, redact_fn=redact).retrieve("hangi editör sever")
    assert out["hits"] >= 1 and "VS Code" in out["context"]
    assert out["engine"] in ("keyword", "chroma")     # dürüst motor bildirimi
