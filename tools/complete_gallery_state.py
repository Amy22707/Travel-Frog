"""Finish finite gallery unlocks in the existing independent save."""

import json

from build_gallery_state import (
    ROOT, complete_gallery_handbook, complete_special_furniture,
    remove_virtual_house_items, repair_gallery_handcraft, game,
)


def main() -> None:
    path = ROOT / "server" / "data" / "gallery_state.json"
    state = json.loads(path.read_text(encoding="utf-8"))
    remove_virtual_house_items(state)
    complete_special_furniture(state)
    repair_gallery_handcraft(state)
    complete_gallery_handbook(state)
    meta = state.setdefault("offline_meta", {})
    if not meta.get("gallery_fragments_initialized"):
        house = game.find_push(state, "item.load_items")["data"]["house"]
        for item_id in game.HANDCRAFT_FRAGMENT_IDS:
            game.set_house_count(house, item_id, 0)
        meta["gallery_fragments_initialized"] = True
    game.normalize_gallery_resources(state)
    game.atomic_write_json(path, state)
    print("Completed gallery flowers, handbook and handcraft records")


if __name__ == "__main__":
    main()
