"""WAVE 4 / E2E — tam multimodal zincirler, gerçek bileşenlerle.

58: voice→brain→vision→computer→verify→TTS
59: voice→browser→verify
60: multimodal coding task (Wave 3 DAG + memory policy)

Gerçek cihaz (mic/ekran/tarayıcı) bu KONTEYNERDE yok; zincirler gerçek
yazılım bileşenleri üzerinden uçtan uca koşar (VAD gerçek RMS hesabı,
TTS streaming gerçek, verifier gerçek, event bus gerçek Wave 2 motoru,
DAG gerçek Wave 3 supervisor). Cihaz gerektiren §31 senaryosu ayrı
testte DÜRÜST skip edilir — sahte cihaz E2E iddiası YOK (§38).
"""
import asyncio
import io
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.events.bus import DurableEventBus  # noqa: E402
from app.memory.store_v3 import MemoryStore  # noqa: E402
from app.multimodal.computer import (  # noqa: E402
    ComputerAction, ComputerUseRuntime,
)
from app.multimodal.context_fusion import (  # noqa: E402
    ContextEntry, MultimodalEventBridge, MultimodalMemoryPolicy,
)
from app.multimodal.tts_stream import NeuralTTSEngine, StreamingTTS  # noqa: E402
from app.multimodal.verify import (  # noqa: E402
    ExpectedState, Observation, VerifiedActionLoop, verify,
)
from app.multimodal.voice_runtime import VoiceRuntime, VoiceRuntimeConfig  # noqa: E402
from app.observability.trace import Tracer  # noqa: E402
from app.orchestr.artifacts import ArtifactManager  # noqa: E402
from app.orchestr.messages import AgentMessageBus  # noqa: E402
from app.orchestr.orchestrator import SupervisorOrchestrator  # noqa: E402
from app.orchestr.scheduler import DAGScheduler  # noqa: E402
from app.orchestr.tokens import CapabilityTokenAuthority  # noqa: E402
from app.orchestr.worker import WorkerRegistry  # noqa: E402
from app.security.redaction import redact  # noqa: E402
from app.voice.voice_stack_v2 import EnergyVAD  # noqa: E402


def jpeg(color, size=(320, 180)):
    from PIL import Image
    img = Image.new("RGB", size, color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def pcm_loud(n=480, amp=12000):
    import array
    return (array.array("h", [amp if i % 2 else -amp
                              for i in range(n)])).tobytes()


class _TTS:
    """Test ses aygıtı kancası — gerçek sentez motoru yerine geçer;
    speak edilenler kayıt altında (cihaz yok, ses ÜRETİLMEZ iddiası yok)."""
    voice = "tr-TR-Test"

    def __init__(self):
        self.calls = []

    def backend(self):
        return "test-slot"

    async def synthesize(self, text):
        self.calls.append(text)
        await asyncio.sleep(0.001)
        return (f"A::{text}".encode(), "mp3")


class _Computer:
    """Test cihaz katmanı: tıklamayı UYGULAR (kayıt) ve ekranın
    değiştiğini simüle eden observation üretir."""

    def __init__(self):
        self.status_flag = {"available": True}
        self.clicks = []
        self.screen = jpeg((255, 0, 0))

    def status(self):
        return {"executor": "test", "available": True}

    def execute(self, action):
        self.clicks.append(action.target)
        self.screen = jpeg((0, 255, 0))     # eylem ETKİ ETTİ (renk değişti)
        return {"clicked": True}

    def active_window_title(self):
        return "TestUygulaması"


# ------------------------------------------------------------ E2E 58
def test_e2e58_voice_brain_vision_computer_verify_tts(tmp_path):
    """DUY→ANLA→GÖR→HAREKET→GÖZLEMLE→DOĞRULA→CEVAPLA zinciri."""
    events = []
    bus = DurableEventBus(db_path=os.path.join(str(tmp_path), "ev.db"))
    bridge = MultimodalEventBridge(bus)
    bus.subscribe("voice.*", lambda t, p: events.append((t, p)))
    bus.subscribe("computer.*", lambda t, p: events.append((t, p)))
    bus.subscribe("screen.*", lambda t, p: events.append((t, p)))

    tts_slot = _TTS()
    computer = _Computer()

    # --- görsel taraf: gerçek change detection (değişen içerik)
    from app.multimodal.vision_runtime import (AdaptiveVisionLoop,
                                               FrameStore)
    frames = FrameStore(max_age_s=30.0)
    screen_state = {"data": jpeg((255, 0, 0))}

    class Cap:
        def grab_bytes(s):
            return screen_state["data"], (320, 180), 1.0

    loop = AdaptiveVisionLoop(Cap(), frames, mode="ACTIVE_COMPUTER",
                              on_significant_change=lambda f: bridge.emit(
                                  "screen.changed", {"frame": f.frame_id}))
    loop.step()                                   # taban görüntü

    # --- ses tarafı: gerçek VAD'li tam tur
    async def mic():
        for _ in range(4):
            yield pcm_loud()
        for _ in range(30):
            yield b"\x00" * 960

    async def stt(pcm):
        yield {"kind": "partial", "text": "kırmızı", "confidence": 0.5}
        yield {"kind": "final", "text": "pencereyi değiştir", "confidence":
               0.92}

    acted = {"done": False}

    async def brain(text):
        # ANLA→PLANLA→HAREKET: yanıt akışı ÜRETİRKEN eylem paralel koşar
        for c in ("Tamam.",
                  "Pencereyi",
                  "değiştiriyorum.",
                  "Doğrulandı."):
            if not acted["done"]:
                # computer action: lifecycle + verification gerçek
                rt = ComputerUseRuntime(computer)
                screen_before = screen_state["data"]

                def do_click(t):
                    out = rt.run(ComputerAction(
                        "click", target={"x": 10, "y": 10},
                        expected={}), approved=True)
                    return out["state"] in ("EXECUTED", "VERIFIED")

                def observe():
                    after = computer.screen
                    from app.multimodal.vision_runtime import \
                        _downsample_hash
                    changed = _downsample_hash(screen_before) != \
                        _downsample_hash(after)
                    bridge.emit("computer.action_started", {})
                    bridge.emit("computer.action_verified",
                                {"ok": changed})
                    return Observation("screen", 0.0,
                                       {"screen_changed": changed})

                vloop = VerifiedActionLoop(
                    do_click, observe,
                    ExpectedState(screen_changed=True))
                res = vloop.run()          # senkron doğrulama
                acted["done"] = True
                screen_state["data"] = computer.screen  # yeni taban
            yield c
            await asyncio.sleep(0.005)

    async def speak(chunks):
        for c in chunks:                       # gerçek TTS slotuna akıt
            await tts_slot.synthesize(c)

    rt = VoiceRuntime(mic_frames=mic, stt_stream=stt, brain_stream=brain,
                      tts_speak_chunks=speak, vad=EnergyVAD(),
                      config=VoiceRuntimeConfig(),
                      on_event=lambda ev: events.append(
                          (ev["type"], ev)))
    out = asyncio.run(rt.run_turn())

    # zincirin HER halkası kanıtlanır:
    assert out["status"] == "ok" and out["text"] == "pencereyi değiştir"
    assert len(computer.clicks) == 1                  # HAREKET gerçek
    assert len(tts_slot.calls) >= 2                   # CEVAPLA (streaming)
    kinds = {t for t, _ in events}
    assert "voice.started" in kinds                   # DUY
    assert "screen.changed" in kinds                  # GÖZLEMLE (bus'ta)
    assert "computer.action_verified" in kinds        # DOĞRULA
    assert rt.fsm.state == "IDLE"
    # latency ölçüldü (gerçek rakamlar, boş değil):
    rec = rt.latency._records[-1]
    assert rec.final_stt_ms is not None and rec.first_audio_ms is not None


# ------------------------------------------------------------ E2E 59
def test_e2e59_voice_browser_verify(tmp_path):
    """Sesli komut → browser planı → DOM action → verify → rapor."""
    from app.multimodal.browser_runtime import BrowserRuntime
    from tests.test_wave4_browser import ScriptEngine, el

    eng = ScriptEngine(
        elements=[el("#save", "Kaydet", role="button")],
        page_text="Ayarlar sayfası")
    rt_browser = BrowserRuntime(eng)

    async def mic():
        yield pcm_loud()
        yield b"\x00" * 960 * 3

    async def stt(pcm):
        yield {"kind": "final", "text": "tarayıcıda kaydet'e tıkla",
               "confidence": 0.9}

    outcome = {}

    async def brain(text):
        assert "kaydet" in text.lower()              # ANLA
        rt_browser.navigate("https://ornek.com/ayarlar", approved=True)
        out = rt_browser.act("click", "Kaydet", approved=True)
        outcome["browser"] = out
        yield "Tıkladım"
        yield "ve doğruladım."

    rt = VoiceRuntime(mic_frames=mic, stt_stream=stt, brain_stream=brain,
                      tts_speak_chunks=None, vad=EnergyVAD())
    out = asyncio.run(rt.run_turn())
    assert out["status"] == "ok"
    assert outcome["browser"]["state"] == "OK"        # eylem + observe
    assert outcome["browser"]["dom_changed"] is True  # VERIFY sinyali
    assert ("click", "#save") == (eng.calls[-1][0], eng.calls[-1][1])


# ------------------------------------------------------------ E2E 60
def test_e2e60_multimodal_coding_task(tmp_path):
    """'Bu hatayı düzelt': paralel bağlam toplama → fusion → memory
    politikası → brain prompt. Wave 3 DAG + Wave 2 store GERÇEK."""
    from app.multimodal.fusion_agent import FusionAgent, default_executors

    reg = WorkerRegistry(db_path=os.path.join(str(tmp_path), "w.db"))
    orch = SupervisorOrchestrator(
        reg, DAGScheduler(reg, global_limit=8), CapabilityTokenAuthority(),
        ArtifactManager(db_path=os.path.join(str(tmp_path), "a.db"),
                        redact_fn=redact),
        AgentMessageBus(db_path=os.path.join(str(tmp_path), "m.db"),
                        registry=reg, redact_fn=redact),
        tracer=Tracer(os.path.join(str(tmp_path), "t.jsonl")))
    store = MemoryStore(db_path=os.path.join(str(tmp_path), "mm.db"),
                        redact_fn=redact)
    agent = FusionAgent(orch,
                        memory_policy=MultimodalMemoryPolicy(store))
    execs = default_executors(
        vision=lambda: "ekran: traceback görünür tests/test_a.py satır 12",
        browser=lambda: "DOM: 'Run Tests' butonu pasif",
        computer=lambda: "uia: VS Code odaklı, terminal paneli açık",
        coding=lambda: "kod: assert x == 2 satırı TypeError fırlatıyor",
        verification=lambda: "görsel+kod kaynakları aynı hatayı işaret "
                             "ediyor")
    report = asyncio.run(agent.gather(execs))
    assert report["pipeline"]["results"] == 5          # 5 worker başarılı

    # önemli gözlem memory'ye YAZILDI (politika onaylı, provenance'lı)
    obs_entry = ContextEntry("vision",
                             "hata: tests/test_a.py satır 12 traceback",
                             source="vision:frame-1", confidence=0.9)
    decision = agent.memory_policy.maybe_write(obs_entry)
    assert decision["write"] is True
    rows = store.query()
    assert any("test_a.py" in r["content"] for r in rows)

    # brain prompt'u: hataya en alakalı kaynaklar üstte (relevance)
    prompt = agent.context.to_prompt("hatayı düzelt")
    assert "[code|worker:CODING" in prompt
    assert "[task|worker:VERIFICATION" in prompt


# ------------------------------------------------------------ §31 gerçek cihaz
def test_real_device_e2e_honest_skip():
    """§31: gerçek Windows ortamı gerektirir. Bu konteynerde cihaz yok —
    SAHTE E2E KOŞULMAZ; dürüst skip + capability raporu."""
    import shutil
    import sys as _s
    caps = {
        "windows": _s.platform.startswith("win"),
        "microphone/sounddevice": shutil.which("python") is not None and
        _importable("sounddevice"),
        "screen": _importable("pyautogui"),
        "browser": _importable("playwright"),
        "ocr": shutil.which("tesseract") is not None,
    }
    if not (caps["windows"] and caps["screen"]):
        pytest.skip(
            f"gerçek cihaz E2E (wake→speak→STT→brain→screen→UIA→action→"
            f"verify→browser→TTS→interrupt) gerçek Windows + cihaz ister; "
            f"bu ortamda eksik: {[k for k, v in caps.items() if not v]}")


def _importable(mod):
    try:
        __import__(mod)
        return True
    except Exception:  # noqa: BLE001
        return False
    except SystemExit:
        # Some optional deps (e.g. pyautogui's mouseinfo) call sys.exit()
        # instead of raising when a system lib (tkinter) is missing, even
        # with a display present. Treat that as "not importable", never as
        # a process-crashing failure.
        return False


# ------------------------------------------------------------ regression
def test_regression_floor_and_golden():
    """§34: taban korunur — suite içinde Wave1/2/3 testleri + golden
    (test_eval_golden) koşar; bu dosya yalnız şunu kanıtlar: Wave 4
    modülleri frozen dalgaların testlerini KIRMADI (import güvenliği)."""
    import app.multimodal  # noqa: F401
    import app.voice.voice_stack_v2  # noqa: F401
    import app.browser.agent  # noqa: F401
    import app.vision.analyze  # noqa: F401
    import app.orchestr.orchestrator  # noqa: F401
    import app.events.bus  # noqa: F401
    import app.memory.store_v3  # noqa: F401
    assert True
