"""Wave 5 §15+§16+§19 — Proactive link, Communication, Observability."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.cognitive.autonomy_safety import AutonomySafety  # noqa: E402
from app.cognitive.cognitive_observability import CognitiveTrace  # noqa: E402
from app.cognitive.communication import ConversationState  # noqa: E402
from app.cognitive.goal_engine import GoalEngine  # noqa: E402
from app.cognitive.proactive_link import ProactiveCortex  # noqa: E402
from app.cognitive.user_intelligence import UserIntelligence  # noqa: E402


# ---------------------------------------------------------------- §15
def make_cortex(tmp, **kw):
    return ProactiveCortex(db_path=os.path.join(tmp, "pc.db"),
                           cooldown_s=30.0, **kw)


def test_notify_high_importance_event():
    with tempfile.TemporaryDirectory() as d:
        pc = make_cortex(tmp=d)
        r = pc.ingest({"kind": "deadline", "summary": "release bugüne",
                       "importance": 0.9})
        assert r["action"] == "NOTIFY"


def test_dedup_suppresses_same_signature():
    with tempfile.TemporaryDirectory() as d:
        pc = make_cortex(tmp=d)
        pc.ingest({"kind": "alert", "summary": "cpu yüksek", "importance": 0.9})
        r2 = pc.ingest({"kind": "alert", "summary": "cpu yüksek",
                        "importance": 0.9})
        assert r2["action"] == "suppressed" and r2["suppressed_by"] == "dedup"


def test_cooldown_suppresses_same_kind():
    with tempfile.TemporaryDirectory() as d:
        pc = make_cortex(tmp=d)
        pc.ingest({"kind": "alert", "summary": "disk doluyor",
                   "importance": 0.9})
        r2 = pc.ingest({"kind": "alert", "summary": "disk neredeyse doldu",
                        "importance": 0.9})   # farklı imza, aynı tip
        assert r2["suppressed_by"] == "cooldown"


def test_low_importance_suppressed():
    with tempfile.TemporaryDirectory() as d:
        pc = make_cortex(tmp=d)
        r = pc.ingest({"kind": "info", "summary": "sayaç arttı",
                       "importance": 0.2})
        assert r["suppressed_by"] == "importance"
        assert pc.stats()["notified"] == 0


def test_goal_alignment_boosts_importance():
    with tempfile.TemporaryDirectory() as d:
        ge = GoalEngine(db_path=os.path.join(d, "g.db"))
        ge.create("release hazırla ve duyur")
        pc = make_cortex(tmp=d, goal_engine=ge)
        r = pc.ingest({"kind": "info", "summary": "release notları hazır",
                       "importance": 0.45})   # eşik altı ama goal hizalı
        assert r["action"] == "NOTIFY" and r["importance"] >= 0.7


def test_autonomous_action_requires_safety_gate():
    with tempfile.TemporaryDirectory() as d:
        s = AutonomySafety(db_path=os.path.join(d, "s.db"))
        pc = make_cortex(tmp=d, safety=s)
        r = pc.ingest({"kind": "maintenance", "summary": "log temizliği",
                       "importance": 0.9,
                       "proposed_action": "delete old logs"})
        # high risk + onaysız → DENY → suppress
        assert r["action"] == "suppressed" and r["suppressed_by"] == "risk-gate-deny"


def test_autonomous_without_safety_engine_never_autonomous():
    with tempfile.TemporaryDirectory() as d:
        pc = make_cortex(tmp=d)  # safety motoru YOK
        r = pc.ingest({"kind": "info", "summary": "rapor",
                       "importance": 0.9, "proposed_action": "read file"})
        # safety yoksa otonom ASLA — düz NOTIFY'ya düşer
        assert r["action"] == "NOTIFY"


def test_minimal_style_preference_reduces_noise():
    with tempfile.TemporaryDirectory() as d:
        ui = UserIntelligence(db_path=os.path.join(d, "u.db"))
        for _ in range(6):
            ui.note_response_length(30)     # concise öğrenildi
        pc = make_cortex(tmp=d, user_intel=ui)
        r = pc.ingest({"kind": "info", "summary": "orta önemli olay",
                       "importance": 0.6})
        assert r["suppressed_by"] == "user-pref-minimal"
        r2 = pc.ingest({"kind": "alert", "summary": "kritik olay",
                        "importance": 0.95})
        assert r2["action"] == "NOTIFY"    # kritik yine geçer


# ---------------------------------------------------------------- §16
def test_turn_tracking_and_interruption():
    cs = ConversationState()
    cs.agent_turn("uzun yanıt başladı")
    r = cs.user_turn("dur, başka bir şey soracağım")
    assert r["interrupted_agent"] is True
    assert cs.summary()["interruptions"] == 1


def test_topic_continuity_and_shift():
    cs = ConversationState()
    cs.user_turn("python performans sorunları hakkında")
    r2 = cs.user_turn("hava durumu nasıl yarın")
    assert r2["topic_shift"] is True
    assert cs.summary()["current_topic"].startswith("hava")


def test_clarification_detection():
    assert ConversationState.needs_clarification("bu doğru mu") is True
    assert ConversationState.needs_clarification("saat kaçta?") is False
    assert ConversationState.needs_clarification("") is True


def test_ambiguity_detection():
    a = ConversationState.ambiguity("şey")
    assert a["ambiguous"] and "too-short" in a["reasons"]
    b = ConversationState.ambiguity("onu da ayarlar mısın")
    assert b["ambiguous"] and "vague-reference" in b["reasons"]
    c = ConversationState.ambiguity(" Deploy sürecini 3 adımda açıkla")
    assert c["ambiguous"] is False


def test_response_plan_style_and_confidence():
    with tempfile.TemporaryDirectory() as d:
        ui = UserIntelligence(db_path=os.path.join(d, "u.db"))
        for _ in range(6):
            ui.note_response_length(200)   # deep
        cs = ConversationState(user_intel=ui)
        cs.user_turn("servis neden çöktü")
        plan = cs.response_plan(confidence=0.3)
        assert plan["style"] == "deep" and plan["length_hint"] == "detailed"
        assert plan["disclose_uncertainty"] is True
        assert "Emin değilim" in plan["prefix_hint"]
        plan2 = cs.response_plan(confidence=0.95)
        assert plan2["disclose_uncertainty"] is False


# ---------------------------------------------------------------- §19
def test_trace_kinds_and_redaction():
    with tempfile.TemporaryDirectory() as d:
        tr = CognitiveTrace(db_path=os.path.join(d, "t.db"))
        tr.log("goal", "g1", {"state": "ACTIVE"})
        tr.log("decision", "d1", {"chosen": "canary", "confidence": 0.8})
        tr.log("prediction", "p1", {"estimate_s": 12.5})
        assert tr.stats()["total"] == 3
        assert tr.stats()["by_kind"]["decision"] == 1
        got = tr.query(kind="decision")
        assert got[0]["data"]["chosen"] == "canary"


def test_trace_secret_never_persisted():
    with tempfile.TemporaryDirectory() as d:
        tr = CognitiveTrace(db_path=os.path.join(d, "t.db"))
        tr.log("research", "kanıt", {"text": "token=supersecret99 bağlandı"})
        tr.log("autonomous", "aksiyon",
               {"detail": "api_key: sk-1234567890abcdef kullanıldı"})
        raw = open(os.path.join(d, "t.db"), "rb").read()
        for secret in (b"supersecret99", b"sk-1234567890abcdef"):
            assert secret not in raw      # secret DB'ye YAZILMADI bile


def test_trace_unknown_kind_rejected():
    with tempfile.TemporaryDirectory() as d:
        tr = CognitiveTrace(db_path=os.path.join(d, "t.db"))
        try:
            tr.log("casual", "x", {})
            assert False
        except ValueError:
            pass
