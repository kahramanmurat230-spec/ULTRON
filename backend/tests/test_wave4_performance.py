"""WAVE 4 / Performance (§29) — GERÇEK ölçüm, cömüş eşikler.

Ortam dürüstlüğü: gerçek mic/ekran/tarayıcı YOK (konteyner). Ölçülen
gecikmeler KATMAN maliyetleridir: EnergyVAD gerçek RMS, PIL gerçek JPEG
encode, grounding gerçek skorlama, DOM fingerprint gerçek hash. Cihaz
gerektiren (OCR binary, tarayıcı navigasyon, hoparlör) ölçümler SKIP —
sahte benchmark YASAK (§38). Rakamlar WAVE4_AUDIT.md'de etiketlenir.
"""
import io
import os
import statistics
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.multimodal.context_fusion import (  # noqa: E402
    ContextBudget, ContextEntry, MultimodalContext,
)
from app.multimodal.grounding import UIElement, ground  # noqa: E402
from app.multimodal.verify import (  # noqa: E402
    ExpectedState, Observation, verify,
)
from app.multimodal.vision_runtime import (  # noqa: E402
    AdaptiveVisionLoop, FrameStore,
)
from app.voice.voice_stack_v2 import EnergyVAD  # noqa: E402


def p50(vals):
    return statistics.median(vals)


def jpeg(color, size=(320, 180)):
    from PIL import Image
    img = Image.new("RGB", size, color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=60)
    return buf.getvalue()


# ---------------------------------------------------------------- VOICE
def test_vad_frame_latency_real_rms():
    """EnergyVAD gerçek RMS — 30ms frame işleme p50 < 1ms (gerçek sayı)."""
    import array
    vad = EnergyVAD()
    frame = (array.array("h", [9000 if i % 2 else -9000
                               for i in range(480)])).tobytes()
    lat = []
    for _ in range(1000):
        t0 = time.perf_counter()
        vad.process_frame(frame)
        lat.append((time.perf_counter() - t0) * 1000)
    assert p50(lat) < 1.0, f"VAD p50 {p50(lat):.3f} ms/frame"


def test_wake_gate_decision_latency():
    from app.multimodal.wake_vad import WakeGate

    class Hit:
        NAME = "bench"
        active = True
        def status(self):
            return {"available": True}
        def process_chunk(self, a):
            return ("jarvis", 0.9)

    gate = WakeGate(Hit(), cooldown_s=0.0)
    data = b"x" * 960
    lat = []
    for _ in range(2000):
        t0 = time.perf_counter()
        gate.process_chunk(data)
        lat.append((time.perf_counter() - t0) * 1000)
    assert p50(lat) < 0.5, f"wake gate p50 {p50(lat):.3f} ms"


def test_stt_noise_filter_overhead_per_chunk():
    from app.multimodal.stt_stream import STTManager

    class Null:
        NAME = "null"
        available = False
        def status(self):
            return {}
    m = STTManager(engines=[Null()])
    silent = b"\x00" * 960
    t0 = time.perf_counter()
    for _ in range(500):
        m._split_noise([silent] * 4)
    per = (time.perf_counter() - t0) / 500 * 1000
    assert per < 2.0, f"noise filter {per:.3f} ms/chunk-grubu"


def test_tts_first_audio_before_total_stream():
    """Streaming gerçek: ilk ses, tüm akışın bitmesinden ÖNCE (oran>1.5)."""
    import asyncio
    from app.multimodal.tts_stream import NeuralTTSEngine, StreamingTTS

    class _Slot:
        voice = "v"
        def backend(s):
            return "bench"
        async def synthesize(s, t):
            await asyncio.sleep(0.01)
            return (f"A::{t}".encode(), "mp3")

    tts = StreamingTTS(engine=NeuralTTSEngine(tts=_Slot()))
    firsts, total = [], None

    async def run():
        nonlocal total
        t0 = time.perf_counter()
        gen = tts.speak_stream(iter([f"Cümle {i}." for i in range(8)]))
        async for p in gen:
            if not firsts:
                firsts.append(time.perf_counter() - t0)
        total = time.perf_counter() - t0

    asyncio.run(run())
    assert firsts and total
    assert total / firsts[0] > 1.5, (f"streaming kazancı yok: ilk "
                                     f"{firsts[0]*1000:.1f}ms toplam "
                                     f"{total*1000:.1f}ms")


# ---------------------------------------------------------------- VISION
def test_capture_encode_latency_real_pil():
    """320x180 JPEG encode (gerçek PIL) p50 < 100ms; hash ek maliyeti."""
    from app.multimodal.vision_runtime import _downsample_hash, _frame_hash
    data = jpeg((120, 40, 200))
    lat = []
    for _ in range(50):
        t0 = time.perf_counter()
        jpeg((120, 40, 200))
        lat.append((time.perf_counter() - t0) * 1000)
    assert p50(lat) < 100, f"JPEG encode p50 {p50(lat):.1f} ms"
    t0 = time.perf_counter()
    for _ in range(20):
        _downsample_hash(data)
        _frame_hash(data)
    dh = (time.perf_counter() - t0) / 20 * 1000
    assert dh < 30, f"hash çifti {dh:.1f} ms/frame"


def test_visual_grounding_latency_500_elements():
    els = [UIElement(f"e{i}", f"Buton {i} hedef", "button",
                     (i % 100, i % 80, 60, 24), "uia", 0.95)
           for i in range(500)]
    t0 = time.perf_counter()
    for _ in range(20):
        ground("hedef butonuna tıkla", els)
    per = (time.perf_counter() - t0) / 20 * 1000
    assert per < 50, f"grounding 500 elementte {per:.1f} ms"


def test_adaptive_no_change_skip_is_cheap():
    """Aynı içerik: step() capture eder ama analiz/event YOK — maliyet
    yalnız hash; aralık dışı step ~0."""
    class Cap:
        def __init__(s):
            s.data = jpeg((1, 2, 3))
        def grab_bytes(s):
            return s.data, (320, 180), 1.0

    loop = AdaptiveVisionLoop(Cap(), FrameStore(), mode="IDLE")
    loop.step()
    t0 = time.perf_counter()
    for _ in range(100):
        loop.step()                      # aralık dolmadı → None, ucuz
    per = (time.perf_counter() - t0) / 100 * 1000
    assert per < 0.05, f"aralık-dışı step {per*1000:.1f} µs — CPU boşa"


# ---------------------------------------------------------------- BROWSER
def test_dom_fingerprint_latency_1000_elements():
    from app.multimodal.browser_runtime import DOMElement, PageState
    els = [DOMElement(f"#e{i}", f"El {i}", "button") for i in range(1000)]
    st = PageState("u", "t", els, 0.0)
    t0 = time.perf_counter()
    for _ in range(10):
        st.dom_fingerprint()
    per = (time.perf_counter() - t0) / 10 * 1000
    assert per < 40, f"DOM fingerprint 1000 el {per:.1f} ms"


def test_browser_grounding_ax_priority_latency():
    from app.multimodal.browser_runtime import DOMElement
    from tests.test_wave4_browser import ScriptEngine
    from app.multimodal.browser_runtime import BrowserRuntime
    els = [DOMElement(f"#e{i}", f"Link {i}", "link") for i in range(300)]
    ax = [DOMElement("[role=button]", "button", "Ana Buton", source="ax")]
    rt = BrowserRuntime(ScriptEngine(elements=els, ax=ax))
    rt.refresh()
    t0 = time.perf_counter()
    for _ in range(20):
        assert rt.ground_element("Ana Buton") is not None
    per = (time.perf_counter() - t0) / 20 * 1000
    assert per < 60, f"AX+DOM grounding {per:.1f} ms"


# ---------------------------------------------------------------- COMPUTER
def test_computer_action_lifecycle_overhead():
    from app.multimodal.computer import ComputerAction, ComputerUseRuntime
    from tests.test_wave4_computer import ScriptExecutor
    rt = ComputerUseRuntime(ScriptExecutor())
    t0 = time.perf_counter()
    for i in range(200):
        rt.run(ComputerAction("click", target={"x": i, "y": i}),
               approved=True)
    per = (time.perf_counter() - t0) / 200 * 1000
    assert per < 5, f"lifecycle overhead {per:.2f} ms/action"


def test_verification_latency_1000_checks():
    exp = ExpectedState(dom_changed=True, window_title_contains="X",
                        text_present="ok")
    obs = Observation("m", 0.0, {"dom_changed": True,
                                 "window_title": "X penceresi",
                                 "texts": ["ok"]})
    t0 = time.perf_counter()
    for _ in range(1000):
        verify(exp, obs)
    per = (time.perf_counter() - t0) / 1000 * 1000
    assert per < 0.05, f"verify {per*1000:.1f} µs/kontrol"


# ---------------------------------------------------------------- MULTIMODAL
def test_context_fusion_latency_1000_entries():
    ctx = MultimodalContext(ContextBudget(max_entries=200, max_tokens=10**9))
    now = time.time()
    t0 = time.perf_counter()
    for i in range(1000):
        ctx.add(ContextEntry("logs", f"kayıt {i} hata yok", source=f"l:{i}",
                             confidence=0.3, ts=now))
    add_per = (time.perf_counter() - t0) / 1000 * 1000
    t0 = time.perf_counter()
    for _ in range(5):
        ctx.ranked("hata düzelt")
    rank_per = (time.perf_counter() - t0) / 5 * 1000
    assert add_per < 1.0, f"add {add_per:.3f} ms"
    assert rank_per < 100, f"rank {rank_per:.1f} ms (200 giriş üstü)"


def test_resource_usage_within_bounds_during_fusion():
    """CPU/RAM etkisi: fusion turu sırasında RAM artışı makul (aşırı iddia
    yok; yalnız üst sınır)."""
    import psutil
    proc = psutil.Process(os.getpid())
    ram0 = proc.memory_info().rss
    ctx = MultimodalContext(ContextBudget(max_entries=64, max_tokens=4000))
    now = time.time()
    for i in range(5000):
        ctx.add(ContextEntry("memory", "m" * 200, source=f"m:{i}",
                             confidence=0.5, ts=now))
        ctx.ranked("sorgu")
    ram_growth_mb = (proc.memory_info().rss - ram0) / 1024 / 1024
    assert ram_growth_mb < 50, f"RAM artışı {ram_growth_mb:.1f} MB"


# ---------------------------------------------------------------- gerçek cihaz yok
def test_device_benchmarks_honestly_skipped():
    """Cihaz gerektiren benchmarklar SAHTE ölçülmez — dürüst skip."""
    import shutil
    have_tesseract = shutil.which("tesseract") is not None
    if not have_tesseract:
        pytest.skip("tesseract binary yok — OCR latency ölçümü dürüstçe atlandı")
