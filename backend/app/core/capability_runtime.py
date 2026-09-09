"""Live capability readiness, separate from the declarative inventory."""
from __future__ import annotations

from typing import Any

from app.core.jarvis_capabilities import capability_inventory
from app.multimodal.camera_adapter import CameraAdapter
from app.multimodal.image_generation import ImageGenerationAdapter
from app.multimodal.pdf_workspace import PDFWorkspace


def live_capability_inventory() -> list[dict[str, Any]]:
    rows = capability_inventory()
    probes = {
        "camera": CameraAdapter().status(),
        "image_generation": ImageGenerationAdapter().status(),
        "pdf": PDFWorkspace().status(),
    }
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        probe = probes.get(item["id"])
        if probe is not None:
            item["runtime"] = probe
            if not probe.get("available", False):
                item["status"] = "adapter"
        else:
            item["runtime"] = {"available": item["status"] == "implemented"}
        out.append(item)
    return out


def readiness_report() -> dict[str, Any]:
    rows = live_capability_inventory()
    total = len(rows)
    implemented = sum(1 for row in rows if row["status"] == "implemented")
    adapters = sum(1 for row in rows if row["status"] == "adapter")
    planned = sum(1 for row in rows if row["status"] == "planned")
    return {
        "total": total,
        "implemented": implemented,
        "adapter": adapters,
        "planned": planned,
        "percent": round((implemented / total) * 100, 1) if total else 100.0,
        "production_ready": planned == 0,
        "capabilities": rows,
    }
