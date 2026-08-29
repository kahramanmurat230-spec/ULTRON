"""WAVE 4 — Vision Runtime: adaptif ekran algısı + frame sözleşmesi (§7-8).

İlkeler:
- Sürekli screenshot ile CPU boğulmaz: adaptif hız (IDLE yavaş, aktif
  görev hızlı), significant-change eşiği altında yeni analiz YAPILMAZ
  (mevcut SmartScreenWatcher'ın zero-cost diff yaklaşımıyla uyumlu).
- Frame şeması (§8): frame_id, timestamp, source, resolution, hash,
  capture_latency, stale, confidence.
- STALE frame ile action YASAK: FrameStore.action_frame() max_age aşımında
  StaleFrameError atar — grounding/computer katmanı bunu yakalar.
- Gerçek capture yoksa (headless) dürüst unavailable; sahte ekran YOK.
"""
from __future__ import annotations

import hashlib
import io
import time
import uuid
from dataclasses import dataclass, field

# adaptif modlar (§30 kaynak seçimi)
MODE_IDLE = "IDLE"                    # minimum algı
MODE_ACTIVE_TASK = "ACTIVE_COMPUTER"  # yüksek algı
MODE_VOICE = "VOICE"                  # ses hattı öncelikli

CAPTURE_INTERVALS = {
    MODE_IDLE: 2.0,
    MODE_ACTIVE_TASK: 0.25,
    MODE_VOICE: 0.75,
}


class StaleFrameError(RuntimeError):
    """Frame çok eski — action için KULLANILAMAZ."""


class CaptureUnavailable(RuntimeError):
    """Gerçek ekran yakalama bu ortamda yok (headless) — sahte frame YOK."""


@dataclass
class VisionFrame:
    frame_id: str
    timestamp: float
    source: str                        # "screen" | "window:<title>" | ...
    resolution: tuple[int, int]
    hash: str
    capture_latency_ms: float
    stale: bool
    confidence: float                  # capture güveni (başarısız düşük)
    image_bytes: bytes = b""           # PNG/JPEG (yalnız bellekte)
    ocr: list = field(default_factory=list)

    def age(self, now: float | None = None) -> float:
        return (now if now is not None else time.time()) - self.timestamp

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in (
            "frame_id", "timestamp", "source", "resolution", "hash",
            "capture_latency", "capture_latency_ms", "stale", "confidence")
            if hasattr(self, k)}


# ---------------------------------------------------------------- capture
class PillowScreenCapture:
    """Gerçek yakalayıcı: PIL ImageGrab (headless'ta dürüst hata)."""

    NAME = "pillow-imagegrab"

    def grab_bytes(self) -> tuple[bytes, tuple[int, int], float]:
        t0 = time.monotonic()
        try:
            from PIL import ImageGrab
            img = ImageGrab.grab(all_screens=True).convert("RGB")
        except Exception as exc:  # noqa: BLE001
            raise CaptureUnavailable(
                f"ekran yakalama yok (headless?): {exc}") from exc
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=60)
        latency = (time.monotonic() - t0) * 1000
        return buf.getvalue(), img.size, latency

    def status(self) -> dict:
        try:
            from PIL import ImageGrab  # noqa: F401
            return {"capture": self.NAME, "available": True}
        except Exception:  # noqa: BLE001
            return {"capture": self.NAME, "available": False}


class FrameStore:
    """Son frame'ler + stale koruması."""

    def __init__(self, max_frames: int = 16, max_age_s: float = 3.0,
                 now=None):
        self.max_frames = max_frames
        self.max_age_s = max_age_s
        self.frames: list[VisionFrame] = []
        self._now = now or time.time

    def add(self, frame: VisionFrame) -> VisionFrame:
        self.frames.append(frame)
        if len(self.frames) > self.max_frames:
            self.frames.pop(0)
        return frame

    def latest(self) -> VisionFrame | None:
        return self.frames[-1] if self.frames else None

    def action_frame(self, max_age: float | None = None) -> VisionFrame:
        """Action için taze frame ZORUNLU — stale RED."""
        f = self.latest()
        age_limit = self.max_age_s if max_age is None else max_age
        if f is None:
            raise StaleFrameError("frame yok — önce capture gerekli")
        age = f.age(self._now())
        if age > age_limit or f.stale:
            raise StaleFrameError(
                f"stale frame ({age:.2f}s > {age_limit}s) — action reddi")
        return f

    def mark_stale_all(self) -> int:
        n = 0
        for f in self.frames:
            if not f.stale:
                f.stale = True
                n += 1
        return n


def _frame_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def _downsample_hash(data: bytes, size=(32, 18)) -> str:
    """Anlamlı değişim hash'i: tam-hash piksel farklı ama İÇERİK aynı
    (imleç yanıp sönmeleri) durumunda aynı kalsın diye küçük ölçek."""
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(data)).convert("L").resize(size)
        return hashlib.sha256(img.tobytes()).hexdigest()[:16]
    except Exception:  # noqa: BLE001
        return _frame_hash(data)


class AdaptiveVisionLoop:
    """Adaptif yakalama döngüsü (kaynak dostu, gereksiz algı YOK).

    step(now): capture zamanı geldiyse yeni frame alır; significant
    change yoksa (downsample hash aynı) OCR/analiz tetiklenMEZ ve
    önceki görsel durum geçerli sayılır.
    """

    def __init__(self, capture, store: FrameStore, *,
                 on_significant_change=None, mode: str = MODE_IDLE,
                 change_threshold: float = 0.02, now=None):
        self.capture = capture
        self.store = store
        self.on_change = on_significant_change
        self.mode = mode
        self.change_threshold = change_threshold
        self._last_content_hash: str | None = None
        self._last_capture_t = -1e9
        self.stats = {"captures": 0, "significant_changes": 0,
                      "skipped_no_change": 0, "errors": 0}
        self._now = now or time.monotonic

    def set_mode(self, mode: str) -> float:
        """Kaynak modu değiştir (§30) → yeni aralık döner."""
        if mode not in CAPTURE_INTERVALS:
            raise ValueError(f"unknown vision mode {mode!r}")
        self.mode = mode
        return CAPTURE_INTERVALS[mode]

    def interval(self) -> float:
        return CAPTURE_INTERVALS[self.mode]

    def step(self) -> VisionFrame | None:
        """Bir adım: süre geldi mi capture et, değişimi değerlendir."""
        t = self._now()
        if t - self._last_capture_t < self.interval():
            return None                      # henüz zamanı değil (CPU dostu)
        self._last_capture_t = t
        try:
            data, size, latency = self.capture.grab_bytes()
        except CaptureUnavailable:
            self.stats["errors"] += 1
            raise
        self.stats["captures"] += 1
        content_hash = _downsample_hash(data)
        changed = content_hash != self._last_content_hash
        frame = VisionFrame(
            frame_id=f"vf-{uuid.uuid4().hex[:10]}",
            timestamp=time.time(), source="screen", resolution=tuple(size),
            hash=_frame_hash(data), capture_latency_ms=round(latency, 2),
            stale=False, confidence=1.0 if changed else 0.98,
            image_bytes=data if changed else b"")   # değişmediyse taşınMAZ
        if not changed:
            self.stats["skipped_no_change"] += 1
            # içerik aynı: eski frame'in zamanı güncellenmez (dürüst),
            # ancak görsel durum geçerli — event yok
            self.store.add(frame)
            return frame
        self._last_content_hash = content_hash
        self.stats["significant_changes"] += 1
        self.store.add(frame)
        if self.on_change:
            self.on_change(frame)
        return frame

    def status(self) -> dict:
        return {"mode": self.mode, "interval_s": self.interval(),
                "capture": getattr(self.capture, "status",
                                   lambda: {"capture": "custom"})(),
                "stats": dict(self.stats)}
