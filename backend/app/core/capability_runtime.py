"""Live capability readiness, separate from the declarative inventory."""
from __future__ import annotations

from typing import Any

from app.core.jarvis_capabilities import capability_inventory
from app.multimodal.camera_adapter import CameraAdapter
from app.multimodal.image_generation import ImageGenerationAdapter
from app.multimodal.pdf_workspace import PDFWorkspace


def _probe(name: str) -> dict[str, Any] | None:
    """Probe only capabilities whose runtime depends on optional providers/devices."""
    probes = {
        "camera": CameraAdapter,
        "image_generation": ImageGenerationAdapter,
        "pdf": PDFWorkspace,
    }
    factory = probes.get(name)
    if factory is None:
        return None
    try:
        result = factory().status()
    except Exception as exc:  # pragma: no cover - defensive boundary
        return {"available": False, "reason": f"probe failed: {exc}"}
    return dict(result) if isinstance(result, dict) else {"available": False, "reason": "invalid probe result"}


def live_capability_inventory() -> list[dict[str, Any]]:
    """Return declarative capabilities enriched with live provider/device state."""
    rows = capability_inventory()
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        probe = _probe(item["id"])
        if probe is not None:
            item["runtime"] = probe
            # A failed optional provider/device probe can only downgrade a
            # capability; it must never manufacture a live implementation.
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
    # "Production ready" means every declared capability is live now, not
    # merely that there are no TODO/planned rows.
    production_ready = total > 0 and implemented == total and adapters == 0 and planned == 0
    return {
        "total": total,
        "implemented": implemented,
        "adapter": adapters,
        "planned": planned,
        "percent": round((implemented / total) * 100, 1) if total else 100.0,
        "production_ready": production_ready,
        "capabilities": rows,
    }
