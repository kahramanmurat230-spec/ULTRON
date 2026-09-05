def ocr_image(path):
    try:
        import pytesseract
        from PIL import Image
    except ImportError as e:
        raise RuntimeError("pytesseract and Pillow are required for OCR.") from e
    return pytesseract.image_to_string(Image.open(path))
