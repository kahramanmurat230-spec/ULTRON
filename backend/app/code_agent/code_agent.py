import json
import subprocess
import sys
import time
from pathlib import Path


class CodeAgent:
    """Bounded repository self-coding agent.

    Flow: ANALYZE -> PROPOSE -> VALIDATE -> APPLY -> TEST -> DIAGNOSE -> RETRY.
    Mutation is approval-gated by the caller and every failed mutation is rolled
    back before another iteration starts.
    """

    MAX_REPAIR_ITERATIONS = 3
    MAX_PATCH_FILES = 8
    MAX_FILE_CHARS = 200_000
    MAX_CHANGED_LINES = 4_000
    MAX_TEST_TIMEOUT = 180

    def __init__(self, brain, root, audit=None):
        self.brain = brain
        self.root = Path(root).resolve()
        self.audit = audit

    def _audit(self, event, message):
        if self.audit is not None:
            try:
                self.audit.write(event, message)
            except Exception:
                pass

    def collect(self, paths=None, max_chars=50_000):
        files = []
        targets = [self.root] if not paths else [(self.root / p).resolve() for p in paths]
        for target in targets:
            if self.root not in target.parents and target != self.root:
                raise PermissionError("Kod analizi proje dışına çıkamaz.")
            if target.is_file():
                files.append(target)
            elif target.is_dir():
                files.extend(target.rglob("*.py"))
        out = []
        total = 0
        for path in files:
            if any(part in path.parts for part in (".git", "__pycache__", "data")):
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            block = f"\n### {path.relative_to(self.root)}\n{text}\n"
            if total + len(block) > max_chars:
                break
            out.append(block)
            total += len(block)
        return "".join(out)

    def analyze(self, paths=None, goal=None, feedback=None):
        target = goal or "Genel sağlık ve kalite incelemesi"
        extra = f"\nÖnceki test/teşhis geri bildirimi:\n{feedback}\n" if feedback else ""
        return self.brain.ask(
            f"Kullanıcı hedefi: {target}\n"
            "Bu Ultron Python projesini analiz et. Güvenlik, bug, mimari ve "
            f"performans sorunlarını bul; dosya ve işlev belirt.{extra}\n"
            + self.collect(paths),
            system="Sen kıdemli Python code reviewer ve güvenlik mühendisisin.",
        )

    def propose_patch(self, paths=None, goal=None, feedback=None):
        target = goal or "Projeyi güvenli şekilde iyileştir; yalnızca gerekli değişiklikleri öner."
        extra = f"\nTest/teşhis geri bildirimi:\n{feedback}\n" if feedback else ""
        prompt = (
            f"Kullanıcı hedefi: {target}\n"
            'Bu Ultron projesi için güvenli patch öner. JSON: '
            '{"summary":str,"files":[{"path":str,"content":str}],"tests":[str]}. '
            "Gereksiz davranış değişikliği yapma. Yeni bağımlılık ekleme. "
            "Güvenlik çekirdeğine dokunma.\n"
            + extra
            + self.collect(paths)
        )
        response = self.brain.ask(prompt, system="Sen kontrollü patch üreten yazılım ajanısın.")
        start, end = response.find("{"), response.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("Patch JSON alınamadı.")
        patch = json.loads(response[start : end + 1])
        self._validate_patch(patch)
        return patch

    def test(self, timeout=180):
        timeout = max(1.0, min(float(timeout), self.MAX_TEST_TIMEOUT))
        started = time.monotonic()
        compile_result = subprocess.run(
            [sys.executable, "-m", "compileall", "-q", "app"],
            cwd=self.root,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if compile_result.returncode:
            return {
                "returncode": compile_result.returncode,
                "stdout": compile_result.stdout,
                "stderr": compile_result.stderr,
                "passed": False,
                "stage": "compile",
            }
        remaining = max(1.0, timeout - (time.monotonic() - started))
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-q"],
            cwd=self.root,
            capture_output=True,
            text=True,
            timeout=remaining,
        )
        return {
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "passed": result.returncode == 0,
            "stage": "pytest",
        }

    def _validate_patch(self, patch):
        if not isinstance(patch, dict):
            raise ValueError("Patch object bekleniyor.")
        items = patch.get("files", [])
        if not isinstance(items, list):
            raise ValueError("Patch files listesi geçersiz.")
        if len(items) > self.MAX_PATCH_FILES:
            raise ValueError(f"Patch dosya sınırı aşıldı: {self.MAX_PATCH_FILES}")
        seen = set()
        changed_lines = 0
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get("path"), str) or not isinstance(item.get("content"), str):
                raise ValueError("Geçersiz patch dosya kaydı.")
            rel = Path(item["path"])
            if rel.is_absolute():
                raise PermissionError("Mutlak path ile self-coding yapılamaz.")
            key = rel.as_posix().lower()
            if key in seen:
                raise ValueError(f"Tekrarlanan patch yolu: {item['path']}")
            seen.add(key)
            if len(item["content"]) > self.MAX_FILE_CHARS:
                raise ValueError(f"Patch dosya boyutu sınırı aşıldı: {item['path']}")
            changed_lines += len(item["content"].splitlines())
        if changed_lines > self.MAX_CHANGED_LINES:
            raise ValueError(f"Patch satır sınırı aşıldı: {self.MAX_CHANGED_LINES}")

    def _apply_patch_checked(self, patch):
        from app.security.risk import SelfCodeBoundary

        self._validate_patch(patch)
        originals = {}
        for item in patch.get("files", []):
            path = (self.root / item["path"]).resolve()
            if self.root not in path.parents:
                raise PermissionError("Proje dışına patch yazılamaz.")
            SelfCodeBoundary.check(path)
            content = item["content"]
            if path.suffix.lower() == ".py":
                try:
                    compile(content, str(path), "exec")
                except SyntaxError as exc:
                    raise ValueError(f"Sözdizimi hatalı patch: {item['path']}: {exc}") from exc
            elif path.suffix.lower() == ".json":
                try:
                    json.loads(content)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"Geçersiz JSON patch: {item['path']}: {exc}") from exc
            originals[item["path"]] = path.read_text(encoding="utf-8", errors="replace") if path.exists() else None
        for item in patch.get("files", []):
            path = (self.root / item["path"]).resolve()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(item["content"], encoding="utf-8")
        return originals, [item["path"] for item in patch.get("files", [])]

    def _rollback(self, originals):
        for rel, original in originals.items():
            path = (self.root / rel).resolve()
            if original is None:
                path.unlink(missing_ok=True)
            else:
                path.write_text(original, encoding="utf-8")

    def self_repair(self, paths=None, apply=False, timeout=180, goal=None):
        """Analyze/propose in dry-run; approved apply uses the bounded loop."""
        if apply:
            return self.self_coding_loop(paths=paths, goal=goal, approved=True, timeout=timeout)
        analysis = self.analyze(paths, goal=goal)
        patch = self.propose_patch(paths, goal=goal)
        return {"analysis": analysis, "patch": patch, "applied": False}

    def self_coding_loop(self, paths=None, goal=None, approved=False, max_iterations=3, timeout=180):
        """Run a bounded analyze→patch→test→diagnose→retry cycle."""
        if not approved:
            return {"ok": False, "status": "APPROVAL_REQUIRED", "iterations": []}
        try:
            limit = max(1, min(int(max_iterations), self.MAX_REPAIR_ITERATIONS))
        except (TypeError, ValueError):
            return {"ok": False, "status": "INVALID_LIMIT", "iterations": []}
        total_timeout = max(1.0, min(float(timeout), self.MAX_TEST_TIMEOUT))
        deadline = time.monotonic() + total_timeout
        iterations = []
        feedback = None
        self._audit("SELF_CODING_START", f"goal={goal or 'default'} max_iterations={limit}")

        for index in range(1, limit + 1):
            if time.monotonic() >= deadline:
                self._audit("SELF_CODING_DEADLINE", f"iteration={index}")
                return {"ok": False, "status": "DEADLINE_EXCEEDED", "iterations": iterations}
            try:
                analysis = self.analyze(paths, goal=goal, feedback=feedback)
                patch = self.propose_patch(paths, goal=goal, feedback=feedback)
            except Exception as exc:
                self._audit("SELF_CODING_GENERATION_FAILED", str(exc))
                return {"ok": False, "status": "PATCH_GENERATION_FAILED", "iterations": iterations, "error": str(exc)}

            entry = {
                "iteration": index,
                "analysis": analysis,
                "patch_summary": patch.get("summary", ""),
                "tests": None,
                "rolled_back": False,
            }
            if not patch.get("files"):
                entry["status"] = "NO_CHANGE"
                iterations.append(entry)
                self._audit("SELF_CODING_NO_CHANGE", f"iteration={index}")
                return {"ok": False, "status": "NO_CHANGE", "iterations": iterations}

            originals = {}
            try:
                originals, changed = self._apply_patch_checked(patch)
                remaining = max(1.0, deadline - time.monotonic())
                test_result = self.test(min(remaining, self.MAX_TEST_TIMEOUT))
                entry["tests"] = test_result
                if test_result["passed"]:
                    entry["status"] = "PASS"
                    entry["changed_files"] = changed
                    iterations.append(entry)
                    self._audit("SELF_CODING_VERIFIED", f"iteration={index} files={len(changed)}")
                    return {"ok": True, "status": "VERIFIED", "iterations": iterations, "final": test_result}
                self._rollback(originals)
                entry["rolled_back"] = True
                entry["status"] = "FAIL_ROLLED_BACK"
                feedback = (test_result.get("stdout", "") + "\n" + test_result.get("stderr", ""))[-12_000:]
                iterations.append(entry)
                self._audit("SELF_CODING_ROLLBACK", f"iteration={index} test_failed")
            except Exception as exc:
                self._rollback(originals)
                entry["rolled_back"] = True
                entry["status"] = "ERROR_ROLLED_BACK"
                entry["error"] = str(exc)
                iterations.append(entry)
                feedback = str(exc)
                self._audit("SELF_CODING_ROLLBACK", f"iteration={index} error={exc}")

        self._audit("SELF_CODING_EXHAUSTED", f"iterations={len(iterations)}")
        return {
            "ok": False,
            "status": "EXHAUSTED",
            "iterations": iterations,
            "error": "Maksimum self-coding iterasyonu içinde doğrulanamadı.",
        }

    def apply_patch(self, patch):
        originals, changed = self._apply_patch_checked(patch)
        return {"applied": len(changed), "rollback_snapshot": list(originals)}
