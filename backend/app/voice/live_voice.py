import threading
class LiveVoice:
    def __init__(self,agent,tts,settings):
        self.agent=agent; self.tts=tts; self.settings=settings; self.running=False; self.thread=None
        self.sample_rate=int(settings.get('voice',{}).get('sample_rate',16000)); self.model_name=settings.get('voice',{}).get('stt_model','small'); self.wake_word=settings.get('wake_word','ultron').lower(); self._model=None
    def _listen_once(self):
        import sounddevice as sd, numpy as np
        from faster_whisper import WhisperModel
        if self._model is None: self._model=WhisperModel(self.model_name,device='auto',compute_type='int8')
        model=self._model; duration=int(self.settings.get('voice',{}).get('listen_seconds',8))
        audio=sd.rec(duration*self.sample_rate,samplerate=self.sample_rate,channels=1,dtype='float32'); sd.wait(); seg,_=model.transcribe(np.squeeze(audio),language='tr'); return ' '.join(s.text for s in seg).strip()
    def run(self):
        self.running=True
        while self.running:
            try: text=self._listen_once()
            except Exception: break
            if not text or self.wake_word not in text.lower(): continue
            command=text.lower().split(self.wake_word,1)[1].strip(' ,.:;-')
            if not command: continue
            answer=self.agent.handle(command)
            try:self.tts.speak(answer)
            except Exception:pass
    def start(self):
        if self.running:return
        self.thread=threading.Thread(target=self.run,daemon=True); self.thread.start()
    def stop(self): self.running=False
