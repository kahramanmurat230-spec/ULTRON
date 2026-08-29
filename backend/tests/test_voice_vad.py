"""PHASE 6: voice foundation — VAD start/end/silence + barge-in, real DSP on synthetic audio."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.voice.voice_stack_v2 import EnergyVAD, VoiceStackV2, _HysteresisVAD  # noqa: E402

SR = 16000


def frame(ms=30, amp=0.0):
    n = int(SR * ms / 1000)
    return (int(amp * 32767)).to_bytes(2, "little") * n


def loud_frame(ms=30):
    # gerçek bir ton: alternating +/- büyük genlik → RMS yüksek
    n = int(SR * ms / 1000)
    return b"".join((32767 if i % 2 == 0 else -32767).to_bytes(2, "little", signed=True)
                    for i in range(n))


def test_energy_vad_start_end_silence():
    v = EnergyVAD(threshold=0.02, hangover_frames=3, sample_rate=SR)
    assert v.process_frame(frame()) is False          # sessizlik
    assert v.process_frame(loud_frame()) is True      # speech START
    assert v.process_frame(loud_frame()) is True
    for _ in range(3):
        v.process_frame(frame())                      # sessizlik
    assert v.process_frame(frame()) is False          # speech END (hangover sonrası)


def test_energy_vad_hangover_keeps_short_pause():
    v = EnergyVAD(threshold=0.02, hangover_frames=12, sample_rate=SR)
    v.process_frame(loud_frame())
    v.process_frame(frame())                          # tek sessiz kare
    assert v._speech is True                          # henüz bitmedi (hysteresis)


class ToggleRaw:
    """İlk `loud_n` kare sesli, sonrası sessiz — gerçek VAD gibi."""
    def __init__(self, loud_n):
        self.n = loud_n
    def process_frame(self, _f):
        if self.n > 0:
            self.n -= 1
            return True
        return False


def test_hysteresis_vad_requires_start_frame_and_end_frames():
    h = _HysteresisVAD(ToggleRaw(10), start_frames=2, end_frames=2)
    assert h.process_frame(b"x") is False             # 1 kare yetmez
    assert h.process_frame(b"x") is True              # 2. kare → başlar
    h2 = _HysteresisVAD(ToggleRaw(1), start_frames=1, end_frames=2)
    assert h2.process_frame(b"x") is True             # speech başlar
    assert h2.process_frame(b"x") is True             # 1 sessizlik: devam (end_frames=2)
    assert h2.process_frame(b"x") is False            # 2 sessizlik: biter


def test_stack_emits_speech_start_and_end():
    events = []
    st = VoiceStackV2(on_event=events.append, sample_rate=SR)
    assert st.vad_kind == "energy"                    # bağımlılıksız birincil yol bu ortamda
    ev1 = st.feed_frame(loud_frame())
    assert ev1["type"] == "speech_start"
    st.feed_frame(loud_frame())
    ev2 = None
    for _ in range(20):
        ev2 = st.feed_frame(frame())
        if ev2:
            break
    assert ev2 and ev2["type"] == "speech_end"
    assert ev2["bytes"] > 0                           # buffering gerçek
    assert events[0]["type"] == "speech_start"


def test_barge_in_interrupts_tts():
    interrupts = []
    events = []
    st = VoiceStackV2(on_event=events.append,
                      on_interrupt=lambda: interrupts.append(True), sample_rate=SR)
    st.tts_start()
    assert st.tts_active is True
    ev = st.feed_frame(loud_frame())
    assert ev["type"] == "barge_in"                   # TTS kesildi
    assert interrupts == [True]
    assert st.tts_active is False                     # dinlemeye geçildi
    assert any(e["type"] == "barge_in" for e in events)


def test_metrics_structure_and_vad_kind():
    st = VoiceStackV2(sample_rate=SR)
    assert set(st.metrics) == {"stt_ms", "llm_ms", "tts_ms", "vad"}
    assert st.metrics["vad"] in ("energy", "webrtcvad", "silero")
