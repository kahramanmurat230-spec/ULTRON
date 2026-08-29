"""WAVE 4 / Vision runtime: frame şeması, adaptif hız, change detection,
stale koruması, headless dürüst unavailable."""
import io
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.multimodal.vision_runtime import (  # noqa: E402
    MODE_ACTIVE_TASK, MODE_IDLE, MODE_VOICE, AdaptiveVisionLoop,
    CaptureUnavailable, FrameStore, PillowScreenCapture, StaleFrameError,
    VisionFrame, _downsample_hash, _frame_hash,
)


def jpeg(color, size=(320, 180)):
    from PIL import Image
    img = Image.new("RGB", size, color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=60)
    return buf.getvalue()


class ScriptCapture:
    """DI seam: gerçek PillowScreenCapture yerine önceden verilmiş
    görüntüleri verir (headless ortamda görüntü ÜRETİLMEZ, test verisi
    olarak verilir — sahte ekran iddiası YOK, capture.status dürüst)."""

    def __init__(self, images):
        self.images = list(images)
        self.calls = 0

    def grab_bytes(self):
        self.calls += 1
        if not self.images:
            raise CaptureUnavailable("test kaynağı tükendi")
        return self.images.pop(0), (320, 180), 5.0


# ---------------------------------------------------------------- frame
def test_frame_schema_complete():
    f = VisionFrame(frame_id="vf-1", timestamp=time.time(), source="screen",
                    resolution=(320, 180), hash="ab" * 8,
                    capture_latency_ms=5.0, stale=False, confidence=1.0)
    d = f.to_dict()
    for key in ("frame_id", "timestamp", "source", "resolution", "hash",
                "capture_latency_ms", "stale", "confidence"):
        assert key in d, key


def test_hashes_separate_content_from_noise():
    # içerik aynı, byte farklı (re-encode) → downsample hash benzer davranır;
    # içerik farklı → hash farklı
    a, b, c = jpeg((255, 0, 0)), jpeg((255, 0, 0)), jpeg((0, 0, 255))
    assert _downsample_hash(a) == _downsample_hash(b)
    assert _downsample_hash(a) != _downsample_hash(c)
    assert _frame_hash(a) != _frame_hash(c)  # farklı içerik farklı hash


# ---------------------------------------------------------------- stale
def test_stale_frame_rejected_for_action():
    store = FrameStore(max_age_s=0.05)
    store.add(VisionFrame("vf-1", time.time() - 1.0, "screen", (1, 1),
                          "h", 1.0, False, 1.0))
    with pytest.raises(StaleFrameError, match="stale"):
        store.action_frame()                    # eski → RED
    store.add(VisionFrame("vf-2", time.time(), "screen", (1, 1), "h", 1.0,
                          False, 1.0))
    got = store.action_frame()
    assert got.frame_id == "vf-2"               # taze → OK


def test_empty_store_and_flagged_stale_rejected():
    store = FrameStore()
    with pytest.raises(StaleFrameError):
        store.action_frame()
    store.add(VisionFrame("vf-1", time.time(), "screen", (1, 1), "h", 1.0,
                          True, 1.0))           # stale etiketli
    with pytest.raises(StaleFrameError):
        store.action_frame()


def test_mark_stale_all_invalidate():
    store = FrameStore()
    store.add(VisionFrame("vf-1", time.time(), "screen", (1, 1), "h", 1.0,
                          False, 1.0))
    assert store.mark_stale_all() == 1
    with pytest.raises(StaleFrameError):
        store.action_frame()


# ---------------------------------------------------------------- adaptive
def test_adaptive_interval_by_mode():
    loop = AdaptiveVisionLoop(ScriptCapture([]), FrameStore())
    assert loop.interval() == 2.0               # IDLE yavaş
    assert loop.set_mode(MODE_ACTIVE_TASK) == 0.25
    assert loop.interval() == 0.25
    loop.set_mode(MODE_VOICE)
    assert loop.interval() == 0.75
    with pytest.raises(ValueError):
        loop.set_mode("TURBO")                  # bilinmeyen mod RED


def test_adaptive_loop_skips_until_interval():
    cap = ScriptCapture([jpeg((255, 0, 0))])
    loop = AdaptiveVisionLoop(cap, FrameStore(), mode=MODE_IDLE)
    assert loop.step() is not None             # ilk adım capture eder
    cap.images.append(jpeg((0, 255, 0)))
    assert loop.step() is None                 # aralık dolmadı → capture YOK
    assert cap.calls == 1                      # CPU korunuyor


def test_significant_change_detection_events():
    changes = []
    cap = ScriptCapture([jpeg((255, 0, 0)), jpeg((255, 0, 0)),
                         jpeg((0, 255, 0))])
    loop = AdaptiveVisionLoop(cap, FrameStore(), mode=MODE_ACTIVE_TASK,
                              on_significant_change=changes.append)
    loop.step()                                 # ilk → değişim (taban)
    assert len(changes) == 1
    while loop.step() is None:                  # aralığı bekle
        time.sleep(0.03)
    loop.step()                                 # aynı içerik → değişim YOK
    assert loop.stats["skipped_no_change"] == 1
    assert len(changes) == 1                    # event tetiklenmedi
    while loop.step() is None:
        time.sleep(0.03)
    assert loop.stats["significant_changes"] == 2
    assert len(changes) == 2


def test_capture_failure_honest_no_frame():
    cap = ScriptCapture([])                     # kaynak tükendi
    loop = AdaptiveVisionLoop(cap, FrameStore())
    with pytest.raises(CaptureUnavailable):
        loop.step()                             # sahte frame YOK
    assert loop.stats["errors"] == 1


def test_pillow_capture_headless_honest():
    pc = PillowScreenCapture()
    try:
        data, size, lat = pc.grab_bytes()
        assert data and size[0] > 0             # gerçek ekranda çalışır
    except CaptureUnavailable as exc:
        assert "headless" in str(exc) or "yakalama yok" in str(exc)
    st = pc.status()
    assert isinstance(st["available"], bool)


def test_status_reports_honest_picture():
    loop = AdaptiveVisionLoop(ScriptCapture([jpeg((1, 2, 3))]),
                              FrameStore(), mode=MODE_VOICE)
    st = loop.status()
    assert st["mode"] == MODE_VOICE and "stats" in st
