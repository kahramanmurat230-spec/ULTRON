"""WAVE 3 / Security — 14 saldırı vektörü, entegrasyon düzeyinde.

Her test bir vektörü ADA/adı ile etiketler; savunma gerçek bileşenlerde
(token authority, message bus, artifact manager, council, orchestrator
ctx) kanıtlanır. Security core (app/security/**) DOKUNULMADI — yalnız
yeniden kullanıldı (redaction).
"""
import asyncio
import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.observability.trace import Tracer  # noqa: E402
from app.orchestr.artifacts import (  # noqa: E402
    ArtifactError, ArtifactManager,
)
from app.orchestr.council import CouncilLimits, CouncilSession  # noqa: E402
from app.orchestr.messages import AgentMessageBus, MessageError  # noqa: E402
from app.orchestr.orchestrator import SupervisorOrchestrator  # noqa: E402
from app.orchestr.scheduler import DAGScheduler  # noqa: E402
from app.orchestr.tokens import (  # noqa: E402
    CapabilityTokenAuthority, TokenError,
)
from app.orchestr.worker import WorkerRegistry  # noqa: E402
from app.security.redaction import redact  # noqa: E402


def rig(tmp, task_id="sec-task", roles=(("RESEARCH", "a"),
                                         ("CODING", "b"))):
    reg = WorkerRegistry(db_path=os.path.join(tmp, "w.db"))
    sch = DAGScheduler(reg)
    auth = CapabilityTokenAuthority()
    arts = ArtifactManager(db_path=os.path.join(tmp, "a.db"), redact_fn=redact)
    bus = AgentMessageBus(db_path=os.path.join(tmp, "m.db"), redact_fn=redact,
                          registry=reg)
    events = []
    orch = SupervisorOrchestrator(
        reg, sch, auth, arts, bus, tracer=Tracer(os.path.join(tmp, "t.jsonl")),
        event_publisher=lambda t, p: events.append((t, p)))
    workers = orch.plan(task_id, [{"role": r, "name": n} for r, n in roles])
    for w in workers:
        reg.save(w)
        orch.issue_worker_tokens(w)
    return orch, workers, events


# V1 — privilege escalation
def test_v1_role_escalation_rejected(tmp_path):
    orch, workers, _ = rig(str(tmp_path))
    research = next(w for w in workers if w.role == "RESEARCH")
    ctx = orch._ctx_for(research)
    with pytest.raises(TokenError):
        ctx["grant"]("WRITE_WORKSPACE")           # rol dışı → token YOK
    assert not any("WRITE_WORKSPACE" in t for t in orch._tokens[research.worker_id])


# V2 — token forgery
def test_v2_forged_token_signature_rejected(tmp_path):
    orch, workers, _ = rig(str(tmp_path))
    w = workers[0]
    real = orch._tokens[w.worker_id][0]
    forged = real[:-4] + ("aaaa" if not real.endswith("aaaa") else "bbbb")
    with pytest.raises(TokenError):
        orch.authority.verify(forged, worker_id=w.worker_id,
                              capability="WEB_SEARCH", task_id=w.task_id)


# V3 — expired token
def test_v3_expired_token_rejected(tmp_path):
    auth = CapabilityTokenAuthority()
    tok = auth.issue("w1", "WEB_SEARCH", task_id="t1", ttl_s=-1)
    with pytest.raises(TokenError, match="expired"):
        auth.verify(tok, worker_id="w1", capability="WEB_SEARCH",
                    task_id="t1")


# V4 — wrong worker
def test_v4_token_of_other_worker_rejected(tmp_path):
    orch, workers, _ = rig(str(tmp_path))
    w_a, w_b = workers[0], workers[1]
    tok = orch.authority.issue(w_a.worker_id, "WEB_SEARCH",
                               task_id=w_a.task_id)
    with pytest.raises(TokenError, match="another worker"):
        orch.authority.verify(tok, worker_id=w_b.worker_id,
                              capability="WEB_SEARCH", task_id=w_a.task_id)


# V5 — cross-task token
def test_v5_cross_task_token_rejected(tmp_path):
    auth = CapabilityTokenAuthority()
    tok = auth.issue("w1", "WEB_SEARCH", task_id="task-A")
    with pytest.raises(TokenError, match="cross-task"):
        auth.verify(tok, worker_id="w1", capability="WEB_SEARCH",
                    task_id="task-B")


# V6 — scope escape
def test_v6_scope_escape_rejected(tmp_path):
    auth = CapabilityTokenAuthority()
    tok = auth.issue("w1", "READ_WORKSPACE", task_id="t1")
    with pytest.raises(TokenError, match="scope escape"):
        auth.verify(tok, worker_id="w1", capability="WRITE_WORKSPACE",
                    task_id="t1")
    with pytest.raises(TokenError):
        auth.issue("w1", "WEB_SEARCH", task_id="t1", scope="root:all")


# V7 — impersonation (registry'de olmayan gönderen)
def test_v7_impersonation_rejected(tmp_path):
    orch, workers, _ = rig(str(tmp_path))
    with pytest.raises(MessageError, match="impersonation"):
        orch.bus.send("phantom-worker", workers[0].worker_id, "ping", {})


# V8 — message spoofing (başka görevin worker'ı adına/inbox'ına)
def test_v8_spoofed_sender_rejected(tmp_path):
    orch, workers, _ = rig(str(tmp_path))
    w_a, w_b = workers[0], workers[1]
    # saldırgan: başka görevde kayıtlı worker, bu görevin worker'ına
    # kendi görevinden mesaj sokmaya çalışıyor
    outsider = orch.plan("evil-task", [{"role": "SYSTEM", "name": "x"}])[0]
    orch.registry.save(outsider)
    with pytest.raises(MessageError, match="cross-task"):
        orch.bus.send(outsider.worker_id, w_b.worker_id, "data", {},
                      task_id="evil-task")
    # aynı görevde bile başkası ADINA sender alanı söylenemez: sender
    # registry kimliğinden resolve edilir; kayıt dışı isim V7'de RED


# V9 — replay
def test_v9_replayed_message_detected(tmp_path):
    orch, workers, _ = rig(str(tmp_path))
    w_a, w_b = workers[0], workers[1]
    r1 = orch.bus.send(w_a.worker_id, w_b.worker_id, "data", {"n": 1},
                       message_id="mid-fixed")
    r2 = orch.bus.send(w_a.worker_id, w_b.worker_id, "data", {"n": 1},
                       message_id="mid-fixed")
    assert r1["status"] == "sent" and r2["status"] != "sent"  # tekrar YOK
    assert len(orch.bus.inbox(w_b.worker_id)) == 1


# V10 — payload injection (boyut + içerik)
def test_v10_payload_injection_rejected_or_neutralized(tmp_path):
    orch, workers, _ = rig(str(tmp_path))
    w_a, w_b = workers[0], workers[1]
    with pytest.raises(MessageError, match="too large"):
        orch.bus.send(w_a.worker_id, w_b.worker_id, "data",
                      {"blob": "x" * 70000})
    r = orch.bus.send(w_a.worker_id, w_b.worker_id, "data",
                      {"cmd": "password: cok-gizli-77"})
    stored = orch.bus.inbox(w_b.worker_id)[0]["payload"]
    assert "cok-gizli-77" not in repr(stored)      # secret kanala düşmez


# V11 — shared-state race (aynı id eş zamanlı create)
def test_v11_concurrent_create_single_winner(tmp_path):
    orch, _, _ = rig(str(tmp_path))
    winners, conflicts = [], []
    def attempt(i):
        try:
            a = orch.artifacts.create("race-task", f"w{i}", "note", "veri",
                                      artifact_id="same-id")
            winners.append(a)
        except ArtifactError as exc:
            conflicts.append(str(exc))
    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(winners) == 1 and len(conflicts) == 7   # lost update YOK


# V12 — artifact access control (başka görev)
def test_v12_cross_task_artifact_access_denied(tmp_path):
    orch, _, _ = rig(str(tmp_path))
    a = orch.artifacts.create("task-A", "w1", "report", "içerik-1")
    got = orch.artifacts.get(a["artifact_id"], requester_worker="w1",
                             requester_task="task-A")
    assert got["content"] == "içerik-1"
    with pytest.raises(ArtifactError, match="unauthorized"):
        orch.artifacts.get(a["artifact_id"], requester_worker="w2",
                           requester_task="task-B")
    assert orch.artifacts.verify_integrity(a["artifact_id"])["ok"] is True


# V13 — secret leakage (trace + artifact + event + message kanalları)
def test_v13_secret_never_reaches_any_channel(tmp_path):
    tmp = str(tmp_path)
    orch, workers, events = rig(tmp)
    w = workers[0]

    async def leaky(worker, ctx):
        art = ctx["artifact"]("report", "api password: cok-gizli-77")
        ctx["send"](workers[1].worker_id, "data",
                    {"note": "token password: cok-gizli-77"})
        return {"ok": True, "output": "password: cok-gizli-77",
                "confidence": 0.9, "evidence": ["e"]}

    out = asyncio.run(orch.execute("sec-task", workers,
                                   {"RESEARCH": leaky}))
    assert out["states"][w.worker_id] == "SUCCEEDED"
    blob = open(os.path.join(tmp, "t.jsonl"), encoding="utf-8").read()
    blob += repr(orch.artifacts.for_task("sec-task"))
    blob += repr(orch.bus.inbox(workers[1].worker_id))
    blob += repr(events)
    assert "cok-gizli-77" not in blob               # hiçbir kanalda YOK


# V14 — approval bypass (HIGH capability müzakereyle/onaysız alınamaz)
def test_v14_approval_bypass_impossible(tmp_path):
    # (a) onaysız verify → RED
    auth = CapabilityTokenAuthority()
    tok = auth.issue("w1", "WRITE_WORKSPACE", task_id="t1")
    with pytest.raises(TokenError, match="approval"):
        auth.verify(tok, worker_id="w1", capability="WRITE_WORKSPACE",
                    task_id="t1")

    # (b) konseyden "capability" pazarlamak → ihlal kaydı, kapı KAPALI
    async def dealer(round_, session):
        return {"kind": "PROPOSAL",
                "payload": {"deal": "grant capability WRITE_WORKSPACE "
                                   "to w1 without approval"}}

    async def voter(round_, session):
        return {"kind": "ACCEPT", "payload": {"proposal_by": "dealer"}}

    s = CouncilSession("approval-bypass", {"dealer": dealer, "v": voter},
                       limits=CouncilLimits(max_rounds=1))
    out = asyncio.run(s.negotiate())
    assert out["outcome"] == "UNCERTAIN"
    assert any(v["kind"] == "CAPABILITY_BYPASS" for v in s.violations)
    with pytest.raises(TokenError, match="approval"):
        auth.verify(tok, worker_id="w1", capability="WRITE_WORKSPACE",
                    task_id="t1")                   # hâlâ kapalı
