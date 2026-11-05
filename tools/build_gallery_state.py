"""Create the independent, fully stocked local gallery save."""

from __future__ import annotations

import copy
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from server import server as game


def config(name: str):
    return json.loads((ROOT / "server" / "config" / name).read_text(encoding="utf-8"))


def payload(state: dict, command: str) -> dict:
    row = game.find_push(state, command)
    if row is None or not isinstance(row.get("data"), dict):
        raise ValueError(f"missing save section: {command}")
    return row["data"]


def complete_encyclopedia(data: dict, cfg: dict) -> None:
    entries = cfg["list"].values()
    data["unlock_list"] = sorted({int(row["long_id"]) for row in entries})
    data["show_sub"] = [
        {"id": int(parent), "sub_id": int(next(iter(
            key for key, row in cfg["list"].items()
            if int(row["id"]) == int(parent)
        )))}
        for parent in sorted({int(row["id"]) for row in cfg["list"].values()})
    ]
    data["unlock_desc"] = [
        {"id": int(parent), "list": sorted(int(index) for index in descriptions)}
        for parent, descriptions in cfg["desc"].items()
    ]


def complete_special_furniture(state: dict) -> None:
    tumbler = payload(state, "furniture.load_tumbler")["tumbler_list"]
    owned_tumblers = {int(row["id"]) for row in tumbler}
    tumbler_ids = {
        int(item_id)
        for variants in game.FURNITURE_SPECIAL_MATERIAL_RECIPES[11001]["variants"].values()
        for item_id in variants
    }
    tumbler.extend({"id": item_id, "layers": []} for item_id in sorted(tumbler_ids - owned_tumblers))
    layered = {int(row["id"]): row["layers"] for row in tumbler if row.get("layers")}
    default_layers = [{"layer": [1106, -3, 3, 5, 0]}, {"layer": [2001, -3, 0, 0, 0]}]
    for row in tumbler:
        if not row.get("layers"):
            family = int(row["id"]) // 10
            donor = next((layers for item_id, layers in layered.items() if item_id // 10 == family), layered.get(20011, default_layers))
            row["layers"] = copy.deepcopy(donor)
    data = payload(state, "furniture.load_tumbler")
    if tumbler and not data.get("show_index"):
        data["show_index"] = 1
    for command, field, filename in (
        ("furniture.load_compost", "compost_list", "compostData_json.json"),
        ("furniture.load_pocket", "list", "pocketData_json.json"),
    ):
        data = payload(state, command)
        current = data[field]
        selected = current[int(data.get("show_index", 1)) - 1] if current else None
        data[field] = sorted({int(key) for key in config(filename)} | {int(item) for item in current})
        if selected in data[field]:
            data["show_index"] = data[field].index(selected) + 1


def remove_virtual_house_items(state: dict) -> None:
    virtual_ids = {
        int(row["id"]) for row in config("Item_json.json")
        if int(row.get("type", -1)) == 14
        and int(row.get("sub_type", 0) or 0) in {1, 2, 3, 4, 5}
    }
    house = payload(state, "item.load_items")["house"]
    house[:] = [row for row in house if int(row["item_id"]) not in virtual_ids]


def complete_gallery_handbook(state: dict) -> None:
    handbook = payload(state, "item.load_handbook")
    handbook["collections"] = sorted(int(row["id"]) for row in config("Collection_json.json"))
    handbook["specialtys"] = sorted({int(row["itemId"]) for row in config("Specialty_json.json")})
    game.sync_museum_progress(state)


def repair_gallery_handcraft(state: dict) -> None:
    craft = payload(state, "pray.load_grays")
    now = int(time.time())
    for row in craft["wishs"]:
        craft_id = int(row["id"])
        if int(row.get("make_time", 0) or 0) <= 0:
            row.update({
                "body": craft_id * 100 + 2,
                "paper": craft_id * 1000 + 1,
                "content": craft_id * 10 - 9,
                "stamp": 1,
                "stamp_state": 3,
                "make_time": now,
                "stamp_time": now,
            })
    for row in craft["stamps"]:
        if int(row.get("time", 0) or 0) <= 0:
            row["time"] = now


def make_gallery_state(source_state: dict) -> dict:
    state = copy.deepcopy(source_state)
    state.setdefault("offline_meta", {})["gallery_edition"] = True

    role = payload(state, "client.load_role")
    role["res"]["clover_point"] = 9999
    role["frog"]["achieves"] = sorted({int(row["id"]) for row in config("Achieve_json.json")})
    settings = role.setdefault("settings", {})
    client_settings = settings.get("client", "{}")
    if isinstance(client_settings, str):
        client_settings = json.loads(client_settings or "{}")
    client_settings.update({
        "guideStep": "Complete", "hasAchieve": True, "hasOpenAttributeView": True,
        "hasEnteredRaffle": True, "hasOpenedDesk": True, "hasBuyTool": True,
        "hasFriendVisit": True, "guideVisitor": True, "guideVisitorGift": True,
        "guideStory": True, "guideStoryGift": True, "hasOpenedNote": True,
        "guideNote": True, "guideHandCraft": True, "guideSlidePicture": True,
        "guideFurniture": 5, "guideFurnitureNotice": True, "guideAnnualReview": True,
        "guideDrawing": 5, "noticeDrawing": -1, "guideCamera": True,
    })
    settings["client"] = json.dumps(client_settings, ensure_ascii=False)
    state["offline_meta"]["tutorial"] = {"door_opened": True, "completion_reward_claimed": True}

    house = payload(state, "item.load_items")["house"]
    for row in config("Item_json.json"):
        item_id = int(row["id"])
        if int(row.get("spend", 0) or 0) == 1 and item_id not in game.HANDCRAFT_FRAGMENT_IDS and not (
            int(row.get("type", -1)) == 14
            and int(row.get("sub_type", 0) or 0) in {1, 2, 3, 4, 5}
        ):
            game.set_house_count(house, item_id, 99)
        elif int(row.get("type", -1)) in {1, 2, 7, 9, 12, 13, 14}:
            game.set_house_count(house, item_id, 999 if item_id == 9000 else max(1, game.get_house_count(house, item_id)))
    game.set_house_count(house, 9000, 999)
    game.set_house_count(house, 9001, 999)
    for item_id in game.HANDCRAFT_FRAGMENT_IDS:
        game.set_house_count(house, item_id, 0)
    state["offline_meta"]["gallery_fragments_initialized"] = True
    remove_virtual_house_items(state)

    story = payload(state, "story.load")
    owned_stories = {int(row["id"]) for row in story["stories"]}
    for row in config("story_json.json")["story"]:
        story_id = int(row["storyid"])
        if story_id not in owned_stories:
            story["stories"].append({"id": story_id, "gift": -1, "partner": story_id, "name": "旅途中的朋友", "feedback": -1})
    story["new_story_id"] = 0

    drawing = payload(state, "guest.load_drawing")
    drawing["pages"] = sorted(int(key) for key in config("drawingPageData_json.json"))
    drawing["colls"] = sorted(int(key) for key in config("drawingCollectData_json.json"))

    furniture = payload(state, "furniture.load_furniture")
    furniture["has_fur"] = sorted(int(key) for key in config("furnitureData_json.json"))
    complete_special_furniture(state)

    craft = payload(state, "pray.load_grays")
    known_prayers = {int(row["id"]) for row in craft["wishs"]}
    known_stamps = {int(row["id"]) for row in craft["stamps"]}
    for prayer_id in game.HANDCRAFT_PRAY_IDS:
        if prayer_id not in known_prayers:
            craft["wishs"].append({"id": prayer_id, "u_id": 10000 + prayer_id, "state": 4, "body": 501, "paper": 0, "stamp": 0, "stamp_state": 0, "make_time": 0, "stamp_time": 0, "content": 0})
    for stamp_id in game.HANDCRAFT_STAMP_IDS:
        if stamp_id not in known_stamps:
            craft["stamps"].append({"id": stamp_id, "u_id": 20000 + stamp_id, "state": 3, "time": 0})
    repair_gallery_handcraft(state)

    complete_encyclopedia(payload(state, "encyclopedia.load"), config("encyclopedia_json.json"))
    complete_encyclopedia(payload(state, "encytravel.load"), config("encytravel_json.json"))

    notes = payload(state, "travel.load_note")
    notes["note_list"] = [
        {"id": int(key), "read": True, "timestamp": int(time.time())}
        for key in sorted(config("Note_json.json"), key=int)
    ]

    album = payload(state, "album.load_all")["id_list"]
    owned_fixed = {int(row["pic_id"]) for row in album}
    fixed_ids = sorted(
        int(row["id"]) for row in config("Picture_json.json")
        if row.get("type") != "Normal"
    )
    for picture_id in fixed_ids:
        if picture_id not in owned_fixed:
            game._album_append_picture_no_save(state, game.make_picture_info(state, picture_id))

    dynamic_ids = sorted(int(key) for key in config("animpictureData_json.json")["pic_map"])
    for picture_id in dynamic_ids:
        count = sum(int(row["pic_id"]) == picture_id for row in album)
        for _ in range(max(0, 6 - count)):
            game._album_append_picture_no_save(state, game.make_picture_info(state, picture_id))

    game.get_animpicture_state(state).update(page_num=999, guide=game.ANIMPICTURE_GUIDE_COMPLETE)
    role["misc"]["picture_cnt"] = len(album)
    complete_gallery_handbook(state)
    game.normalize_gallery_resources(state)
    game.complete_gallery_guide_tasks(state)
    game.complete_gallery_moments(state)
    return state


def main() -> None:
    source = ROOT / "server" / "data" / "state.json"
    destination = ROOT / "server" / "data" / "gallery_state.json"
    if destination.exists():
        raise SystemExit(f"Gallery save already exists: {destination}")
    state = make_gallery_state(json.loads(source.read_text(encoding="utf-8")))
    game.atomic_write_json(destination, state)
    album = payload(state, "album.load_all")["id_list"]
    fixed_ids = [row for row in config("Picture_json.json") if row.get("type") != "Normal"]
    dynamic_ids = config("animpictureData_json.json")["pic_map"]
    print(f"gallery save: {destination}")
    print(f"fixed photos: {len(fixed_ids)}; dynamic sources: {len(dynamic_ids)} x 6; album entries: {len(album)}")
    print(f"album pages needed: {(len(album) + 5) // 6}; available expansion items: 999")


if __name__ == "__main__":
    main()
