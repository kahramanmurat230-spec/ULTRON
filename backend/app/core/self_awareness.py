"""Stage 7 self-awareness: bounded, read-only capability introspection."""
from __future__ import annotations


class SelfAwareness:
    """Expose what ULTRON can actually use right now.

    This layer is informational only: it never grants permissions, changes risk
    levels, or executes tools. Availability is derived from real runtime objects.
    """

    def __init__(self, registry=None, doctor=None, tts=None, brain=None):
        self.registry = registry
        self.doctor = doctor
        self.tts = tts
        self.brain = brain

    def report(self) -> dict:
        tools = []
        if self.registry is not None:
            for name in self.registry.names():
                item = self.registry.get(name) or {}
                tools.append({
                    "name": name,
                    "dangerous": bool(item.get("dangerous")),
                    "available": callable(item.get("fn")),
                })

        voice_backend = None
        if self.tts is not None:
            try:
                voice_backend = self.tts.backend()
            except Exception:
                voice_backend = None

        return {
            "read_only": True,
            "tools": tools,
            "tool_count": len(tools),
            "local_tts": {
                "available": voice_backend in ("piper-local", "espeak-ng-local"),
                "backend": voice_backend,
            },
            "brain": {
                "available": self.brain is not None,
                "model": getattr(self.brain, "model", None),
            },
        }
