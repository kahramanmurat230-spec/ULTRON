import json, urllib.request, urllib.error

def ollama_status(base_url="http://127.0.0.1:11434", model=None):
    base=base_url.rstrip("/")
    try:
        with urllib.request.urlopen(base+"/api/tags", timeout=5) as r:
            data=json.loads(r.read().decode())
        models=[x.get("name","") for x in data.get("models",[])]
        installed = model in models if model else None
        return {"connected":True,"models":models,"model":model,"model_installed":installed}
    except Exception as e:
        return {"connected":False,"models":[],"model":model,"model_installed":False,"error":str(e)}
