"""WAVE 4 / Multimodal fusion: bağlam bütçesi/ranking/provenance, event
köprüsü dedup+origin, memory policy, Wave 3 paralel toplama."""
import asyncio
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.memory.store_v3 import MemoryStore  # noqa: E402
from app.multimodal.context_fusion import (  # noqa: E402
    ContextBudget, ContextEntry, MultimodalContext,
    MultimodalEventBridge, MultimodalMemoryPolicy,
)
from app.multimodal.fusion_agent import FusionAgent, default_executors  # noqa: E402
from app.observability.trace import Tracer  # noqa: E402
from app.orchestr.artifacts import ArtifactManager  # noqa: E402
from app.orchestr.messages import AgentMessageBus  # noqa: E402
from app.orchestr.orchestrator import SupervisorOrchestrator  # noqa: E402
from app.orchestr.scheduler import DAGScheduler  # noqa: E402
from app.orchestr.tokens import CapabilityTokenAuthority  # noqa: E402
from app.orchestr.worker import WorkerRegistry  # noqa: E402
from app.security.redaction import redact  # noqa: E402


# ---------------------------------------------------------------- entry
def test_entry_requires_provenance():
    with pytest.raises(ValueError, match="provenance"):
        ContextEntry("voice", "merhaba", source="")
    with pytest.raises(ValueError, match="bilinmeyen"):
        ContextEntry("telepati", "?", source="x:y")


def test_freshness_decays():
    e_new = ContextEntry("voice", "komut", source="stt:final",
                         ts=time.time())
    e_old = ContextEntry("voice", "eski komut", source="stt:final",
                         ts=time.time() - 700)
    assert e_new.freshness() > 0.9
    assert e_old.freshness() == 0.0                      # horizon dışı


def test_relevance_query_overlap_beats_noise():
    q = "hata düzelt stack trace"
    hit = ContextEntry("terminal", "Traceback hata dosya.py satır 3",
                       source="terminal:tail", confidence=0.9)
    miss = ContextEntry("screen", "masaüstü görüntüsü boş",
                        source="vision:frame", confidence=0.9)
    now = time.time()
    assert hit.relevance(q, now) > miss.relevance(q, now)


# ---------------------------------------------------------------- budget
def test_context_budget_caps_entries_and_evicts_weakest():
    ctx = MultimodalContext(ContextBudget(max_entries=5, max_tokens=10000))
    for i in range(8):
        ctx.add(ContextEntry("logs", f"log-{i}", source=f"logs:{i}",
                             confidence=0.3))
    assert len(ctx.entries) <= 5
    assert ctx.dropped["by_budget"] >= 3
    assert ctx.stats()["entries"] == len(ctx.entries)


def test_context_token_budget_enforced():
    ctx = MultimodalContext(ContextBudget(max_entries=100, max_tokens=50))
    for i in range(20):
        ctx.add(ContextEntry("memory", "y" * 80, source=f"mem:{i}"))  # 20 tk
    assert sum(e.tokens for e in ctx.entries) <= 50      # tavan tutuldu
    assert ctx.dropped["by_budget"] >= 15                # zayıflar atıldı
    # tek başına bütçeyi aşan dev kayıt hiç girmez
    assert ctx.add(ContextEntry("logs", "z" * 4000,
                                source="logs:huge")) is False
    assert ctx.dropped["overflow"] == 1


def test_to_prompt_respects_budget_and_labels():
    ctx = MultimodalContext(ContextBudget(max_tokens=30))
    ctx.add(ContextEntry("voice", "şu hatayı düzelt", source="stt:final",
                         confidence=0.95))
    ctx.add(ContextEntry("code", "def f(): pass", source="code:buffer",
                         confidence=0.9))
    out = ctx.to_prompt("hata")
    assert "[voice|stt:final|conf=0.95]" in out            # provenance etiket
    assert ctx.stats()["tokens"] <= 30


# ---------------------------------------------------------------- §22 events
def test_event_bridge_publishes_with_origin_and_dedup():
    published = []

    def pub(topic, payload):
        published.append((topic, payload))

    br = MultimodalEventBridge(publisher=pub, now=lambda: time.monotonic())
    assert br.emit("screen.changed", {"hash": "ab"}) is True
    assert br.emit("screen.changed", {"hash": "ab"}) is False  # dedup
    time.sleep(0.06)
    assert br.emit("screen.changed", {"hash": "cd"}) is True
    assert published[0][1]["origin"] == "multimodal"         # etiket ZORUNLU
    assert br.suppressed == 1 and br.published == 2


def test_event_bridge_rejects_unknown_topic():
    br = MultimodalEventBridge(publisher=lambda *a: None)
    with pytest.raises(ValueError):
        br.emit("brain.arbitrary", {})


def test_event_bridge_to_durable_bus_real():
    """Gerçek Wave 2 DurableEventBus'a yayın (loop korumalı)."""
    from app.events.bus import DurableEventBus
    tmp = os.path.join("/tmp", f"mm-bus-{os.getpid()}.db")
    bus = DurableEventBus(db_path=tmp)
    got = []
    bus.subscribe("voice.*", lambda topic, payload: got.append((topic,
                                                                payload)))
    br = MultimodalEventBridge(bus)
    br.emit("voice.started", {"turn": 1})
    time.sleep(0.02)
    br.emit("voice.interrupted", {"reason": "barge-in"})
    time.sleep(0.05)
    assert {t for t, _ in got} >= {"voice.started", "voice.interrupted"}
    assert all(p.get("origin") == "multimodal" for _, p in got)


# ---------------------------------------------------------------- §23 memory
def test_memory_policy_ephemeral_default_no_write():
    pol = MultimodalMemoryPolicy()
    low = ContextEntry("screen", "boş masaüstü", source="vision:frame",
                       confidence=0.4)
    res = pol.maybe_write(low)
    assert res["write"] is False and res["class"] == "ephemeral"


def test_memory_policy_requires_provenance():
    pol = MultimodalMemoryPolicy()
    bad = ContextEntry("voice", "notlar", source="bilinmeyen-kaynak",
                       confidence=0.95)
    res = pol.maybe_write(bad)
    assert res["write"] is False and "provenance" in res["reason"]


def test_memory_policy_writes_observation_to_real_store(tmp_path):
    store = MemoryStore(db_path=os.path.join(str(tmp_path), "v3.db"),
                        redact_fn=redact)
    pol = MultimodalMemoryPolicy(store)
    obs = ContextEntry("ocr", "Terminal: 3 hata, dosya.py", source="ocr:f1",
                       confidence=0.9)
    res = pol.maybe_write(obs)
    assert res["write"] is True and res["class"] == "observation"
    rows = store.query()
    assert len(rows) == 1 and rows[0]["source_id"] == "mm:ocr:f1"
    assert rows[0]["record_kind"] == "OBSERVATION"


def test_memory_policy_never_auto_writes_frames():
    """Her frame/transcript OTOMATİK yazılmaz — kanıt."""
    store = MemoryStore(db_path=os.path.join(str(tmp_path), "auto.db"),
                        redact_fn=redact) if False else None
    tmpd = "/tmp"  # yukarıdaki koşullu kullanılmaz
    store = MemoryStore(db_path=os.path.join(tmpd, f"mm-auto-{os.getpid()}"
                                             f".db"), redact_fn=redact)
    pol = MultimodalMemoryPolicy(store)
    for i in range(10):
        pol.maybe_write(ContextEntry("vision", f"frame {i} değişmedi",
                                     source=f"vision:f{i}",
                                     confidence=0.3))
    assert store.query() == []                    # hiçbiri yazılmadı


# ---------------------------------------------------------------- §21 agent
def _orch(tmp):
    reg = WorkerRegistry(db_path=os.path.join(tmp, "w.db"))
    orch = SupervisorOrchestrator(
        reg, DAGScheduler(reg, global_limit=8), CapabilityTokenAuthority(),
        ArtifactManager(db_path=os.path.join(tmp, "a.db"), redact_fn=redact),
        AgentMessageBus(db_path=os.path.join(tmp, "m.db"), registry=reg,
                        redact_fn=redact),
        tracer=Tracer(os.path.join(tmp, "t.jsonl")))
    return orch


def test_fusion_agent_parallel_gather_into_context(tmp_path):
    """Wave 3 DAG gerçek: 4 kaynak paralel + verification bağımlı;
    sonuçlar bağlama kaynak etiketiyle düşer."""
    import time as _t
    orch = _orch(str(tmp_path))
    bridge = MultimodalEventBridge(publisher=lambda *a: None)
    agent = FusionAgent(orch, bridge=bridge)
    calls = {"t0": _t.monotonic()}

    def vision():
        return "ekran: 2 hata penceresi görünür"
    def browser():
        return "DOM: 'Rerun' butonu aktif"
    def computer():
        return "uia: Terminal odaklı"
    def coding():
        return "kod: tests/test_x.py son commit kırık"
    def verify():
        return "tüm kaynaklar tutarlı"

    execs = default_executors(vision=vision, browser=browser,
                              computer=computer, coding=coding,
                              verification=verify, bridge=bridge)
    report = asyncio.run(agent.gather(execs))
    assert report["pipeline"]["results"] == 5
    st = MultimodalContext()
    kinds = sorted(e.kind for e in agent.context.entries)
    assert kinds == ["browser", "code", "screen", "task", "vision"]
    assert all(e.source.startswith("worker:") for e in agent.context.entries)
    # bağlam, hata sorgusuna göre terminal/kod ağırlıklı sıralanabildi
    prompt = agent.context.to_prompt("hata")
    assert "hata" in prompt


def test_fusion_agent_unavailable_sources_honest(tmp_path):
    """Kaynak yoksa worker FAILED/düşük güven — SAHTE veri girmez."""
    orch = _orch(str(tmp_path))
    agent = FusionAgent(orch)
    execs = default_executors()               # hiç kaynak bağlı değil
    report = asyncio.run(agent.gather(execs))
    assert all(s != "SUCCEEDED" for s in report["states"].values())
    assert agent.context.entries == []        # bağlama hiçbir şey girmedi
