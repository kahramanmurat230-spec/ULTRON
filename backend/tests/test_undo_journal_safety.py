from app.core.undo_journal import UndoJournal


def test_undo_refuses_stale_change(tmp_path):
    path = tmp_path / "sample.txt"
    path.write_text("before", encoding="utf-8")
    journal = UndoJournal(tmp_path)
    before = journal.snapshot(path)
    path.write_text("after", encoding="utf-8")
    entry_id = journal.record("write", before, journal.snapshot(path))
    path.write_text("newer", encoding="utf-8")

    result = journal.undo(entry_id)

    assert result["status"] == "STALE"
    assert path.read_text(encoding="utf-8") == "newer"
    assert journal.list()


def test_snapshot_rejects_large_files(tmp_path):
    path = tmp_path / "large.txt"
    path.write_text("1234567890", encoding="utf-8")
    journal = UndoJournal(tmp_path, max_snapshot_bytes=5)

    try:
        journal.snapshot(path)
    except ValueError as exc:
        assert "boyutu sınırını" in str(exc)
    else:
        raise AssertionError("large snapshot should be rejected")
