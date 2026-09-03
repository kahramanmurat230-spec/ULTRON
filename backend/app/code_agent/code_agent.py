import json,subprocess,sys
from pathlib import Path


class CodeAgent:
    MAX_REPAIR_ITERATIONS = 3

    def __init__(self,brain,root):
        self.brain=brain; self.root=Path(root).resolve()

    def collect(self,paths=None,max_chars=50000):
        files=[]; targets=[self.root] if not paths else [(self.root/p).resolve() for p in paths]
        for t in targets:
            if t.is_file(): files.append(t)
            elif t.is_dir(): files.extend(t.rglob('*.py'))
        out=[]; total=0
        for p in files:
            if any(x in p.parts for x in ['.git','__pycache__','data']):continue
            try:s=p.read_text(encoding='utf-8',errors='ignore')
            except Exception:continue
            block=f'\n### {p.relative_to(self.root)}\n{s}\n'
            if total+len(block)>max_chars:break
            out.append(block);total+=len(block)
        return ''.join(out)

    def analyze(self,paths=None,goal=None,feedback=None):
        target=goal or "Genel sağlık ve kalite incelemesi"
        extra=f"\nÖnceki test/teşhis geri bildirimi:\n{feedback}\n" if feedback else ""
        return self.brain.ask(f"Kullanıcı hedefi: {target}\nBu Ultron Python projesini analiz et. Güvenlik, bug, mimari ve performans sorunlarını bul; dosya ve işlev belirt.{extra}\n"+self.collect(paths),system="Sen kıdemli Python code reviewer ve güvenlik mühendisisin.")

    def propose_patch(self,paths=None,goal=None,feedback=None):
        target = goal or "Projeyi güvenli şekilde iyileştir; yalnızca gerekli değişiklikleri öner."
        extra=f"\nTest/teşhis geri bildirimi:\n{feedback}\n" if feedback else ""
        prompt = (f"Kullanıcı hedefi: {target}\n"
                  'Bu Ultron projesi için güvenli patch öner. JSON: {"summary":str,"files":[{"path":str,"content":str}],"tests":[str]}. '
                  "Gereksiz davranış değişikliği yapma.\n" + extra + self.collect(paths))
        r=self.brain.ask(prompt,system='Sen kontrollü patch üreten yazılım ajanısın.')
        a,b=r.find('{'),r.rfind('}')
        if a<0 or b<=a:raise ValueError('Patch JSON alınamadı.')
        patch=json.loads(r[a:b+1])
        if not isinstance(patch,dict) or not isinstance(patch.get('files',[]),list):
            raise ValueError('Geçersiz patch şeması.')
        return patch

    def test(self,timeout=180):
        r=subprocess.run([sys.executable,'-m','pytest','-q'],cwd=self.root,capture_output=True,text=True,timeout=timeout)
        return {'returncode':r.returncode,'stdout':r.stdout,'stderr':r.stderr,'passed':r.returncode==0}

    def _apply_patch_checked(self,patch):
        from app.security.risk import SelfCodeBoundary
        originals={}
        changed=[]
        for item in patch.get('files',[]):
            if not isinstance(item,dict) or not isinstance(item.get('path'),str) or not isinstance(item.get('content'),str):
                raise ValueError('Geçersiz patch dosya kaydı.')
            p=(self.root/item['path']).resolve()
            if self.root not in p.parents:
                raise PermissionError('Proje dışına patch yazılamaz.')
            SelfCodeBoundary.check(p)
            originals[item['path']] = p.read_text(encoding='utf-8',errors='replace') if p.exists() else None
            p.parent.mkdir(parents=True,exist_ok=True)
            p.write_text(item['content'],encoding='utf-8')
            changed.append(item['path'])
        return originals,changed

    def _rollback(self,originals):
        for rel, orig in originals.items():
            p=(self.root/rel).resolve()
            if orig is None:
                p.unlink(missing_ok=True)
            else:
                p.write_text(orig,encoding='utf-8')

    def self_repair(self, paths=None, apply=False, timeout=180, goal=None):
        """Analyze -> propose -> optionally apply -> test -> rollback on failure."""
        analysis=self.analyze(paths, goal=goal)
        patch=self.propose_patch(paths, goal=goal)
        result={'analysis':analysis,'patch':patch,'applied':False}
        if not apply:
            return result
        originals={}
        try:
            originals, _ = self._apply_patch_checked(patch)
            result['applied']=True
            result['tests']=self.test(timeout)
            if not result['tests']['passed']:
                raise RuntimeError('self-repair tests failed')
            return result
        except Exception as exc:
            self._rollback(originals)
            result['rolled_back']=True
            result['error']=str(exc)
            return result

    def self_coding_loop(self, paths=None, goal=None, approved=False, max_iterations=3, timeout=180):
        """Bounded analyze→patch→test→diagnose→retry loop.

        Nothing is written unless approved=True. Every failed iteration is rolled
        back before the next patch is attempted. A successful iteration stops
        immediately; the method never reports success without a passing test run.
        """
        if not approved:
            return {'ok':False,'status':'APPROVAL_REQUIRED','iterations':[]}
        limit=max(1,min(int(max_iterations),self.MAX_REPAIR_ITERATIONS))
        iterations=[]; feedback=None
        for index in range(1,limit+1):
            analysis=self.analyze(paths,goal=goal,feedback=feedback)
            try:
                patch=self.propose_patch(paths,goal=goal,feedback=feedback)
            except Exception as exc:
                return {'ok':False,'status':'PATCH_GENERATION_FAILED','iterations':iterations,'error':str(exc)}
            originals={}
            entry={'iteration':index,'analysis':analysis,'patch_summary':patch.get('summary',''),'tests':None,'rolled_back':False}
            try:
                originals,_=self._apply_patch_checked(patch)
                test_result=self.test(timeout)
                entry['tests']=test_result
                if test_result['passed']:
                    entry['status']='PASS'
                    iterations.append(entry)
                    return {'ok':True,'status':'VERIFIED','iterations':iterations,'final':test_result}
                self._rollback(originals)
                entry['rolled_back']=True
                entry['status']='FAIL_ROLLED_BACK'
                feedback=(test_result.get('stdout','')+'\n'+test_result.get('stderr',''))[-12000:]
                iterations.append(entry)
            except Exception as exc:
                self._rollback(originals)
                entry['rolled_back']=True; entry['status']='ERROR_ROLLED_BACK'; entry['error']=str(exc)
                iterations.append(entry)
                feedback=str(exc)
        return {'ok':False,'status':'EXHAUSTED','iterations':iterations,'error':'Maksimum self-coding iterasyonu içinde doğrulanamadı.'}

    def apply_patch(self,patch):
        originals,_=self._apply_patch_checked(patch)
        return {'applied':len(patch.get('files',[])),'rollback_snapshot':list(originals)}
