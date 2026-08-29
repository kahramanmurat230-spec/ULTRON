"""PHASE 5: REAL wake-word engines — audio-level spotting, honest unavailability."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.voice.wake import (  # noqa: E402
    OWW_FRAME, PORCUPINE_FRAME, OpenWakeWordEngine, PorcupineEngine,
    WakeWordManager,
)


# ------------------------------------------------------------ porcupine
def test_porcupine_keyword_file_found():
    e = PorcupineEngine(keyword="jarvis", access_key="x")
    if e.keyword_path is None:  # pvporcupine kurulu değilse dürüst hata
        assert "pvporcupine" in (e.error or "")
        return
    assert e.keyword_path.endswith(".ppn")
    assert "jarvis" in e.keyword_path


def test_porcupine_unavailable_without_key():
    e = PorcupineEngine(keyword="jarvis", access_key=None)
    old = os.environ.pop("PICOVOICE_ACCESS_KEY", None)
    try:
        assert e.available is False
        st = e.status()
        assert st["available"] is False and ("ACCESS_KEY" in st["error"])
    finally:
        if old:
            os.environ["PICOVOICE_ACCESS_KEY"] = old


def test_porcupine_start_requires_availability():
    e = PorcupineEngine(keyword="jarvis", access_key=None)
    with pytest.raises(RuntimeError):
        e.start()


# ------------------------------------------------------------ openwakeword
def test_openwakeword_honest_without_models(tmp_path):
    e = OpenWakeWordEngine(model_dir=str(tmp_path))
    assert e.available is False
    st = e.status()
    assert st["available"] is False and "model dosyası" in st["error"]
    with pytest.raises(RuntimeError):
        e.start()


# ------------------------------------------------------------ manager (DI)
class FakeEngine:
    def __init__(self, name, available=True, fire_on=None):
        self.NAME = name
        self._available = available
        self.fire_on = fire_on or []
        self.started = False
        self.stopped = False
        self.error = None

    @property
    def available(self):
        return self._available

    def status(self):
        return {"engine": self.NAME, "available": self._available,
                "frame_samples": OWW_FRAME, "error": self.error}

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def process(self, frame):
        if self.fire_on:
            word = self.fire_on.pop(0)
            return (word, 0.87)
        return {}


def test_manager_selects_first_available():
    a = FakeEngine("dead", available=False)
    b = FakeEngine("alive")
    m = WakeWordManager(engines=[a, b])
    st = m.start()
    assert st["available"] is True and st["active"] == "alive"
    assert b.started and not a.started
    m.stop()
    assert b.stopped


def test_manager_unavailable_reports_all_engines():
    m = WakeWordManager(engines=[FakeEngine("a", available=False),
                                 FakeEngine("b", available=False)])
    st = m.start()
    assert st["available"] is False
    assert st["active"] is None and "manuel" in st["note"]
    assert len(st["engines"]) == 2


def test_manager_chunking_and_detection():
    eng = FakeEngine("fake", fire_on=["jarvis"])
    m = WakeWordManager(engines=[eng])
    m.start()
    # 3.5 çerçeve gönder: arabellek doğru bölünmeli, tek event dönmeli
    chunk = b"\x01\x00" * int(OWW_FRAME * 3.5)
    res = m.process_chunk(chunk)
    assert res == ("jarvis", 0.87)
    assert len(m._buf) == (len(chunk) % (OWW_FRAME * 2))


def test_manager_without_active_engine_silent():
    m = WakeWordManager(engines=[FakeEngine("x", available=False)])
    m.start()
    assert m.process_chunk(b"\x00\x00" * 100) is None


def test_real_porcupine_if_key(monkeypatch):
    key = os.environ.get("PICOVOICE_ACCESS_KEY", "")
    if not key:
        pytest.skip("PICOVOICE_ACCESS_KEY yok (kullanıcı anahtarı) — PHASE 5 PARTIAL")
    e = PorcupineEngine(keyword="jarvis", access_key=key)
    e.start()
    try:
        import array
        silence = array.array("h", [0] * PORCUPINE_FRAME)
        assert e.process(silence) is False  # sessizlik: asla tetiklenmez
    finally:
        e.stop()
