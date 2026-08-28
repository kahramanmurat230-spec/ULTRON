import webbrowser
from urllib.parse import quote_plus

def search_web(query):
    url = "https://www.google.com/search?q=" + quote_plus(query)
    webbrowser.open(url)
    return f"Web araması açıldı: {url}"
