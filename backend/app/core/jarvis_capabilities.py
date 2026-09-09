"""Unified JARVIS capability contract for ULTRON.

This module is intentionally declarative: it describes the complete experience
we are building while distinguishing capabilities that already have a real
local implementation from adapters that still need a provider. It never
claims a capability is available merely because a UI control exists.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable


@dataclass(frozen=True)
class Capability:
    id: str
    name: str
    layer: str
    status: str
    tools: tuple[str, ...] = ()
    safety: str = "standard"
    description: str = ""


CAPABILITIES: tuple[Capability, ...] = (
    Capability("brain", "Brain / Planner / Supervisor", "core", "implemented", ("planner", "self_awareness"), description="Reasoning, planning, delegation, verification and recovery."),
    Capability("memory", "Persistent Memory", "core", "implemented", ("memory",), description="Long-term and semantic memory with auditability."),
    Capability("autonomous_tasks", "Long Tasks / Autonomous Coding", "core", "implemented", ("autonomous_task", "self_repair", "apply_code_patch"), safety="approval", description="Bounded task loops with checkpoints, tests, rollback and approval."),
    Capability("voice", "Voice / Wake Word / TTS", "input-output", "implemented", ("live_voice", "wake_word", "tts"), description="Continuous voice pipeline with local TTS options."),
    Capability("computer_control", "Computer Control", "automation", "implemented", ("gui_click", "gui_type", "gui_key", "open_application"), safety="approval", description="Mouse, keyboard and application control behind risk gates."),
    Capability("web", "Web Search / Browser Agent", "tools", "implemented", ("search_web", "browser_navigate", "browser_read", "browser_click"), safety="approval", description="Search, navigate, inspect and interact with web pages."),
    Capability("screen_vision", "Screen Vision / OCR", "vision", "implemented", ("screen", "screen_ocr"), description="Screen capture and OCR/vision analysis where local dependencies exist."),
    Capability("camera", "Camera Vision", "vision", "adapter", ("camera",), safety="permission", description="Real camera capture requires an explicit device permission and runtime backend."),
    Capability("image_generation", "Image Generation", "multimodal", "adapter", ("image_generate",), safety="provider", description="Provider-backed image generation adapter; no fake local implementation."),
    Capability("pdf", "PDF Workspace", "multimodal", "adapter", ("pdf_open", "pdf_extract"), description="PDF ingestion/viewer adapter to be wired into the multimodal workspace."),
    Capability("shell", "Shell / PowerShell", "automation", "adapter", ("shell_exec",), safety="approval", description="Command execution must remain approval-gated and sandbox-aware."),
    Capability("widgets", "HUD Widgets", "ui", "implemented", ("hud_overview", "hud_trends"), description="Dockable status, memory, automation and monitoring panels."),
    Capability("hud", "JARVIS HUD", "ui", "implemented", ("hud_overview",), description="Cockpit HUD with telemetry, status, activity and operator controls."),
    Capability("three_d", "3D Interface", "ui", "implemented", ("three", "particle_sphere"), description="Three.js cockpit/particle interface and extensible 3D scene layer."),
    Capability("image_viewer", "Image Viewer / Drop Zone", "ui", "adapter", ("image_view",), description="Visual workspace target for generated and uploaded images."),
    Capability("notifications", "Proactive Notifications", "core", "implemented", ("proactive", "notifications"), description="Proactive monitoring and operator notifications."),
    Capability("security", "Approval / Vault / Audit / Sandbox", "security", "implemented", ("approval_gate", "vault", "audit", "sandbox"), safety="mandatory", description="Dangerous actions require trusted server-side approval; audit and rollback remain enabled."),
    Capability("mesh", "PC / Mobile Mesh", "connectivity", "implemented", ("mesh",), description="Authenticated device capability exchange and sync."),
)


def capability_inventory() -> list[dict]:
    """Return a stable, JSON-friendly inventory for UI/diagnostics."""
    return [asdict(item) for item in CAPABILITIES]


def capability_ids() -> tuple[str, ...]:
    return tuple(item.id for item in CAPABILITIES)


def capabilities_for_layer(layer: str) -> list[dict]:
    return [asdict(item) for item in CAPABILITIES if item.layer == layer]


def validate_inventory(items: Iterable[Capability] = CAPABILITIES) -> None:
    """Fail fast on duplicate ids or invalid lifecycle states."""
    allowed = {"implemented", "adapter", "planned"}
    seen: set[str] = set()
    for item in items:
        if item.id in seen:
            raise ValueError(f"duplicate capability id: {item.id}")
        seen.add(item.id)
        if item.status not in allowed:
            raise ValueError(f"invalid capability status: {item.status}")
        if item.safety not in {"standard", "approval", "permission", "provider", "mandatory"}:
            raise ValueError(f"invalid safety class: {item.safety}")


validate_inventory()
