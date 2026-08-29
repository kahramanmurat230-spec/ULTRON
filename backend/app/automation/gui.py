import time
class GUIAutomation:
    def _pyautogui(self):
        try: import pyautogui; return pyautogui
        except ImportError as e: raise RuntimeError('GUI otomasyonu için pyautogui kurulmalı.') from e
    def click(self,x,y): p=self._pyautogui(); p.click(int(x),int(y)); return {'clicked':[int(x),int(y)]}
    def double_click(self,x,y): p=self._pyautogui(); p.doubleClick(int(x),int(y)); return {'double_clicked':[int(x),int(y)]}
    def right_click(self,x,y): p=self._pyautogui(); p.rightClick(int(x),int(y)); return {'right_clicked':[int(x),int(y)]}
    def scroll(self,amount,x=None,y=None): p=self._pyautogui(); p.scroll(int(amount),None if x is None else int(x),None if y is None else int(y)); return {'scrolled':int(amount)}
    def move(self,x,y): p=self._pyautogui(); p.moveTo(int(x),int(y),duration=.15); return {'moved':[int(x),int(y)]}
    def type_text(self,text): p=self._pyautogui(); p.write(str(text),interval=.01); return {'typed':len(str(text))}
    def press(self,key): p=self._pyautogui(); p.press(str(key)); return {'pressed':key}
    def hotkey(self,keys): p=self._pyautogui(); p.hotkey(*[str(k) for k in keys]); return {'hotkey':keys}
    def locate_and_click(self,image,confidence=.8):
        p=self._pyautogui(); box=p.locateOnScreen(image,confidence=float(confidence))
        if not box: return {'found':False}
        x,y=p.center(box); p.click(x,y); time.sleep(.15); return {'found':True,'clicked':[x,y]}
    def _pygetwindow(self):
        try: import pygetwindow as gw; return gw
        except ImportError as e: raise RuntimeError('Pencere yönetimi için pygetwindow kurulmalı.') from e
    def find_window(self,title):
        gw=self._pygetwindow()
        hits=[w.title for w in gw.getWindowsWithTitle(str(title)) if w.title]
        return {'found':len(hits)>0,'windows':hits[:5]}
    def focus_window(self,title):
        gw=self._pygetwindow()
        ws=gw.getWindowsWithTitle(str(title))
        if not ws: return {'focused':False}
        w=ws[0]
        try:
            if w.isMinimized: w.restore()
            w.activate()
        except Exception as e:
            return {'focused':False,'error':str(e)}
        return {'focused':True,'title':w.title}

    # ---- PHASE 7: vision-anchored computer use (OCR -> click) ----
    @staticmethod
    def find_text_in_elements(elements, text):
        """Pure matcher: best OCR element for a text label (testable headless)."""
        t = str(text).strip().lower()
        best = None
        for el in elements or []:
            label = str(el.get("text", "")).strip().lower()
            if not label or t != label and t not in label:
                continue
            score = (2 if label == t else 1) * 100 + float(el.get("conf", 0))
            if best is None or score > best[0]:
                best = (score, el)
        return best[1] if best else None

    def read_screen_elements(self):
        """Ekranı okur ve OCR ile tıklanabilir metin öğelerini döner (SAFE)."""
        from app.vision.screen import capture_screen
        from app.vision.analyze import ocr_elements
        shot = capture_screen()
        els = ocr_elements(shot)
        return {"path": shot, "count": len(els), "elements": els[:60]}

    def click_text(self, text, settle_s=0.15, verify: bool = True):
        """OCR ile bulduğu metne tıklar (dangerous): görüntü -> hedef -> eylem -> VERIFY."""
        from app.vision.analyze import screenshot_fresh, verify_visual_change
        info = self.read_screen_elements()
        fresh = screenshot_fresh(info["path"], max_age_s=20.0)
        if not fresh["fresh"]:
            return {"found": False, "text": text, "screen": info["path"],
                    "stale_screenshot": fresh}  # eski kareye tıklanmaz
        el = self.find_text_in_elements(info["elements"], text)
        if el is None:
            return {"found": False, "text": text, "screen": info["path"]}
        b = el["box"]
        x = b["x"] + b["w"] // 2
        y = b["y"] + b["h"] // 2
        self.click(x, y)
        time.sleep(settle_s)
        out = {"found": True, "clicked": [x, y], "matched": el["text"],
               "conf": el["conf"]}
        if verify:
            try:
                from app.vision.screen import capture_screen
                after = capture_screen()
                out["verification"] = verify_visual_change(info["path"], after)
            except Exception as exc:  # noqa: BLE001
                out["verification"] = {"action_effective": None,
                                       "error": str(exc)[:120]}  # dürüst: doğrulanamadı
        return out
