from pathlib import Path
import os

HOME = Path.home()

def known_folder(name):
    n=name.lower().strip()
    mapping={
        "masaüstü": HOME/"Desktop", "masaüstüm": HOME/"Desktop", "desktop": HOME/"Desktop",
        "indirilenler": HOME/"Downloads", "downloads": HOME/"Downloads",
        "belgeler": HOME/"Documents", "documents": HOME/"Documents",
    }
    return mapping.get(n)

def list_directory(root):
    p=Path(root).expanduser()
    if not p.exists(): raise FileNotFoundError(str(p))
    items=[]
    for x in sorted(p.iterdir(), key=lambda q:(not q.is_dir(), q.name.lower()))[:300]:
        items.append({"name":x.name,"type":"folder" if x.is_dir() else "file","path":str(x)})
    return items

def find_files(root, pattern):
    root=Path(root).expanduser()
    if not root.exists(): raise FileNotFoundError(str(root))
    return [str(p) for p in root.rglob(pattern) if p.is_file()][:300]

def find_project(start=None, name_hint="Ultron"):
    roots=[]
    if start: roots.append(Path(start).expanduser())
    roots += [HOME/"Downloads", HOME/"Desktop", HOME/"Documents"]
    seen=set()
    for root in roots:
        if not root.exists(): continue
        try:
            for p in root.rglob("*"):
                if not p.is_dir(): continue
                s=str(p).lower()
                if s in seen: continue
                seen.add(s)
                if name_hint.lower() in p.name.lower():
                    markers=list(p.glob("requirements*.txt"))+list(p.glob("pyproject.toml"))+list(p.glob("tests"))
                    if markers: return str(p)
        except (PermissionError,OSError):
            continue
    return None

def read_text(path):
    p=Path(path); return p.read_text(encoding="utf-8",errors="replace")[:50000]

def write_text(path, content):
    p=Path(path); p.parent.mkdir(parents=True,exist_ok=True); p.write_text(content,encoding="utf-8"); return str(p)
