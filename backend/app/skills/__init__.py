"""Skills — declarative, user-extensible agent capabilities.

A skill = JSON: {name, description, steps:[{tool, args}], params, result_key}.
Execution goes through the SAME Executor as manual tool calls: approval
gates for dangerous tools stay fully intact. Risk is computed from the
tools a skill ACTUALLY uses (registry dangerous flags), never from what
the skill author declares.

Security split:
  - `skill_run` (SAFE tool): executes only all-safe skills; a skill with
    any dangerous tool is refused with needs_approval=True.
  - `skill_run_dangerous` (dangerous=True tool): the ONLY path that can
    execute dangerous-step skills, and it passes through the standard
    approval gate like every other dangerous tool.

Built-ins live in config/skills/*.json; user skills in data/skills/*.json.
"""
import json
import time
from pathlib import Path


class SkillError(RuntimeError):
    pass


RANK = {"SAFE": 0, "MEDIUM": 1, "HIGH": 2}


class Skill:
    """PHASE 10: her skill tam metadata taşır —
    id, name, description, permissions(=tools), risk_level(computed),
    input_schema, output_schema, timeout_s, verification, version."""

    def __init__(self, data: dict, source: str = ""):
        if not isinstance(data, dict) or not data.get("name") or not data.get("steps"):
            raise SkillError("skill requires name + steps")
        self.name = str(data["name"])
        self.skill_id = str(data.get("id", f"skill::{self.name}"))
        self.description = str(data.get("description", ""))
        self.steps = data["steps"]
        self.params = data.get("params", {})
        self.version = str(data.get("version", "1.0.0"))
        self.timeout_s = float(data.get("timeout_s", 120))
        if not 1 <= self.timeout_s <= 600:
            raise SkillError(f"timeout_s 1..600 arası olmalı: {self.timeout_s}")
        self.input_schema = data.get("input_schema") or {
            "type": "object",
            "properties": {k: {"type": "string"} for k in self.params}}
        self.output_schema = data.get("output_schema") or {"type": "object"}
        self.verification = data.get("verification")  # opsiyonel: {"expect": "..."}
        self.source = source

    def dangerous_tools(self, registry) -> list[str]:
        out = []
        for st in self.steps:
            t = registry.get(st.get("tool"))
            if t is None:
                raise SkillError(f"bilinmeyen tool: {st.get('tool')}")
            if t.get("dangerous"):
                out.append(st["tool"])
        return out

    def compute_risk(self, registry) -> str:
        worst = "SAFE"
        for st in self.steps:
            t = registry.get(st.get("tool"))
            if t is None:
                raise SkillError(f"bilinmeyen tool: {st.get('tool')}")
            if t.get("dangerous"):
                lvl = "MEDIUM"
            else:
                lvl = "SAFE"
            if RANK[lvl] > RANK[worst]:
                worst = lvl
        return worst

    def as_dict(self, registry=None):
        risk = "UNKNOWN"
        dangerous = []
        if registry is not None:
            try:
                risk = self.compute_risk(registry)
                dangerous = self.dangerous_tools(registry)
            except SkillError:
                risk = "INVALID"
        return {"id": self.skill_id, "name": self.name,
                "description": self.description, "version": self.version,
                "steps": len(self.steps), "risk": risk,
                "permissions": [st.get("tool") for st in self.steps],
                "dangerous_tools": dangerous, "params": self.params,
                "input_schema": self.input_schema,
                "output_schema": self.output_schema,
                "timeout_s": self.timeout_s, "verification": self.verification,
                "source": self.source}


class SkillRunner:
    def __init__(self, registry, executor=None, audit=None,
                 builtin_dir="config/skills", user_dir="data/skills"):
        self.registry = registry
        self.executor = executor          # real Executor (approval + audit intact)
        self.audit = audit
        self.builtin_dir = Path(builtin_dir)
        self.user_dir = Path(user_dir)
        self.user_dir.mkdir(parents=True, exist_ok=True)

    def load_skills(self) -> list[Skill]:
        skills: list[Skill] = []
        for d in (self.builtin_dir, self.user_dir):
            if not d.exists():
                continue
            for f in sorted(d.glob("*.json")):
                try:
                    raw = json.loads(f.read_text(encoding="utf-8"))
                except Exception:
                    continue
                try:
                    skills.append(Skill(raw, source=str(d)))
                except SkillError:
                    continue
        return skills

    def list(self) -> list[dict]:
        return [s.as_dict(self.registry) for s in self.load_skills()]

    def _resolve_args(self, args: dict, params: dict) -> dict:
        out = {}
        for k, v in args.items():
            if isinstance(v, str) and v.startswith("$"):
                out[k] = params.get(v[1:], v)
            else:
                out[k] = v
        return out

    def run(self, name: str, params: dict | None = None,
            approved: bool = False, allow_dangerous: bool = False) -> dict:
        params = params or {}
        skill = next((s for s in self.load_skills() if s.name == name), None)
        if skill is None:
            raise SkillError(f"bilinmeyen skill: {name}")
        try:
            dangerous = skill.dangerous_tools(self.registry)
        except SkillError as exc:
            return {"ok": False, "error": f"skill geçersiz: {exc}"}
        if dangerous and not allow_dangerous:
            if self.audit:
                self.audit.write("SKILL_NEEDS_APPROVAL",
                                 f"{name} tools={dangerous}")
            return {"ok": False, "needs_approval": True,
                    "dangerous_tools": dangerous,
                    "error": "skill dangerous tool içeriyor — skill_run_dangerous + onay gerekir"}
        if dangerous and not approved:
            if self.audit:
                self.audit.write("SKILL_APPROVAL_REQUIRED", f"{name}")
            return {"ok": False, "needs_approval": True,
                    "dangerous_tools": dangerous,
                    "error": "onay gerekir (approved=false)"}
        results = []
        t0 = time.time()
        for i, st in enumerate(skill.steps):
            if time.time() - t0 > skill.timeout_s:  # PHASE 10: skill timeout bütçesi
                if self.audit:
                    self.audit.write("SKILL_TIMEOUT", f"{name} after {i} steps")
                return {"ok": False, "error": f"skill timeout ({skill.timeout_s}s)",
                        "failed_step": i, "results": results,
                        "elapsed_s": round(time.time() - t0, 2)}
            tool = st.get("tool")
            args = self._resolve_args(st.get("args", {}), params)
            try:
                if self.executor is not None:
                    res = self.executor.execute([(tool, args)], approved=approved)[0]
                else:  # direct registry fallback (tests without executor)
                    t = self.registry.get(tool)
                    res = t["fn"](**args)
                results.append({"step": i, "tool": tool, "ok": True, "result": res})
            except Exception as exc:  # noqa: BLE001
                if self.audit:
                    self.audit.write("SKILL_STEP_FAIL",
                                     f"{name} step={i} tool={tool}: {str(exc)[:120]}")
                return {"ok": False, "failed_step": i, "tool": tool,
                        "error": str(exc), "results": results,
                        "elapsed_s": round(time.time() - t0, 2)}
        final = results[-1]["result"] if results else None
        # verification beklentisi (opsiyonel): sonuç JSON'ında expect substr aranır
        verified = None
        if skill.verification and skill.verification.get("expect"):
            expect = str(skill.verification["expect"])
            try:
                import json as _json
                verified = expect.lower() in _json.dumps(
                    final, default=str, ensure_ascii=False).lower()
            except Exception:
                verified = False
        if self.audit:
            self.audit.write("SKILL_RUN", f"{name} steps={len(results)} ok")
        return {"ok": True, "skill": name, "steps": len(results),
                "result": final, "verified": verified,
                "elapsed_s": round(time.time() - t0, 2)}
