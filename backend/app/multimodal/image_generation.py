"""Provider-neutral image generation adapter.

The adapter supports an OpenAI-compatible image endpoint when explicitly
configured. It never fabricates an image or silently falls back to a fake
provider.
"""
from __future__ import annotations

import base64
import json
import os
import urllib.request
from typing import Any


class ImageGenerationAdapter:
    def __init__(self, endpoint: str | None = None, api_key: str | None = None, model: str | None = None) -> None:
        self.endpoint = endpoint or os.environ.get("ULTRON_IMAGE_ENDPOINT", "").strip()
        self.api_key = api_key or os.environ.get("ULTRON_IMAGE_API_KEY", "").strip()
        self.model = model or os.environ.get("ULTRON_IMAGE_MODEL", "").strip()

    def status(self) -> dict[str, Any]:
        configured = bool(self.endpoint and self.api_key and self.model)
        return {
            "available": configured,
            "configured": configured,
            "backend": "openai-compatible" if configured else None,
            "reason": None if configured else "ULTRON_IMAGE_ENDPOINT/API_KEY/MODEL not configured",
        }

    def generate(self, prompt: str, size: str = "1024x1024") -> dict[str, Any]:
        prompt = str(prompt).strip()
        if not prompt:
            return {"ok": False, "error": "empty prompt"}
        if not self.endpoint or not self.api_key or not self.model:
            return {"ok": False, "error": "image provider is not configured"}
        payload = json.dumps({"model": self.model, "prompt": prompt, "size": size}).encode("utf-8")
        req = urllib.request.Request(
            self.endpoint,
            data=payload,
            method="POST",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as response:  # noqa: S310 - explicit operator-configured endpoint
                data = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            return {"ok": False, "error": f"image provider request failed: {exc}"}
        item = (data.get("data") or [{}])[0]
        if item.get("b64_json"):
            return {"ok": True, "mime": "image/png", "image_b64": item["b64_json"], "model": self.model}
        url = item.get("url")
        if url:
            return {"ok": True, "url": url, "model": self.model}
        return {"ok": False, "error": "provider returned no image payload"}
