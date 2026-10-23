"""Portable saves for the normal and gallery editions."""

from __future__ import annotations

import copy
import json
import time
import zipfile
from datetime import datetime
from pathlib import Path

from server import server as game

EDITIONS = {"normal": "普通版", "gallery": "图鉴版"}
HISTORY_MARKERS = (
    "stage6c1_migrated", "stage6f1_lottery_phase171_migrated",
    "stage6f2_lottery_extra_mail_repaired", "stage6g_calendar_sep2026_calibrated",
    "stage6h_album_full_rescued",
)


def mark_portable(state: dict) -> dict:
    meta = state.setdefault("offline_meta", {})
    meta["portable_save"] = True
    for key in HISTORY_MARKERS:
        meta.setdefault(key, True)
    state.pop("source_snapshot", None)
    # These cached login responses are not gameplay state.
    responses = state.setdefault("responses", {})
    for key in list(responses):
        if key.startswith("hall."):
            del responses[key]
    return state


def new_state(name: str = "呱呱") -> dict:
    """Create fresh progress without reading a personal save or capture."""
    now = int(time.time())
    furniture = game.load_static_json("furnitureData_json.json")
    base = sorted((r for r in furniture.values() if int(r.get("style", -1)) == 1), key=lambda r: int(r["id"]))
    puts = [{"type": int(r["type"]), "id": int(r["id"])} for r in base]
    data = {
        "client.load_role": {
            "uid": 1, "misc": {"picture_cnt": 0, "create_time": now, "wx_my_reward": False, "wx_push_reward": False},
            "frog": {"name": name.strip() or "呱呱", "status": 0, "motion": 1, "icon": 0,
                     "today_step": 0, "achieves": [], "achieves_time": {}, "cur_achieve": 0},
            "res": {"clover_point": 500, "ticket": 0},
            "settings": {"lang": "zh-CN", "push_switch": False, "rank_switch": False, "client": "{}"},
        },
        "guest.load": {"id": -1}, "story.load": {"stories": [], "new_story_id": 0},
        "visit.load": {"acquire": []},
        "clover.load_clovers": [{"clover_id": i, "last_harvest": 0, "rebirth_span": 10800,
                                "sprite": 1, "element": 0} for i in range(1, 21)],
        "item.load_items": {"house": [{"item_id": 0, "count": 3}], "gacha": {"color_ball": -1},
                            "bag": [-1]*4, "desk": [-1]*6, "bag_completed": False,
                            "desk_conflict": False, "bag_conflict": False},
        "item.load_select_gift": {"list": {}}, "item.load_handbook": {"specialtys": [], "collections": []},
        "item.load_shop_info": {"purchased": []}, "mail.load": [],
        "cooking.load_cooking": {"refresh_time": 0, "select": 1, "month_pro": 0, "month": datetime.now().month,
                                 "task_list": [], "complete": False, "week": 1},
        "pray.load_grays": {"stamps": [], "wishs": [], "boxes": {}},
        "travel.load_note": {"note_list": []}, "client.load_events": [],
        "album.load_all": {"id_list": []},
        "album.load_new": {"pictures": [], "is_share": False, "visted_pic": [], "has_ads": False},
        "guest.load_drawing": {"colls": [], "pages": [], "pen_motion": "write", "bag": [-1]*4,
                               "show_coll": 0, "state": 0, "guest": -1},
        "recharge.load": {"change": 0, "sack": {}, "field": [], "water": 0},
        "recharge.load_gift": {"gift": {}},
        "furniture.load_furniture": {"replace_fur": [], "put_fur": puts, "has_fur": [r["id"] for r in puts],
                                     "bench": [-1]*10, "bench_lock": False, "mate_list": [], "mood": 0,
                                     "shop": {"start_time": 0, "leave_time": 0, "shop_list": []}},
        "furniture.load_tumbler": {"replace_index": 0, "show_index": 1,
                                   "tumbler_list": [{"id": 20011, "layers": [{"layer": [1106, -3, 3, 5, 0]}, {"layer": [2001, -3, 0, 0, 0]}]}]},
        "furniture.load_compost": {"replace_index": 0, "compost_list": [21000], "show_index": 1,
                                   "box_index": 0, "state": 0, "box_list": [0]*6},
        "furniture.load_pocket": {"replace_index": 0, "list": [22001], "clover": 0, "show_index": 1},
        "furniture.load_flowerpot": {"replace_index": 0, "list": [23001], "show_index": 1,
                                     "plant_list": [{"index": i, "id": 0, "stage": 0} for i in (1, 2)]},
        "task.load": {"list": [], "tasks": []}, "task.load_list": {"reward": []},
        "adsmgr.load": {"gift_can_get": 0, "day_left": 0, "gift_time": 0, "gift_get": 0,
                        "item_list": {}, "gift_id": 0, "can_pop": False, "can_banner": False},
        "client.load_publicity": {"id_list": []},
        "encyclopedia.load": {"unlock_list": [], "show_sub": [], "unlock_desc": []},
        "encytravel.load": {"unlock_list": [], "show_sub": [], "unlock_desc": []},
        "misc.moment_load": {"list": []},
        "animpicture.load": {"page_num": 0, "making_index": -1, "item_num": 0, "pic_list": [], "guide": 0},
        "other.load_touch": {"list": {}, "cur": 0},
        "client.load_decorate": {"has_list": [], "status": 0, "put_id": 0},
        "weather.load": {"hours_type": 1, "weather": 1, "season": 1},
    }
    tasks = game.load_task_static_data()
    data["task.load"]["tasks"] = [{"id": int(i), "pro": 0, "is_reward": False} for i in tasks.get("task_list", {})]
    data["task.load"]["list"] = [{"id": int(i), "pro": 0} for i in tasks.get("list_map", {})]
    data["task.load_list"]["reward"] = [{"id": int(i), "pro": 0} for i in tasks.get("list_type", {})]
    state = mark_portable({"format_version": 18, "created_at": now, "responses": {},
                           "early_pushes": [{"cmd": "easteregg.load", "data": {"egg_list": []}}],
                           "sync_pushes": [{"cmd": k, "data": v} for k, v in data.items()], "offline_meta": {}})
    # Initialize modules through the same defaults used by normal login.
    game.get_lottery_data(state)
    game.get_calendar_state(state)
    game.get_wishing_pool_state(state)
    game.museum_day_payload(state)
    game.greet_card_payload(state)
    game.spring_card_payload(state)
    game.capsule_payload(state)
    game.party_cake_payload(state)
    return state


def validate_state(state: dict) -> None:
    if not isinstance(state, dict) or not isinstance(state.get("sync_pushes"), list):
        raise ValueError("这不是兼容的离线存档或完整登录快照。")
    required = {"client.load_role": dict, "item.load_items": dict, "clover.load_clovers": list}
    for cmd, kind in required.items():
        row = game.find_push(state, cmd)
        if not row or not isinstance(row.get("data"), kind):
            raise ValueError(f"存档缺少有效的 {cmd} 数据。")
    role = game.find_push(state, "client.load_role")["data"]
    if not isinstance(role.get("frog"), dict) or not isinstance(role.get("res"), dict):
        raise ValueError("存档的青蛙或资源数据不完整。")


def read_save(path: Path) -> tuple[dict, str | None]:
    edition = None
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            manifest = json.loads(z.read("manifest.json"))
            if manifest.get("format") != "travel-frog-save" or manifest.get("version") != 1:
                raise ValueError("不支持这个存档包的格式。")
            edition = manifest["edition"]
            state = json.loads(z.read("state.json"))
    elif path.suffix.lower() == ".jsonl":
        state = game.build_state_from_snapshot(path)
        latest = {}
        for row in state["sync_pushes"]:
            latest[row["cmd"]] = row
        state["sync_pushes"] = list(latest.values())
        # A capture may include album detail responses in addition to login.
        full = state.setdefault("offline_meta", {}).setdefault("album_full", {})
        def collect_pictures(value):
            if isinstance(value, dict):
                if all(key in value for key in ("id", "pic_id", "layers")):
                    full[str(int(value["id"]))] = copy.deepcopy(value)
                else:
                    for child in value.values():
                        collect_pictures(child)
            elif isinstance(value, list):
                for child in value:
                    collect_pictures(child)
        for record in game.read_jsonl(path):
            if record.get("kind") == "recv":
                collect_pictures(record.get("message", {}).get("data"))
    else:
        state = json.loads(path.read_text(encoding="utf-8-sig"))
    validate_state(state)
    if edition is None:
        edition = "gallery" if state.get("offline_meta", {}).get("gallery_edition") else "normal"
    return mark_portable(copy.deepcopy(state)), edition


def save_path(root: Path, edition: str) -> Path:
    if edition not in EDITIONS:
        raise ValueError("未知版本。")
    return root / edition / ("gallery_state.json" if edition == "gallery" else "state.json")


def export_save(state_path: Path, destination: Path, edition: str) -> None:
    state = json.loads(state_path.read_text(encoding="utf-8"))
    validate_state(state)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_name(destination.name + ".tmp")
    with zipfile.ZipFile(temp, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps({"format": "travel-frog-save", "version": 1,
                                               "edition": edition, "exported_at": int(time.time())}))
        z.writestr("state.json", json.dumps(mark_portable(state), ensure_ascii=False))
    temp.replace(destination)


def import_save(source: Path, root: Path, edition: str) -> Path:
    state, source_edition = read_save(source)
    if source_edition != edition:
        raise ValueError(f"这是{EDITIONS.get(source_edition, source_edition)}存档，请切换到对应版本后导入。")
    target = save_path(root, edition)
    target.parent.mkdir(parents=True, exist_ok=True)
    with game.state_process_lock(target):
        if target.exists():
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            export_save(target, root / edition / "import-backups" / f"before-import-{stamp}.frogsave", edition)
        game.atomic_write_json(target, state)
    return target


def create_save(root: Path, edition: str, name: str = "呱呱") -> Path:
    target = save_path(root, edition)
    if target.exists():
        raise FileExistsError("已有存档，请先导出备份或选择另一个数据目录。")
    state = new_state(name)
    if edition == "gallery":
        from tools.build_gallery_state import make_gallery_state
        state = make_gallery_state(state)
    game.atomic_write_json(target, state)
    return target
