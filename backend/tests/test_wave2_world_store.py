"""WAVE 2 / World Store: entity schema, versioning, snapshot+stale, delta,
temporal history + trend narrative, retention aggregation, KG links."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.world.store import AGG_BUCKET_S, RAW_HISTORY_PER_ENTITY, WorldStore  # noqa: E402


def make(tmp, clock=None):
    w = WorldStore(db_path=os.path.join(tmp, "world.db"))
    if clock:
        w.now = clock
    return w


# ------------------------------------------------------------ upsert/version
def test_upsert_creates_and_versions(tmp_path):
    w = make(str(tmp_path))
    r1 = w.upsert("SCREEN", "SCREEN", {"app": "terminal"}, source="VISION")
    assert r1["version"] == 1 and r1["changed"] is True
    r2 = w.upsert("SCREEN", "SCREEN", {"app": "browser"}, source="VISION")
    assert r2["version"] == 2 and r2["changed"] is True
    r3 = w.upsert("SCREEN", "SCREEN", {"app": "browser"}, source="VISION")
    assert r3["version"] == 3 and r3["changed"] is False   # aynı değer → sürüm artar, değişim yok
    rec = w.get("SCREEN")
    assert rec["state"] == {"app": "browser"} and rec["version"] == 3
    assert rec["entity_type"] == "SCREEN" and rec["source"] == "VISION"


def test_upsert_rejects_bad_entity(tmp_path):
    w = make(str(tmp_path))
    with pytest.raises(ValueError):
        w.upsert("X", "NOT_A_TYPE", {})
    with pytest.raises(ValueError):
        w.upsert("X", "SCREEN", "state-dict-degil")
    with pytest.raises(ValueError):
        w.upsert("X", "SCREEN", {"k": "v" * 300_000})


# ------------------------------------------------------------ snapshot/stale
def test_snapshot_stale_flagging(tmp_path):
    clock = {"t": 1000.0}
    w = make(str(tmp_path), clock=lambda: clock["t"])
    w.upsert("SCREEN", "SCREEN", {"app": "x"}, source="VISION")         # fresh
    w.upsert("OS", "OS", {"name": "win"}, source="SYSTEM")              # fresh
    clock["t"] = 1000.0 + 400.0                                        # 400sn geç
    snap = w.snapshot()
    by_id = {e["entity_id"]: e for e in snap["entities"]}
    assert by_id["SCREEN"]["stale"] is True       # 120sn eşiği aşıldı
    assert by_id["OS"]["stale"] is False          # 3600sn eşiği içinde
    fresh_only = w.snapshot(include_stale=False)
    assert {e["entity_id"] for e in fresh_only["entities"]} == {"OS"}


def test_valid_until_expiry_marks_stale(tmp_path):
    clock = {"t": 1000.0}
    w = make(str(tmp_path), clock=lambda: clock["t"])
    w.upsert("PRESENCE:boss", "PRESENCE", {"present": True}, source="TOOL",
             valid_until=1100.0)
    assert w.get("PRESENCE:boss")["stale"] is False
    clock["t"] = 1101.0
    assert w.get("PRESENCE:boss")["stale"] is True


# ------------------------------------------------------------ delta
def test_delta_changed_since(tmp_path):
    clock = {"t": 1000.0}
    w = make(str(tmp_path), clock=lambda: clock["t"])
    w.upsert("SCREEN", "SCREEN", {"app": "a"})
    clock["t"] = 1010.0
    w.upsert("NETWORK", "NETWORK", {"online": True})
    d = w.delta(since_ts=1005.0)
    assert [e["entity_id"] for e in d["changed"]] == ["NETWORK"]
    assert w.delta(since_ts=2000.0)["changed"] == []


# ------------------------------------------------------------ temporal
def test_history_and_trend_narrative(tmp_path):
    """§16 örneği: 23:00 %20 → 23:05 %80 → 23:06 %95 → 23:07 %40."""
    t0 = 1_000_000.0
    clock = {"t": t0}
    w = make(str(tmp_path), clock=lambda: clock["t"])
    for dt, cpu in ((0, 20), (300, 80), (360, 95), (420, 40)):
        clock["t"] = t0 + dt
        w.upsert("SYSTEM_HEALTH", "SYSTEM_HEALTH", {"cpu_percent": cpu})
    h = w.history("SYSTEM_HEALTH")
    assert [x["state"]["cpu_percent"] for x in h] == [20, 80, 95, 40]
    tr = w.trend("SYSTEM_HEALTH", "cpu_percent", window_s=420.0)
    assert tr["direction"] == "rising"                  # ilk üçlük 20, son üçlük ~40+
    assert "yükseldi" in tr["narrative"]
    # düşüş senaryosu
    for dt, cpu in ((430, 10), (440, 5)):
        clock["t"] = t0 + dt
        w.upsert("SYSTEM_HEALTH", "SYSTEM_HEALTH", {"cpu_percent": cpu})
    tr2 = w.trend("SYSTEM_HEALTH", "cpu_percent", window_s=60.0)
    assert tr2["direction"] == "falling" and "düştü" in tr2["narrative"]


def test_retention_aggregation_compacts_history(tmp_path):
    """Ham noktalar sınırlı; eskiler saatlik özetlere iner (sınırsız büyüme yok)."""
    t0 = 1_000_000.0
    clock = {"t": t0}
    w = make(str(tmp_path), clock=lambda: clock["t"])
    n = RAW_HISTORY_PER_ENTITY + 50
    for i in range(n):                                   # ~2.5 saatlik 5sn'lik veri
        clock["t"] = t0 + i * 5
        w.upsert("SYSTEM_HEALTH", "SYSTEM_HEALTH", {"cpu_percent": 10 + (i % 50)})
    import sqlite3
    raw = sqlite3.connect(w.path).execute(
        "SELECT COUNT(*) FROM world_history").fetchone()[0]
    assert raw <= RAW_HISTORY_PER_ENTITY                 # ham kesildi
    aggs = w.aggregates("SYSTEM_HEALTH", "cpu_percent")
    assert len(aggs) >= 1 and all(a["count"] >= 1 for a in aggs)
    assert all(a["min"] <= a["avg"] <= a["max"] for a in aggs)


# ------------------------------------------------------------ restart
def test_world_restart_preserves_state(tmp_path):
    path = os.path.join(tmp_path, "world.db")
    w = WorldStore(db_path=path)
    w.upsert("TASKS", "TASKS", {"active": 1})
    w.close()
    w2 = WorldStore(db_path=path)                        # restart
    rec = w2.get("TASKS")
    assert rec is not None and rec["state"] == {"active": 1}
    assert len(w2.history("TASKS")) == 1                 # tarih de kaldı


# ------------------------------------------------------------ KG prep
def test_entity_links_kg_preparation(tmp_path):
    w = make(str(tmp_path))
    w.upsert("USER:master", "USER", {"name": "boss"})
    w.upsert("DEVICE:pc", "DEVICE", {"hostname": "ultron-pc"})
    w.link("USER:master", "owns", "DEVICE:pc")
    w.link("USER:master", "works_on", "PROJECT:ultron")
    with pytest.raises(ValueError):
        w.link("USER:master", "hacks", "DEVICE:pc")      # bilinmeyen ilişki reddi
    ls = w.links("USER:master")
    assert {(l["relation"], l["object"]) for l in ls} == {
        ("owns", "DEVICE:pc"), ("works_on", "PROJECT:ultron")}


def test_integrity_check_ok(tmp_path):
    w = make(str(tmp_path))
    w.upsert("OS", "OS", {"k": 1})
    assert w.integrity_check()["ok"] is True
