"""Wave 5 §20 — E2E: cognitive zincir uçtan uca (gerçek nesnelerle)."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.cognitive.autonomy_loop import AutonomousGoalLoop, Budgets  # noqa: E402
from app.cognitive.autonomy_safety import AutonomySafety  # noqa: E402
from app.cognitive.capability_discovery import (  # noqa: E402
    CapabilityDiscovery, MetaReasoning)
from app.cognitive.cognitive_observability import CognitiveTrace  # noqa: E402
from app.cognitive.context_engine import ContextEngine  # noqa: E402
from app.cognitive.decision_engine import DecisionEngine  # noqa: E402
from app.cognitive.goal_engine import GoalEngine  # noqa: E402
from app.cognitive.knowledge_engine import KnowledgeEngine  # noqa: E402
from app.cognitive.learning_loop import LearningLoop  # noqa: E402
from app.cognitive.proactive_link import ProactiveCortex  # noqa: E402
from app.cognitive.research_engine import ResearchEngine  # noqa: E402
from app.cognitive.user_intelligence import UserIntelligence  # noqa: E402


def build(tmp):
    """Tüm bilişsel katmanlar gerçek instance'larla."""
    trace = CognitiveTrace(db_path=os.path.join(tmp, "t.db"))
    ui = UserIntelligence(db_path=os.path.join(tmp, "u.db"))
    ctx = ContextEngine(db_path=os.path.join(tmp, "c.db"))
    ge = GoalEngine(db_path=os.path.join(tmp, "g.db"))
    de = DecisionEngine(db_path=os.path.join(tmp, "d.db"))
    ke = KnowledgeEngine(db_path=os.path.join(tmp, "k.db"))
    safety = AutonomySafety(db_path=os.path.join(tmp, "s.db"))
    loop = AutonomousGoalLoop(db_path=os.path.join(tmp, "a.db"),
                              safety=safety, goal_engine=ge)
    ll = LearningLoop(db_path=os.path.join(tmp, "l.db"), knowledge=ke,
                      user_intel=ui)
    cortex = ProactiveCortex(db_path=os.path.join(tmp, "p.db"),
                             safety=safety, goal_engine=ge, user_intel=ui)
    return dict(trace=trace, ui=ui, ctx=ctx, ge=ge, de=de, ke=ke,
                safety=safety, loop=loop, ll=ll, cortex=cortex)


def test_e2e_full_cognitive_chain():
    with tempfile.TemporaryDirectory() as tmp:
        s = build(tmp)
        # 1) KULLANICI ÖĞRENİR (explicit tercih + davranış)
        assert s["ui"].set_explicit("work", "review_style", "concise")["ok"]
        s["ui"].record_event("open_editor")
        s["ui"].record_event("run_tests")
        # 2) CONTEXT: görev bağlamı kurulur
        s["ctx"].add("proje", "ultron", namespace="project:ultron",
                     modality="text", importance=0.8)
        snap = s["ctx"].snapshot("project:ultron")
        assert snap["items"]
        # 3) GOAL: hedef + alt hedefler
        gid = s["ge"].create("sürüm notlarını yayınla",
                             intent="kullanıcı istedi")["goal"]
        subs = s["ge"].decompose(gid, ["notları yaz", "tag oluştur",
                                       "duyur"])["subgoals"]
        # 4) DECISION: strateji seçimi kayıtlı
        dec = s["de"].decide("publish", "kanal", options=[
            {"id": "gh_release", "value": 80, "cost": 5, "confidence": 0.9},
            {"id": "email", "value": 40, "cost": 2, "confidence": 0.7}])
        assert dec["ok"] and dec["chosen"] == "gh_release"
        s["de"].verify(dec["decision_id"], "gh_release yayınlandı", True)
        # 5) AUTONOMY LOOP: alt hedefler güvenli döngüde koşar
        run = s["loop"].run(
            "sürüm notlarını yayınla",
            [{"name": f"read {sub}"} for sub in subs],
            lambda a: {"ok": True, "output": f"ok:{a['name']}",
                       "cost": 0.01, "tokens": 5},
            verifier=lambda r, g: r.get("ok"),
            budgets=Budgets(max_iterations=10), goal_id=subs[0])
        assert run["status"] == "COMPLETED"
        # 6) LEARNING: gerçek sonuçtan ders
        s["ll"].record_outcome("release", expected="3 alt hedef",
                               actual="3 alt hedef", success=True,
                               latency_s=run["budget"]["consumed"]["seconds"])
        assert s["ll"].metrics("release")["success_rate"] == 1.0
        # 7) KNOWLEDGE: ders bilgi katmanında erişilebilir
        found = s["ke"].search("release")
        assert found["results"]
        # 8) OBSERVABILITY: iz kanalları dolu
        s["trace"].log("goal", gid, {"state": "DONE"})
        s["trace"].log("autonomous", "loop", {"run": run["run_id"],
                                              "status": run["status"]})
        st = s["trace"].stats()
        assert st["by_kind"]["goal"] == 1 and st["by_kind"]["autonomous"] == 1
        # 9) PROACTIVE: tamamlandı bildirimi spam'siz
        p1 = s["cortex"].ingest({"kind": "info",
                                 "summary": "sürüm notları yayınlandı",
                                 "importance": 0.85})
        p2 = s["cortex"].ingest({"kind": "info",
                                 "summary": "sürüm notları yayınlandı",
                                 "importance": 0.85})
        assert p1["action"] == "NOTIFY" and p2["suppressed_by"] == "dedup"


def test_e2e_secret_never_reaches_cognitive_channels():
    with tempfile.TemporaryDirectory() as tmp:
        s = build(tmp)
        secret = "zz-cog-secret-4242"
        # secret, context'e ve trace'e girmeye ÇALIŞIYOR (saldırgan senaryosu)
        s["ctx"].add("token", f"token={secret}", namespace="attack")
        s["trace"].log("decision", "d", {"detail": f"token={secret}"})
        raw_trace = open(os.path.join(tmp, "t.db"), "rb").read()
        assert secret.encode() not in raw_trace          # trace RED
        # context DB'si secret DEPOLAR (kullanıcının verisi) ama snapshot
        # raporu kanala giderken redact edilir — publish API yoktur; burada
        # kanal testi: audit + trace temiz
        from app.security.audit import AuditLog
        audit = AuditLog(path=os.path.join(tmp, "a.log"))
        audit.write("USER", f"not: şifrem {secret} sakla")
        log = open(os.path.join(tmp, "a.log"), encoding="utf-8").read()
        assert secret not in log                          # audit RED


def test_e2e_failure_recovery_and_honest_stop():
    with tempfile.TemporaryDirectory() as tmp:
        s = build(tmp)
        calls = {"n": 0}

        def flaky(action):
            calls["n"] += 1
            if calls["n"] == 1:
                return {"ok": False, "error": "ağ koptu"}
            return {"ok": True, "output": "kurtarıldı"}

        run = s["loop"].run(
            "dayanıklı yayın", [{"name": "publish"}], flaky,
            verifier=lambda r, g: r.get("ok"),
            replan_fn=lambda f, r: [{"name": "publish"}],
            budgets=Budgets(max_iterations=6))
        assert run["status"] == "COMPLETED" and run["replans"] == 1
        # bütçe tükenmesi dürüst STOP (uydurma tamamlandı YOK)
        run2 = s["loop"].run("bütçeli", [{"name": f"x{i}"} for i in range(9)],
                             lambda a: {"ok": True, "output": "y"},
                             budgets=Budgets(max_iterations=2))
        assert run2["status"] == "STOPPED_BUDGET"
        # checkpoint'ler kalıcı — geri alınabilir
        assert s["loop"].checkpoints(run["run_id"])


def test_e2e_research_to_knowledge_with_citations():
    with tempfile.TemporaryDirectory() as tmp:
        s = build(tmp)
        fake = [{"url": "https://arxiv.org/abs/51", "title":
                 "retrieval augmented generation survey",
                 "snippet": "RAG reduces hallucination 42 pct"},
                {"url": "https://arxiv.org/abs/52", "title":
                 "rag systems evaluation", "snippet": "RAG grounded 42 pct"}]
        re_ = ResearchEngine(db_path=os.path.join(tmp, "r.db"),
                             search_fn=lambda q: fake)
        res = re_.research("rag hallucination")
        assert res["ok"] and res["citations"]
        # bulgular bilgi katmanına provenance ile iner
        did = s["ke"].ingest(
            "rag-bulgular", "RAG halüsinasyonu azaltır; 42 pct grounded",
            source=res["citations"][0], source_confidence=0.8,
            claims={"rag_etkisi": "halüsinasyon azaltır"})["doc_id"]
        got = s["ke"].search("rag")
        assert any(r["doc_id"] == did for r in got["results"])
        assert got["results"][0]["source"].startswith("https://arxiv.org")
        # meta-reasoning: güvenilir bileşim
        mr = MetaReasoning()
        conf = mr.reasoning_confidence(
            evidences=[{"confidence": res["confidence"], "text": "42"}],
            claims=[{"key": "rag_etkisi", "value": "halüsinasyon azaltır",
                     "source": did}],
            citations=res["citations"], answer="42 pct grounded")
        assert conf["confidence"] > 0


def test_e2e_capability_gate_missing_needs_user():
    with tempfile.TemporaryDirectory() as tmp:
        s = build(tmp)
        cd = CapabilityDiscovery(available_fn=lambda c: c != "voice")
        disc = cd.discover("sesli not al ve mail ile gönder")
        assert "voice" in disc["missing"]
        assert disc["decision"] == "NEEDS_USER_INPUT"   # varmış gibi DEĞİL
        # simülasyon: mail gönderimi policy kapısında
        sim_out = s["de"].decide("notify", "kanal", options=[
            {"id": "email", "value": 60, "cost": 1, "confidence": 0.8}],
            constraints=[])
        assert sim_out["authority"] == "AUTONOMOUS"  # low risk read
        ev = s["safety"].evaluate("bildirim", "send email report",
                                  reversibility="partially-reversible")
        assert ev["decision"] == "NOTIFY"            # policy izni gerekli
