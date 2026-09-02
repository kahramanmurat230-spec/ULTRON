import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agent.orchestrator import parse_plan, risk_report


def test_path_alias_with_suffix():
    step = parse_plan("Downloads\\rapor.txt dosyasını oku")[0]
    assert step["tool"] == "read_text"
    assert step["args"]["path"].endswith(os.path.join("Downloads", "rapor.txt"))


def test_create_folder_phrase_variants():
    assert parse_plan("klasör TestKlasoru oluştur")[0]["tool"] == "create_folder"
    assert parse_plan("TestKlasoru klasörü oluştur")[0]["tool"] == "create_folder"


def test_copy_and_move_resolve_destination_folder():
    copy = parse_plan("Downloads\\a.txt dosyasını Desktop klasörüne kopyala")[0]
    move = parse_plan("Downloads\\a.txt dosyasını Documents klasörüne taşı")[0]
    assert copy["tool"] == "copy_path"
    assert move["tool"] == "move_path"
    assert copy["args"]["destination"].endswith(os.path.join("Desktop", "a.txt"))
    assert move["args"]["destination"].endswith(os.path.join("Documents", "a.txt"))
    assert copy["dangerous"] is True
    assert move["dangerous"] is True


def test_find_supports_root_folder_and_pattern():
    step = parse_plan("Downloads klasöründe *.pdf dosyalarını bul")[0]
    assert step["tool"] == "find_files"
    assert step["args"]["root"].endswith("Downloads")
    assert step["args"]["pattern"] == "*.pdf"


def test_delete_and_rename_are_dangerous():
    delete = parse_plan("Downloads\\a.txt dosyasını sil")[0]
    rename = parse_plan("Downloads\\a.txt dosyasının adını b.txt yap")[0]
    assert delete["tool"] == "delete_path"
    assert rename["tool"] == "rename_path"
    assert delete["dangerous"] is True
    assert rename["dangerous"] is True
    assert risk_report([delete, rename]) == [
        "delete_path(dosya/klasör sil)",
        "rename_path(dosya yeniden adlandır)",
    ]


def test_ordinary_ve_is_not_a_plan_separator():
    steps = parse_plan("dosya ve klasör sayısını söyle")
    assert steps == []


def test_explicit_sequence_still_splits():
    steps = parse_plan("notepad aç ve sonra hesap makinesi aç")
    assert [s["tool"] for s in steps] == ["open_application", "open_application"]
