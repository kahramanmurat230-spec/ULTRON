"""Contract tests for live capability/readiness reporting."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.capability_runtime import live_capability_inventory, readiness_report


def test_readiness_report_is_consistent():
    report = readiness_report()
    rows = report["capabilities"]
    assert report["total"] == len(rows)
    assert report["implemented"] + report["adapter"] + report["planned"] == report["total"]
    assert 0 <= report["percent"] <= 100
    assert report["planned"] == 0
    assert report["production_ready"] is True


def test_capability_ids_are_unique_and_runtime_is_explicit():
    rows = live_capability_inventory()
    ids = [row["id"] for row in rows]
    assert len(ids) == len(set(ids))
    for row in rows:
        assert row["status"] in {"implemented", "adapter", "planned"}
        assert "runtime" in row
        assert isinstance(row["runtime"], dict)


def test_unavailable_provider_or_device_never_looks_implemented(monkeypatch):
    from app.core import capability_runtime as cr

    class Unavailable:
        def status(self):
            return {"available": False, "reason": "test-disabled"}

    monkeypatch.setattr(cr, "CameraAdapter", Unavailable)
    monkeypatch.setattr(cr, "ImageGenerationAdapter", Unavailable)
    monkeypatch.setattr(cr, "PDFWorkspace", Unavailable)

    rows = {row["id"]: row for row in cr.live_capability_inventory()}
    for name in ("camera", "image_generation", "pdf"):
        assert rows[name]["runtime"]["available"] is False
        assert rows[name]["status"] == "adapter"


def test_runtime_probe_can_only_downgrade_status():
    rows = live_capability_inventory()
    for row in rows:
        if row["runtime"].get("available") is False:
            assert row["status"] != "implemented"
