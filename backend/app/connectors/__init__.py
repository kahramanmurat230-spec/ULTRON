"""Connectors — external services with vault-backed credentials.

Rules honored here:
  - Credentials are NEVER hardcoded: they resolve from CredentialVault at
    call time (e.g. `openweathermap`). A connector without its credential
    reports a precise, honest error instead of pretending to work.
  - HTTP is stdlib urllib with hard timeouts and response size caps
    (sync on purpose: the tool executor runs tools on worker threads).
  - WeatherConnector: OpenWeatherMap (keyed) with wttr.in keyless fallback;
    base URLs are overridable for tests/proxies.
  - CalendarConnector: real .ics parsing (stdlib) from a configured
    directory; on Windows, an optional Outlook COM export when pywin32
    is installed. No fake events are ever generated.
"""
import json
import re
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

MAX_BYTES = 200_000
UA = {"User-Agent": "Ultron-Agent/16 (+local)"}


class ConnectorError(RuntimeError):
    pass


def _http_get(url: str, params: dict | None = None, timeout_s: float = 10.0) -> dict:
    if params:
        url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=timeout_s) as r:
            body = r.read(MAX_BYTES + 1).decode("utf-8", errors="replace")
            status = r.status
    except urllib.error.HTTPError as exc:
        return {"status": exc.code, "body": exc.read(MAX_BYTES).decode("utf-8", "replace") if exc.fp else ""}
    except Exception as exc:  # noqa: BLE001
        raise ConnectorError(f"network error: {str(exc)[:120]}") from exc
    if len(body) > MAX_BYTES:
        raise ConnectorError("response too large")
    return {"status": status, "body": body}


class WeatherConnector:
    """OpenWeatherMap (vault key `openweathermap`) + wttr.in fallback."""

    NAME = "weather"

    def __init__(self, vault=None, owm_base="https://api.openweathermap.org/data/2.5",
                 wttr_base="https://wttr.in"):
        self.vault = vault
        self.owm_base = owm_base.rstrip("/")
        self.wttr_base = wttr_base.rstrip("/")

    def health(self) -> dict:
        key = None
        if self.vault is not None:
            try:
                key = self.vault.get("openweathermap")
            except Exception:
                key = None
        return {"connector": self.NAME, "openweathermap": bool(key),
                "wttr_fallback": True}

    def current(self, city: str) -> dict:
        if not city or not re.match(r"^[\w\sçğıöşüÇĞİÖŞÜ\-,.]{1,64}$", city):
            raise ConnectorError(f"geçersiz şehir: {city!r}")
        key = None
        if self.vault is not None:
            try:
                key = self.vault.get("openweathermap")
            except Exception:
                key = None
        if key:  # primary: keyed API
            try:
                r = _http_get(f"{self.owm_base}/weather",
                              {"q": city, "appid": key, "units": "metric", "lang": "tr"})
                if r["status"] == 200:
                    d = json.loads(r["body"])
                    return {"source": "openweathermap", "city": d.get("name", city),
                            "temp_c": d["main"]["temp"], "feels_c": d["main"]["feels_like"],
                            "desc": (d.get("weather") or [{}])[0].get("description", ""),
                            "humidity": d["main"].get("humidity")}
                if r["status"] == 401:
                    raise ConnectorError("openweathermap anahtarı geçersiz (vault)")
            except ConnectorError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise ConnectorError(f"openweathermap hatası: {str(exc)[:120]}")
        # fallback: wttr.in (keyless, honest best-effort)
        try:
            r = _http_get(f"{self.wttr_base}/{urllib.parse.quote(city)}",
                          {"format": "j1"}, timeout_s=12.0)
            if r["status"] != 200:
                raise ConnectorError(f"wttr.in status {r['status']}")
            d = json.loads(r["body"])
            cur = d["current_condition"][0]
            desc = (cur.get("lang_tr") or [{}])[0].get("value") or \
                   (cur.get("weatherDesc") or [{}])[0].get("value", "")
            return {"source": "wttr.in", "city": city,
                    "temp_c": float(cur["temp_C"]), "feels_c": float(cur["FeelsLikeC"]),
                    "desc": desc, "humidity": int(cur.get("humidity", 0))}
        except ConnectorError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ConnectorError(
                f"hava durumu alınamadı (openweathermap key yok / ağ): {str(exc)[:120]}")

    def forecast(self, city: str) -> dict:
        """Tomorrow summary via wttr.in json (keyless)."""
        r = _http_get(f"{self.wttr_base}/{urllib.parse.quote(city)}",
                      {"format": "j1"}, timeout_s=12.0)
        if r["status"] != 200:
            raise ConnectorError(f"wttr.in status {r['status']}")
        d = json.loads(r["body"])
        w = d.get("weather", [])
        if not w:
            raise ConnectorError("tahmin verisi yok")
        tmr = w[1] if len(w) > 1 else w[0]
        humidity = None
        if tmr.get("hourly"):
            try:
                humidity = int(tmr["hourly"][4]["humidity"])
            except Exception:
                humidity = None
        return {"source": "wttr.in", "city": city, "date": tmr.get("date"),
                "min_c": float(tmr["mintempC"]), "max_c": float(tmr["maxtempC"]),
                "avg_humidity": humidity}


# ---------------------------------------------------------------- calendar
_ICS_EVT = re.compile(r"BEGIN:VEVENT(?P<body>.*?)END:VEVENT", re.S)
_ICS_FIELD = {
    "summary": re.compile(r"^SUMMARY[^:]*:(?P<v>.*)$", re.M),
    "dtstart": re.compile(r"^DTSTART[^:]*:(?P<v>.*)$", re.M),
    "dtend": re.compile(r"^DTEND[^:]*:(?P<v>.*)$", re.M),
    "location": re.compile(r"^LOCATION[^:]*:(?P<v>.*)$", re.M),
}


def _clean_ics_val(v: str) -> str:
    return v.strip().replace("\\,", ",").replace("\\n", " ")[:140]


def _parse_ics_dt(v: str) -> datetime | None:
    m = re.match(r"^(\d{4})(\d{2})(\d{2})(?:T(\d{2})(\d{2})(\d{2}))?", v.strip())
    if not m:
        return None
    y, mo, d, hh, mm, ss = m.groups()
    try:
        return datetime(int(y), int(mo), int(d), int(hh or 0), int(mm or 0), int(ss or 0))
    except ValueError:
        return None


class CalendarConnector:
    """Real .ics calendar. Sources: configured dir (default data/calendar),
    plus on Windows an Outlook COM export when pywin32 is available."""

    NAME = "calendar"

    def __init__(self, ics_dir="data/calendar", vault=None):
        self.ics_dir = Path(ics_dir)
        self.ics_dir.mkdir(parents=True, exist_ok=True)
        self.vault = vault

    def health(self) -> dict:
        files = list(self.ics_dir.glob("*.ics"))
        try:
            import win32com.client  # noqa: F401
            outlook = True
        except Exception:
            outlook = False
        return {"connector": self.NAME, "ics_files": len(files),
                "outlook_com_available": outlook}

    def _export_outlook(self) -> int:
        """Windows: export upcoming Outlook events to data/calendar/outlook.ics."""
        try:
            import win32com.client
        except Exception as exc:
            raise ConnectorError("pywin32 kurulu değil (Outlook export kapalı)") from exc
        outlook = win32com.client.Dispatch("Outlook.Application").GetNamespace("MAPI")
        cal = outlook.GetDefaultFolder(9)  # olFolderCalendar
        items = cal.Items
        items.Sort("[Start]")
        items.IncludeRecurrences = True
        horizon = (datetime.now() + timedelta(days=30)).strftime("%m/%d/%Y %H:%M")
        items.Restrict(f"[Start] <= '{horizon}'")
        lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Ultron//Calendar Export//TR"]
        n = 0
        for item in items:
            try:
                start = item.Start.Format("%Y%m%dT%H%M%S")
                end = item.End.Format("%Y%m%dT%H%M%S")
            except Exception:
                continue
            lines += ["BEGIN:VEVENT", f"SUMMARY:{str(item.Subject)[:140]}",
                      f"DTSTART:{start}", f"DTEND:{end}", "END:VEVENT"]
            n += 1
            if n >= 500:
                break
        lines.append("END:VCALENDAR")
        (self.ics_dir / "outlook.ics").write_text("\r\n".join(lines), encoding="utf-8")
        return n

    def events(self, days: int = 7, export_outlook: bool = True) -> list[dict]:
        """Upcoming events within `days`. Real parse — no fabricated events."""
        if export_outlook:
            try:
                self._export_outlook()
            except ConnectorError:
                pass  # honest: pywin32/Outlook absent in this environment
        now = datetime.now().replace(microsecond=0)
        horizon = now + timedelta(days=max(1, min(int(days), 60)))
        out: list[dict] = []
        for f in self.ics_dir.glob("*.ics"):
            try:
                text = f.read_text(encoding="utf-8", errors="replace")[:1_000_000]
            except OSError:
                continue
            for m in _ICS_EVT.finditer(text):
                body = m.group("body")
                vals = {}
                for k, rx in _ICS_FIELD.items():
                    mm = rx.search(body)
                    vals[k] = _clean_ics_val(mm.group("v")) if mm else ""
                start = _parse_ics_dt(vals["dtstart"])
                end = _parse_ics_dt(vals["dtend"]) or start
                if start is None:
                    continue
                if now <= start <= horizon:
                    out.append({"summary": vals["summary"] or "(başlıksız)",
                                "start": start.isoformat(timespec="minutes"),
                                "end": end.isoformat(timespec="minutes"),
                                "location": vals["location"], "source": f.name})
        out.sort(key=lambda e: e["start"])
        return out[:100]
