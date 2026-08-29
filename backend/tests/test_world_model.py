"""PHASE 4: world model aggregation + LLM context injection."""
from app.world.model import WorldModel


def make_world(now=None):
    return WorldModel(sources={
        "presence": lambda: {"available": True, "boss_in_room": True, "confidence": 0.9},
        "workspace": lambda: {"available": True, "window": "VS Code", "mode": "CODING_MODE"},
        "screen": lambda: {"available": True, "status": "active", "diff": 0.12, "age_s": 3.0},
        "task": lambda: {"available": True, "goal": "backend analizi", "status": "RUNNING",
                         "current_step": 1, "steps_total": 3},
        "system": lambda: {"available": True, "cpu_percent": 18, "ram_percent": 62, "disk_percent": 48},
        "apps": lambda: {"available": True, "titles": ["VS Code", "Chrome", "Terminal"]},
        "files": lambda: {"available": True, "files": ["backend/server.py", "README.md"]},
        "iot": lambda: {"available": True, "devices": 5, "active": 2, "last_scene": "CODING_FOCUS"},
        "events": lambda: {"available": True, "latest": "Ollama connected"},
    }, now=now or (lambda: 1000.0))


def test_snapshot_merges_all_sources():
    w = make_world()
    s = w.snapshot()
    assert s["presence"]["boss_in_room"] is True
    assert s["workspace"]["window"] == "VS Code"
    assert s["system"]["cpu_percent"] == 18
    assert s["ts"] == 1000.0


def test_missing_source_degrades_gracefully():
    w = WorldModel(sources={
        "presence": lambda: {"boss_in_room": False},
        "broken": lambda: (_ for _ in ()).throw(RuntimeError("boom")),
    })
    s = w.snapshot()
    assert s["broken"] == {"available": False}
    assert s["presence"]["boss_in_room"] is False


def test_llm_context_compact_and_capped():
    w = make_world()
    ctx = w.context_for_llm(max_chars=200)
    assert len(ctx) <= 200
    assert "user=present" in ctx and "cpu=18" in ctx


def test_llm_context_empty_when_nothing_available():
    w = WorldModel()
    assert w.context_for_llm() == ""


def test_staleness_tracking():
    t = [1000.0]
    w = make_world(now=lambda: t[0])
    w.snapshot()
    t[0] = 1030.0
    assert abs(w.staleness_s() - 30.0) < 0.001
