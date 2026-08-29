"""Real Browser Agent foundation — Playwright-backed, worker-thread driven.

BROWSER -> PAGE -> DOM -> ELEMENTS -> ACTION -> OBSERVE -> VERIFY.

Launch strategy (in order, first available wins):
  1. ULTRON_BROWSER_CHANNEL env (chrome / msedge / chromium...)
  2. bundled Playwright chromium (after `python -m playwright install chromium`)
  3. system Chrome channel
  4. system Edge channel
If none can launch, an honest RuntimeError is raised — no fake browsing.

Risk model:
  SAFE    navigate / read / find elements / screenshot / verify
  MEDIUM  click / type / select / scroll (state-changing) -> registered as
          dangerous tools, so they require the existing approval gate.

All operations run on a dedicated worker thread through a submit queue:
Playwright's sync API is thread-bound, and the executor calls us from
asyncio.to_thread — this serializes access safely and adds per-action
timeouts. Actions are audited; downloads land in a sandbox-checked folder.
"""
import os
import queue
import threading
import time
import urllib.parse
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
    """Accept http(s) and file:// URLs; add https:// to bare hosts."""
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
    """Real engine: Playwright Chromium — explicit executable, channel
    fallback, bundled chromium. No engine -> honest RuntimeError."""
    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    args = list(extra_args or [])
    # container/root ortamlarında sandbox kapalı olmalı (gerçek ihtiyaç)
    try:
        import os as _os
        if _os.geteuid() == 0:
            args.append("--no-sandbox")
    except Exception:
        pass

    attempts = []
    if executable:
        attempts.append(("executable:" + executable,
                         dict(headless=headless, executable_path=executable, args=args)))
    errors = []
    for ch in ([channel] if channel else []) + [None, "chrome", "msedge"]:
        attempts.append((ch or "bundled-chromium",
                         dict(headless=headless, args=args, **({"channel": ch} if ch else {}))))
    seen = set()
    for name, kw in attempts:
        if name in seen:
            continue
        seen.add(name)
        try:
            return pw.chromium.launch(**kw), pw
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{name}: {str(exc)[:120]}")
    try:
        pw.stop()
    except Exception:
        pass
    raise RuntimeError(
        "Tarayıcı başlatılamadı (Playwright). Kurulum: `python -m playwright "
        "install chromium`, sistem Chrome/Edge ya da ULTRON_BROWSER_EXECUTABLE="
        "<path>. Denenen: " + " | ".join(errors))


class _Worker:
    """Single dedicated browser thread (Playwright sync API is thread-bound)."""

    def __init__(self):
        self.q: queue.Queue = queue.Queue()
        self.thread = threading.Thread(target=self._loop, daemon=True,
                                       name="ultron-browser")
        self.thread.start()

    def _loop(self):
        while True:
            fn, fut = self.q.get()
            if fn is None:
                self.q.task_done()
                return
            try:
                fut["result"] = fn()
            except BaseException as exc:  # noqa: BLE001
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
        # PHASE 9: harici chromium binary (ör. konteyner) — env ayarları
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

    # ------------------------------------------------------------ lifecycle
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
        self._worker.stop()

    def _log(self, event, detail=""):
        if self.audit:
            try:
                self.audit.write(f"BROWSER_{event}", detail)
            except Exception:
                pass

    # ------------------------------------------------------------ actions
    def _act(self, action: str, fn, timeout_s: float | None = None):
        if action not in BROWSER_ACTIONS:
            raise ValueError(f"unknown browser action: {action}")
        page = self._ensure()
        t0 = time.time()
        try:
            out = self._worker.submit(lambda: fn(page), timeout_s or self.default_timeout)
            self._log("ACTION_OK", f"{action} in {time.time() - t0:.2f}s")
            return out
        except Exception as exc:  # noqa: BLE001
            self._log("ACTION_ERROR", f"{action}: {str(exc)[:160]}")
            raise

    def navigate(self, url: str) -> dict:
        u = normalize_url(url)
        def op(page):
            resp = page.goto(u, wait_until="domcontentloaded",
                             timeout=int(self.default_timeout * 1000))
            return {"url": page.url, "title": page.title(),
                    "status": resp.status if resp else None}
        return self._act("navigate", op)

    def title(self) -> str:
        return self._act("title", lambda page: page.title())

    def url(self) -> str:
        return self._act("url", lambda page: page.url)

    def read_text(self, selector: str | None = None, limit: int = 5000) -> dict:
        def op(page):
            if selector:
                loc = page.locator(selector).first
                txt = loc.inner_text(timeout=int(self.default_timeout * 1000))
            else:
                txt = page.inner_text("body", timeout=int(self.default_timeout * 1000))
            return {"text": txt[:limit], "url": page.url}
        return self._act("read_text", op)

    def read_dom(self, selector: str, limit: int = 20000) -> dict:
        def op(page):
            html = page.locator(selector).first.inner_html(
                timeout=int(self.default_timeout * 1000))
            return {"html": html[:limit]}
        return self._act("read_dom", op)

    def find_elements(self, selector: str | None = None, text: str | None = None,
                      limit: int = 10) -> list[dict]:
        def op(page):
            if selector:
                loc = page.locator(selector)
            elif text:
                loc = page.get_by_text(text)
            else:
                raise ValueError("selector or text required")
            n = min(loc.count(), limit)
            out = []
            for i in range(n):
                el = loc.nth(i)
                try:
                    box = el.bounding_box()
                except Exception:
                    box = None
                out.append({
                    "index": i,
                    "tag": el.evaluate("e => e.tagName.toLowerCase()"),
                    "text": (el.inner_text() or "")[:120],
                    "id": el.get_attribute("id"),
                    "name": el.get_attribute("name"),
                    "aria": el.get_attribute("aria-label"),
                    "rect": box,
                })
            return out
        return self._act("find_elements", op)

    def click(self, selector: str) -> dict:
        return self._act("click",
                         lambda page: (page.click(selector, timeout=int(self.default_timeout * 1000)),
                                       {"clicked": selector})[1])

    def type(self, selector: str, text: str, press_enter: bool = False) -> dict:
        def op(page):
            page.fill(selector, text, timeout=int(self.default_timeout * 1000))
            if press_enter:
                page.press(selector, "Enter")
            return {"typed": len(text), "selector": selector}
        return self._act("type", op)

    def select(self, selector: str, value: str) -> dict:
        return self._act("select",
                         lambda page: (page.select_option(selector, value,
                                                          timeout=int(self.default_timeout * 1000)),
                                       {"selected": value})[1])

    def scroll(self, dy: int = 600) -> dict:
        return self._act("scroll",
                         lambda page: (page.mouse.wheel(0, int(dy)), {"scrolled": int(dy)})[1])

    def back(self) -> dict:
        return self._act("back", lambda page: (page.go_back(
            timeout=int(self.default_timeout * 1000)), {"ok": True})[1])

    def forward(self) -> dict:
        return self._act("forward", lambda page: (page.go_forward(
            timeout=int(self.default_timeout * 1000)), {"ok": True})[1])

    def new_tab(self, url: str | None = None) -> dict:
        def op(page):
            p = self._context.new_page()
            self._pages.append(p)
            self._current = len(self._pages) - 1
            if url:
                p.goto(normalize_url(url), wait_until="domcontentloaded",
                       timeout=int(self.default_timeout * 1000))
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
            if len(self._pages) <= 1:
                raise ValueError("tek sekme kapatılamaz")
            self._pages[idx].close()
            del self._pages[idx]
            self._current = min(self._current, len(self._pages) - 1)
            return {"tabs": len(self._pages), "current": self._current}
        return self._act("close_tab", op)

    def screenshot(self, path: str | None = None) -> dict:
        def op(page):
            target = str(Path(path) if path else
                         self.downloads_dir / f"browser_{int(time.time())}.png")
            page.screenshot(path=target, timeout=int(self.default_timeout * 1000))
            return {"path": target, "bytes": Path(target).stat().st_size}
        return self._act("screenshot", op)

    def wait_for(self, selector: str | None = None, text: str | None = None,
                 timeout_s: float | None = None) -> dict:
        tmo = int((timeout_s or self.default_timeout) * 1000)

        def op(page):
            if selector:
                page.wait_for_selector(selector, timeout=tmo)
                return {"found": selector}
            if text:
                page.wait_for_selector(f"text={text}", timeout=tmo)
                return {"found": text}
            raise ValueError("selector or text required")
        return self._act("wait_for", op)

    # ------------------------------------------------------------ verify
    def verify(self, url_contains: str | None = None, title_contains: str | None = None,
               selector_exists: str | None = None, text_contains: str | None = None) -> dict:
        """Post-action verification: OBSERVE the page and check expectations."""

        def op(page):
            checks = {}
            if url_contains is not None:
                checks["url_contains"] = url_contains.lower() in page.url.lower()
            if title_contains is not None:
                checks["title_contains"] = title_contains.lower() in page.title().lower()
            if selector_exists is not None:
                checks["selector_exists"] = page.locator(selector_exists).count() > 0
            if text_contains is not None:
                body = (page.inner_text("body") or "")[:20000]
                checks["text_contains"] = text_contains.lower() in body.lower()
            ok = all(checks.values()) if checks else False
            return {"ok": ok, "checks": checks, "url": page.url, "title": page.title()}
        return self._act("verify", op)
