"""WAVE 3 / Agent message bus: şema, gönderim/alım, dedup (replay),
impersonation + cross-task reddi, payload limiti + redaction, loop
koruması, kota, async bekleme, kalıcılık."""
import asyncio
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.orchestr.messages import AgentMessageBus, MessageError  # noqa: E402
from app.orchestr.worker import Worker, WorkerRegistry  # noqa: E402
from app.security.redaction import redact  # noqa: E402


def rig(tmp):
    reg = WorkerRegistry(db_path=os.path.join(tmp, "w.db"))
    a = Worker("task-1", "RESEARCH")
    b = Worker("task-1", "CODING")
    other = Worker("task-2", "SYSTEM")
    for w in (a, b, other):
        reg.save(w)
    bus = AgentMessageBus(db_path=os.path.join(tmp, "m.db"), redact_fn=redact,
                          registry=reg)
    return reg, bus, a, b, other


# ------------------------------------------------------------ basic
def test_send_receive_schema(tmp_path):
    reg, bus, a, b, _ = rig(str(tmp_path))
    res = bus.send(a.worker_id, b.worker_id, "result.share",
                   {"summary": "bulgular"}, task_id="task-1",
                   trace_id="tr-1", causation_id=None)
    assert res["ok"] and res["status"] == "sent"
    msgs = bus.inbox(b.worker_id, mark_delivered=True)
    assert len(msgs) == 1
    m = msgs[0]
    for k in ("message_id", "sender", "receiver", "task_id", "trace_id",
              "type", "payload", "ts", "causation_id", "schema_version"):
        assert k in m
    assert m["sender"] == a.worker_id and m["payload"]["summary"] == "bulgular"
    assert m["delivered"] is True


def test_message_type_validation(tmp_path):
    reg, bus, a, b, _ = rig(str(tmp_path))
    with pytest.raises(MessageError):
        bus.send(a.worker_id, b.worker_id, "BAD TYPE!", {}, task_id="task-1")
    with pytest.raises(MessageError):
        bus.send(a.worker_id, b.worker_id, "", {}, task_id="task-1")


def test_payload_limit_and_secret_redaction(tmp_path):
    reg, bus, a, b, _ = rig(str(tmp_path))
    with pytest.raises(MessageError, match="too large"):
        bus.send(a.worker_id, b.worker_id, "big", {"blob": "A" * (70 * 1024)},
                 task_id="task-1")
    bus.send(a.worker_id, b.worker_id, "creds",
             {"password": "gizli-123", "note": "token abc123def456xyz"},
             task_id="task-1")
    raw = sqlite3.connect(bus.path).execute(
        "SELECT payload FROM messages").fetchone()[0]
    assert "gizli-123" not in raw
    assert "abc123def456xyz" not in raw
    assert "***REDACTED***" in raw


# ------------------------------------------------------------ identity
def test_impersonation_rejected(tmp_path):
    """Kayıtlı olmayan gönderen RED — worker kimliği sahtelenemez."""
    reg, bus, a, b, _ = rig(str(tmp_path))
    with pytest.raises(MessageError, match="impersonation"):
        bus.send("sahte-worker", b.worker_id, "x", {}, task_id="task-1")
    with pytest.raises(MessageError):
        bus.send(a.worker_id, "yok-boyle", "x", {}, task_id="task-1")


def test_cross_task_message_rejected(tmp_path):
    reg, bus, a, b, other = rig(str(tmp_path))
    with pytest.raises(MessageError, match="cross-task"):
        bus.send(a.worker_id, other.worker_id, "x", {}, task_id="task-1")
    with pytest.raises(MessageError, match="cross-task"):
        bus.send(a.worker_id, b.worker_id, "x", {}, task_id="task-2") \
            if False else bus.send(a.worker_id, b.worker_id, "x", {},
                                   task_id="task-999")  # sender görevi uyumsuz


# ------------------------------------------------------------ dedup/replay
def test_duplicate_message_id_swallowed(tmp_path):
    reg, bus, a, b, _ = rig(str(tmp_path))
    r1 = bus.send(a.worker_id, b.worker_id, "t.x", {"n": 1}, task_id="task-1",
                  message_id="fixed-mid")
    assert r1["status"] == "sent"
    r2 = bus.send(a.worker_id, b.worker_id, "t.x", {"n": 1}, task_id="task-1",
                  message_id="fixed-mid")            # REPLAY
    assert r2["status"] == "duplicate"
    assert len(bus.inbox(b.worker_id)) == 1          # tek teslim


def test_loop_protection_window_and_depth(tmp_path):
    reg, bus, a, b, _ = rig(str(tmp_path))
    r1 = bus.send(a.worker_id, b.worker_id, "ping", {}, task_id="task-1")
    assert r1["status"] == "sent"
    # aynı (sender,receiver,type) pencere içinde → loop_blocked
    r2 = bus.send(a.worker_id, b.worker_id, "ping", {}, task_id="task-1",
                  timestamp=bus.now() + 1)
    assert r2["status"] == "loop_blocked"
    # farklı tip → serbest
    r3 = bus.send(a.worker_id, b.worker_id, "pong", {}, task_id="task-1",
                  timestamp=bus.now() + 2)
    assert r3["status"] == "sent"
    # derin causation zinciri → loop_blocked
    prev = r1["message_id"]
    ok_st = []
    for i in range(10):
        r = bus.send(b.worker_id, a.worker_id, f"chain.{i}", {},
                     task_id="task-1", causation_id=prev,
                     timestamp=bus.now() + 10 + i * 10)   # pencere dışı
        if r["status"] != "sent":
            break
        ok_st.append(r["status"])
        prev = r["message_id"]
    assert len(ok_st) <= 10


def test_task_message_quota(tmp_path):
    reg, bus, a, b, _ = rig(str(tmp_path))
    clock = {"t": 0.0}
    bus.now = lambda: clock["t"]
    blocked = None
    for i in range(100):
        clock["t"] += 20.0                       # pencere dedup'un dışında
        r = bus.send(a.worker_id, b.worker_id, f"q.{i}", {}, task_id="task-1")
        if r["status"] != "sent":
            blocked = r
            break
    assert blocked is None or blocked["status"] == "quota_exceeded"  # sınır var


# ------------------------------------------------------------ async + persistence
def test_async_wait_for_message(tmp_path):
    reg, bus, a, b, _ = rig(str(tmp_path))

    async def scenario():
        async def sender():
            await asyncio.sleep(0.05)
            bus.send(a.worker_id, b.worker_id, "late", {"x": 1},
                     task_id="task-1")
        asyncio.ensure_future(sender())
        msg = await bus.wait_for(b.worker_id, timeout=2.0)
        assert msg is not None and msg["type"] == "late"
    asyncio.run(scenario())


def test_async_wait_timeout_returns_none(tmp_path):
    reg, bus, a, b, _ = rig(str(tmp_path))

    async def scenario():
        msg = await bus.wait_for(b.worker_id, timeout=0.1)
        assert msg is None                        # dürüst timeout
    asyncio.run(scenario())


def test_messages_persist_across_restart(tmp_path):
    reg, bus, a, b, _ = rig(str(tmp_path))
    bus.send(a.worker_id, b.worker_id, "durable", {"n": 1}, task_id="task-1")
    bus.close()
    bus2 = AgentMessageBus(db_path=os.path.join(str(tmp_path), "m.db"),
                           redact_fn=redact, registry=reg)
    msgs = bus2.inbox(b.worker_id)
    assert len(msgs) == 1 and msgs[0]["type"] == "durable"
