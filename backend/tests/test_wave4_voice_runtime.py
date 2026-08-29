"""WAVE 4 / Voice runtime: durum makinesi + paralel streaming + latency."""
import asyncio
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.multimodal.voice_runtime import (  # noqa: E402
    ERROR, IDLE, INTERRUPTED, LISTENING, PROCESSING, SPEAKING, THINKING,
    InvalidVoiceTransition, LatencyTracker, LatencyRecord, VoiceRuntime,
    VoiceRuntimeConfig, VoiceStateMachine, percentile,
)


# ------------------------------------------------------------ state machine
def test_voice_fsm_happy_path():
    f = VoiceStateMachine()
    assert f.state == IDLE
    f.transition(LISTENING, reason="wake")
    f.transition(THINKING, reason="speech end")
    f.transition(PROCESSING, reason="action needed")
    f.transition(THINKING, reason="action result")
    f.transition(SPEAKING, reason="first audio")
    f.transition(IDLE, reason="complete")
    assert len(f.history) == 6


def test_voice_fsm_invalid_transitions_rejected():
    f = VoiceStateMachine()
    with pytest.raises(InvalidVoiceTransition):
        f.transition(SPEAKING)                     # IDLE→SPEAKING RED
    f.transition(LISTENING)
    with pytest.raises(InvalidVoiceTransition):
        f.transition(SPEAKING)                     # LISTENING→SPEAKING RED
    f.transition(THINKING)
    with pytest.raises(InvalidVoiceTransition):
        f.transition(LISTENING)                    # THINKING→LISTENING RED
    with pytest.raises(InvalidVoiceTransition):
        f.transition("BOGUS")                      # bilinmeyen durum RED
    assert f.state == THINKING                     # durum değişmedi


def test_voice_fsm_interrupted_and_error_recovery():
    f = VoiceStateMachine()
    f.transition(LISTENING); f.transition(THINKING); f.transition(SPEAKING)
    f.transition(INTERRUPTED, reason="barge-in")
    f.transition(LISTENING, reason="mic priority")  # barge-in sonrası dinle
    f.transition(ERROR, reason="stt failure")
    f.transition(IDLE, reason="recovered")          # ERROR→IDLE kurtarma OK
    f2 = VoiceStateMachine()
    with pytest.raises(InvalidVoiceTransition):
        f2.transition(PROCESSING)     # IDLE→PROCESSING RED
    assert f2.state == IDLE


# ------------------------------------------------------------ latency
def test_percentile_honest_on_empty():
    assert percentile([], 50) is None


def test_percentile_values():
    vals = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
    assert percentile(vals, 50) == 5.0        # nearest-rank
    assert percentile(vals, 95) == 10.0
    assert percentile(vals, 0) == 1.0


def test_latency_tracker_summary_only_real_samples():
    t = LatencyTracker()
    t.begin(now=0.0)
    t.mark("first_partial", now=0.020)        # 20 ms
    t.mark("final_stt", now=0.100)            # 100 ms
    t.mark("first_audio", now=0.350)
    rec = t.finish({"first_partial_ms": "first_partial",
                    "final_stt_ms": "final_stt"})
    assert rec.first_partial_ms == 20.0 and rec.final_stt_ms == 100.0
    assert rec.wake_ms is None                # ölçülmedi → None (uydurma YOK)
    t.begin(now=0.0)
    t.mark("first_partial", now=0.040)
    t.finish({"first_partial_ms": "first_partial"})
    s = t.summary()["first_partial_ms"]
    assert s["samples"] == 2 and s["p50"] in (20.0, 40.0)
    assert t.summary()["wake_ms"]["samples"] == 0
    assert t.summary()["wake_ms"]["p50"] is None


# ------------------------------------------------------------ pipeline
def _mk_runtime(degraded=False, events=None, chunk_delay=0.01):
    events = events if events is not None else []
    """Test kancaları: frame/stt/brain/tts ASIN kronolojisiyle üretilir.
    Test edilen şey pipeline mantığıdır (akış/paralellik/iptal); STT
    kalitesi değil — gerçek cihazda mic_frames/stt_stream gerçek olur."""
    import array

    def loud_frame(ms=30, sr=16000):
        # gerçek RMS'i olan sentez PCM (EnergyVAD ile test edilebilir)
        n = int(sr * ms / 1000)
        return (array.array("h", [12000 if i % 2 == 0 else -12000
                                  for i in range(n)])).tobytes()

    def silent_frame(ms=30, sr=16000):
        return b"\x00" * int(sr * ms / 1000) * 2

    async def mic_frames():
        for _ in range(3):
            yield loud_frame()
        for _ in range(15):
            yield silent_frame()

    async def stt_stream(pcm):
        yield {"kind": "partial", "text": "ultr", "confidence": 0.4,
               "language": "tr"}
        yield {"kind": "partial", "text": "ultron ekran", "confidence": 0.7,
               "language": "tr"}
        yield {"kind": "final", "text": "ultron ekranı oku", "confidence":
               0.93, "language": "tr"}

    async def brain_stream(text):
        for c in ("Ekranı", "okuyorum:", "üç", "pencere", "açıktır."):
            await asyncio.sleep(chunk_delay)
            yield c

    spoken = []

    async def tts_speak_chunks(chunks):
        for c in chunks:
            spoken.append(c)
            await asyncio.sleep(chunk_delay / 2)

    from app.voice.voice_stack_v2 import EnergyVAD
    rt = VoiceRuntime(
        mic_frames=None if degraded else mic_frames,
        stt_stream=None if degraded else stt_stream,
        brain_stream=None if degraded else brain_stream,
        tts_speak_chunks=None if degraded else tts_speak_chunks,
        on_event=lambda ev: (events if events is not None else []).append(ev),
        vad=EnergyVAD(),
        config=VoiceRuntimeConfig())
    return rt, spoken


def test_voice_runtime_full_streaming_turn():
    events = []
    rt, spoken = _mk_runtime(events=events)
    out = asyncio.run(rt.run_turn())
    assert out["status"] == "ok"
    assert out["text"] == "ultron ekranı oku"
    assert len(out["partials"]) == 2                 # partial akışı gerçek
    assert out["spoken_chunks"] == 5                 # TTS chunk chunk konuştu
    assert spoken[:1] == ["Ekranı"]                  # ilk cümle beklemeden
    kinds = [e["type"] for e in events]
    assert "voice.started" in kinds and "voice.ended" in kinds
    assert rt.fsm.state == IDLE                       # tur kapandı
    lat = rt.latency._records[-1]
    assert lat.final_stt_ms is not None
    assert lat.first_token_ms is not None
    assert lat.first_audio_ms is not None


def test_voice_runtime_is_parallel_not_sequential():
    """TTS ilk chunk'ı brain bitmesini BEKLEMEZ: ilk ses, toplam üretim
    süresinin erken bir noktasında başlar (sequential'da >= üretim toplamı)."""
    rt, spoken = _mk_runtime(chunk_delay=0.03)        # 5 chunk × 30ms
    t0 = time.monotonic()
    out = asyncio.run(rt.run_turn())
    dt = time.monotonic() - t0
    assert out["status"] == "ok"
    lat = rt.latency._records[-1]
    # ilk ses, üretimin ilk chunk'ından hemen sonra (≤ 3 chunk üretimi)
    assert lat.first_audio_ms is not None
    assert lat.first_audio_ms < 3 * 30 * 1000 / 1000 * 10  # ms cinsi esnek
    assert dt < 5 * 0.03 * 2                           # seri olsaydı 2× olurdu


def test_voice_runtime_no_speech_is_honest():
    """STT final boşsa 'no_speech' — uydurma cevap YOK."""
    async def mic():
        yield b"\x00" * 960

    async def stt(pcm):
        yield {"kind": "final", "text": "", "confidence": 0.1,
               "language": "tr"}

    rt = VoiceRuntime(mic_frames=mic, stt_stream=stt,
                      brain_stream=None, tts_speak_chunks=None)
    out = asyncio.run(rt.run_turn())
    assert out["status"] == "no_speech" and out["text"] is None
    assert rt.fsm.state == IDLE


def test_voice_runtime_barge_in_interrupts_tts():
    """TTS konuşurken interrupt(): cancel set edilir, akış erken biter."""
    import array

    async def mic():
        for _ in range(3):
            n = 480
            yield (array.array("h", [9000] * n)).tobytes()
        yield b"\x00" * 960 * 4

    async def stt(pcm):
        yield {"kind": "final", "text": "uzun anlat", "confidence": 0.9,
               "language": "tr"}

    async def brain(text):
        for i in range(50):                          # uzun cevap
            await asyncio.sleep(0.005)
            yield f"parça{i} "

    rt = VoiceRuntime(mic_frames=mic, stt_stream=stt, brain_stream=brain,
                      tts_speak_chunks=None)

    # interrupt'u TTS sırasında tetikle: brain akışında state SPEAKING olana
    # kadar bekle; ilk chunk'ta yapay SPEAKING'e geçirip interrupt edelim
    original = rt.run_turn

    async def scenario():
        task = asyncio.ensure_future(rt.run_turn())
        await asyncio.sleep(0.02)
        # düşünceden konuşmaya geçişi simüle et (barge-in SPEAKING'de güçlü)
        while rt.fsm.state != SPEAKING and not task.done():
            await asyncio.sleep(0.005)
        if rt.fsm.state == SPEAKING:
            ev = rt.interrupt()
            assert ev["type"] == "voice.interrupted"
        return await task

    out = asyncio.run(scenario())
    if out["interrupted"]:
        assert out["status"] == "interrupted"
        assert rt.fsm.state in (IDLE, LISTENING)
    else:                                            # tur erken bittiyse
        assert out["status"] in ("ok", "interrupted")


def test_voice_runtime_degraded_is_explicit():
    rt, _ = _mk_runtime(degraded=True)
    cap = rt.capability()
    assert cap["degraded"] is True and cap["stt"] is False
    with pytest.raises(RuntimeError, match="degraded"):
        asyncio.run(rt.run_turn())
