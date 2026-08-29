"""Vision foundation — dependency-light, REAL image analysis (no LLM needed).

Everything here runs on Pillow + numpy and fails honestly when a required
binary (tesseract) is missing:

- analyze_image(): geometry, brightness/contrast, dominant colors, edge
  density, dark-mode likelihood — works on ANY image, headless included.
- diff_images(): pixel-level change ratio + changed-region count between
  two frames -> screen-change / motion detection for proactive layer.
- ocr_elements(): pytesseract image_to_data -> text elements with boxes
  and confidence, the bridge from "what's on screen" to clickable
  targets for GUI automation.
"""
from pathlib import Path

import numpy as np
from PIL import Image


class VisionFoundationError(RuntimeError):
    pass


def _load(path) -> Image.Image:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(str(p))
    try:
        return Image.open(p).convert("RGB")
    except Exception as exc:  # noqa: BLE001
        raise VisionFoundationError(f"görüntü okunamadı: {p} ({exc})") from exc


def analyze_image(path: str) -> dict:
    img = _load(path)
    arr = np.asarray(img)
    gray = arr.mean(axis=2)
    h, w = gray.shape
    # brightness / contrast
    brightness = float(gray.mean())
    contrast = float(gray.std())
    # dark-mode likelihood: both low absolute brightness and low channel spread
    is_dark = brightness < 90.0
    # edge density (simple gradient magnitude on a downsampled grid)
    small = gray[:: max(1, h // 200) or 1, :: max(1, w // 200) or 1]
    gx = np.abs(np.diff(small, axis=1)).mean() if small.shape[1] > 1 else 0.0
    gy = np.abs(np.diff(small, axis=0)).mean() if small.shape[0] > 1 else 0.0
    edge_density = float(gx + gy)
    # dominant colors via 4-bit quantization
    q = (arr // 16).reshape(-1, 3)
    colors, counts = np.unique(q, axis=0, return_counts=True)
    order = np.argsort(-counts)[:5]
    total = q.shape[0]
    dominant = [{"rgb_approx": [int(c[0]) * 16 + 8, int(c[1]) * 16 + 8, int(c[2]) * 16 + 8],
                 "share": round(float(counts[i]) / total, 3)}
                for i, c in zip(order, colors)]
    return {
        "path": str(path), "width": w, "height": h,
        "brightness": round(brightness, 1), "contrast": round(contrast, 1),
        "dark_mode_likely": bool(is_dark),
        "edge_density": round(edge_density, 2),
        "dominant_colors": dominant,
        "mostly_uniform": bool(contrast < 12.0),
        "aspect": round(w / h, 3) if h else None,
    }


def diff_images(path_a: str, path_b: str, pixel_threshold: float = 12.0,
                region_grid: int = 8) -> dict:
    """Compare two frames: change ratio + per-region change map."""
    a = _load(path_a)
    b = _load(path_b)
    if a.size != b.size:
        b = b.resize(a.size)
    ga = np.asarray(a.convert("L")).astype(np.int16)
    gb = np.asarray(b.convert("L")).astype(np.int16)
    delta = np.abs(ga - gb)
    changed = delta > pixel_threshold
    ratio = float(changed.mean())
    # region map (coarse where-changed)
    h, w = changed.shape
    rs = max(1, h // region_grid)
    cs = max(1, w // region_grid)
    regions = []
    for ri, r0 in enumerate(range(0, h, rs)):
        for ci, c0 in enumerate(range(0, w, cs)):
            cell = changed[r0:r0 + rs, c0:c0 + cs]
            if cell.size:
                share = float(cell.mean())
                if share > 0.05:
                    regions.append({"row": ri, "col": ci, "share": round(share, 2)})
    return {
        "a": str(path_a), "b": str(path_b),
        "changed_ratio": round(ratio, 4),
        "changed": bool(ratio > 0.01),
        "changed_regions": regions[:40],
        "n_changed_regions": len(regions),
    }


def ocr_elements(path: str, min_conf: int = 40) -> list[dict]:
    """Text elements with boxes via tesseract. Raises honestly if absent."""
    try:
        import pytesseract
    except ImportError as exc:
        raise VisionFoundationError(
            "pytesseract kurulu değil (pip install pytesseract + tesseract binary)") from exc
    img = _load(path)
    try:
        data = pytesseract.image_to_data(img, lang="tur+eng",
                                         output_type=pytesseract.Output.DICT)
    except pytesseract.TesseractNotFoundError as exc:
        raise VisionFoundationError(
            "tesseract binary bulunamadı (kurulum gerekli)") from exc
    out = []
    n = len(data.get("text", []))
    for i in range(n):
        txt = (data["text"][i] or "").strip()
        try:
            conf = float(data["conf"][i])
        except (ValueError, TypeError):
            continue
        if txt and conf >= min_conf:
            out.append({
                "text": txt[:80],
                "conf": round(conf, 1),
                "box": {"x": data["left"][i], "y": data["top"][i],
                        "w": data["width"][i], "h": data["height"][i]},
            })
    return out


# ------------------------------------------------------------ PHASE 7 additions
def screenshot_fresh(path: str, max_age_s: float = 20.0, now=None) -> dict:
    """Stale-screenshot protection: an action must not be based on an old frame.

    Verifies the file exists AND its mtime is within max_age_s. Returns
    {fresh, age_s, path}; never raises for staleness — the caller decides."""
    import time as _t
    p = Path(path)
    now = _t.time() if now is None else now
    if not p.exists():
        return {"fresh": False, "age_s": None, "path": str(p), "reason": "missing"}
    age = max(0.0, now - p.stat().st_mtime)
    fresh = age <= max_age_s
    return {"fresh": fresh, "age_s": round(age, 1), "path": str(p),
            "reason": None if fresh else f"screenshot {age:.0f}s old (limit {max_age_s}s)"}


def verify_visual_change(before: str, after: str, min_change: float = 0.002) -> dict:
    """Action verification for GUI/computer-use: did the screen actually change?

    Wraps diff_images with an honest verdict; identical screens after an
    action mean the action likely did nothing (or hit the wrong target)."""
    res = diff_images(before, after)
    changed = res["changed_ratio"] >= min_change
    return {"action_effective": changed,
            "changed_ratio": res["changed_ratio"],
            "regions": res["n_changed_regions"],
            "verdict": "changed" if changed else
                       "no visual change — action may have missed its target"}
