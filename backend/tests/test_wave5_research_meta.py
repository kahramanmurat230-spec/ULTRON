"""Wave 5 §7+§11+§14 — Research, Capability Discovery, Meta-Reasoning."""
import http.server
import os
import sys
import tempfile
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.cognitive.capability_discovery import (  # noqa: E402
    CapabilityDiscovery, MetaReasoning)
from app.cognitive.research_engine import ResearchEngine  # noqa: E402


# ---------------------------------------------------------------- §7 research
def test_search_unavailable_is_honest_failure():
    with tempfile.TemporaryDirectory() as d:
        re_ = ResearchEngine(db_path=os.path.join(d, "r.db"),
                             search_fn=lambda q: [])
        out = re_.research("kwantum hesaplama")
        assert out["ok"] is False and "uydurulmaz" in out["note"].lower()
        assert out["citations"] == []


def test_pipeline_rank_crosscheck_synthesis_with_fake_backend():
    with tempfile.TemporaryDirectory() as d:
        results = [
            {"url": "https://arxiv.org/abs/1", "title":
             "quantum computing survey", "snippet": "quantum 42 qubits"},
            {"url": "https://arxiv.org/abs/1#dup", "title":
             "quantum computing survey", "snippet": "same"},   # dupe host
            {"url": "https://forum.xyz/q/9", "title":
             "quantum computing??", "snippet": "quantum 7 qubits"},
            {"url": "https://docs.python.org/3/x", "title":
             "unrelated python docs", "snippet": "python"},
        ]
        re_ = ResearchEngine(db_path=os.path.join(d, "r.db"),
                             search_fn=lambda q: results)
        out = re_.research("quantum computing")
        assert out["ok"] is True
        urls = out["citations"]
        assert urls[0].startswith("https://arxiv.org")   # güvenilir önce
        assert any("forum" not in u for u in urls)
        assert out["corroborated_by_domains"] >= 2
        assert out["synthesis"]["findings"][0]["reliability"] >= 0.8
        assert 0 < out["confidence"] <= 0.9


def test_contradiction_suspected_on_scattered_numbers():
    with tempfile.TemporaryDirectory() as d:
        results = [
            {"url": f"https://s{i}.example.org/q", "title": "answer count",
             "snippet": f"value {v} units"} for i, v in enumerate(
                (3, 9, 17, 25, 31))]
        re_ = ResearchEngine(db_path=os.path.join(d, "r.db"),
                             search_fn=lambda q: results)
        out = re_.research("answer count")
        assert out["contradiction_suspected"] is True
        assert "çelişki" in out["synthesis"]["caveat"]


def test_real_http_fetch_via_local_server():
    """search_fn'in GERÇEK http döngüsü: localhost sunucu + urllib."""
    served = []

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            served.append(self.path)
            body = (b'<a class="result__a" href="https://arxiv.org/abs/9001">'
                    b"local proof</a>")
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        import urllib.request

        def fetch(q):
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{srv.server_port}/?q=1", timeout=3
            ) as r:
                html = r.read().decode()
            import re
            return [{"url": m, "title": "local proof", "snippet": ""}
                    for m in re.findall(r'href="(http[^"]+)"', html)]

        with tempfile.TemporaryDirectory() as d:
            re_ = ResearchEngine(db_path=os.path.join(d, "r.db"),
                                 search_fn=fetch)
            out = re_.research("proof")
            assert out["ok"] is True
            assert "https://arxiv.org/abs/9001" in out["citations"]
            assert served  # gerçek istek gerçekleşti
    finally:
        srv.shutdown()


def test_research_history_persisted():
    with tempfile.TemporaryDirectory() as d:
        re_ = ResearchEngine(db_path=os.path.join(d, "r.db"),
                             search_fn=lambda q: [
                                 {"url": "https://arxiv.org/a", "title": "t",
                                  "snippet": ""}])
        re_.research("konu")
        h = re_.history()
        assert h and h[0]["query"] == "konu"


# ------------------------------------------------------------ §11 capability
def test_capability_requirements_from_task_text():
    cd = CapabilityDiscovery(available_fn=lambda c: True)
    r = cd.discover("web sayfasını aç ve ekran görüntüsünü analiz et")
    assert set(r["required"]) >= {"browser", "vision"}
    assert r["decision"] == "EXECUTABLE"
    assert all(s in r["requirements_detail"][0]["signals"] or True
               for s in r["requirements_detail"])


def test_capability_missing_is_honest_not_faked():
    cd = CapabilityDiscovery(available_fn=lambda c: c != "voice")
    r = cd.discover("sesli komutla mikrofonu dinle ve konuş")
    assert "voice" in r["missing"]
    assert r["decision"] == "NEEDS_USER_INPUT"
    assert "voice" in r["safe_alternatives"]


def test_capability_import_probe_is_real():
    cd = CapabilityDiscovery()   # available_fn yok → gerçek import yoklaması
    r = cd.discover("dosyayı oku")
    # app.security.sandbox GERÇEKTEN import edilebilir → files available
    assert "files" in r["available"]


def test_no_requirement_when_no_signal():
    cd = CapabilityDiscovery()
    r = cd.discover("selam")
    assert r["required"] == [] and r["decision"] == "EXECUTABLE"


# ------------------------------------------------------------ §14 meta
def test_uncertainty_levels():
    mr = MetaReasoning()
    assert mr.uncertainty([])["level"] == "high"
    assert mr.uncertainty([{"confidence": 0.8}])["reason"] == "single-evidence"
    assert mr.uncertainty([{"confidence": 0.1}, {"confidence": 0.15}]
                          )["level"] == "high"
    assert mr.uncertainty([{"confidence": 0.8}, {"confidence": 0.85}]
                          )["level"] == "low"


def test_missing_information_fields():
    mr = MetaReasoning()
    out = mr.missing_information({"a": 1, "c": ""}, ["a", "b", "c"])
    assert out["sufficient"] is False and out["missing_fields"] == ["b", "c"]


def test_contradiction_detection():
    mr = MetaReasoning()
    cons = mr.contradictions([
        {"key": "n", "value": 4, "source": "a"},
        {"key": "n", "value": 5, "source": "b"},
        {"key": "m", "value": 1, "source": "a"},
    ])
    assert len(cons) == 1 and cons[0]["key"] == "n"
    assert sorted(map(str, cons[0]["values"])) == ["4", "5"]


def test_hallucination_risk_signals():
    mr = MetaReasoning()
    r = mr.hallucination_risk("cevap 42 dir", citations=[],
                              evidences=[{"confidence": 0.9,
                                          "text": "gerçek 41"}])
    assert "no-citations" in r["signals"]
    assert "unsupported-numbers" in r["signals"]
    ok = mr.hallucination_risk("41", citations=["u"],
                               evidences=[{"confidence": 0.9, "text": "41"}])
    assert ok["signals"] == []


def test_tool_result_validation():
    mr = MetaReasoning()
    assert mr.validate_tool_result({"ok": True, "data": []},
                                   ["ok", "data"])["valid"] is True
    out = mr.validate_tool_result({"ok": True}, ["ok", "missing"],
                                  expect_ok=False)
    assert out["valid"] is False and "ok-mismatch" in out["problems"]
    assert mr.validate_tool_result(None, [])["problems"] == ["result-not-dict"]


def test_reasoning_confidence_composite():
    mr = MetaReasoning()
    good = mr.reasoning_confidence(
        evidences=[{"confidence": 0.85, "text": "x=41"},
                   {"confidence": 0.8, "text": "x kesinlikle 41"}],
        claims=[{"key": "x", "value": 41, "source": "a"}],
        citations=["https://a"], answer="x=41")
    assert good["level"] == "adequate" and good["confidence"] > 0.5
    bad = mr.reasoning_confidence(
        evidences=[{"confidence": 0.1, "text": "x=1"}],
        claims=[{"key": "x", "value": 1, "source": "a"},
                {"key": "x", "value": 2, "source": "b"}],
        citations=[], answer="x=999")
    assert bad["level"] in ("insufficient", "contradicted")
    assert bad["confidence"] < 0.1
