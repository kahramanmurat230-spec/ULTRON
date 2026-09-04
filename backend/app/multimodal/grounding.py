
def _color_close(a: tuple, b: tuple, tol: int = 90) -> bool:
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5 <= tol


def dominant_rgb(image_bytes: bytes, bbox=None) -> tuple | None:
    """Görüntü (veya bbox bölgesi) baskın rengi — grounding için gerçek
    görüntü analizi (PIL); hata → None (tahmin yok)."""
    import io
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        if bbox:
            x, y, w, h = bbox
            img = img.crop((x, y, x + w, y + h))
        img = img.resize((8, 8))
        px = list(img.get_flattened_data())
        r = sum(p[0] for p in px) // len(px)
        g = sum(p[1] for p in px) // len(px)
        b = sum(p[2] for p in px) // len(px)
        return (r, g, b)
    except Exception:  # noqa: BLE001
        return None
