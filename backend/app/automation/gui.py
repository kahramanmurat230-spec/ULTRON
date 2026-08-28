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
