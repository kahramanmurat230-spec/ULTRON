"""WAVE 4 / Failure injection (§32) — her arıza GERÇEK enjekte edilir.

DETECT → CLASSIFY → RECOVER → VERIFY → REPLAN; sessiz başarı YOK,
sonsuz retry YOK. Enjeksiyon noktaları DI kancaları + gerçek bileşenler
(frame store, DOM engine, STT/TTS zincirleri, Wave 3 orchestrator)."""
import asyncio
import io
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.multimodal.tts_stream import StreamingTTS, TTSUnavailable  # noqa: E402
from app.multimodal.stt_stream import (  # noqa: E402
    STTEvent, STTManager, STTManagerConfig,
)
from app.multimodal.voice_runtime import IDLE, VoiceRuntime  # noqa: E402


def jpeg(color, size=(320, 180)):
    from PIL import Image
    img = Image.new("RGB", size, color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


# ---------------------------------------------------------------- VOICE
def _loud(n=480):
    import array
    return (array.array("h", [11000 if i % 2 else -11000
                              for i in range(n)])).tobytes()


class FlakyEngine:
    NAME = "flaky"

    def __init__(self, events=None, crash=False, delay=0.0):
        self.available = True
        self.events = events or []
        self.crash = crash
        self.delay = delay
        self.calls = 0

    async def stream(self, speech, language):
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.crash:
            raise ConnectionResetError("STT süreci çöktü")
        for ev in self.events:
            yield ev


# F1 — mic disconnect (frame kaynağı aniden biter)
def test_f1_mic_disconnect_stream_ends_cleanly():
    async def mic():
        yield _loud()
        raise IOError("mikrofon bağlantısı koptu")

    async def stt(pcm):
        yield {"kind": "final", "text": "ks", "confidence": 0.9}

    rt = VoiceRuntime(mic_frames=mic, stt_stream=stt,
                      brain_stream=None, tts_speak_chunks=None)
    out = asyncio.run(rt.run_turn())     # hata YUTULMAZ: mic_error raporu
    assert out["status"] == "mic_error" and "koptu" in out["error"]
    assert rt.fsm.state == IDLE          # ERROR→IDLE kurtarma


# F2 — speaker failure (TTS çıktı aygıtı yok → dürüst hata)
def test_f2_speaker_failure_honest():
    class _Slot:
        voice = "v"
        def backend(s): return "x"
        async def synthesize(s, t):
            raise OSError("ses aygıtı yok")

    from app.multimodal.tts_stream import NeuralTTSEngine
    eng = NeuralTTSEngine(tts=_Slot())
    tts = StreamingTTS(engine=eng)

    async def run():
        async for _ in tts.speak_stream(iter(["Konuş."])):
            pass

    with pytest.raises(TTSUnavailable, match="başarısız"):
        asyncio.run(run())               # sahte ses YOK


# F3 — STT process crash → retry → tümü çökerse dürüst error
def test_f3_stt_crash_retry_then_honest_failure():
    eng = FlakyEngine(crash=True)
    m = STTManager(engines=[eng], config=STTManagerConfig(retry_limit=1))
    out = asyncio.run(m.transcribe([_loud()]))
    assert eng.calls == 2                # 1+retry — sonsuz değil
    assert out[-1].kind == "error" and "çöktü" in out[-1].error


# F4 — TTS process crash mid-stream → kalan cümle yok, cancel sayılır
def test_f4_tts_crash_mid_stream_stops():
    class _Slot:
        voice = "v"
        def __init__(s):
            s.calls = 0
        def backend(s): return "x"
        async def synthesize(s, t):
            s.calls += 1
            if s.calls == 1:
                return (b"AUDIO1", "mp3")
            raise RuntimeError("tts süreci çöldü")

    from app.multimodal.tts_stream import NeuralTTSEngine
    tts = StreamingTTS(engine=NeuralTTSEngine(tts=_Slot()))
    got, err = [], None

    async def run():
        nonlocal err
        try:
            async for p in tts.speak_stream(iter(["Bir.", "İki.", "Üç."])):
                got.append(p["text"])
        except TTSUnavailable as exc:
            err = str(exc)

    asyncio.run(run())
    assert got == ["Bir."]               # ilki konuşuldu, sonrası DURDU
    assert err and "çöldü" in err


# F5 — VAD timeout (konuşma hiç bitmedi → listen timeout)
def test_f5_vad_never_ending_speech_timeout():
    async def mic():
        while True:                       # sonsuz ses — VAD end gelmez
            yield _loud()

    async def stt(pcm):
        yield {"kind": "final", "text": "x", "confidence": 0.9}

    from app.multimodal.voice_runtime import VoiceRuntimeConfig
    rt = VoiceRuntime(mic_frames=mic, stt_stream=stt, brain_stream=None,
                      tts_speak_chunks=None,
                      config=VoiceRuntimeConfig(listen_timeout_s=0.1))
    out = asyncio.run(rt.run_turn())
    assert out["status"] == "listen_timeout"   # takılma YOK — timeout


# F6 — TTS interruption (barge-in) anında durur
def test_f6_tts_interruption_immediate():
    class _Slot:
        voice = "v"
        def backend(s): return "x"
        async def synthesize(s, t):
            await asyncio.sleep(0.01)
            return (f"A::{t}".encode(), "mp3")

    from app.multimodal.tts_stream import NeuralTTSEngine
    tts = StreamingTTS(engine=NeuralTTSEngine(tts=_Slot()))
    spoken = []

    async def run():
        gen = tts.speak_stream(iter(["A." * 30] * 8))
        async for p in gen:
            spoken.append(p)
            if len(spoken) == 2:
                tts.cancel()              # barge-in
    asyncio.run(run())
    assert len(spoken) <= 3               # gerisi üretilmedi


# ---------------------------------------------------------------- VISION
# F7 — screen capture failure (sahte frame YOK)
def test_f7_capture_failure_raises_honest():
    from app.multimodal.vision_runtime import (AdaptiveVisionLoop,
                                               CaptureUnavailable, FrameStore)

    class DeadCapture:
        def grab_bytes(self):
            raise CaptureUnavailable("display yok")

    loop = AdaptiveVisionLoop(DeadCapture(), FrameStore())
    with pytest.raises(CaptureUnavailable):
        loop.step()
    assert loop.stats["errors"] == 1


# F8 — stale frame → action RED + kurtarma: yeniden capture
def test_f8_stale_frame_then_refresh_recovers():
    from app.multimodal.vision_runtime import (AdaptiveVisionLoop, FrameStore,
                                               StaleFrameError, VisionFrame)

    class Cap:
        def __init__(s):
            s.n = 0
        def grab_bytes(s):
            s.n += 1
            return jpeg((s.n * 20 % 255, 0, 0)), (320, 180), 3.0

    store = FrameStore(max_age_s=0.05)
    store.add(VisionFrame("eski", time.time() - 1, "screen", (1, 1), "h",
                          1.0, False, 1.0))
    with pytest.raises(StaleFrameError):
        store.action_frame()              # RED
    loop = AdaptiveVisionLoop(Cap(), store)
    loop.step()                           # kurtarma: taze capture
    got = store.action_frame()
    assert got.frame_id != "eski"         # VERIFY: yeni frame kullanımda


# F9 — OCR unavailable (tesseract yok) → ProviderUnavailable, zincir sürer
def test_f9_ocr_unavailable_chain_continues():
    from app.multimodal.grounding import (OCRProvider, ProviderUnavailable,
                                          UIElement, UIHierarchy)
    from tests.test_wave4_grounding import FakeProvider
    h = UIHierarchy([OCRProvider(), FakeProvider("dom", [
        UIElement("d", "Kaydet", "button", (0, 0, 5, 5), "dom", 0.9)])])
    els = h.elements()                    # OCR düştü → DOM devraldı
    assert els[0].name == "Kaydet"


# ---------------------------------------------------------------- BROWSER
# F10 — browser crash (motor öldü) → doğru hata sınıfı
def test_f10_browser_crash_surfaces():
    from app.multimodal.browser_runtime import BrowserRuntime

    class CrashedEngine:
        NAME = "crashed"
        def status(self):
            return {"engine": self.NAME, "available": False}
        def dom_elements(self):
            raise RuntimeError("browser process crashed")
        def ax_elements(self):
            return []
        def page_text(self):
            return ""

    rt = BrowserRuntime(CrashedEngine())
    with pytest.raises(RuntimeError, match="crashed"):
        rt.refresh()                      # sessiz boş sayfa YOK


# F11 — browser timeout (uzun yanıt) → engine seviyesinde TimeoutError
def test_f11_browser_timeout_propagates():
    from app.multimodal.browser_runtime import BrowserRuntime

    class SlowEngine:
        NAME = "slow"
        def status(self):
            return {"engine": self.NAME, "available": True}
        def dom_elements(self):
            raise TimeoutError("page load 20s")
        def ax_elements(self):
            return []
        def page_text(self):
            return ""

    rt = BrowserRuntime(SlowEngine())
    with pytest.raises(TimeoutError):
        rt.refresh()


# F12 — DOM mutation mid-action → stale RED → REPLAN sinyali
def test_f12_dom_mutation_detected():
    from app.multimodal.browser_runtime import BrowserRuntime, StaleDOMError
    from tests.test_wave4_browser import ScriptEngine, el
    eng = ScriptEngine(elements=[el("#a", "Gönder")])
    rt = BrowserRuntime(eng)
    rt.refresh()
    eng.mutated = True
    with pytest.raises(StaleDOMError):
        rt.act("click", "Gönder", approved=True)
    assert rt.stats["stale_rejects"] == 1 # sinyal sayıldı → replan


# F13 — UI element disappearance → grounding başarısız FAILED
def test_f13_element_disappears_honest_fail():
    from app.multimodal.browser_runtime import BrowserRuntime
    from tests.test_wave4_browser import ScriptEngine, el
    eng = ScriptEngine(elements=[])        # eleman yok oldu
    rt = BrowserRuntime(eng)
    rt.refresh()
    out = rt.act("click", "Gönder", approved=True)
    assert out["state"] == "FAILED" and "bulunamadı" in out["error"]


# F14 — wrong window → RED (computer)
def test_f14_wrong_window_red():
    from app.multimodal.computer import (ComputerAction, ComputerUseRuntime,
                                         WrongWindowError)
    from tests.test_wave4_computer import ScriptExecutor
    rt = ComputerUseRuntime(ScriptExecutor(windows="A Uygulaması"))
    with pytest.raises(WrongWindowError):
        rt.run(ComputerAction("click", target={"x": 1, "y": 1},
                              window="B Uygulaması"), approved=True)


# ---------------------------------------------------------------- AGENT
# F15 — worker crash (Wave 3 fusion worker'ı çöker) → FAILED, kalan sürer
def test_f15_worker_crash_in_fusion(tmp_path):
    from app.multimodal.context_fusion import MultimodalEventBridge
    from app.multimodal.fusion_agent import FusionAgent, default_executors
    from app.observability.trace import Tracer
    from app.orchestr.artifacts import ArtifactManager
    from app.orchestr.messages import AgentMessageBus
    from app.orchestr.orchestrator import SupervisorOrchestrator
    from app.orchestr.scheduler import DAGScheduler, SchedulerPolicy
    from app.orchestr.tokens import CapabilityTokenAuthority
    from app.orchestr.worker import WorkerRegistry
    from app.security.redaction import redact as _r
    reg = WorkerRegistry(db_path=os.path.join(str(tmp_path), "w.db"))
    orch = SupervisorOrchestrator(
        reg, DAGScheduler(reg), CapabilityTokenAuthority(),
        ArtifactManager(db_path=os.path.join(str(tmp_path), "a.db")),
        AgentMessageBus(db_path=os.path.join(str(tmp_path), "m.db"),
                        registry=reg),
        tracer=Tracer(os.path.join(str(tmp_path), "t.jsonl")))
    agent = FusionAgent(orch)

    async def crasher(role):
        async def fn(w, c):
            raise RuntimeError(f"{role} worker çöktü")
        return fn

    execs = default_executors(vision=lambda: "ekran ok",
                              browser=lambda: "dom ok",
                              computer=lambda: "uia ok",
                              coding=lambda: "kod ok",
                              verification=lambda: "tutarlı")
    execs["VISION"] = asyncio.run(crasher("vision"))     # gerçek enjeksiyon
    report = asyncio.run(agent.gather(execs))
    states = report["states"]
    assert "FAILED" in states.values()    # çökme GÖRÜNDÜ
    assert sum(1 for s in states.values() if s == "SUCCEEDED") >= 3


# F16 — network loss (STT ağı) → retry sonra dürüst error
def test_f16_network_loss_stt():
    class NetFail(FlakyEngine):
        async def stream(self, speech, language):
            self.calls += 1
            raise ConnectionError("ağ yok")
            yield  # unreachable: async-generator işareti

    eng = NetFail()
    m = STTManager(engines=[eng], config=STTManagerConfig(retry_limit=2))
    out = asyncio.run(m.transcribe([_loud()]))
    assert out[-1].kind == "error" and "ağ yok" in out[-1].error


# F17 — model unavailable (brain yoksa) → degraded, sahte cevap YOK
def test_f17_model_unavailable_degrades():
    async def mic():
        yield _loud()

    async def stt(pcm):
        yield {"kind": "final", "text": "selam", "confidence": 0.9}

    rt = VoiceRuntime(mic_frames=mic, stt_stream=stt, brain_stream=None,
                      tts_speak_chunks=None)
    # brain yok → capability degraded; run_turn STT sonrası brain akışı
    # olmadığı için chunks boş, sahte yanıt ÜRETİLMEZ
    out = asyncio.run(rt.run_turn())
    assert out.get("chunks") in (None, []) and out["status"] in ("ok",
                                                                 "no_speech")


# F18 — permission denied (browser onaysız) → BrowserDenied
def test_f18_permission_denied_browser():
    from app.multimodal.browser_runtime import BrowserDenied, BrowserRuntime
    from tests.test_wave4_browser import ScriptEngine, el
    rt = BrowserRuntime(ScriptEngine(elements=[el("#x", "Git")]))
    with pytest.raises(BrowserDenied):
        rt.act("click", "Git")            # onaysız RED


# F19 — mid-action cancellation + supervisor restart kurtarması
def test_f19_mid_action_cancel_and_restart_recovery(tmp_path):
    """Computer aksiyonu ortada iptal; Wave 3 recover() SUCCEEDED'ı korur."""
    from app.multimodal.computer import (ComputerAction, ComputerUseRuntime)
    from tests.test_wave4_computer import ScriptExecutor
    from app.observability.trace import Tracer
    from app.orchestr.artifacts import ArtifactManager
    from app.orchestr.messages import AgentMessageBus
    from app.orchestr.orchestrator import SupervisorOrchestrator
    from app.orchestr.scheduler import DAGScheduler
    from app.orchestr.tokens import CapabilityTokenAuthority
    from app.orchestr.worker import WorkerRegistry
    from app.security.redaction import redact as _r
    reg = WorkerRegistry(db_path=os.path.join(str(tmp_path), "w.db"))
    orch = SupervisorOrchestrator(
        reg, DAGScheduler(reg, global_limit=1), CapabilityTokenAuthority(),
        ArtifactManager(db_path=os.path.join(str(tmp_path), "a.db")),
        AgentMessageBus(db_path=os.path.join(str(tmp_path), "m.db"),
                        registry=reg),
        tracer=Tracer(os.path.join(str(tmp_path), "t.jsonl")))

    async def slow_w(w, c):
        await asyncio.sleep(3)

    async def ok_w(w, c):
        return {"ok": True, "output": "bitti", "confidence": 0.9,
                "evidence": ["e"]}

    ws = orch.plan("f19", [{"role": "RESEARCH", "name": "hızlı"},
                           {"role": "CODING", "name": "yavaş",
                            "depends_on": ["hızlı"]}])

    async def scenario():
        fut = asyncio.ensure_future(orch.execute(
            "f19", ws, {"RESEARCH": ok_w, "CODING": slow_w}))
        await asyncio.sleep(0.15)
        fut.cancel()
        try:
            await fut
        except BaseException:
            pass

    asyncio.run(scenario())
    plan = orch.recover("f19")
    hızlı = ws[0].worker_id
    assert plan["keep"] == [hızlı]        # SUCCEEDED asla yeniden koşmaz
    # computer aksiyonu iptal: ComputerUseRuntime interrupt benzeri davranış
    rt = ComputerUseRuntime(ScriptExecutor())
    a = ComputerAction("click", target={"x": 1, "y": 1})
    a.state = "APPROVED"                  # ortada iptal
    rt._advance(a, "DENIED")
    assert a.state == "DENIED"            # terminal — yarım kalmadı
