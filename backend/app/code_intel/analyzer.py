"""ULTRON Code Intelligence — static self-analysis, no LLM required.

Scans the project, extracts structure (imports/exports/functions/classes),
builds an internal dependency graph and runs deterministic checks:
potential bugs, duplicate code, unused code, security risks, perf hints,
architecture violations and TODO/FIXME markers.
"""
import ast
import hashlib
import json
import re
from pathlib import Path

SKIP_DIRS = {"node_modules", "dist", "build", ".git", "__pycache__", ".venv", "venv",
             "data", ".arena", ".cache", ".next", "out", "target", "coverage"}
EXT_LANG = {".py": "python", ".ts": "ts", ".tsx": "tsx", ".js": "js", ".css": "css", ".json": "json"}
SECRET_RE = re.compile(r"(api[_-]?key|secret|password|passwd|token)\s*=\s*['\"][^'\"]{8,}['\"]", re.I)
TODO_RE = re.compile(r"(TODO|FIXME|XXX|HACK)\b[^\n]*", re.I)


class CodeIntel:
    def __init__(self, root: str):
        self.root = Path(root).resolve()

    # ------------------------------------------------------------------ scan
    def _files(self, target: str = "all") -> list[Path]:
        if target in ("all", "", None):
            bases = [self.root]
        else:
            bases = [self.root / target]
        out = []
        for base in bases:
            if not base.exists():
                continue
            for p in base.rglob("*"):
                if not p.is_file() or p.suffix not in EXT_LANG:
                    continue
                if any(s in p.parts for s in SKIP_DIRS):
                    continue
                out.append(p)
        return sorted(out)

    # ---------------------------------------------------------------- python
    def _py(self, path: Path, text: str, issues: list, info: dict) -> None:
        try:
            tree = ast.parse(text)
        except SyntaxError as e:
            issues.append({"severity": "error", "kind": "syntax", "file": str(path), "line": e.lineno,
                           "message": f"SyntaxError: {e.msg}"})
            return
        funcs, classes, imports = [], [], []
        imported_names = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                funcs.append(node.name)
                for d in node.args.defaults + [d for d in node.args.kw_defaults if d]:
                    if isinstance(d, (ast.List, ast.Dict, ast.Set)) or (
                        isinstance(d, ast.Call) and isinstance(d.func, ast.Name) and d.func.id in ("list", "dict", "set")):
                        issues.append({"severity": "warn", "kind": "bug-risk", "file": str(path), "line": node.lineno,
                                       "message": f"Mutable default argument in {node.name}()"})
            elif isinstance(node, ast.ClassDef):
                classes.append(node.name)
            elif isinstance(node, ast.Import):
                for a in node.names:
                    imports.append(a.name.split(".")[0])
                    imported_names[a.asname or a.name.split(".")[0]] = node.lineno
            elif isinstance(node, ast.ImportFrom):
                mod = (node.module or "").split(".")[0]
                imports.append(mod)
                for a in node.names:
                    imported_names[a.asname or a.name] = node.lineno
            elif isinstance(node, ast.ExceptHandler):
                if node.type is None:
                    issues.append({"severity": "warn", "kind": "bug-risk", "file": str(path), "line": node.lineno,
                                   "message": "Bare except clause"})
            elif isinstance(node, ast.Call):
                f = node.func
                if isinstance(f, ast.Name) and f.id in ("eval", "exec"):
                    issues.append({"severity": "high", "kind": "security", "file": str(path), "line": node.lineno,
                                   "message": f"Use of {f.id}()"})
                if isinstance(f, ast.Attribute) and f.attr == "run" and any(
                    kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value is True for kw in node.keywords):
                    issues.append({"severity": "high", "kind": "security", "file": str(path), "line": node.lineno,
                                   "message": "subprocess with shell=True"})
        # unused imports (name never referenced again in source)
        for name, line in imported_names.items():
            if name in ("os", "sys", "json") and text.count(name) <= 1:
                issues.append({"severity": "info", "kind": "unused", "file": str(path), "line": line,
                               "message": f"Import '{name}' appears unused"})
            elif text.count(name) == 1:
                issues.append({"severity": "info", "kind": "unused", "file": str(path), "line": line,
                               "message": f"Import '{name}' appears unused"})
        info.update({"functions": funcs, "classes": classes, "imports": imports})

    # -------------------------------------------------------------------- js
    def _js(self, path: Path, text: str, issues: list, info: dict) -> None:
        imports = re.findall(r"from\s+['\"]([^'\"]+)['\"]|require\(['\"]([^'\"]+)['\"]\)", text)
        flat = [a or b for a, b in imports]
        exports = re.findall(r"export\s+(?:default\s+)?(?:function|class|const|let|var)\s+([A-Za-z0-9_]+)", text)
        funcs = re.findall(r"(?:function|const|let)\s+([A-Za-z0-9_]+)\s*(?:=\s*)?\(", text)
        if re.search(r"\beval\s*\(", text):
            issues.append({"severity": "high", "kind": "security", "file": str(path), "line": 0, "message": "Use of eval()"})
        console_n = len(re.findall(r"console\.log", text))
        if console_n > 5:
            issues.append({"severity": "info", "kind": "perf", "file": str(path), "line": 0,
                           "message": f"{console_n} console.log calls"})
        info.update({"imports": flat, "exports": exports, "functions": funcs, "classes": []})

    # ------------------------------------------------------------------ main
    def analyze(self, target: str = "all") -> dict:
        files = self._files(target)
        report = {"target": target, "files": [], "issues": [], "deps": {}, "duplicates": [],
                  "todo": [], "summary": {"files": len(files), "by_lang": {}, "functions": 0, "classes": 0}}
        blocks: dict[str, list] = {}
        all_names: dict[str, int] = {}
        for p in files:
            rel = str(p.relative_to(self.root))
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            lang = EXT_LANG[p.suffix]
            report["summary"]["by_lang"][lang] = report["summary"]["by_lang"].get(lang, 0) + 1
            info: dict = {"file": rel, "lang": lang, "functions": [], "classes": [], "imports": [], "exports": [], "lines": len(text.splitlines())}
            if lang == "python":
                self._py(p, text, report["issues"], info)
            elif lang in ("ts", "tsx", "js"):
                self._js(p, text, report["issues"], info)
            elif lang == "json":
                try:
                    json.loads(text)
                except Exception as e:
                    report["issues"].append({"severity": "error", "kind": "syntax", "file": rel, "line": 0, "message": f"Invalid JSON: {e}"})
            for m in TODO_RE.finditer(text):
                line = text[: m.start()].count("\n") + 1
                report["todo"].append({"file": rel, "line": line, "text": m.group(0).strip()[:80]})
            for m in SECRET_RE.finditer(text):
                report["issues"].append({"severity": "high", "kind": "security", "file": rel,
                                         "line": text[: m.start()].count("\n") + 1,
                                         "message": f"Possible hardcoded credential: {m.group(1)}"})
            # duplicate blocks (normalized 8-line windows)
            if lang in ("python", "ts", "tsx", "js"):
                lines = [re.sub(r"\s+", " ", l.strip()) for l in text.splitlines() if l.strip()]
                for i in range(0, len(lines) - 8, 8):
                    chunk = "\n".join(lines[i:i + 8])
                    if len(chunk) < 120:
                        continue
                    h = hashlib.md5(chunk.encode()).hexdigest()
                    entry = blocks.setdefault(h, [])
                    if len(entry) < 2:
                        entry.append(rel)
                # unused-code hints: count name references across project later
                for n in info["functions"] + info["classes"]:
                    all_names[n] = all_names.get(n, 0) + 1
            # internal deps
            internal = [i for i in info["imports"] if i.startswith((".", "app", "components", "lib", "hooks", "three"))]
            if internal:
                report["deps"][rel] = internal
            info["name_refs"] = None
            report["files"].append(info)
            report["summary"]["functions"] += len(info["functions"])
            report["summary"]["classes"] += len(info["classes"])
        # duplicates + unused (second pass over names in full text corpus)
        corpus_names: dict[str, int] = {}
        for p in files:
            try:
                t = p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for n in all_names:
                if n in t:
                    corpus_names[n] = corpus_names.get(n, 0) + 1
        unused = [n for n, c in all_names.items() if corpus_names.get(n, 0) <= 1]
        for n in unused[:40]:
            report["issues"].append({"severity": "info", "kind": "unused-code", "file": "(project)", "line": 0,
                                     "message": f"'{n}' defined once, never referenced (possibly unused)"})
        report["duplicates"] = [{"block": h, "files": locs} for h, locs in blocks.items() if len(locs) > 1][:20]
        # architecture violations
        for f in report["files"]:
            rel = f["file"]
            for imp in f["imports"]:
                if rel.startswith("frontend") and ("app." in imp or imp.startswith("app/")):
                    report["issues"].append({"severity": "warn", "kind": "architecture", "file": rel, "line": 0,
                                             "message": "Frontend imports backend package"})
                if rel.startswith("backend") and imp in ("react", "react-dom", "three"):
                    report["issues"].append({"severity": "warn", "kind": "architecture", "file": rel, "line": 0,
                                             "message": "Backend imports UI library"})
        report["issues"].sort(key=lambda i: {"high": 0, "error": 0, "warn": 1, "info": 2}[i["severity"]])
        return report

    def explain_file(self, rel: str) -> dict:
        p = (self.root / rel).resolve()
        if not str(p).startswith(str(self.root)) or not p.exists():
            return {"error": "file not found in project"}
        text = p.read_text(encoding="utf-8", errors="ignore")
        lang = EXT_LANG.get(p.suffix, "?")
        info: dict = {"file": rel, "lang": lang, "functions": [], "classes": [], "imports": [], "exports": [], "lines": len(text.splitlines())}
        issues: list = []
        if lang == "python":
            self._py(p, text, issues, info)
        elif lang in ("ts", "tsx", "js"):
            self._js(p, text, issues, info)
        doc = ast.get_docstring(ast.parse(text)) if lang == "python" else None
        info["docstring"] = doc or (text.splitlines()[0][:160] if text.splitlines() else "")
        info["issues"] = issues
        return info
