"""PHASE 6: vision foundation — real Pillow/numpy analysis on synthetic images."""
import os
import sys

import pytest
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.vision.analyze import VisionFoundationError, analyze_image, diff_images, ocr_elements


def white(path):
    Image.new("RGB", (200, 100), (245, 245, 245)).save(path)
    return str(path)


def dark(path):
    Image.new("RGB", (200, 100), (20, 22, 30)).save(path)
    return str(path)


def busy(path):
    img = Image.new("RGB", (300, 300), (250, 250, 250))
    d = ImageDraw.Draw(img)
    for i in range(0, 300, 10):
        d.line([(0, i), (300, i)], fill=(30, 30, 160), width=2)
        d.line([(i, 0), (i, 300)], fill=(160, 30, 30), width=2)
    img.save(path)
    return str(path)


def test_analyze_image_basic(tmp_path):
    res = analyze_image(white(tmp_path / "w.png"))
    assert res["width"] == 200 and res["height"] == 100
    assert res["brightness"] > 200 and res["dark_mode_likely"] is False
    assert res["mostly_uniform"] is True
    assert 0 < len(res["dominant_colors"]) <= 5


def test_analyze_image_dark_mode(tmp_path):
    res = analyze_image(dark(tmp_path / "d.png"))
    assert res["dark_mode_likely"] is True and res["brightness"] < 60


def test_analyze_image_busy_edges(tmp_path):
    res = analyze_image(busy(tmp_path / "b.png"))
    plain = analyze_image(white(tmp_path / "w2.png"))
    assert res["edge_density"] > plain["edge_density"]
    assert res["mostly_uniform"] is False


def test_analyze_image_dominant_color_ordering(tmp_path):
    path = tmp_path / "colors.png"
    img = Image.new("RGB", (100, 100), (240, 16, 16))
    for x in range(20):
        for y in range(100):
            img.putpixel((x, y), (16, 240, 16))
    img.save(path)
    top = analyze_image(str(path))["dominant_colors"][0]
    assert top["share"] == 0.8
    assert top["rgb_approx"][0] >= 224 and top["rgb_approx"][1] < 32


def test_analyze_missing_file():
    with pytest.raises(FileNotFoundError):
        analyze_image("/yok/boyle/dosya.png")


def test_diff_images_identical_vs_changed(tmp_path):
    a = white(tmp_path / "a.png")
    b = white(tmp_path / "b.png")
    same = diff_images(a, b)
    assert same["changed"] is False and same["changed_ratio"] < 0.01
    c = busy(tmp_path / "c.png")
    diff = diff_images(a, c)
    assert diff["changed"] is True and diff["changed_ratio"] > 0.3
    assert diff["n_changed_regions"] > 0


def test_diff_images_size_mismatch_handled(tmp_path):
    a = white(tmp_path / "a.png")
    img = Image.new("RGB", (100, 100), (10, 10, 10))
    img.save(tmp_path / "small.png")
    res = diff_images(a, str(tmp_path / "small.png"))
    assert res["changed"] is True


def test_ocr_elements_honest_without_tesseract(tmp_path):
    p = white(tmp_path / "x.png")
    try:
        import pytesseract  # noqa: F401
        import shutil
        if shutil.which("tesseract"):
            els = ocr_elements(p)
            assert isinstance(els, list)
            return
    except ImportError:
        pass
    with pytest.raises(VisionFoundationError):
        ocr_elements(p)


def test_screenshot_fresh(tmp_path):
    import time as t
    from app.vision.analyze import screenshot_fresh
    f = tmp_path / "shot.png"
    f.write_bytes(b"x")
    ok = screenshot_fresh(str(f), max_age_s=20.0)
    assert ok["fresh"] is True and ok["age_s"] < 5
    stale = screenshot_fresh(str(f), max_age_s=0.001, now=t.time() + 60)
    assert stale["fresh"] is False and "old" in stale["reason"]
    missing = screenshot_fresh(str(tmp_path / "yok.png"))
    assert missing["fresh"] is False and missing["reason"] == "missing"


def test_verify_visual_change_verdict(tmp_path):
    from app.vision.analyze import verify_visual_change
    a = tmp_path / "a.png"; b = tmp_path / "b.png"; c = tmp_path / "c.png"
    Image.new("RGB", (100, 100), (255, 255, 255)).save(a)
    Image.new("RGB", (100, 100), (255, 255, 255)).save(b)
    Image.new("RGB", (100, 100), (0, 0, 0)).save(c)
    same = verify_visual_change(str(a), str(b))
    assert same["action_effective"] is False and "no visual change" in same["verdict"]
    diff = verify_visual_change(str(a), str(c))
    assert diff["action_effective"] is True and diff["verdict"] == "changed"
