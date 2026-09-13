import re

CALC_PATTERNS = ("kaç", "hesapla", "topla", "çıkar", "çarp", "böl", "yüzde", "karekök", "karekoku")

# Shared by bridge AND V16 Agent._direct so NO entry point can route a vision
# request into the text-only chat path.
VISION_RE = re.compile(
    r"ekran\w*\s*(?:analiz|incele|ne\s+var|ne\s+görüyorsun|ne\s+acik|ne\s+açık)"
    r"|ekran\s+görüntüs\w*\s*(?:analiz|incele)"
    r"|analiz\s+et:\s*\S+\.(?:png|jpe?g)"
    r"|analyze\s+(?:my\s+)?screen"
    r"|ekranda\s+ne\s+(?:var|görüyorsun|açık)",
    re.I,
)

VISION_PROMPT = ("Ekranda ne görüyorsun? Açık uygulamalar, pencereler, butonlar, metinler ve hata "
                 "mesajları dahil gördüklerini Türkçe, doğal bir dille anlat.")

def vision_pattern(text: str) -> bool:
    return bool(VISION_RE.search(text or ""))

def _pure_math(text: str) -> bool:
    """True when the whole message is just an arithmetic expression (37×24, 2+2).

    Math requests must NEVER fall through to the browser/Google. Dots/commas
    alone (dates, versions) don't count — a real operator between two numbers
    is required. Unicode operators (× ÷) and digit-x-digit ("2x3") are accepted.
    """
    t = (text or "").lower().strip().rstrip("=?").strip()
    if not t or not re.fullmatch(r"[\d\s.,+\-*/%()×÷x]+", t):
        return False
    return bool(re.search(r"\d\s*[+\-*/%×÷x]\s*\d", t))

def classify(text: str) -> str:
    t = text.lower().strip()
    if any(x in t for x in ["ekran görüntüsü", "screenshot", "ekranı gör", "ekranımda ne var", "ekranımda", "ekrandaki", "ekranda ne var"]):
        return "screen"
    if any(x in t for x in ["kendini kontrol et", "kendini teşhis", "öz teşhis", "öz-teşhis", "self diagnostic", "self-diagnostic", "sistem teşhisi", "tam teşhis", "hata varsa bildir", "tüm modülleri kontrol"]):
        return "self_diagnostic"
    if any(x in t for x in ["cpu", "ram", "gpu", "disk", "sistem durumu", "bilgisayarımın durumu", "bilgisayarımın sistem"]):
        return "system_status"
    if any(x in t for x in ["kamera", "webcam", "web kamerası", "web kamerasi", "fotoğraf çek", "fotograf cek", "take a photo", "capture a photo", "open camera", "open the camera"]):
        return "camera"
    if any(x in t for x in CALC_PATTERNS) and re.search(r"\d", t):
        return "calculate"
    if _pure_math(t):
        return "calculate"
    if any(x in t for x in ["ollama", "model bağlantısı", "model baglantisi"]):
        return "ollama_status"
    if any(x in t for x in ["işlem geçmiş", "son işlemler", "audit log", "hata kayıt", "hata log"]):
        return "audit"
    if any(x in t for x in ["modüllerin", "modüllerin aktif", "hangi araçlara", "hangi araçlar", "hangi modelle"]):
        return "capabilities"
    if any(x in t for x in ["indirilenler klasörü", "masaüstümde", "dosyaları listele", "dosyaları bul", "dosya bul", "dosyayı bul", "klasörde ara", "ultron projesini bul", "ultron projesi"]):
        return "file_task"
    if any(x in t for x in ["testleri çalıştır", "test çalıştır", "test sonuç", "test durumu"]):
        return "run_tests"
    if any(x in t for x in ["duyuyor musun", "beni duyuyor"]):
        return "hearing"
    if any(x in t for x in ["sesli cevap", "sesli olarak", "sesli söyle"]):
        return "speak"
    if any(x in t for x in ["sistemi izlemeye başla", "arka planda", "önemli bir değişiklik", "kendiliğinden uyar"]):
        return "proactive"
    if any(x in t for x in ["chrome", "edge", "notepad", "hesap makinesi", "calculator", "discord", "spotify",
                            "firefox", "opera", "tarayıcı", "tarayici", "not defteri", "not defterini"]):
        return "open_app"
    if any(x in t for x in ["google'da", "google da", "google'de", "google de", "web'de", "internette ara", "arama yap"]):
        return "web_search"
    if any(x in t for x in ["sil", "kalıcı olarak kaldır"]):
        return "destructive"
    return "conversation"
