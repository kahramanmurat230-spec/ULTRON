"""Wave 5 §6+§8 — Knowledge Engine + Knowledge Graph testleri."""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.cognitive.knowledge_engine import KnowledgeEngine, chunk_text  # noqa: E402
from app.cognitive.knowledge_graph import KnowledgeGraph  # noqa: E402


# ---------------------------------------------------------------- chunks
def test_chunking_respects_sentence_and_size():
    text = ("Bu ilk cümle. " * 20) + "Son cümle."
    chunks = chunk_text(text, max_chars=200, overlap=40)
    assert len(chunks) > 1
    assert all(len(c) <= 210 for c in chunks)      # sert sınır (+örtüşme payı)
    assert "Son cümle." in chunks[-1]


def test_chunking_empty_text_honest():
    assert chunk_text("") == []


# ------------------------------------------------------------ retrieval
DOC1 = ("ULTRON yerel öncelikli bir kişisel asistandır. Tüm veriler cihazda "
        "kalır. Bulut yalnızca kullanıcı izniyle kullanılır. ")
DOC2 = ("Tarif: menemen için domates, biber ve yumurta gerekir. Kahvaltıda "
        "yenir. ")


def make_ke(d):
    return KnowledgeEngine(db_path=os.path.join(d, "k.db"))


def test_ingest_and_bm25_retrieval_with_citations():
    with tempfile.TemporaryDirectory() as d:
        ke = make_ke(d)
        r1 = ke.ingest("ultron-overview", DOC1 * 3, source="docs/ultron.md",
                       source_confidence=0.9)
        r2 = ke.ingest("menemen", DOC2 * 3, source="docs/mutfak.md",
                       source_confidence=0.6)
        res = ke.search("yerel veriler cihazda kalır")
        assert res["engine"] == "lexical-bm25"      # dürüst etiket: vector değil
        assert res["results"] and res["results"][0]["doc_id"] == r1["doc_id"]
        hit = res["results"][0]
        assert hit["citation"] > 0                  # köken chunk id
        assert "cihazda" in hit["snippet"]
        res2 = ke.search("menemen malzemeleri")
        assert res2["results"][0]["doc_id"] == r2["doc_id"]


def test_reranking_prefers_trusted_source():
    with tempfile.TemporaryDirectory() as d:
        ke = make_ke(d)
        ke.ingest("a", "quantum computing nedir explained " * 10,
                  source="wiki", source_confidence=0.9)
        ke.ingest("b", "quantum computing nedir explained " * 10,
                  source="forum", source_confidence=0.2)
        res = ke.search("quantum computing")
        assert res["results"][0]["source"] == "wiki"


def test_scope_isolation_in_search():
    with tempfile.TemporaryDirectory() as d:
        ke = make_ke(d)
        ke.ingest("pub", "ortak konu: deploy süreci " * 5, scope="global")
        ke.ingest("priv", "gizli: projenin deploy anahtarı " * 5,
                  scope="project:ultron")
        r_scope = ke.search("deploy", scope="project:ultron")
        titles = [x["title"] for x in r_scope["results"]]
        assert "priv" in titles and "pub" in titles  # global+scope görünür
        r_other = ke.search("deploy", scope="project:diger")
        titles2 = [x["title"] for x in r_other["results"]]
        assert "priv" not in titles2                 # izolasyon


def test_supersede_on_update_and_tombstone_delete():
    with tempfile.TemporaryDirectory() as d:
        ke = make_ke(d)
        v1 = ke.ingest("sürüm belgesi", "eskisürüm içeriği", source="a")
        v2 = ke.ingest("sürüm belgesi", "yenisürüm içeriği", source="a")
        assert v2["supersedes"] == v1["doc_id"]
        res = ke.search("içeriği")
        assert all(x["doc_id"] == v2["doc_id"] for x in res["results"])
        st = ke.stats()
        assert st["superseded"] == 1
        assert ke.delete(v2["doc_id"])["tombstone"] is True
        assert ke.search("içeriği")["results"] == []   # silinen geri gelmez
        assert ke.stats()["tombstones"] == 1


def test_stale_documents_detection():
    with tempfile.TemporaryDirectory() as d:
        ke = make_ke(d)
        ke.ingest("eski", "içerik", source="x")
        with ke.lock:
            ke.db.execute("UPDATE documents SET ingested=? WHERE title='eski'",
                          (time.time() - 200 * 86400,))
            ke.db.commit()
        st = ke.stale_documents()
        assert any(x["title"] == "eski" for x in st)
        res = ke.search("içerik")
        assert res["results"][0]["stale"] is True      # sonuç işaretli


def test_contradiction_detection_and_claim():
    with tempfile.TemporaryDirectory() as d:
        ke = make_ke(d)
        ke.ingest("d1", "metin", claims={"deploy_aracı": "jenkins"},
                  source_confidence=0.9)
        ke.ingest("d2", "metin2", claims={"deploy_aracı": "make"},
                  source_confidence=0.5)
        cons = ke.contradictions()
        assert any(c["key"] == "deploy_aracı" for c in cons)
        cl = ke.claim("deploy_aracı")
        assert cl["value"] == "jenkins" and cl["conflict"] is True


def test_search_empty_corpus_honest():
    with tempfile.TemporaryDirectory() as d:
        ke = make_ke(d)
        r = ke.search("herhangi")
        assert r["results"] == [] and "canlı belge yok" in r.get("note", "")


# ---------------------------------------------------------------- graph
def make_kg(d):
    return KnowledgeGraph(db_path=os.path.join(d, "kg.db"))


def test_entities_scoped_isolation():
    with tempfile.TemporaryDirectory() as d:
        kg = make_kg(d)
        kg.add_entity("ultron", "ULTRON", "project", scope="project:a")
        assert kg.entity("ultron", scope="project:b") is None  # izole
        assert kg.entity("ultron", scope="project:a")["name"] == "ULTRON"


def test_relate_requires_known_entities():
    with tempfile.TemporaryDirectory() as d:
        kg = make_kg(d)
        kg.add_entity("a", "A")
        r = kg.relate("a", "ghost", "uses")
        assert r["ok"] is False and "unknown entity" in r["error"]


def test_supersede_and_live_edges():
    with tempfile.TemporaryDirectory() as d:
        kg = make_kg(d)
        for e in ("ultron", "ollama", "openai"):
            kg.add_entity(e, e.title())
        kg.relate("ultron", "ollama", "uses", confidence=0.9)
        kg.relate("ultron", "openai", "uses", confidence=0.5)  # sürüm
        live = kg.live_edges("ultron", "uses")
        dsts = [e["dst"] for e in live if e["live"]]
        assert dsts == ["openai"]              # eski kenar emekli
        assert kg.stats()["retired"] == 1


def test_temporal_relationship_expiry():
    with tempfile.TemporaryDirectory() as d:
        kg = make_kg(d)
        kg.add_entity("kullanici", "K")
        kg.add_entity("proje", "P")
        kg.relate("kullanici", "proje", "works_on",
                  from_ts=100.0, to_ts=200.0, supersede=False)
        in_range = kg.live_edges("kullanici", "works_on", now=150.0)
        after = kg.live_edges("kullanici", "works_on", now=300.0)
        assert [e["dst"] for e in in_range if e["live"]] == ["proje"]
        assert all(not e["live"] for e in after)       # süre bitti


def test_conflict_detection():
    with tempfile.TemporaryDirectory() as d:
        kg = make_kg(d)
        for e in ("a", "b", "c"):
            kg.add_entity(e, e)
        kg.relate("a", "b", "owns", supersede=False)
        kg.relate("a", "c", "owns", supersede=False)   # bilinçli çift
        cons = kg.conflicts()
        assert any(x["src"] == "a" and x["relation"] == "owns"
                   and len(x["candidates"]) == 2 for x in cons)


def test_traversal_bfs_depth_and_cycle_safety():
    with tempfile.TemporaryDirectory() as d:
        kg = make_kg(d)
        for e in ("a", "b", "c", "a2"):              # döngü: c→a
            kg.add_entity(e, e)
        kg.relate("a", "b", "knows", supersede=False)
        kg.relate("b", "c", "knows", supersede=False)
        kg.relate("c", "a", "knows", supersede=False)  # çevrim
        t = kg.traverse("a", max_depth=5)
        assert t["reached_depth"] == 2
        assert set(t["nodes"]) == {"b", "c"}           # sonsuz döngü yok


def test_traversal_min_confidence_filter():
    with tempfile.TemporaryDirectory() as d:
        kg = make_kg(d)
        for e in ("a", "b", "c"):
            kg.add_entity(e, e)
        kg.relate("a", "b", "knows", confidence=0.9, supersede=False)
        kg.relate("a", "c", "knows", confidence=0.2, supersede=False)
        t = kg.traverse("a", min_confidence=0.5)
        assert set(t["nodes"]) == {"b"}


def test_stale_edge_sweep():
    with tempfile.TemporaryDirectory() as d:
        kg = make_kg(d)
        kg.add_entity("x", "X")
        kg.add_entity("y", "Y")
        kg.relate("x", "y", "temp", from_ts=10.0, to_ts=20.0,
                  supersede=False)
        st = kg.stale_edges(now=100.0)
        assert st and st[0]["edge_id"] > 0
        assert kg.retire(st[0]["edge_id"])["ok"] is True
        assert kg.stale_edges(now=100.0) == []
