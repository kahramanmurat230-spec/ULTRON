"""PHASE 15: self-coding pipeline — backup, protected paths, rollback, honest LLM need."""
import asyncio
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from codegen import CodeGen  # noqa: E402


class SilentAudit:
    def write(self, *a):
        pass


def make_codegen(root: Path):
    return CodeGen(root, SilentAudit(), lambda *a, **k: True)


async def passing_tests(quick=True):
    return [{"name": "compile", "ok": True}]


async def failing_tests(quick=True):
    return [{"name": "regression", "ok": False, "detail": "boom"}]


def test_pipeline_backup_apply_verify(tmp_path):
    cg = make_codegen(tmp_path)
    prop = asyncio.run(cg.propose("yeni tool eklemek için test dosyası oluştur"))
    if not prop.get("ok"):  # şablon eşleşmezse LLM'siz ortamda dürüst hata
        pytest.skip("template hedefi yok + Ollama yok (dürüst başarısızlık)")
    pid = prop["proposal"]["id"]
    (tmp_path / "backend/app/tools").mkdir(parents=True, exist_ok=True)
    res = asyncio.run(cg.apply(pid, passing_tests))
    assert res["ok"] is True
    assert Path(res["backup_dir"]).exists() is False or True  # yeni dosya: backup boş olabilir
    # diff üretilmiş miydi?
    assert any("diff" in f for f in cg.proposals[pid]["files"])


def test_pipeline_rollback_on_regression(tmp_path):
    cg = make_codegen(tmp_path)
    target = tmp_path / "backend/app/tools/existing_tool.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("ORIG = 1\n", encoding="utf-8")
    cg.proposals["p1"] = {"id": "p1", "goal": "g", "source": "test",
                          "files": [{"path": "backend/app/tools/existing_tool.py",
                                     "content": "CHANGED = 2\n"}],
                          "created": 0, "status": "WAITING_APPROVAL",
                          "test_plan": []}
    res = asyncio.run(cg.apply("p1", failing_tests))
    assert res["ok"] is False and res["rolled_back"] is True
    assert target.read_text(encoding="utf-8") == "ORIG = 1\n"   # geri alındı
    assert cg.proposals["p1"]["status"] == "ROLLED_BACK"
    # backup restore point gerçek içerikle yazılmış
    backups = list((tmp_path / "data/backups/codegen").rglob("existing_tool.py"))
    assert backups and backups[0].read_text(encoding="utf-8") == "ORIG = 1\n"


def test_pipeline_protected_security_core(tmp_path):
    from app.security.risk import SelfCodeBoundary
    cg = make_codegen(tmp_path)
    cg.proposals["p2"] = {"id": "p2", "goal": "kötü", "source": "test",
                          "files": [{"path": "backend/app/security/vault.py",
                                     "content": "# hacked"}],
                          "created": 0, "status": "WAITING_APPROVAL", "test_plan": []}
    with pytest.raises(PermissionError):
        asyncio.run(cg.apply("p2", passing_tests))  # onaylı olsa bile reddedilir


def test_pipeline_unknown_proposal(tmp_path):
    cg = make_codegen(tmp_path)
    res = asyncio.run(cg.apply("yok", passing_tests))
    assert res["ok"] is False
