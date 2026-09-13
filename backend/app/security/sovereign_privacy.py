"""Sovereign Privacy Shield — local-first audit + architectural cloud block."""
import re,time
_LOCAL_HOSTS=("127.0.0.1","localhost","::1","0.0.0.0")
LOCAL_TTS_BACKENDS=("kokoro","pykokoro")
state={"sovereign_mode":False,"denied":[]}
class SovereignViolation(Exception): pass
def configure(settings:dict)->None:
    # Do not accidentally disable an already-enforced global shield when a
    # nested component passes partial settings (e.g. Brain's llm-only config).
    if "sovereign_mode" in settings:
        state["sovereign_mode"]=bool(settings.get("sovereign_mode"))
def is_local(endpoint:str)->bool:
    if not endpoint:return True
    if endpoint.startswith(("/",".")):return True
    m=re.match(r"(?:https?://)?([^/:]+)",endpoint); host=(m.group(1) if m else endpoint).lower()
    return host in _LOCAL_HOSTS or host.endswith(".local")
def assert_local(endpoint:str)->None:
    if state["sovereign_mode"] and not is_local(endpoint):
        state["denied"].append({"ts":time.time(),"endpoint":endpoint}); msg=f"Sovereign mode aktif, dış çağrı reddedildi: {endpoint}"; print(f"[ULTRON-SOVEREIGN] {msg}",flush=True); raise SovereignViolation(msg)
def assert_vision_residency(dest:str)->None:
    if not is_local(dest):
        msg=f"Sovereign: ekran verisi yerel dışına çıkamaz: {dest}"; print(f"[ULTRON-SOVEREIGN] {msg}",flush=True); raise SovereignViolation(msg)
CLOUD_TTS=("edge","azure","google","amazon","polly","openai","eleven")
def _tts_is_local(backend:str|None)->bool:
    if not backend:return False
    return not any(c in backend.lower() for c in CLOUD_TTS)
def audit(llm_host:str,tts_backend:str|None,stt_local:bool,ollama_connected:bool)->dict:
    llm_local=is_local(llm_host or ""); tts_local=bool(tts_backend) and tts_backend in LOCAL_TTS_BACKENDS and _tts_is_local(tts_backend)
    return {"sovereign_mode":state["sovereign_mode"],"llm":{"endpoint":llm_host,"local":llm_local,"status":"PASS" if llm_local and ollama_connected else ("LOCAL-BUT-OFFLINE" if llm_local else "FAIL")},"tts":{"backend":tts_backend,"local":tts_local,"status":"PASS" if tts_local else ("CLOUD" if tts_backend else "NONE")},"stt":{"local":bool(stt_local),"status":"PASS" if stt_local else "BROWSER-CLOUD-OR-ABSENT"},"denied_calls":state["denied"][-10:]}
def sovereign_status(a:dict)->str:
    if a["sovereign_mode"]:return "ENFORCED" if a["llm"]["local"] else "VIOLATED"
    return "LOCAL-READY" if a["llm"]["local"] and a["tts"]["local"] else "PARTIAL"
