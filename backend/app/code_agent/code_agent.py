import json,subprocess,sys
from pathlib import Path
class CodeAgent:
    def __init__(self,brain,root): self.brain=brain; self.root=Path(root).resolve()
    def collect(self,paths=None,max_chars=50000):
        files=[]; targets=[self.root] if not paths else [(self.root/p).resolve() for p in paths]
        for t in targets:
            if t.is_file():files.append(t)
            elif t.is_dir():files.extend(t.rglob('*.py'))
        out=[]; total=0
        for p in files:
            if any(x in p.parts for x in ['.git','__pycache__','data']):continue
            try:s=p.read_text(encoding='utf-8',errors='ignore')
            except Exception:continue
            block=f'\n### {p.relative_to(self.root)}\n{s}\n'
            if total+len(block)>max_chars:break
            out.append(block);total+=len(block)
        return ''.join(out)
    def analyze(self,paths=None,goal=None):
        target=goal or "Genel sağlık ve kalite incelemesi"
        return self.brain.ask(f"Kullanıcı hedefi: {target}\nBu Ultron Python projesini analiz et. Güvenlik, bug, mimari ve performans sorunlarını bul; dosya ve işlev belirt.\n"+self.collect(paths),system="Sen kıdemli Python code reviewer ve güvenlik mühendisisin.")
    def propose_patch(self,paths=None,goal=None):
        target = goal or "Projeyi güvenli şekilde iyileştir; yalnızca gerekli değişiklikleri öner."
        prompt = (f"Kullanıcı hedefi: {target}\n"
                  'Bu Ultron projesi için güvenli patch öner. JSON: {"summary":str,"files":[{"path":str,"content":str}],"tests":[str]}. '
                  "Gereksiz davranış değişikliği yapma.\n" + self.collect(paths))
        r=self.brain.ask(prompt,system='Sen kontrollü patch üreten yazılım ajanısın.')
        a,b=r.find('{'),r.rfind('}')
        if a<0 or b<=a:raise ValueError('Patch JSON alınamadı.')
        return json.loads(r[a:b+1])
    def test(self,timeout=180):
        r=subprocess.run([sys.executable,'-m','pytest','-q'],cwd=self.root,capture_output=True,text=True,timeout=timeout); return {'returncode':r.returncode,'stdout':r.stdout,'stderr':r.stderr,'passed':r.returncode==0}
    def self_repair(self, paths=None, apply=False, timeout=180, goal=None):
        """Analyze -> propose -> optionally apply -> test -> rollback on failure."""
        analysis=self.analyze(paths, goal=goal)
        patch=self.propose_patch(paths, goal=goal)
        result={'analysis':analysis,'patch':patch,'applied':False}
        if not apply:
            return result
        originals={}
        try:
            for item in patch.get('files',[]):
                p=(self.root/item['path']).resolve()
                if self.root not in p.parents:
                    raise PermissionError('Proje dışına patch yazılamaz.')
                from app.security.risk import SelfCodeBoundary
                SelfCodeBoundary.check(p)  # security core: never self-patched
                originals[item['path']] = p.read_text(encoding='utf-8',errors='replace') if p.exists() else None
                p.parent.mkdir(parents=True,exist_ok=True)
                p.write_text(item['content'],encoding='utf-8')
            result['applied']=True
            result['tests']=self.test(timeout)
            if not result['tests']['passed']:
                raise RuntimeError('self-repair tests failed')
            return result
        except Exception as exc:
            for rel, orig in originals.items():
                p=(self.root/rel).resolve()
                if orig is None:
                    p.unlink(missing_ok=True)
                else:
                    p.write_text(orig,encoding='utf-8')
            result['rolled_back']=True
            result['error']=str(exc)
            return result

    def apply_patch(self,patch):
        for item in patch.get('files',[]):
            p=(self.root/item['path']).resolve()
            if self.root not in p.parents:raise PermissionError('Proje dışına patch yazılamaz.')
            p.parent.mkdir(parents=True,exist_ok=True);p.write_text(item['content'],encoding='utf-8')
        return {'applied':len(patch.get('files',[]))}
