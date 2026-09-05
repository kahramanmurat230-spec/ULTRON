"""WAVE 2 / Durable Event Bus: şema, publish/consume, persistence, dedup,
ordering, replay, retry→DLQ, idempotency, loop guard, secret redaction,
oversized/invalid olay reddi, restart, consumer crash."""
import asyncio
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.events.bus import DurableEventBus  # noqa: E402
from app.security.redaction import redact  # noqa: E402


def make_bus(tmp, **kw):
    kw.setdefault("redact_fn", redact)
    return DurableEventBus(db_path=os.path.join(tmp, "bus.db"), **kw)


# ------------------------------------------------------------ basic
def test_publish_persist_consume(tmp_path):
    bus = make_bus(str(tmp_path))
    got = []
    bus.subscribe("world.change.*", lambda t, p: got.append((t, p)))
    res = bus.publish("world.change.screen", {"app": "editor"})
    assert res["ok"] and res["status"] == "published" and res["seq"] == 1
    assert res["class"] == "EPHEMERAL"
    assert got == [("world.change.screen", {"app": "editor"})]
    # kalıcılık: DB'de duruyor
    row = sqlite3.connect(bus.path).execute(
        "SELECT event_type FROM events").fetchone()
    assert row == ("world.change.screen",)


def test_event_schema_fields(tmp_path):
    bus = make_bus(str(tmp_path))
    bus.publish("world.change.cpu", {"v": 5}, source="SYSTEM",
                entity_id="SYSTEM_HEALTH", correlation_id="corr-1",
                causation_id=None, confidence=0.9)
    ev = bus.event(1)
    for k in ("event_id", "event_type", "ts", "source", "entity_id", "payload",
              "correlation_id", "causation_id", "confidence", "schema_version"):
        assert k in ev
    assert ev["entity_id"] == "SYSTEM_HEALTH" and ev["confidence"] == 0.9


def test_invalid_and_oversized_events_rejected(tmp_path):
    bus = make_bus(str(tmp_path))
    with pytest.raises(ValueError):
        bus.publish("GEÇERSİZ TİP!", {})               # regex dışı
    with pytest.raises(ValueError):
        bus.publish("ok.type", {"x": 1}, schema_version=99)   # sürüm reddi
    with pytest.raises(ValueError):
        bus.publish("ok.type", {"blob": "A" * (70 * 1024)})   # 64KB üstü payload
    assert bus.stats()["events"] == 0                  # hiçbiri yazılmadı


def test_secret_redacted_from_event_log(tmp_path):
    bus = make_bus(str(tmp_path))
    bus.publish("world.change.clipboard",
                {"text": "password: gizli-sifre-42", "nested": {"token": "abc123tokenval"}})
    raw = open(os.path.join(str(tmp_path), "bus.db"), "rb").read()
    # WAL'a taşınmış olabilir; DB üzerinden kontrol:
    payload = sqlite3.connect(bus.path).execute(
        "SELECT payload FROM events").fetchone()[0]
    assert "gizli-sifre-42" not in payload
    assert "abc123tokenval" not in payload
    assert "***REDACTED***" in payload


# ------------------------------------------------------------ dedup/order
def test_duplicate_event_id_swallowed(tmp_path):
    bus = make_bus(str(tmp_path))
    calls = []
    bus.subscribe("*", lambda t, p: calls.append(t))
    bus.publish("task.created", {}, event_id="fixed-id-1")
    res2 = bus.publish("task.created", {}, event_id="fixed-id-1")   # tekrar
    assert res2["status"] == "duplicate"
    assert bus.stats()["events"] == 1 and calls == ["task.created"]


def test_global_ordering_by_seq(tmp_path):
    bus = make_bus(str(tmp_path))
    order = []
    bus.subscribe("ord.*", lambda t, p: order.append(t))
    for i in range(5):
        bus.publish(f"ord.{i}", {"i": i})
    assert order == [f"ord.{i}" for i in range(5)]     # seq sırası korunur
    seqs = [bus.event(i + 1)["event_type"] for i in range(5)]
    assert seqs == [f"ord.{i}" for i in range(5)]


# ------------------------------------------------------------ retry → DLQ
def test_consumer_retry_then_dlq(tmp_path):
    bus = make_bus(str(tmp_path), retry_attempts=3)
    calls = {"n": 0}

    def flaky(t, p):
        calls["n"] += 1
        raise RuntimeError("consumer crash")           # her denemede patlar

    bus.subscribe("fragile.*", flaky)
    res = bus.publish("fragile.bomb", {})
    assert res["results"][0]["status"] == "dlq"
    assert calls["n"] == 3                             # 3 deneme — sonra DUR
    dlq = bus.dlq()
    assert len(dlq) == 1 and "3 attempts" in dlq[0]["reason"]
    # olayın kendisi kaybolmadı (persistence + DLQ incelemesi)
    assert bus.stats()["events"] == 1


def test_consumer_recovers_within_retry(tmp_path):
    bus = make_bus(str(tmp_path), retry_attempts=3)
    state = {"fails_left": 1}
    got = []

    def flaky(t, p):
        if state["fails_left"] > 0:
            state["fails_left"] -= 1
            raise RuntimeError("geçici hata")
        got.append(t)

    bus.subscribe("svc.*", flaky)
    res = bus.publish("svc.call", {})
    assert res["results"][0]["status"] == "delivered" and got == ["svc.call"]
    assert bus.dlq() == []


# ------------------------------------------------------------ replay/idempotency
def test_replay_respects_delivered_and_force(tmp_path):
    bus = make_bus(str(tmp_path))
    calls = []
    bus.publish("a.x", {})                             # abone yokken yayımlandı
    bus.subscribe("a.*", lambda t, p: calls.append(t))
    r1 = bus.replay(1, 1)                              # kaçırdığını teslim eder
    assert r1["redelivered"] == 1 and calls == ["a.x"]
    r2 = bus.replay(1, 1)                              # idempotent: atlanır
    assert r2["skipped_delivered"] == 1 and calls == ["a.x"]
    r3 = bus.replay(1, 1, force=True)                  # zorla yeniden
    assert r3["redelivered"] == 1 and calls == ["a.x", "a.x"]


def test_replay_after_restart(tmp_path):
    path = os.path.join(str(tmp_path), "bus.db")
    bus = DurableEventBus(db_path=path, redact_fn=redact)
    bus.publish("w.a", {"n": 1})
    bus.publish("w.b", {"n": 2})
    bus.close()
    bus2 = DurableEventBus(db_path=path, redact_fn=redact)   # restart
    got = []
    bus2.subscribe("w.*", lambda t, p: got.append((t, p["n"])))
    out = bus2.replay(1, 2)
    assert got == [("w.a", 1), ("w.b", 2)] and out["events"] == 2


# ------------------------------------------------------------ loop guard
def test_loop_guard_blocks_deep_causation_chain(tmp_path):
    bus = make_bus(str(tmp_path))
    prev_id = None
    for i in range(7):                                  # derin zincir kur
        res = bus.publish(f"task.step.{i}", {"i": i},
                          event_id=f"e{i}", causation_id=prev_id,
                          correlation_id="flow-1", _loop_guard=False)
        assert res["status"] == "published"
        prev_id = "e%d" % i
    # zincir e0→e6 derinliği 7 (> 6): bir daha ekle → RED
    res = bus.publish("task.step.7", {"i": 7}, event_id="e7",
                      causation_id=prev_id, correlation_id="flow-1")
    assert res["ok"] is False and res["status"] == "loop_guard_blocked"
    assert bus.dlq()[0]["reason"] == "loop_guard"


def test_loop_guard_same_type_entity_window(tmp_path):
    bus = make_bus(str(tmp_path))
    ok = bus.publish("world.change.task", {"id": "t1"},
                     entity_id="TASK:t1", correlation_id="c9")
    assert ok["status"] == "published"
    dup = bus.publish("world.change.task", {"id": "t1"},
                      entity_id="TASK:t1", correlation_id="c9")   # aynı pencere
    assert dup["status"] == "loop_guard_blocked"
    # farklı correlation → normal akış, engellenmez
    ok2 = bus.publish("world.change.task", {"id": "t1"},
                      entity_id="TASK:t1", correlation_id="c10")
    assert ok2["status"] == "published"


def test_normal_chains_not_blocked(tmp_path):
    bus = make_bus(str(tmp_path))
    r1 = bus.publish("user.command", {}, correlation_id="cc")
    r2 = bus.publish("world.change.screen", {}, correlation_id="cc",
                     causation_id=None)
    assert r1["status"] == "published" and r2["status"] == "published"


# ------------------------------------------------------------ async + compat
def test_wave1_compat_signature(tmp_path):
    """Wave 1 scheduler imzasıyla birebir uyum: publish(topic, payload)."""
    bus = make_bus(str(tmp_path))
    got = []
    sid = bus.subscribe("world.change.battery.*", lambda t, p: got.append(p))
    assert isinstance(sid, str)
    n = bus.publish("world.change.battery.low", {"pct": 12})
    assert n["ok"] and got == [{"pct": 12}]
    assert bus.unsubscribe(sid) is True


def test_async_subscriber_supported(tmp_path):
    bus = make_bus(str(tmp_path))
    seen = []

    async def handler(topic, payload):
        seen.append(topic)

    bus.subscribe("async.*", handler)

    async def run():
        bus.publish("async.op", {})
        await asyncio.sleep(0.05)
    asyncio.run(run())
    assert seen == ["async.op"]


def test_stats(tmp_path):
    bus = make_bus(str(tmp_path))
    bus.subscribe("*", lambda t, p: None)
    bus.publish("a.b", {})
    st = bus.stats()
    assert st["events"] == 1 and st["delivered"] == 1 and st["dlq"] == 0
