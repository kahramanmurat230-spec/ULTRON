"""WAVE 4 / Wake+VAD+Barge-in: debounce/cooldown, false positive, echo
koruması, TTS sırasında barge-in, dürüst degraded."""
import array
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.multimodal.wake_vad import (  # noqa: E402
    BargeInController, EchoGuard, WakeGate,
)
from app.voice.voice_stack_v2 import EnergyVAD  # noqa: E402
from app.voice.wake import WakeWordManager  # noqa: E402


def loud_frame(amp=12000, n=480):
    return (array.array("h", [amp if i % 2 else -amp
                              for i in range(n)])).tobytes()


def quiet_frame(amp=900, n=480):
    return (array.array("h", [amp if i % 2 else -amp
                              for i in range(n)])).tobytes()


def silent_frame(n=480):
    return b"\x00" * n * 2


class ScriptedWake:
    """Test kancası: önceden verilmiş hit dizisini yutar (gerçek engine
    produksiyonda WakeWordManager'dan gelir)."""

    NAME = "scripted"

    def __init__(self, hits):
        self.hits = list(hits)
        self.active = self  # manager sözleşmesi: active engine

    def status(self):
        return {"active": self.NAME, "available": True}

    def process_chunk(self, audio):
        return self.hits.pop(0) if self.hits else None


# ---------------------------------------------------------------- wake gate
def test_wake_gate_degraded_without_engine():
    mgr = WakeWordManager({})               # gerçek engine yok (kurulu değil)
    gate = WakeGate(mgr)
    st = gate.status()
    assert st["degraded"] is True and st["available"] is False
    assert gate.process_chunk(loud_frame()) is None   # sahte tetikleme YOK


def test_wake_gate_accepts_strong_hit():
    gate = WakeGate(ScriptedWake([("jarvis", 0.9)]), threshold=0.5)
    ev = gate.process_chunk(loud_frame())
    assert ev is not None and ev["type"] == "wake_detected"
    assert ev["score"] == 0.9 and gate.state == "LISTEN"
    gate.listening_done()
    assert gate.state == "IDLE"


def test_wake_gate_suppresses_low_score_false_positive():
    gate = WakeGate(ScriptedWake([("jarvis", 0.2), ("jarvis", 0.3)]),
                    threshold=0.5)
    assert gate.process_chunk(loud_frame()) is None
    assert gate.process_chunk(loud_frame()) is None   # 2. düşük hit de
    assert gate.stats["suppressed_threshold"] == 2
    assert gate.state == "IDLE"


def test_wake_gate_cooldown_blocks_rapid_retrigger():
    hits = [("jarvis", 0.9), ("jarvis", 0.9)]
    gate = WakeGate(ScriptedWake(hits), cooldown_s=5.0)
    first = gate.process_chunk(loud_frame())
    assert first is not None
    gate.listening_done()
    second = gate.process_chunk(loud_frame())          # hemen yeniden
    assert second is None
    assert gate.stats["suppressed_cooldown"] == 1


def test_wake_gate_debounce_requires_consecutive():
    gate = WakeGate(ScriptedWake([("jarvis", 0.8), ("jarvis", 0.8)]),
                    debounce_hits=2)
    assert gate.process_chunk(loud_frame()) is None    # ilk tek hit yetmez
    ev = gate.process_chunk(loud_frame())
    assert ev is not None and gate.stats["suppressed_debounce"] == 1


# ---------------------------------------------------------------- echo guard
def test_echo_guard_conservative_suppresses_tts_playback():
    g = EchoGuard(base_threshold=0.02)
    g.set_tts(True, reference=b"\x01" * 960)
    # TTS sırasında kısık kayıt (echo) → echo sayılır
    assert g.check(quiet_frame(), rms=0.01) is True
    # TTS kapalı → asla echo demez
    g.set_tts(False)
    assert g.check(quiet_frame(), rms=0.01) is False


def test_echo_guard_threshold_boost_only_when_tts():
    g = EchoGuard(base_threshold=0.02, tts_rms_margin=1.6)
    assert g.effective_threshold() == 0.02
    g.set_tts(True)
    assert abs(g.effective_threshold() - 0.032) < 1e-9
    assert g.effective_start_frames(2) == 4   # boost


def test_echo_guard_aec_injection_wins():
    calls = []

    def aec(frame, ref):
        calls.append((len(frame), len(ref or b"")))
        return True                          # gerçek AEC: echo dedi

    g = EchoGuard(is_echo=aec)
    g.set_tts(True, reference=b"\x01" * 960)
    assert g.check(loud_frame(), rms=0.5) is True
    assert calls and calls[0][1] == 960      # referans karşılaştırıldı


# ---------------------------------------------------------------- barge-in
def test_vad_speech_start_and_end():
    ctl = BargeInController(EnergyVAD(threshold=0.02))
    assert ctl.feed_frame(loud_frame(15000))[0]["type"] == "speech_start"
    for _ in range(20):
        ctl.feed_frame(silent_frame())
    # EnergyVAD hangover sonrası end geldi mi?
    assert ctl.stats["speech_ends"] == 1


def test_barge_in_fires_when_user_speaks_over_tts():
    ctl = BargeInController(EnergyVAD(threshold=0.02))
    ctl.set_tts(True, reference=b"\x01" * 960)
    got = []
    for _ in range(4):
        got += ctl.feed_frame(loud_frame(20000))       # güçlü insan sesi
    types = [g["type"] for g in got]
    assert "barge_in" in types
    assert ctl.stats["barge_ins"] == 1
    assert ctl.tts_active is False                     # TTS durdu


def test_tts_playback_not_mistaken_for_user():
    """TTS hoparlörden çalan (kısık kaydedilen) ses barge-in ETMEZ."""
    ctl = BargeInController(EnergyVAD(threshold=0.02))
    ctl.set_tts(True, reference=b"\x01" * 960)
    got = []
    for _ in range(6):
        got += ctl.feed_frame(quiet_frame())           # echo-benzeri seviye
    assert "barge_in" not in [g["type"] for g in got]
    assert ctl.stats["echo_suppressed"] >= 1
    assert ctl.tts_active is True                      # TTS devam


def test_no_barge_in_when_tts_idle_quiet_sound():
    ctl = BargeInController(EnergyVAD(threshold=0.04))  # kısık = parazit
    got = []
    for _ in range(4):
        got += ctl.feed_frame(quiet_frame())
    types = [g["type"] for g in got]
    assert "barge_in" not in types and "speech_start" not in types


def test_controller_status_honest():
    ctl = BargeInController(EnergyVAD(), wake_gate=WakeGate(WakeWordManager({})))
    st = ctl.status()
    assert st["echo_mode"] == "conservative"
    assert st["wake"]["degraded"] is True               # engine yok dürüst
