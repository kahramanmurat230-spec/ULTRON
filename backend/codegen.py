"""Self code generation pipeline.

REQUEST → ANALYZE → PLAN → GENERATE → REVIEW → TEST → APPROVAL → APPLY

Nothing touches disk without explicit approval; every apply runs the test
suite first and rolls back automatically on failure. All steps are audited.
"""
import difflib
import re
import time
import uuid
from pathlib import Path


class CodeGen:
    def __init__(self, root: Path, audit, notify, sandbox=None):
        self.root = root.resolve()
        self.audit = audit
        self.notify = notify
        self.sandbox = sandbox  # optional FilesystemSandbox (server injects)
        self.proposals: dict[str, dict] = {}

    # ------------------------------------------------------------ generation
    def _template_tool(self, name: str) -> dict:
        safe = re.sub(r"[^a-z0-9_]", "_", name.lower()) or "new_tool"
        rel = f"backend/app/tools/{safe}_tool.py"
        content = (
            f'"""Generated tool: {safe}. Read-only by default; register as dangerous if it mutates state."""\n\n\n'
            f"def {safe}():\n"
            f'    """Placeholder only. Implement and test real behaviour before registration."""\n'
            f'    raise NotImplementedError("Generated tool is not implemented: {safe}")\n'
        )
        return {"path": rel, "content": content,
                "note": f"Register in runtime._register_tools(): reg.register('{safe}', {safe}, '...')"}

    def _template_panel(self, name: str) -> dict:
        safe = re.sub(r"[^a-zA-Z0-9]", "", name) or "NewPanel"
        rel = f"frontend/src/components/{safe}.tsx"
        content = (
            "import { useApp } from \"../lib/store\";\nimport { Panel } from \"./Panel\";\n\n"
            f"export function {safe}() {{\n"
            "  const connected = useApp((s) => s.connected);\n"
            "  return (\n"
            f"    <Panel title=\"{safe}\">\n"
            "      <div className=\"mono dim\" style={{ fontSize: 10 }}>\n"
            "        {connected ? \"LIVE\" : \"OFFLINE\"}\n      </div>\n"
            "    </Panel>\n  );\n}\n"
        )
        return {"path": rel, "content": content, "note": "Add to the layout grid in App.tsx / Drawer."}

    def _diff_for(self, rel: str, new_content: str) -> str:
        p = self.root / rel
        old = p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""
        return "\n".join(
            difflib.unified_diff(old.splitlines(), new_content.splitlines(), f"a/{rel}", f"b/{rel}", lineterm="")
        )

    # --------------------------------------------------------------- propose
    async def propose(self, goal: str, brain=None) -> dict:
        self.audit.write("CODEGEN_REQUEST", goal)
        files = []
        source = "template"
        g = goal.lower()
        m = re.search(r"(?:tool|araç|panel|bileşen)[\w\s]{0,24}?(?:için|adında)?\s*([\w\-]{3,24})", g)
        if "tool" in g and any(k in g for k in ("oluştur", "yarat", "ekle", "yaz", "üret")):
            stop = {"yeni", "bir", "tool", "araç", "oluştur", "yarat", "ekle", "yaz", "üret", "için", "ultron", "the", "a", "an"}
            nouns = [w for w in re.findall(r"[\wçğıöşü]+", g) if w not in stop]
            name = "_".join(nouns[:2]) or "new_tool"
            files.append(self._template_tool(name))
        elif any(k in g for k in ("panel", "component", "bileşen")) and any(k in g for k in ("oluştur", "yarat", "ekle", "üret")):
            files.append(self._template_panel(goal.split()[-1][:20]))
        elif brain is not None:
            source = "llm"
            try:
                patch = await _llm_patch(brain, self.root, goal)
                for item in patch.get("files", []):
                    files.append({"path": item["path"], "content": item["content"], "note": patch.get("summary", "")})
            except Exception as e:  # noqa: BLE001
                return {"ok": False, "error": f"LLM patch üretilemedi (Ollama gerekli): {e}"}
        else:
            return {"ok": False, "error": "Bu istek için şablon yok; Ollama bağlı değil → LLM üretimi kullanılamıyor."}
        if not files:
            return {"ok": False, "error": "Üretilecek dosya belirlenemedi."}
        for f in files:
            p = (self.root / f["path"]).resolve()
            if not str(p).startswith(str(self.root)):
                return {"ok": False, "error": f"Proje dışına yazım reddedildi: {f['path']}"}
            f["diff"] = self._diff_for(f["path"], f["content"])
        pid = uuid.uuid4().hex[:10]
        proposal = {"id": pid, "goal": goal, "source": source, "files": files, "created": time.time(),
                    "status": "WAITING_APPROVAL",
                    "test_plan": ["tsc --noEmit", "compileall", "REST/WS checks", "rollback on failure"]}
        self.proposals[pid] = proposal
        self.audit.write("CODEGEN_PROPOSED", f"{pid} files={len(files)} source={source}")
        return {"ok": True, "proposal": proposal}

    # ----------------------------------------------------------------- apply
    async def apply(self, pid: str, run_tests, commit: bool = False) -> dict:
        """APPROVAL -> BACKUP -> APPLY -> TEST(REGRESSION) -> VERIFY ->
        COMMIT (optional) or ROLLBACK. Security-core paths are always refused."""
        prop = self.proposals.get(pid)
        if not prop:
            return {"ok": False, "error": "unknown proposal"}
        if prop["status"] != "WAITING_APPROVAL":
            return {"ok": False, "error": f"proposal already {prop['status']}"}
        self.audit.write("CODEGEN_APPROVAL", f"{pid} APPROVE&APPLY")
        # PHASE 15: BACKUP — restore point BEFORE any write
        backup_dir = self.root / "data/backups/codegen" / f"{pid}_{int(time.time())}"
        originals = {}
        # PHASE 15: ön doğrulama — korumalı yol varsa hiç yazmadan RED (raise)
        from app.security.risk import SelfCodeBoundary
        for f in prop["files"]:
            rel = str(f.get("path", ""))
            p = (self.root / rel).resolve()
            if self.root not in p.parents:
                raise PermissionError(f"Patch path outside project: {rel}")
            if self.sandbox is not None:
                self.sandbox.validate_write(p)  # PHASE 10: sandbox-enforced
            SelfCodeBoundary.check(p)  # security core is untouchable
        try:
            for f in prop["files"]:
                rel = str(f.get("path", ""))
                p = (self.root / rel).resolve()
                originals[rel] = p.read_text(encoding="utf-8", errors="replace") if p.exists() else None
                # backup kopyası (gerçek dosya içeriği)
                bdir = backup_dir / rel
                bdir.parent.mkdir(parents=True, exist_ok=True)
                if originals[rel] is not None:
                    bdir.write_text(originals[rel], encoding="utf-8")
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(f["content"], encoding="utf-8")
            results = await run_tests(quick=True)
            failed = [r for r in results if not r["ok"]]
            if failed:
                raise RuntimeError("tests failed: " + ", ".join(r["name"] for r in failed))
            prop["status"] = "APPLIED"
            self.audit.write("CODEGEN_APPLIED", f"{pid} tests=PASS")
            commit_info = None
            if commit:
                # PHASE 15: COMMIT adımı — sadece patch dosyaları stage edilir
                import subprocess
                import sys as _sys
                files = [str((self.root / f["path"]).resolve()) for f in prop["files"]]
                try:
                    subprocess.run(["git", "add", *files], cwd=self.root, check=True,
                                   timeout=30)
                    cp = subprocess.run(
                        ["git", "commit", "-q", "-m",
                         f"codegen({pid}): {prop['goal'][:80]}"],
                        cwd=self.root, check=True, capture_output=True, text=True,
                        timeout=60)
                    commit_info = {"committed": True,
                                   "note": cp.stdout.strip()[:120] or "ok"}
                    self.audit.write("CODEGEN_COMMIT", f"{pid}")
                except Exception as exc:  # noqa: BLE001
                    raise RuntimeError(f"git commit failed: {str(exc)[:120]}") from exc
            self.notify("codegen", f"Patch {pid} applied — all tests passed.", "success", force=True)
            return {"ok": True, "tests": results,
                    "backup_dir": str(backup_dir), "commit": commit_info}
        except Exception as e:  # noqa: BLE001
            for rel, orig in originals.items():
                p = self.root / rel
                if orig is None:
                    p.unlink(missing_ok=True)
                else:
                    p.write_text(orig, encoding="utf-8")
            prop["status"] = "ROLLED_BACK"
            self.audit.write("CODEGEN_ROLLBACK", f"{pid} {e}")
            if backup_dir.exists():
                self.audit.write("CODEGEN_BACKUP_KEPT", str(backup_dir))  # restore point
            self.notify("codegen", f"Patch {pid} failed tests → rolled back.", "error", force=True)
            return {"ok": False, "error": str(e), "rolled_back": True}

    def reject(self, pid: str) -> dict:
        prop = self.proposals.get(pid)
        if not prop:
            return {"ok": False, "error": "unknown proposal"}
        prop["status"] = "REJECTED"
        self.audit.write("CODEGEN_REJECTED", pid)
        return {"ok": True}

    def pending(self) -> list[dict]:
        return [p for p in self.proposals.values() if p["status"] == "WAITING_APPROVAL"]


async def _llm_patch(brain, root: Path, goal: str) -> dict:
    import asyncio
    from app.code_agent.code_agent import CodeAgent
    agent = CodeAgent(brain, root)
    return await asyncio.to_thread(agent.propose_patch, goal=goal)
