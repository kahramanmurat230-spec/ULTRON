"""Real camera adapter with permission-aware probing and capture.

No simulated camera availability is ever returned. OpenCV is optional so the
rest of ULTRON remains usable on machines without camera dependencies.
"""
from __future__ import annotations

import base64
import time
from typing import Any


class CameraAdapter:
    def __init__(self, index: int = 0) -> None:
        self.index = int(index)

    def status(self) -> dict[str, Any]:
        try:
            import cv2  # type: ignore
        except Exception as exc:
            return {"available": False, "permission_required": True, "reason": f"opencv unavailable: {exc}"}
        cap = cv2.VideoCapture(self.index)
        try:
            opened = bool(cap.isOpened())
            return {
                "available": opened,
                "permission_required": True,
                "index": self.index,
                "backend": "opencv",
                "reason": None if opened else "camera could not be opened",
            }
        finally:
            cap.release()

    def capture_jpeg(self, quality: int = 85) -> dict[str, Any]:
        try:
            import cv2  # type: ignore
        except Exception as exc:
            return {"ok": False, "error": f"opencv unavailable: {exc}"}
        cap = cv2.VideoCapture(self.index)
        try:
            if not cap.isOpened():
                return {"ok": False, "error": "camera unavailable or permission denied"}
            ok, frame = cap.read()
            if not ok:
                return {"ok": False, "error": "camera frame capture failed"}
            ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), max(1, min(100, int(quality)))])
            if not ok:
                return {"ok": False, "error": "jpeg encoding failed"}
            return {
                "ok": True,
                "mime": "image/jpeg",
                "image_b64": base64.b64encode(encoded.tobytes()).decode("ascii"),
                "timestamp": time.time(),
                "index": self.index,
            }
        finally:
            cap.release()
