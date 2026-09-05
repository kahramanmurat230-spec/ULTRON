from pathlib import Path
from datetime import datetime

def capture_screen(path=None):
    try: from PIL import ImageGrab
    except ImportError as e: raise RuntimeError("Pillow is required for screenshots.") from e
    if path is None: path=Path("data/logs")/f"screen_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')[:-3]}.png"
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    image=ImageGrab.grab(all_screens=True); image.save(path); return str(path)

def screen_ocr(path=None):
    if path is None: path=capture_screen()
    try:
        import pytesseract
        from PIL import Image
        text=pytesseract.image_to_string(Image.open(path), lang="tur+eng")
        return {"path":str(path),"text":text.strip()}
    except Exception as e:
        return {"path":str(path),"text":"","error":str(e)}
