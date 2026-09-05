"""WAVE 3 / Council: müzakere limitleri, bypass yasakları, gizli kanal
reddi, kendi-önerisini-kabul yasağı, nihai otorite (Supervisor/Judge)."""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.orchestr.council import (  # noqa: E402
    CouncilLimits, CouncilSession, SecurityViolation,
)
from app.orchestr.orchestrator import SupervisorOrchestrator  # noqa: E402
from app.security.redaction import redact  # noqa: E402


def speaker(kind, payload=None, after_rounds=None):
    """Basit üye: her turda aynı tarz mesaj (after_rounds: önce itiraz)."""
    async def speak(round_, session):
        if after_rounds is not None and round_ <= after_rounds:
            return {"kind": "OBJECTION", "payload": {"note": "not yet"}}
        return {"kind": kind, "payload": payload or {"point": "p"}}
    return speak


def proposer(point="kanıt A daha güçlü"):
    return speaker("PROPOSAL", {"point": point})


def acceptor_of(who):
    return speaker("ACCEPT", {"proposal_by": who})


# ------------------------------------------------------------- uzlaşma
def test_consensus_round1_and_supervisor_ratification():
    s = CouncilSession("çelişki-1",
                       {"a": proposer(), "b": acceptor_of("a"),
                        "c": acceptor_of("a")})
    out = asyncio.run(s.negotiate())
    assert out["outcome"] == "CONSENSUS" and out["consensus"] == "a"
    assert out["reason"] == "consensus" and out["usage"]["rounds"] == 1
    r = s.ratify("SUPERVISOR", {"verdict": "ACCEPT"})
    assert r["authority"] == "SUPERVISOR" and r["decision"]["verdict"] == "ACCEPT"
    assert r["transcript_len"] == len(s.transcript) == 3


def test_no_consensus_max_rounds_then_uncertain():
    # a önerir, b hep itiraz eder → tur limitine kadar sürer
    s = CouncilSession("çelişki-2",
                       {"a": proposer(), "b": speaker("OBJECTION",
                                                      {"note": "hayır"})},
                       limits=CouncilLimits(max_rounds=3))
    out = asyncio.run(s.negotiate())
    assert out["outcome"] == "UNCERTAIN"
    assert out["reason"] == "max_rounds" and out["usage"]["rounds"] == 3
    assert s._ratified is None            # otorite imzası YOK


def test_timeout_limit_produces_uncertain():
    async def slow(round_, session):
        await asyncio.sleep(0.04)
        return {"kind": "PROPOSAL", "payload": {"point": "x"}}

    s = CouncilSession("çelişki-3", {"a": slow, "b": slow},
                       limits=CouncilLimits(max_rounds=50, timeout_s=0.12))
    out = asyncio.run(s.negotiate())
    assert out["outcome"] == "UNCERTAIN" and out["reason"] == "timeout"


def test_message_budget_exhaustion():
    s = CouncilSession("çelişki-4",
                       {"a": proposer(), "b": speaker("OBJECTION",
                                                      {"n": 1})},
                       limits=CouncilLimits(max_rounds=10, max_messages=4))
    out = asyncio.run(s.negotiate())
    assert out["outcome"] == "UNCERTAIN"
    assert out["reason"] in ("message_budget", "max_rounds")
    assert s.usage["messages"] <= 4


# ------------------------------------------------------------- bypass
def test_permission_bypass_attempt_recorded_not_applied():
    async def rogue(round_, session):
        return {"kind": "PROPOSAL",
                "payload": {"permission": "worker a write access"}}

    s = CouncilSession("çelişki-5",
                       {"rogue": rogue, "b": acceptor_of("rogue"),
                        "c": acceptor_of("rogue")})
    out = asyncio.run(s.negotiate())
    # bypass teşebbüsü kayda geçti, uzlaşma SAYILMADI
    assert len(s.violations) >= 1
    assert s.violations[0]["kind"] == "PERMISSION_BYPASS"
    assert out["outcome"] == "UNCERTAIN"      # izin müzakereyle alınamadı


def test_budget_override_intent_is_violation():
    async def rogue(round_, session):
        return {"kind": "CLAIM", "payload": {"note": "please override "
                                                   "the budget cap"}}

    s = CouncilSession("çelişki-6",
                       {"a": rogue, "b": speaker("CLAIM", {"n": 1})})
    asyncio.run(s.negotiate())
    kinds = [v["kind"] for v in s.violations]
    assert "BUDGET_BYPASS" in kinds


def test_hidden_channel_kind_rejected():
    async def sneaky(round_, session):
        return {"kind": "SECRET_NOTE", "payload": {"x": 1}}

    s = CouncilSession("çelişki-7",
                       {"a": sneaky, "b": speaker("CLAIM", {"n": 1})})
    asyncio.run(s.negotiate())
    assert any(v["kind"] == "HIDDEN_CHANNEL" for v in s.violations)
    # transcript'te SECRET_NOTE YOK — gizli kanal kayda girmedi
    assert all(m.kind != "SECRET_NOTE" for m in s.transcript)


def test_non_member_cannot_submit():
    s = CouncilSession("çelişki-8", {"a": proposer(), "b": acceptor_of("a")})
    with pytest.raises(SecurityViolation):
        s.submit("intruder", "CLAIM", {"x": 1})


def test_direct_submit_forbidden_area_raises():
    s = CouncilSession("çelişki-9", {"a": proposer(), "b": acceptor_of("a")})
    with pytest.raises(SecurityViolation):
        s.submit("a", "PROPOSAL", {"security": "disable sandbox"})
    with pytest.raises(SecurityViolation):
        s.submit("b", "CLAIM", {"capability": "ROOT"})


# ------------------------------------------------------------- kurallar
def test_self_acceptance_does_not_count():
    # a kendi önerisini "kabul" ediyor (PROPOSAL + ACCEPT aynı üye) — saymaz
    async def self_dealer(round_, session):
        if round_ == 1:
            return {"kind": "PROPOSAL", "payload": {"point": "ben haklıyım"}}
        return {"kind": "ACCEPT", "payload": {"proposal_by": "a"}}

    s = CouncilSession("çelişki-10",
                       {"a": self_dealer, "b": speaker("OBJECTION",
                                                      {"n": 1})},
                       limits=CouncilLimits(max_rounds=2))
    out = asyncio.run(s.negotiate())
    assert out["outcome"] == "UNCERTAIN"    # kendi onayı uzlaşma DEĞİL


def test_quorum_all_vs_majority():
    # 3 üye: 1 önerici + 1 kabul + 1 itiraz
    members = {"a": proposer(), "b": acceptor_of("a"),
               "c": speaker("OBJECTION", {"note": "kuşkulu"})}
    s_all = CouncilSession("q1", members, limits=CouncilLimits(max_rounds=1))
    out_all = asyncio.run(s_all.negotiate())
    assert out_all["outcome"] == "UNCERTAIN"          # 'all' quorum sağlanmadı

    s_maj = CouncilSession("q2", members,
                           limits=CouncilLimits(max_rounds=1,
                                                quorum="majority"))
    out_maj = asyncio.run(s_maj.negotiate())
    assert out_maj["outcome"] == "CONSENSUS"          # majority: b yeterli


def test_ratify_requires_matching_authority():
    s = CouncilSession("çelişki-11",
                       {"a": proposer(), "b": acceptor_of("a")},
                       final_authority="SUPERVISOR")
    asyncio.run(s.negotiate())
    with pytest.raises(SecurityViolation):
        s.ratify("JUDGE", {"verdict": "ACCEPT"})   # yanlış otorite RED


def test_transcript_redacted_and_late_objection_flow():
    async def leaky(round_, session):
        return {"kind": "CLAIM",
                "payload": {"note": "db password: cok-gizli-77"}}

    s = CouncilSession("çelişki-12",
                       {"a": leaky, "b": acceptor_of("a")},  # b öneri yok
                       redact_fn=redact,
                       limits=CouncilLimits(max_rounds=1))
    out = asyncio.run(s.negotiate())
    blob = repr([m.payload for m in s.transcript])
    assert "cok-gizli-77" not in blob and out["outcome"] == "UNCERTAIN"


# ------------------------------------------------------------- entegrasyon
def _rig(tmp):
    from app.orchestr.artifacts import ArtifactManager
    from app.orchestr.messages import AgentMessageBus
    from app.orchestr.scheduler import DAGScheduler
    from app.orchestr.tokens import CapabilityTokenAuthority
    from app.orchestr.worker import WorkerRegistry
    from app.observability.trace import Tracer
    reg = WorkerRegistry(db_path=os.path.join(tmp, "w.db"))
    orch = SupervisorOrchestrator(
        reg, DAGScheduler(reg),
        CapabilityTokenAuthority(),
        ArtifactManager(db_path=os.path.join(tmp, "a.db")),
        AgentMessageBus(db_path=os.path.join(tmp, "m.db"), registry=reg),
        tracer=Tracer(path=os.path.join(tmp, "t.jsonl")),
        event_publisher=lambda t, p: events.append((t, p)))
    events = []
    return orch, events


def test_orchestrator_convene_end_to_end(tmp_path):
    """Pipeline'da UNCERTAIN kalan çelişki konseye gider; Supervisor
    nihai kararı imzalar; olaylar denetim kanalına düşer."""
    orch, events = _rig(str(tmp_path))
    members = {"research": proposer("kaynak-1 gerçek"),
               "vision": acceptor_of("research"),
               "system": acceptor_of("research")}

    async def run():
        return await orch.convene(
            "subject:what-changed", members,
            limits=CouncilLimits(max_rounds=2),
            decision={"verdict": "ACCEPT",
                      "basis": "2 bağımsız kabul + kanıt"})

    out = asyncio.run(run())
    assert out["outcome"] == "CONSENSUS"
    assert out["ratified"]["authority"] == "SUPERVISOR"
    assert out["ratified"]["decision"]["verdict"] == "ACCEPT"
    kinds = [t for t, _ in events]
    assert "council.opened" in kinds and "council.resolved" in kinds
    assert "council.ratified" in kinds
    assert len(out["transcript"]) == 3


def test_orchestrator_convene_uncertain_escalates_without_decision(tmp_path):
    """Uzlaşma yoksa otorite kararı ZORLANMAZ — UNCERTAIN kalır."""
    orch, events = _rig(str(tmp_path))
    members = {"research": proposer("A"), "coding": speaker("OBJECTION",
                                                            {"n": 1})}

    async def run():
        return await orch.convene(
            "subject:how-to-fix", members,
            limits=CouncilLimits(max_rounds=2),
            decision={"verdict": "ACCEPT"})

    out = asyncio.run(run())
    assert out["outcome"] == "UNCERTAIN" and out["ratified"] is None
    resolved = [p for t, p in events if t == "council.resolved"]
    assert resolved[0]["outcome"] == "UNCERTAIN"
