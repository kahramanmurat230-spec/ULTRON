from app.core.jarvis_capabilities import capability_ids, capability_inventory, validate_inventory


def test_jarvis_inventory_is_unique_and_valid():
    validate_inventory()
    ids = capability_ids()
    assert len(ids) == len(set(ids))
    assert {"hud", "voice", "computer_control", "web", "image_generation", "camera", "pdf", "shell", "widgets", "three_d"}.issubset(ids)


def test_inventory_distinguishes_real_modules_from_adapters():
    rows = {row["id"]: row for row in capability_inventory()}
    assert rows["voice"]["status"] == "implemented"
    assert rows["computer_control"]["status"] == "implemented"
    assert rows["three_d"]["status"] == "implemented"
    assert rows["image_generation"]["status"] == "adapter"
    assert rows["camera"]["safety"] == "permission"
    assert rows["shell"]["safety"] == "approval"
