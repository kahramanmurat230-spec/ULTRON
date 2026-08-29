"""WAVE 4 / STT+TTS streaming: partial/final, noise, retry/fallback,
cümle sentezi, cache, iptal, barge-in, dürüst unavailable."""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.multimodal.stt_stream import (  # noqa: E402
    STTEvent, STTManager, STTManagerConfig, STTUnavailable, VoskEngine,
    WhisperEngine,
)
from app.multimodal.tts_stream import (  # noqa: E402
    NeuralTTSEngine, StreamingTTS, TTSUnavailable, split_sentences,
)


# ---------------------------------------------------------------- STT
def _loud(n=480, amp=11000):
    import array
    return (array.array("h", [amp if i % 2 else -amp
                              for i in range(n)])).tobytes()


def _silent(n=480):
    return b"\x00" * n * 2


class FakeRealEngine:
    """DI seam: gerçek engine'in stream sözleşmesini taklit eden test
    kancası (sistemde faster_whisper yerine geçer; transcript'i TEST
    verir, produksiyonda asla taklit edilmez)."""

    NAME = "test-engine"

    def __init__(self, events, fail_first=0):
        self.available = True
        self.events = list(events)
        self.fail_first = fail_first
        self.calls = 0

    async def stream(self, speech, language):
        self.calls += 1
        if self.calls <= self.fail_first:
            raise ConnectionError("engine bağlantı hatası")
        for ev in self.events:
            yield ev


def test_real_engines_honestly_unavailable_here():
    w, v = WhisperEngine(), VoskEngine()
    # bu ortamda gerçek binary yok → available False + açık sebep
    assert w.available is False and "kurulu" in w.error
    assert v.available is False
    m = STTManager()
    assert m.status()["available"] is False


def test_stt_unavailable_never_fakes_transcript():
    m = STTManager(engines=[WhisperEngine()])
    speech = [_loud()]
    out = asyncio.run(m.transcribe(speech))
    kinds = [e.kind for e in out]
    assert "final" not in kinds and "error" in kinds
    assert all(not e.text for e in out)     # uydurma metin YOK


def test_stt_noise_only_returns_ended_no_stt_call():
    calls = []

    class Probe(FakeRealEngine):
        async def stream(self, speech, language):
            calls.append(1)
            yield STTEvent(kind="final", text="x")

    m = STTManager(engines=[Probe([])])
    out = asyncio.run(m.transcribe([_silent(), _silent()]))
    assert out[-1].kind == "end" and calls == []   # sessizlik STT'e gitmedi


def test_stt_partial_then_final_with_metadata():
    evs = [STTEvent(kind="partial", text="ultr", confidence=0.4,
                    language="tr", t_start=0.2, t_end=0.6),
           STTEvent(kind="final", text="ultron ekranı oku", confidence=0.93,
                    language="tr", t_start=0.2, t_end=1.4)]
    eng = FakeRealEngine(evs)
    m = STTManager(engines=[eng])
    partials = []
    out = asyncio.run(m.transcribe([_loud()], on_partial=partials.append))
    assert out[-1].kind == "final"
    assert out[-1].text == "ultron ekranı oku"
    assert out[-1].confidence == 0.93 and out[-1].language == "tr"
    assert out[-1].t_start == 0.2 and out[-1].t_end == 1.4
    assert len(partials) == 1


def test_stt_retry_then_success():
    evs = [STTEvent(kind="final", text="tekrar tamam", confidence=0.9)]
    eng = FakeRealEngine(evs, fail_first=1)      # ilk deneme çöker
    m = STTManager(engines=[eng])
    out = asyncio.run(m.transcribe([_loud()]))
    assert eng.calls == 2                          # retry gerçek
    assert out[-1].kind == "final"


def test_stt_fallback_to_second_engine():
    bad = FakeRealEngine([STTEvent(kind="final", text="x")],
                         fail_first=99)            # hep çöker
    good = FakeRealEngine([STTEvent(kind="final", text="yedek ok",
                                    confidence=0.8)])
    m = STTManager(engines=[bad, good],
                   config=STTManagerConfig(retry_limit=1))
    out = asyncio.run(m.transcribe([_loud()]))
    assert out[-1].kind == "final" and out[-1].text == "yedek ok"


def test_stt_retry_limit_not_infinite():
    bad = FakeRealEngine([], fail_first=99)
    m = STTManager(engines=[bad], config=STTManagerConfig(retry_limit=2))
    t0 = asyncio.run(m.transcribe([_loud()]))
    assert bad.calls == 3                          # 1 + retry_limit(2) — durdu
    assert t0[-1].kind == "error"


def test_stt_cancellation_propagates():
    import array as _arr

    class Slow(FakeRealEngine):
        async def stream(self, speech, language):
            await asyncio.sleep(5)
            yield STTEvent(kind="final", text="geç")

    m = STTManager(engines=[Slow([])])

    async def scenario():
        task = asyncio.ensure_future(m.transcribe([_loud()]))
        await asyncio.sleep(0.02)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())


# ---------------------------------------------------------------- splitter
def test_split_sentences_basic():
    assert split_sentences("Bir. İki! Üç?") == ["Bir.", "İki!", "Üç?"]
    parts = split_sentences("A" * 500)              # uzun satır kırılır
    assert all(len(p) <= 220 for p in parts) and "".join(parts).count("A") == 500
    assert split_sentences("") == []
    assert split_sentences("   ") == []


# ---------------------------------------------------------------- TTS engine
def test_neural_engine_real_status_honest():
    eng = NeuralTTSEngine()                          # gerçek edge-tts sarmalayıcı
    st = eng.status()
    assert st["backend"] == "edge-tts-neural"        # kütüphane kurulu
    with pytest.raises(Exception):                   # ağ yok → dürüst hata
        asyncio.run(eng.synthesize("test cümlesi."))


class DictationEngine(NeuralTTSEngine):
    """Test kancası: sentez çağrılarını kaydeder, gerçek ses baytı üretir
    (produksiyon engine'i asla taklit edilmez; burada hat/zincir testi)."""

    NAME = "dictation"

    def __init__(self, delay=0.01, fail_times=0):
        super().__init__(tts=_SlotTTS(delay, fail_times))


class _SlotTTS:
    def __init__(self, delay, fail_times):
        self.delay = delay
        self.fail_times = fail_times
        self.calls = []
        self.voice = "tr-TR-Test"

    def backend(self):
        return "dictation"

    async def synthesize(self, text):
        import asyncio as _a
        self.calls.append(text)
        if len(self.calls) <= self.fail_times:
            raise RuntimeError("sentez hatası")
        await _a.sleep(self.delay)
        return (f"AUDIO::{text}".encode("utf-8"), "mp3")


def test_tts_streaming_sentence_by_sentence():
    eng = DictationEngine()
    tts = StreamingTTS(engine=eng)
    chunks = ["Ekranı okuyorum. ", "Üç pencere ", "açıktır."]
    out = []

    async def run():
        async for piece in tts.speak_stream(iter(chunks)):
            out.append(piece)
            if len(out) == 1:
                # ilk cümle akış BİTMEDEN konuşuldu (streaming kanıtı)
                assert "Üç" not in eng.tts.calls[0]
        return out

    got = asyncio.run(run())
    texts = [g["text"] for g in got]
    assert texts[0] == "Ekranı okuyorum."          # cümle tamamlanınca ses
    assert got[0]["audio"] == "AUDIO::Ekranı okuyorum.".encode("utf-8")
    assert tts.metrics["first_audio_ms"] is not None
    assert tts.metrics["synthesized"] >= 2


def test_tts_cache_second_hit_no_resynth():
    eng = DictationEngine()
    tts = StreamingTTS(engine=eng)

    async def twice():
        r = []
        for _ in range(2):
            async for p in tts.speak_stream(iter(["Aynı cümle."])):
                r.append(p)
        return r

    out = asyncio.run(twice())
    assert len(out) == 2
    assert out[0]["cached"] is False and out[1]["cached"] is True
    assert tts.metrics["cache_hits"] == 1


def test_tts_cancel_stops_mid_stream():
    eng = DictationEngine(delay=0.02)
    tts = StreamingTTS(engine=eng)
    produced = []

    async def run():
        gen = tts.speak_stream(iter(["Bir.", "İki.", "Üç.", "Dört.",
                                     "Beş."]))
        async for p in gen:
            produced.append(p["text"])
            if len(produced) == 2:
                tts.cancel()                        # barge-in
        return produced

    out = asyncio.run(run())
    assert len(out) <= 3                            # kalanlar üretilmedi
    assert tts.metrics["cancelled"] == 1


def test_tts_error_recovery_retry_then_honest_failure():
    eng = DictationEngine(fail_times=1)             # ilk sentez hatası
    tts = StreamingTTS(engine=eng)
    out = []

    async def run():
        async for p in tts.speak_stream(iter(["Tek cümle."])):
            out.append(p)

    asyncio.run(run())                              # retry ile kurtuldu
    assert out and out[0]["audio"]

    eng2 = DictationEngine(fail_times=99)           # hep hata
    tts2 = StreamingTTS(engine=eng2)

    async def fail():
        async for _ in tts2.speak_stream(iter(["Bu olmayacak."])):
            pass

    with pytest.raises(TTSUnavailable):
        asyncio.run(fail())                         # sahte ses YOK


def test_tts_status_reports_engine_and_cache():
    tts = StreamingTTS(engine=DictationEngine(), voice="tr-TR-AhmetNeural")
    st = tts.status()
    assert st["voice"] == "tr-TR-AhmetNeural"
    assert st["engine"]["backend"] == "dictation"
    assert st["cache_size"] == 0
