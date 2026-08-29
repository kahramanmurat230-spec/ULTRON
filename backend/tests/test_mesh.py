"""PHASE 12: mobile mesh — pairing-key auth, heartbeat TTL, sealed rules."""
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.mesh.dual_node_engine import HEARTBEAT_TTL, NodeRegistry  # noqa: E402
from app.mesh.mesh_sync import MeshSync  # noqa: E402
from app.mesh.task_router import classify_weight, route  # noqa: E402
from app.memory.sqlite_memory import Memory  # noqa: E402
from app.personal.user_dna import UserDNA  # noqa: E402
from app.personal.user_dna import MasterRules  # noqa: E402


def make_sync(tmp):
    mem = Memory(path=os.path.join(tmp, "m.db"))
    dna = UserDNA(path=os.path.join(tmp, "d.db"))
    rules = MasterRules(path=os.path.join(tmp, "r.json"))
    return MeshSync(mem, dna, rules), mem, dna, rules


def test_handshake_requires_auth():
    reg = NodeRegistry()
    bad = reg.handshake("phone", "NODE_MOBILE", "1.0", auth_ok=False)
    assert bad["ok"] is False and "token" in bad["error"]
    ok = reg.handshake("phone", "NODE_MOBILE", "1.0", auth_ok=True)
    assert ok["ok"] is True and "sha-sealed-no-merge" == ok["rules_policy"]
    bad_role = reg.handshake("x", "NODE_TOASTER", "1.0", auth_ok=True)
    assert bad_role["ok"] is False


def test_heartbeat_ttl_expiry():
    clock = {"t": 1000.0}
    reg = NodeRegistry(now=lambda: clock["t"])
    reg.handshake("phone", "NODE_MOBILE", "1.0", auth_ok=True)
    assert reg.mobile_online() is True
    clock["t"] += HEARTBEAT_TTL + 1
    assert reg.mobile_online() is False  # TTL doldu -> offline
    assert reg.heartbeat("phone") is True
    assert reg.mobile_online() is True   # heartbeat canlandırdı
    assert reg.heartbeat("ghost") is False


def test_pc_node_always_online_self():
    reg = NodeRegistry()
    assert reg.pc_online() is True
    st = reg.status()
    assert any(n["id"] == "ultron-pc" and n["online"] for n in st)


def test_sync_push_pull_roundtrip_and_dedupe(tmp_path):
    msy, mem, dna, rules = make_sync(str(tmp_path))
    payload = {"memories": [{"kind": "FACT", "content": "mesh-fact-1",
                             "created_at": "2026-01-01T00:00:00", "sync_ts": 100.0}],
               "dna": [{"ts": 111, "activity": "mesh", "app": None, "note": "mesh-note"}]}
    r1 = msy.push(payload)
    assert r1["added_memories"] == 1 and r1["added_dna"] == 1
    r2 = msy.push(payload)  # ikinci push tamamen dedupe
    assert r2["added_memories"] == 0 and r2["skipped_dupes"] == 1
    pulled = msy.pull(since_ts=0.0)
    kinds = [m["content"] for m in pulled["memories"]]
    assert "mesh-fact-1" in kinds


def test_sync_master_rules_never_merged(tmp_path):
    msy, mem, dna, rules = make_sync(str(tmp_path))
    before = rules.path.read_bytes()
    msy.push({"memories": [], "dna": [],
              "master_rules": {"override": "kill_all_humans"}})  # saldırı denemesi
    after = rules.path.read_bytes()
    assert before == after  # SHA-sealed: dosya değişmedi


def test_task_router_routing():
    assert route("ekranı analiz et", pc_online=True)["target"] == "PC_DEEP_COMPUTE"
    offline = route("ekranı analiz et", pc_online=False)
    assert offline["target"] == "PC_OFFLINE_NOTICE" and offline["notice"]
    assert route("sabah 8e alarm kur", pc_online=False)["target"] == "LOCAL_IMMEDIATE"
    assert classify_weight("projeyi derle") == "heavy"
    assert classify_weight("2+2 kaç eder") == "light"


# ---------------- PHASE 14: capability declaration ----------------
def test_handshake_capability_declaration():
    reg = NodeRegistry()
    res = reg.handshake("phone", "NODE_MOBILE", "2.0", auth_ok=True,
                        caps=["camera", "microphone", "notifications",
                              "presence", "remote_commands"])
    assert res["ok"] and len(res["declared_caps_accepted"]) == 5
    node = next(n for n in reg.status() if n["id"] == "phone")
    assert "camera" in node["declared_caps"]
    assert "offline_first" in node["caps"]  # rol yetenekleri korunur


def test_pc_node_capability_surface():
    reg = NodeRegistry()
    pc = next(n for n in reg.status() if n["role"] == "NODE_PC")
    assert set(pc["caps"]) >= {"llm_heavy", "vision", "compile"}
