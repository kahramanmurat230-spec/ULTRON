"""WAVE 4 / Action verification: expected vs actual, gözlem sağlayıcıları,
retry/alternative/replan zinciri, sonsuz retry YOK."""
import io
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.multimodal.verify import (  # noqa: E402
    ALTERNATIVE, GIVE_UP, REPLAN, RETRY, ExpectedState, Observation,
    RecoveryPlanner, VerifiedActionLoop, observe_dom, observe_screen,
    observe_uia, observe_window, verify,
)


def jpeg(color, size=(320, 180)):
    from PIL import Image
    img = Image.new("RGB", size, color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


# ---------------------------------------------------------------- verify
def test_verify_success_multi_criteria():
    exp = ExpectedState(dom_changed=True, window_title_contains="Notepad",
                        text_present="kaydedildi")
    obs = Observation("mixed", 0.0, {"dom_changed": True,
                                     "window_title": "Belge - Notepad",
                                     "texts": ["Dosya kaydedildi"]})
    res = verify(exp, obs)
    assert res.ok is True and len(res.checked) == 3


def test_verify_mismatch_reports_exact_criterion():
    exp = ExpectedState(dom_changed=True)
    res = verify(exp, Observation("dom", 0.0, {"dom_changed": False}))
    assert res.ok is False and "dom_changed=False" in res.detail


def test_verify_missing_observation_fails_not_guesses():
    exp = ExpectedState(screen_changed=True)
    res = verify(exp, Observation("screen", 0.0, {}))   # gözlem yok
    assert res.ok is False and "belirsiz" in res.detail


def test_verify_no_criteria_fails():
    res = verify(ExpectedState(), Observation("x", 0.0, {"a": 1}))
    assert res.ok is False and "tanımsız" in res.detail


def test_verify_element_gone_and_absent_text():
    obs = Observation("uia", 0.0, {"elements": ["Menü", "Ayarlar"],
                                   "texts": ["silindi"]})
    ok = verify(ExpectedState(element_gone="Sil", text_absent="hata"), obs)
    assert ok.ok is True
    bad = verify(ExpectedState(element_gone="Menü"), obs)
    assert bad.ok is False and "hâlâ görünür" in bad.detail


# ---------------------------------------------------------------- gözlem
def test_observe_screen_real_image_diff():
    before = jpeg((255, 0, 0))
    from app.multimodal.vision_runtime import _downsample_hash
    h1 = _downsample_hash(before)
    same = observe_screen(h1, jpeg((255, 0, 0)))
    diff = observe_screen(h1, jpeg((0, 255, 0)))
    assert same.data["screen_changed"] is False   # gerçek analiz: aynı
    assert diff.data["screen_changed"] is True    # gerçek analiz: farklı


def test_observe_dom_window_uia():
    assert observe_dom("aa", "bb").data["dom_changed"] is True
    assert observe_dom("aa", "aa").data["dom_changed"] is False
    assert observe_dom(None, "x").data["dom_changed"] is False  # taban yok
    assert observe_window("Chrome").data["window_title"] == "Chrome"
    obs = observe_uia([{"name": "Kaydet"}, {"text": "hazır"}])
    assert obs.data["elements"] == ["Kaydet"] and obs.data["texts"] == ["hazır"]


# ---------------------------------------------------------------- planner
def test_recovery_order_retry_alt_replan_giveup():
    p = RecoveryPlanner(max_retries=2)
    assert p.next(1, 0) == RETRY
    assert p.next(2, 0) == RETRY
    assert p.next(3, 2) == ALTERNATIVE       # yedek var → yedekle dene
    assert p.next(3, 0) == REPLAN            # yedek yok → üst kat
    assert p.next(5, 0) == GIVE_UP           # tavan — sonsuz döngü YOK


# ---------------------------------------------------------------- loop
def test_verified_loop_first_try_success():
    calls = []

    def exec_fn(t):
        calls.append(t)
        return True

    loop = VerifiedActionLoop(
        exec_fn, lambda: Observation("dom", 0.0, {"dom_changed": True}),
        ExpectedState(dom_changed=True), targets=["#btn"])
    out = loop.run()
    assert out["state"] == "VERIFIED" and out["attempts"] == 1
    assert calls == ["#btn"]


def test_verified_loop_retry_then_success():
    n = {"i": 0}

    def obs():
        n["i"] += 1
        # ilk gözlem: değişiklik yok; ikinci: değişti (gerçek UI gecikmesi)
        return Observation("dom", 0.0, {"dom_changed": n["i"] >= 2})

    loop = VerifiedActionLoop(lambda t: True, obs,
                              ExpectedState(dom_changed=True),
                              targets=["#btn"])
    out = loop.run()
    assert out["state"] == "VERIFIED" and out["attempts"] == 2


def test_verified_loop_alternative_grounding_then_replan():
    exec_log = []
    loop = VerifiedActionLoop(
        lambda t: exec_log.append(t) or True,
        lambda: Observation("dom", 0.0, {"dom_changed": False}),
        ExpectedState(dom_changed=True),
        targets=["#ana", "#yedek", "#ucuncu"])
    out = loop.run()
    assert out["state"] == "REPLAN"           # hiçbiri doğrulanamadı
    # her hedef retry-limiti kadar denendi, sonra yedeğe geçildi
    assert exec_log.count("#ana") == 3 and exec_log.count("#yedek") == 3
    assert exec_log[-1] == "#ucuncu"          # son hedef REPLAN üretti
    assert len(exec_log) <= 12                # global tavan


def test_verified_loop_giveup_not_infinite():
    count = {"n": 0}

    def exec_fn(t):
        count["n"] += 1
        return False                          # hep başarısız

    loop = VerifiedActionLoop(exec_fn,
                              lambda: Observation("x", 0.0, {}),
                              ExpectedState(dom_changed=True),
                              targets=["#tek"])
    out = loop.run()
    assert count["n"] <= 5                    # üst sınır — sonsuz YOK
    assert out["state"] in (REPLAN, GIVE_UP)


def test_loop_reports_log_with_details():
    loop = VerifiedActionLoop(lambda t: True,
                              lambda: Observation("d", 0.0,
                                                  {"dom_changed": True}),
                              ExpectedState(dom_changed=True),
                              targets=["#x"])
    loop.run()
    entry = loop.log[0]
    assert {"target", "attempt", "ok", "detail"} <= set(entry)
