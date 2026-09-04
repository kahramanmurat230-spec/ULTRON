"""Real Browser Agent — Playwright-backed, worker-thread driven.

BROWSER -> PAGE -> DOM -> TARGET -> ACTION -> OBSERVE -> VERIFY.

Production uses a real Playwright browser. If no browser can launch, the
agent raises an honest RuntimeError; it never fabricates browser results.
State-changing operations remain MEDIUM risk and are protected by the
existing executor/approval gate.
"""
import os
import queue
import threading
import time
from pathlib import Path

DEFAULT_TIMEOUT_S = 20.0
SAFE_ACTIONS = {"navigate", "read_text", "read_dom", "find_elements",
                "screenshot", "verify", "wait_for", "title", "url"}
STATE_CHANGING = {"click", "type", "select", "scroll", "press_enter",
                  "submit", "new_tab", "switch_tab", "close_tab", "back", "forward"}
BROWSER_ACTIONS = SAFE_ACTIONS | STATE_CHANGING


def browser_action_risk(action: str) -> str:
    a = (action or "").lower().strip()
    if a in SAFE_ACTIONS:
        return "SAFE"
    if a in STATE_CHANGING:
        return "MEDIUM"
    return "UNKNOWN"


def normalize_url(url: str) -> str:
    """Accept http(s), file and about:blank; add https:// to bare hosts."""
    u = (url or "").strip()
    if not u:
        raise ValueError("empty url")
    if u.startswith(("http://", "https://", "file://", "about:blank")):
        return u
    if " " in u or not u.split("/")[0].replace(".", "").replace("-", "").replace("_", "").isalnum():
        raise ValueError(f"unsupported url scheme: {u[:60]}")
    return "https://" + u


def _default_engine(headless: bool, channel: str | None,
                    executable: str | None = None, extra_args=None):
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    args = list(extra_args or [])
    try:
        if os.geteuid() == 0:
            args.append("--no-sandbox")
    except Exception:
        pass
    attempts = []
    if executable:
        attempts.append(("executable:" + executable,
                         dict(headless=headless, executable_path=executable, args=args)))
    for ch in ([channel] if channel else []) + [None, "chrome", "msedge"]:
        attempts.append((ch or "bundled-chromium",
                         dict(headless=headless, args=args, **({"channel": ch} if ch else {}))))
    errors = []
    seen = set()
    for name, kw in attempts:
        if name in seen:
            continue
        seen.add(name)
        try:
            return pw.chromium.launch(**kw), pw
        except Exception as exc:
            errors.append(f"{name}: {str(exc)[:120]}")
    try:
        pw.stop()
    except Exception:
        pass
    raise RuntimeError(
        "Tarayıcı başlatılamadı (Playwright). Kurulum: `python -m playwright install chromium`, "
        "sistem Chrome/Edge ya da ULTRON_BROWSER_EXECUTABLE=<path>. Denenen: "
        + " | ".join(errors))


class _Worker:
    """Single dedicated browser thread; Playwright sync API is thread-bound."""
    def __init__(self):
        self.q: queue.Queue = queue.Queue()
        self.thread = threading.Thread(target=self._loop, daemon=True, name="ultron-browser")
        self.thread.start()

    def _loop(self):
        while True:
            fn, fut = self.q.get()
            if fn is None:
                self.q.task_done()
                return
            try:
                fut["result"] = fn()
            except BaseException as exc:
                fut["error"] = exc
            finally:
                fut["done"] = True
                self.q.task_done()

    def submit(self, fn, timeout_s: float):
        fut = {"done": False, "result": None, "error": None}
        self.q.put((fn, fut))
        deadline = time.time() + timeout_s
        while not fut["done"]:
            if time.time() > deadline:
                raise TimeoutError(f"browser action timeout ({timeout_s}s)")
            time.sleep(0.01)
        if fut["error"] is not None:
            raise fut["error"]
        return fut["result"]

    def stop(self):
        self.q.put((None, {"done": True}))


class BrowserAgent:
    def __init__(self, settings=None, audit=None, engine_factory=None,
                 downloads_dir="data/downloads"):
        bs = (settings or {}).get("browser", {})
        self.headless = bool(bs.get("headless", True))
        self.channel = os.environ.get("ULTRON_BROWSER_CHANNEL", bs.get("channel"))
        self.executable = os.environ.get("ULTRON_BROWSER_EXECUTABLE", bs.get("executable_path"))
        self.default_timeout = float(bs.get("timeout_seconds", DEFAULT_TIMEOUT_S))
        self.audit = audit
        self.engine_factory = engine_factory or (
            lambda: _default_engine(self.headless, self.channel, self.executable))
        self.downloads_dir = Path(downloads_dir)
        self.downloads_dir.mkdir(parents=True, exist_ok=True)
        self._worker = _Worker()
        self._browser = None
        self._pw = None
        self._pages: list = []
        self._current = -1

    def _ensure(self):
        if self._browser is None:
            def _launch():
                browser, pw = self.engine_factory()
                context = browser.new_context(accept_downloads=True)
                page = context.new_page()
                return browser, pw, context, page
            self._browser, self._pw, self._context, page = self._worker.submit(
                _launch, self.default_timeout * 3)
            self._pages = [page]
            self._current = 0
            self._log("LAUNCH", f"channel={self.channel or 'auto'}")
        return self._pages[self._current]

    def close(self):
        if self._browser is not None:
            try:
                self._worker.submit(lambda: self._browser.close(), 10)
            except Exception:
                pass
            try:
                self._worker.submit(self._pw.stop, 5)
            except Exception:
                pass
            self._browser = None
            self._pages = []
            self._current = -1
        self._worker.stop()

    def _log(self, event, detail=""):
        if self.audit:
            try:
                self.audit.write(f"BROWSER_{event}", detail)
            except Exception:
                pass

    def _act(self, action: str, fn, timeout_s: float | None = None):
        if action not in BROWSER_ACTIONS:
            raise ValueError(f"unknown browser action: {action}")
        page = self._ensure()
        t0 = time.time()
        try:
            out = self._worker.submit(lambda: fn(page), timeout_s or self.default_timeout)
            self._log("ACTION_OK", f"{action} in {time.time() - t0:.2f}s")
            return out
        except Exception as exc:
            self._log("ACTION_ERROR", f"{action}: {str(exc)[:160]}")
            raise

    @staticmethod
    def _target(page, target: str):
        """Resolve explicit CSS/text/role/label targets without guessing."""
        if not isinstance(target, str) or not target.strip():
            raise ValueError("target required")
        t = target.strip()
        if t.startswith("role="):
            spec = t[5:].strip()
            if not spec:
                raise ValueError("role target missing role")
            if "[name=" in spec and spec.endswith("]"):
                role, rest = spec.split("[name=", 1)
                name = rest[:-1].strip().strip("\"'")
                return page.get_by_role(role.strip(), name=name)
            return page.get_by_role(spec)
        if t.startswith("text="):
            return page.get_by_text(t[5:])
        if t.startswith("label="):
            return page.get_by_label(t[6:])
        if t.startswith("placeholder="):
            return page.get_by_placeholder(t[12:])
        return page.locator(t)

    def navigate(self, url: str) -> dict:
        u = normalize_url(url)
        def op(page):
            resp = page.goto(u, wait_until="domcontentloaded", timeout=int(self.default_timeout * 1000))
            return {"url": page.url, "title": page.title(), "status": resp.status if resp else None}
        return self._act("navigate", op)

    def title(self) -> str:
        return self._act("title", lambda page: page.title())

    def url(self) -> str:
        return self._act("url", lambda page: page.url)

    def read_text(self, selector: str | None = None, limit: int = 5000) -> dict:
        def op(page):
            loc = self._target(page, selector) if selector else page.locator("body")
            txt = loc.first.inner_text(timeout=int(self.default_timeout * 1000))
            return {"text": txt[:max(0, int(limit))], "url": page.url}
        return self._act("read_text", op)

    def read_dom(self, selector: str = "body", limit: int = 20000) -> dict:
        def op(page):
            html = self._target(page, selector).first.inner_html(timeout=int(self.default_timeout * 1000))
            return {"html": html[:max(0, int(limit))], "url": page.url}
        return self._act("read_dom", op)

    def find_elements(self, selector: str | None = None, text: str | None = None,
                      role: str | None = None, name: str | None = None,
                      limit: int = 10) -> list[dict]:
        def op(page):
            if role:
                loc = page.get_by_role(role, name=name) if name else page.get_by_role(role)
            elif text:
                loc = page.get_by_text(text)
            elif selector:
                loc = self._target(page, selector)
            else:
                raise ValueError("selector, text or role required")
            n = min(max(0, int(loc.count())), max(0, int(limit)))
            out = []
            for i in range(n):
                el = loc.nth(i)
                try:
                    box = el.bounding_box()
                except Exception:
                    box = None
                out.append({"index": i,
                            "tag": el.evaluate("e => e.tagName.toLowerCase()"),
                            "text": (el.inner_text() or "")[:120],
                            "id": el.get_attribute("id"),
                            "name": el.get_attribute("name"),
                            "aria": el.get_attribute("aria-label"),
                            "rect": box})
            return out
        return self._act("find_elements", op)

    def click(self, selector: str) -> dict:
        def op(page):
            self._target(page, selector).first.click(timeout=int(self.default_timeout * 1000))
            return {"clicked": selector}
        return self._act("click", op)

    def type(self, selector: str, text: str, press_enter: bool = False) -> dict:
        def op(page):
            loc = self._target(page, selector).first
            loc.fill(text, timeout=int(self.default_timeout * 1000))
            if press_enter:
                loc.press("Enter")
            return {"typed": len(text), "selector": selector, "submitted": bool(press_enter)}
        return self._act("type", op)

    def press_enter(self, selector: str) -> dict:
        return self._act("press_enter", lambda page: (self._target(page, selector).first.press("Enter"),
                                                        {"pressed": "Enter", "selector": selector})[1])

    def submit(self, selector: str | None = None) -> dict:
        def op(page):
            if selector:
                loc = self._target(page, selector).first
                tag = (loc.evaluate("e => e.tagName.toLowerCase()") or "").lower()
                if tag == "form":
                    loc.evaluate("e => e.requestSubmit()")
                else:
                    loc.press("Enter")
                return {"submitted": selector, "method": "requestSubmit" if tag == "form" else "Enter"}
            forms = page.locator("form")
            if forms.count() != 1:
                raise ValueError("submit without selector requires exactly one form")
            forms.first.evaluate("e => e.requestSubmit()")
            return {"submitted": "form", "method": "requestSubmit"}
        return self._act("submit", op)

    def select(self, selector: str, value: str) -> dict:
        def op(page):
            self._target(page, selector).first.select_option(value, timeout=int(self.default_timeout * 1000))
            return {"selected": value, "selector": selector}
        return self._act("select", op)

    def scroll(self, dy: int = 600) -> dict:
        return self._act("scroll", lambda page: (page.mouse.wheel(0, int(dy)), {"scrolled": int(dy)})[1])

    def back(self) -> dict:
        return self._act("back", lambda page: (page.go_back(timeout=int(self.default_timeout * 1000)), {"ok": True})[1])

    def forward(self) -> dict:
        return self._act("forward", lambda page: (page.go_forward(timeout=int(self.default_timeout * 1000)), {"ok": True})[1])

    def new_tab(self, url: str | None = None) -> dict:
        def op(page):
            p = self._context.new_page()
            self._pages.append(p)
            self._current = len(self._pages) - 1
            if url:
                p.goto(normalize_url(url), wait_until="domcontentloaded", timeout=int(self.default_timeout * 1000))
            return {"tab": self._current, "url": p.url}
        return self._act("new_tab", op)

    def switch_tab(self, index: int) -> dict:
        def op(_page):
            idx = int(index)
            if not 0 <= idx < len(self._pages):
                raise ValueError(f"tab {idx} yok (0..{len(self._pages) - 1})")
            self._current = idx
            return {"tab": idx, "url": self._pages[idx].url}
        return self._act("switch_tab", op)

    def close_tab(self, index: int | None = None) -> dict:
        def op(_page):
            idx = self._current if index is None else int(index)
            if not 0 <= idx < len(self._pages):
                raise ValueError(f"tab {idx} yok")
            if len(self._pages) <= 1:
                raise ValueError("tek sekme kapatılamaz")
            self._pages[idx].close()
            del self._pages[idx]
            self._current = min(self._current, len(self._pages) - 1)
            return {"tabs": len(self._pages), "current": self._current}
        return self._act("close_tab", op)

    def screenshot(self, path: str | None = None) -> dict:
        def op(page):
            target = str(Path(path) if path else self.downloads_dir / f"browser_{int(time.time())}.png")
            page.screenshot(path=target, timeout=int(self.default_timeout * 1000))
            return {"path": target, "bytes": Path(target).stat().st_size}
        return self._act("screenshot", op)

    def wait_for(self, selector: str | None = None, text: str | None = None,
                 timeout_s: float | None = None) -> dict:
        tmo = int((timeout_s or self.default_timeout) * 1000)
        def op(page):
            if selector:
                self._target(page, selector).first.wait_for(timeout=tmo)
                return {"found": selector}
            if text:
                page.get_by_text(text).first.wait_for(timeout=tmo)
                return {"found": text}
            raise ValueError("selector or text required")
        return self._act("wait_for", op)

    def verify(self, url_contains: str | None = None, title_contains: str | None = None,
               selector_exists: str | None = None, text_contains: str | None = None) -> dict:
        """Post-action verification: OBSERVE the page and check all expectations."""
        def op(page):
            checks = {}
            if url_contains is not None:
                checks["url_contains"] = url_contains.lower() in page.url.lower()
            if title_contains is not None:
                checks["title_contains"] = title_contains.lower() in page.title().lower()
            if selector_exists is not None:
                checks["selector_exists"] = self._target(page, selector_exists).count() > 0
            if text_contains is not None:
                body = (page.inner_text("body") or "")[:20000]
                checks["text_contains"] = text_contains.lower() in body.lower()
            ok = all(checks.values()) if checks else False
            return {"ok": ok, "checks": checks, "url": page.url, "title": page.title()}
        return self._act("verify", op)
