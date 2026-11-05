from __future__ import annotations

import argparse
import asyncio
import copy
import json
import logging
import hashlib
import os
import re
import sys
import time
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import websockets


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DEFAULT_SNAPSHOT = ROOT / "capture" / "initial_sync" / "protocol_full_LOCAL_ONLY.jsonl"
DATA_DIR = Path(__file__).resolve().parent / "data"
CONFIG_DIR = Path(__file__).resolve().parent / "config"
POLICY_FILE = Path(__file__).resolve().parent / "offline_policy.json"
STATE_FILE = DATA_DIR / "state.json"
BACKUP_DIR = DATA_DIR / "backups"
UNKNOWN_LOG = DATA_DIR / "unknown_commands.jsonl"
LOG_FILE = Path(__file__).resolve().parent / "server.log"
GALLERY_MODE = False
GALLERY_DECORATION_IDS = (
    100, 101, 102, 103, 104, 105, 106,
    1001, 1002, 1003, 1004, 1005,
    10011, 10012, 10013, 10021, 10022, 10023,
    10031, 10032, 10033, 10041, 10042, 10043, 10044,
    10051, 10052, 10053, 10054, 10061, 10062, 10063,
)
GALLERY_MOMENT_IDS = tuple(range(1, 22)) + tuple(range(101, 109)) + (901,)
BACKUP_INTERVAL_SECONDS = 60
_LAST_BACKUP_AT: float | None = None

# The client arms a millisecond timer from end_time. Renew this short horizon
# at each load so permanent events stay open without overflowing that timer.
ALWAYS_ON_HORIZON = 20 * 86400
LUNAR_FESTIVALS = json.loads((CONFIG_DIR / "lunar_festivals.json").read_text(encoding="utf-8"))


def seasonal_activity_window(kind: str, now: int) -> tuple[int, int, int]:
    """Return the active local-time window (inclusive) and its season year."""
    if GALLERY_MODE:
        return now - 86400, now + ALWAYS_ON_HORIZON, 0
    today = datetime.fromtimestamp(now).date()
    if kind == "party_cake":
        season_year = today.year if today.month == 12 else today.year - 1
        start = date(season_year, 12, 14)
        finish = date(season_year + 1, 1, 12)
    else:
        dates = LUNAR_FESTIVALS.get(str(today.year))
        if dates is None:
            return 0, 0, 0
        festival = date.fromisoformat(dates[0 if kind == "spring_card" else 1])
        radius = 15 if kind == "spring_card" else 10
        start, finish, season_year = festival - timedelta(days=radius), festival + timedelta(days=radius), today.year
    if not start <= today <= finish:
        return 0, 0, 0
    return int(datetime.combine(start, datetime.min.time()).timestamp()), int(datetime.combine(finish + timedelta(days=1), datetime.min.time()).timestamp()) - 1, season_year


def always_on_end_time(previous: int, now: int) -> int:
    if now + 14 * 86400 <= previous <= now + ALWAYS_ON_HORIZON:
        return previous
    return now + ALWAYS_ON_HORIZON

LOCAL_TOKEN = "frog-local-token"
LOCAL_ACCOUNT = "frog_local_offline"

EARLY_PUSH_CMDS = {"easteregg.load"}

# 这些是初始化阶段正常出现、且不需要服务器回包的客户端信息。
NO_RESPONSE_CMDS = {
    "client.set_ads",
    "client.set_channel",
    "client.set_channel_id",
    "client.set_client_envinfo",
}

# 第一阶段明确允许持久化的无回包设置。
PERSISTENT_NO_RESPONSE_CMDS = {
    "client.set_client",
    "client.set_achieve",
    "client.set_icon",
}

# Stage 6A: evidence-backed frog/drawing states.
FROG_HOME = 0
FROG_TRAVEL = 1
FROG_PARTY = 3

# These are the two explicit GuideTask client reports in the preserved client:
# opening the map and opening Travel Notes.  Their list entries are defined in
# the same official Task config that supplies the title, target and rewards.
GUIDE_CLIENT_PROGRESS_LIST_IDS = {
    "Map": 103,
    "NoteFriend": 306,
}

DRAW_WAIT = 0
DRAW_INVITE = 1
DRAW_ACCEPT = 2
DRAW_LOCK = 3
DRAW_VISIT = 4

LOTTERY_OPEN = 0
LOTTERY_SELECT = 1
LOTTERY_COMPLETE = 2
LOTTERY_REWARD = 3
LOTTERY_CLOVER_ITEM_ID = 200000
LOTTERY_TICKET_ITEM_ID = 200001
LOTTERY_WEEK_SECONDS = 7 * 86400
LOTTERY_PHASE_171_START = 1789142400  # 2026-09-12 00:00 UTC+8

# Stage 6G: official client constants for the permanent shop Gacha.
# These are preserved in main.min.js; unlike server-side event probabilities,
# they are not guessed local policy values.
GACHA_NEED_TICKETS = 5
GACHA_RANK_WEIGHTS = ((0, 40), (1, 25), (2, 22), (3, 9), (4, 3), (5, 1))
CALENDAR_CLOVER_TARGET = 30
CALENDAR_GACHA_TARGET = 1
STAGE6G_CALENDAR_CALIBRATION_AT = 1789142400  # 2026-09-12 00:00 China Standard Time

# Stage 6H: photo preservation / dynamic-photo constants.
ANIM_PICTURE_PAGE_ITEM_ID = 9001
ANIM_PICTURE_ALBUM_ITEM_ID = 9002
ANIM_PICTURE_DEVELOPER_IDS = {8002, 8003, 8004}

# The preserved client sends only ComposeId (5502), not selected material IDs.
# Its compose panel groups the three wooden-amulet fragments by ItemType.COMPOSE
# sub-type: 8501 / 8502 / 8503.
HANDCRAFT_COMPOSE_ID = 5502
HANDCRAFT_COMPONENTS = ((8501,), (8502,), (8503,))
HANDCRAFT_AMULET_IDS = tuple(range(1300, 1310))
HANDCRAFT_TOOL_ID = 7000
HANDCRAFT_MATERIAL_ID = 8000
HANDCRAFT_FRAGMENT_IDS = (8501, 8502, 8503)
HANDCRAFT_PRAY_IDS = tuple(range(1, 8))
HANDCRAFT_STAMP_IDS = tuple(range(1, 10)) + tuple(range(101, 110)) + tuple(range(201, 210))
HANDCRAFT_PRAY_STAGE_COSTS = (1, 2, 3, 4)
HANDCRAFT_STAMP_STAGE_COSTS = (1, 2, 3)

# Official furniture and gift tables identify these as the sixteen regular
# 夕拾 rewards and the six permanent special-gift entries in 嘟嘟's shop.
XI_SHI_PARTY_FURNITURE_IDS = tuple(range(10001, 10017))
DUDU_FURNITURE_GIFT_TARGETS = {
    201001: ("furniture", 1531),  # 书桌·古琴
    201002: ("furniture", 1532),  # 衣柜·古琴
    201003: ("furniture", 10017), # 森之国度·小鱼干
    201004: ("furniture", 10018), # 森之国度·小鱼冻
    201008: ("furniture", 10019), # 森之国度·猫猫帽
    201009: ("flowerpot", 23002), # 迎春花盆
}

# The preserved Item/Furniture tables name these as the Wishing Pool rewards.
# The first three are direct furniture grants; the single-use gift bag supplies
# one of the remaining three pieces in the same style.
WISHING_POOL_FURNITURE_TARGETS = {
    201005: 10101,  # 帘幕·许愿池
    201006: 10102,  # 地毯·许愿池
    201007: 10103,  # 墙壁·许愿池
}
WISHING_POOL_GIFT_BAG_TARGETS = (10104, 10105, 10106)
WISHING_POOL_REWARDS = (201005, 201006, 201007, 204002)

# Production captures preserved by the project. These are observations, not
# reconstructed probabilities. They let the offline server reproduce the two
# phases actually seen in the account snapshots without inventing a pool.
CAPTURED_LOTTERY_SELECT_LISTS = {
    170: [4008,4011,4009,4005,4015,4001,4017,4010,3012,3004,4000,3008,4002,4004,3000],
    171: [4009,4016,4014,4012,4011,3005,4013,4001,4006,4005,3019,4002,4010,4015,4000],
    172: [202244,202242,202101,202105,202109,202223,202107,1000,202254,202231,1201,202251,202108,202212,202102],
}

# Client constant Tabikaeru.Define.FROG_STANDBY_WAIT_MIN == 60.
# Use the same minimum as the local deterministic departure delay.
DEPART_DELAY_SECONDS = 60

# Stage 6C static configuration. These files are extracted from the user's
# preserved official config.eab, not scraped/reconstructed data.
_STATIC_CACHE: dict[str, Any] = {}

DEFAULT_OFFLINE_POLICY = {
    "guest": {
        # UNKNOWN CURRENT SERVER VALUE. Local simulation placeholder only.
        "party_invite_percent": 15,
        # Legacy official client constants.
        "normal_weights": {"clover": 80, "four_clover": 18, "ticket": 2},
        "rare_weights": {"clover": 20, "four_clover": 50, "ticket": 30},
        # Exact 2026 server amount formula is not present in the client.
        "clover_min": 20,
        "clover_max": 60,
        "ticket_min": 1,
        "ticket_max": 3,
    },
    "visitor_schedule": {
        "enabled": True,
        # Official legacy client constants:
        # FRIEND_VISIT_COOL=21600, FRIEND_VISIT_RNDSEC=1800,
        # FRIEND_VISIT_RNDPER=10, FRIEND_RNDPOS_MAX=3.
        # The local scheduler interprets them as a 6h cooldown followed by
        # one 10% appearance check every 30 min. This interpretation is
        # official-derived but NOT verified as the exact 2026 server algorithm.
        "cooldown_seconds": 21600,
        "check_interval_seconds": 1800,
        "appearance_percent_per_check": 10,
        "position_count": 3,
        "max_checks": 48,
        "stay_min_seconds": 7200,
        "stay_max_seconds": 21600,
        "served_departure_min_seconds": 7200,
        "served_departure_max_seconds": 14400,
    },
    "courtyard_traveler": {
        # Local preservation timing; official server matching is unavailable.
        "arrival_min_seconds": 172800,
        "arrival_max_seconds": 432000,
        "stay_min_seconds": 1200,
        "stay_max_seconds": 3600,
    },
    "party": {
        # Return timing/reward probabilities are not present in the current
        # client. These are explicit local preservation-policy defaults.
        "auto_return": True,
        "duration_seconds": 1800,
        "clover_min": 50,
        "clover_max": 120,
        "ticket_percent": 50,
        "ticket_min": 1,
        "ticket_max": 4,
        "page_percent": 20,
        "collection_percent": 10,
        # All sixteen ordinary 夕拾 items are part of the permanent
        # companion-party reward pool. The retired server probability is not
        # present in the client, so offline play uses one missing item per
        # completed party until this set is collected.
        "furniture_percent": 100,
    },
    "lottery": {
        # Production captures: phase 171 opened with 10 clover + item 101x1;
        # phase 172 opened with 8 clover + item 105x1.
        "open_clover": 10,
        "open_clover_by_phase": {"171": 10, "172": 8},
        "extra_item_by_phase": {
            "171": {"item_id": 101, "count": 1},
            "172": {"item_id": 105, "count": 1},
        },
        "candidate_count": 15,
        "selection_count": 5,
        "allow_local_future_phase_pool": True,
    },
    "museum_day": {
        # Museum ids, their ticket rewards and the eight-step map presentation
        # are retained by the official client/config. The retired service no
        # longer exposes a live activity clock or refresh quota, so the local
        # server keeps the event available and records its own route state.
        "enabled": True,
        "refreshes_per_exploration": 3,
        "restart_clover": [100, 200, 200],
    },
    "greet_card": {
        # The Mid-Autumn greeting-card layouts, tag IDs, mooncake selections
        # and reward pool are retained in greetCard_json. The service-owned
        # schedule follows the annual lunar Mid-Autumn date.
        "enabled": True,
        "tag_price": 20,
        "background_prices": [0, 100, 150, 200],
    },
    "spring_card": {
        # springCard_json retains all five card layouts and twelve sticker
        # types. Its box-drop table was server-owned, so local play preserves
        # the official blessing-box outcome when no fixed item reward exists.
        "enabled": True,
        "bag_price": 20,
        "bag_limit": 6,
        "share_limit": 3,
    },
    "party_cake": {
        # PartyCakeData_json retains the five cake parts, fourteen layer costs
        # and every layer reward. The retired event service no longer supplies
        # its daily task callbacks, so the annual anniversary window provisions the
        # exact total of cream and sugar required for one complete cake.
        "enabled": True,
        "starter_cream": 68,
        "starter_sugar": 41,
    },
    "special_titles": {
        # The original event service issued expiring title props.  Offline
        # milestones grant their corresponding title permanently instead.
        "party_cake_light": 900,
        "museum_day_collection": 901,
        "furniture_creation": 902,
    },
    "capsule": {
        # capsuleData_json retains the 16-draw board, its 25 reward entries,
        # three milestone gifts and the 30-clover fast-complete cost.
        "enabled": True,
        "fast_consume": 30,
        "starter_coins": 2,
        "remaining_task_count": 14,
    },
    "giftbox_friend": {
        # Gift-box storage and TravelFriends (壁虎/刺猬/萤火虫) are official.
        # Trigger timing is local preservation policy; reward type weights below
        # come from legacy official FRIEND_GIFTPER_NORMAL constants.
        "enabled": True,
        "picture_capacity": 30,
        "specialty_capacity": 30,
        "delay_seconds": 1800,
        "trigger_percent_per_login": 25,
        "consume_specialty_on_visit": True,
        "consume_picture_on_visit": True,
        "normal_weights": {"clover": 80, "four_clover": 18, "ticket": 2},
        "clover_amount": 20,
        "ticket_min": 1,
        "ticket_max": 3,
        # Official 1.7.0 notes say a travel friend MAY return an exclusive postcard,
        # but the exact server probability is not present in current static config.
        # 100% is the user's preservation preference and remains editable policy.
        "postcard_percent": 100,
        # Candidate postcards are NOT invented: they are read from PictureTag rows
        # tagged Unique + _TRAVELER_ID + the corresponding TravelFriend id.
        "postcard_source": "official_traveler_tags",
    },
    "story_social": {
        # story_json.const.friend_choice_percent == 25 is an official static value.
        # Offline mode removes real-player identity and return-gift obligation.
        "enabled": True,
        "encounter_percent": 25,
        "amulet_encounter_bonus_percent": {"1012": 10, "1100": 20},
        "anonymize_existing_names": True,
        "generic_name": "旅途中的朋友",
        "return_gift_required": False,
    },
    "vase": {
        # The two client decoration sprites are the fresh and wilted states.
        # Current static assets do not retain production timing, so this is the
        # offline preservation cadence: two days fresh, then one day wilted.
        "fresh_seconds": 172800,
        "withered_seconds": 86400,
        "frog_return_arrange_percent": 35,
    },
    "calendar": {
        # Ad/share-only solar-term task is removed in the offline build.
        # Clover and lottery tasks remain authoritative.
        "offline_complete_ad_share_task": True,
    },
    "home_motion": {
        # The client retains the four daytime Frogpattern rows but not the
        # retired service's home schedule. Keep a different, saved bedtime
        # for each local day instead of a fixed 22:00–06:00 block.
        "refresh_min_seconds": 3600,
        "refresh_max_seconds": 10800,
        "sleep_start_min_hour": 20,
        "sleep_start_max_hour": 23,
        "sleep_min_seconds": 25200,
        "sleep_max_seconds": 32400,
        # Watermelon is a separate EasterEgg clip, not FrogMotionName.eat.
        # It is a short summer daytime appearance so regular home actions
        # remain visible for the rest of the day.
        "summer_watermelon_months": [6, 7, 8],
        "summer_watermelon_start_min_hour": 11,
        "summer_watermelon_start_max_hour": 17,
        "summer_watermelon_duration_seconds": 5400,
    },
    "handcraft": {
        "enabled": True,
        "job_seconds": 1800,
        "material_travel_percent": 35,
        "fragment_travel_percent": 35,
        "travel_effects": {
            "1300": {"clover_bonus": 30},
            "1301": {"duration_multiplier": 0.7},
            "1302": {"collection_percent_bonus": 35},
            "1303": {"ticket_percent_bonus": 35},
            "1304": {"specialty_percent_bonus": 35},
            "1305": {"base_picture_percent_bonus": 25},
            "1306": {"unique_picture_percent_bonus": 25},
            "1307": {"tool_picture_percent_bonus": 35},
            "1308": {"normal_picture_percent_bonus": 35},
            "1309": {"story_encounter_bonus_percent": 25},
        },
    },
    "furniture": {
        # The client defines the workbench slots and completion event, but not
        # the production-server's recipe or timer. These are local offline
        # preservation values for ordinary material consumption.
        "enabled": True,
        "start_delay_min_seconds": 7200,
        "start_delay_max_seconds": 172800,
        "job_min_seconds": 3600,
        "job_max_seconds": 43200,
        "ordinary_material_count": 3,
        # Guides and live behavior confirm that repeated manual redecorating
        # can upset the frog and leave the workbench unusable. The threshold,
        # probability and recovery cadence are local offline values.
        "manual_replace_mess_after": 12,
        "manual_replace_mess_percent": 35,
        "mess_recovery_trips": 3,
        "home_auto_replace_percent": 35,
    },
    "picture_generation": {
        # First choice is always an exact official layer template captured from
        # the user's account. Static reconstruction is only a fallback.
        "prefer_official_templates": True,
        # For still-unseen Goal/Tools character resources, Stage 6H uses the
        # median dimensions of the same role rather than the old Qinghai-only
        # hard-coded offset. This is local fallback policy, not an official rule.
        "allow_estimated_character_size": True,
    },
    "animpicture": {
        # The six templates, phase thresholds, item IDs and protocol lifecycle
        # are official client/static data. The current client does NOT expose
        # the production server's EXP gain per developer liquid, so these gains
        # are explicit local preservation policy.
        "developer_exp": {"8002": 20, "8003": 50, "8004": 100},
        "extra_photo_bonus_per_photo": 0,
    },
    "garden": {
        # Static species/seed/furniture relations are official config.eab data.
        # Growth points/times are local preservation policy because the current
        # client does not contain the 2026 server formulas.
        "enabled": True,
        "stage_points": [0, 100, 200, 300],
        "passive_growth_points_per_hour": 10,
        "guest_growth_points": 60,
        "auto_plant_on_guest_departure": True,
        "max_auto_plants_per_guest": 1,
        "fertility_multiplier_by_state": {"0": 1.0, "1": 1.1, "2": 1.25, "3": 1.5, "4": 1.8},
        "fertilizer_points": {
            "20001": 35, "20002": 70, "20003": 120,
            "20004": 30, "20005": 60, "20006": 100
        },
        "specialty_compost_points": 18,
        "fertilizer_duration_seconds": {
            "20001": 86400, "20002": 86400, "20003": 86400,
            "20004": 259200, "20005": 259200, "20006": 259200
        },
        "specialty_compost_duration_seconds": 86400,
        # The current client exposes the queued/fermenting slot but does not
        # include the retired-server timers. These are local offline timings:
        # a new input has a short retrieval window, then one queued slot
        # ferments at a time and is consumed.
        "compost_lock_delay_seconds": 300,
        "compost_absorb_seconds": 7200,
        "pocket_capacity": 50,
        "dudu_arrive_hour": 12,
        "dudu_leave_hour": 24,
        "dudu_min_stay_minutes": 360,
        "dudu_max_stay_minutes": 480,
        "dudu_total_slots": 10,
        "dudu_seed_slots": 3,
        "dudu_fertilizer_slots": 2
    },
    "clover": {
        # Official CloverFarm client constants: fourLeaf_percent=0.5,
        # growth mean ~=7200s and max 14400s. The exact 2026 server-side
        # basket/net algorithm is not present in the client; overflow collection
        # below is a local preservation policy based on the observed mechanic.
        "net_enabled": True,
        "net_max_credit_per_login": 100,
        "four_leaf_percent": 0.5,
        # Server probability for spontaneous courtyard weeds is unknown.
        # Existing imported element=3 plants are preserved exactly; generation
        # of new weeds stays disabled until calibrated.
        "wild_plant_percent": 0.0,
        "wild_decoration_ids": [1001, 1002, 1003, 1004, 1005],
    },
    "travel": {
        "auto_return": True,
        # Exact current-server route/duration algorithm is server-side. These
        # durations are local policy. Set force_duration_seconds > 0 for tests.
        "force_duration_seconds": 0,
        # The user observed production can sometimes depart without manually
        # locking the bag. Exact 2026 server timing is unknown, so this
        # compatibility path is supported but disabled by default.
        "allow_unconfirmed_auto_departure": False,
        "unconfirmed_departure_seconds": 21600,
        "duration_seconds_by_food": {
            "0": 1800,
            "1": 3600,
            "2": 7200,
            "3": 5400,
            "default": 7200
        },
        # Exact official client constants retained in current main.min.js.
        "base_picture_percent": 70,
        # Normal postcards are the generic on-the-way photos.  Reuse the
        # legacy base-photo percentage for their independently drawn slot.
        "normal_picture_percent": 70,
        "max_pictures": 4,
        # Captured production behavior: eligible pictures grant 20 clover on
        # share, and the final frog-back prompt offers two additional photos.
        "photo_share_clover": 20,
        "frog_back_bonus_picture_count": 2,
        "frog_back_bonus_picture_percent": 100,
        "tool_picture_bonus": {"0": 0, "1": 15, "2": 30, "3": 50},
        # PictureTag defines four authored poses for every tool context:
        # index 0 has no matching carried tool, while 1..3 are the three
        # shop tiers.  The place/context association below follows the
        # verified guide; its probability remains an explicit local policy.
        "tool_picture_percent": 40,
        "tool_picture_environment_by_place": {
            "1": ["town"], "2": ["wet"], "3": ["fuza"],
            "4": ["wet"], "5": ["wet"], "6": ["wet"], "7": ["wet"],
            "8": ["dry", "wet"], "9": ["dry"], "10": ["dry"],
            "11": ["town"], "12": ["fuza", "town"],
            "13": ["wet", "fuza", "town"], "14": ["wet", "town"],
            "15": ["dry", "wet"], "16": ["dry", "wet"], "17": ["dry"],
            "18": ["dry", "wet", "fuza"], "19": ["dry", "wet", "fuza"],
            "20": ["wet"], "21": ["dry", "wet"], "22": ["wet", "fuza"],
            "23": ["dry", "wet", "town"], "24": ["dry", "wet"],
            "25": ["wet", "fuza"], "27": ["fuza", "town"],
            "28": ["dry", "wet", "town"]
        },
        "specialty_percent": 60,
        "max_travel_items": 10,
        # Server-side quantities/probabilities not exposed by current client.
        "collection_percent": 25,
        "clover_min": 30,
        "clover_max": 90,
        "ticket_percent": 35,
        "ticket_max": 2,
        "museum_picture_percent": 80,
        "museum_collection_percent": 45,
        "direction_bias_weight": 5,
        "amulet_place_bias_map": {"1100": [24, 25]},
        "amulet_place_bias_weight": {"1100": 20},
        # These authored Unique cards are rare on-the-way encounters rather
        # than food, route, or event rewards.  They use the same trip return
        # path as Normal cards while remaining a separate rare family.
        "unique_picture_percent": 15,
        "unique_picture_pool": [
            3014, 3017, 3018, 3019, 3021, 3031, 3035, 3038, 3039, 3040,
            3041, 3042, 3043, 3044, 3085, 3088, 3101, 3104, 3105, 3106
        ],
        "food_affinity_weight": 5,
        "unseen_bias_weight": 4,
        # Local representative mapping, not the official route algorithm.
        # Food 109 (pineapple pastry) selects GoalNumber 9 (垦丁) or 10 (十分)
        # with equal 50/50 candidate probability.
        "target_food_place_map": {
            "101": 1, "102": 2, "103": 3, "104": 4, "105": 5,
            "106": 6, "107": 7, "108": 8, "110": 12, "111": 18,
            "112": 21, "113": 14, "114": 13, "115": 16, "116": 15,
            "109": [9, 10],
            "117": 17, "118": 19, "119": 20, "120": 11, "121": 23,
            "122": 22, "123": 24, "124": 25, "125": 26, "126": 27,
            "127": 28, "128": 29, "129": 30, "130": 31, "131": 32,
            "132": 33, "133": 35, "134": 34
        },
        # Current content-map relations. The current client keeps the photo
        # families but leaves reward selection to the retired service.
        "food_picture_map": {
            "18": [3062], "20": [3068, 3168], "21": [3049, 3149, 3209],
            "22": [3050, 3150, 3210], "23": [3071, 3171], "24": [3072, 3172],
            "25": [3073, 3173, 3109, 3110], "26": [3074, 3174], "27": [3055, 3155],
            "28": [3076, 3176, 3180], "29": [3077, 3177, 3181],
            "30": [3058, 3158, 3182], "31": [3059, 3159, 3206],
            "32": [3062], "34": [3060], "35": [3048, 3148, 3208],
            "36": [3069, 3169], "37": [3070, 3170], "38": [3051, 3151, 3211],
            "39": [3052, 3152, 3212], "40": [3053, 3153, 3213],
            "41": [3054, 3154], "42": [3075, 3175], "43": [3056, 3156],
            "44": [3057, 3157], "45": [3078, 3178], "46": [3079, 3179],
            "47": [3030, 3080, 3111], "48": [3030, 3080, 3111],
            "49": [3030, 3080, 3111], "50": [3030, 3080, 3111],
            "51": [3030, 3080, 3111], "52": [3030, 3080, 3111],
            "53": [3030, 3080, 3111], "54": [3061, 3081], "55": [3086],
            "56": [3092, 3093, 3094, 3095, 3098, 3099, 3100],
            "57": [3102, 3103, 3207]
        },
        # The client exposes only the standard 70% picture chance. Foods
        # whose text says "chance" use that rate; the two explicit guarantees
        # keep their documented 100% behavior.
        "food_picture_percent": {
            "18": 25, "20": 70, "21": 70, "22": 70, "23": 70, "24": 70,
            "25": 70, "26": 70, "27": 70, "28": 70, "29": 70, "30": 70,
            "31": 70, "34": 70, "35": 70, "36": 70, "37": 70, "38": 70,
            "39": 70, "40": 70, "41": 70, "42": 70, "43": 70, "44": 70,
            "45": 70, "46": 70, "47": 70, "48": 70, "49": 70, "50": 70,
            "51": 70, "52": 70, "53": 70, "54": 70, "55": 70, "56": 70
        },
        # 旅行照片按原本的随机池抽取，只有彩叶幸运草会优先补齐指定
        # 特殊食物尚未拥有的照片；伴柠伴桔是 50% 蓝蚂蚁照的独立条件。
        "food_picture_prefer_unowned_amulet": {
            "1200": [18, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31,
                     35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 54, 56]
        },
        "food_amulet_picture_map": {"18": {"1200": [3062]}},
        "food_amulet_picture_percent": {"18": {"1200": 50}},
        "road_sign_route_map": {
            "1101": [105, 106], "1102": [105, 106], "1103": [105, 106]
        },
        "road_sign_route_percent": {"1101": 20, "1102": 35, "1103": 50},
        "special_route_picture_map": {
            "105": [124, 125, 2107, 2108, 3046, 3047],
            "106": [130, 131, 2138, 2139, 3090, 3091]
        },
        "amulet_picture_map": {
            "1105": [126, 3022, 3023, 3045],
            "1106": [3092, 3093, 3094, 3095, 3098, 3099, 3100]
        },
        "amulet_picture_percent": {"1105": 35, "1106": 100},
        "lucky_flower_picture_pool": [201, 202, 203, 204, 205, 206, 207,
                                       3033, 3034, 3096, 3097, 3107, 3108,
                                       3201, 3202, 3203, 3204, 3205],
        "lucky_flower_picture_percent": 35
    },
    "wishing_pool": {
        "enabled": True,
        "starter_coins": 4,
        "items": [
            {"id": 201005, "num": 1, "limit": 1},
            {"id": 201006, "num": 1, "limit": 1},
            {"id": 201007, "num": 1, "limit": 1},
            {"id": 204002, "num": 1, "limit": 1},
        ],
    },
    "tutorial": {
        "completion_rewards": {
            "clover": 100,
            "ticket": 5,
            "items": [{"item_id": 1000, "count": 1}],
        },
    },
}


def load_static_json(filename: str) -> Any:
    if filename not in _STATIC_CACHE:
        path = CONFIG_DIR / filename
        with path.open("r", encoding="utf-8") as f:
            _STATIC_CACHE[filename] = json.load(f)
    return _STATIC_CACHE[filename]


def load_task_static_data() -> dict:
    """Read the official GuideTask table directly from the preserved bundle."""
    cache_key = "__taskData_json__"
    cached = _STATIC_CACHE.get(cache_key)
    if isinstance(cached, dict):
        return cached

    from tools.resources.scan_eab_cache import parse_eab

    parsed = parse_eab(ROOT / "client" / "config" / "config.eab")
    if parsed is None:
        raise RuntimeError("无法读取 client/config/config.eab 中的 taskData_json")
    assets, offset, raw = parsed
    for asset in assets:
        size = int(asset.get("s", 0) or 0)
        if asset.get("n") == "taskData_json":
            data = json.loads(raw[offset:offset + size].decode("utf-8"))
            if not isinstance(data, dict):
                raise RuntimeError("taskData_json 不是对象")
            _STATIC_CACHE[cache_key] = data
            return data
        offset += size
    raise RuntimeError("config.eab 中缺少 taskData_json")


def load_offline_policy() -> dict:
    policy = copy.deepcopy(DEFAULT_OFFLINE_POLICY)
    if POLICY_FILE.exists():
        try:
            with POLICY_FILE.open("r", encoding="utf-8") as f:
                custom = json.load(f)
            for section, values in custom.items():
                if isinstance(values, dict) and isinstance(policy.get(section), dict):
                    policy[section].update(values)
                else:
                    policy[section] = values
        except Exception:
            logging.exception("读取 offline_policy.json 失败，使用默认本地模拟参数")
    return policy


def deterministic_roll(state: dict, label: str, upper: int) -> int:
    """Persistent deterministic pseudo-random roll in [0, upper).

    It is intentionally reproducible from state.json: restarting the local
    server cannot reroll a result by accident.
    """
    if upper <= 0:
        return 0
    meta = state.setdefault("offline_meta", {})
    counter = int(meta.get("rng_counter", 0) or 0) + 1
    meta["rng_counter"] = counter
    raw = f"{LOCAL_ACCOUNT}|{counter}|{label}".encode("utf-8")
    value = int.from_bytes(hashlib.sha256(raw).digest()[:8], "big")
    return value % upper


def weighted_choice(state: dict, label: str, weights: dict[str, int]) -> str:
    rows = [(str(k), max(0, int(v))) for k, v in weights.items()]
    total = sum(v for _, v in rows)
    if total <= 0:
        return rows[0][0] if rows else "clover"
    r = deterministic_roll(state, label, total)
    acc = 0
    for key, weight in rows:
        acc += weight
        if r < acc:
            return key
    return rows[-1][0]


def configure_logging() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(LOG_FILE, encoding="utf-8"),
        ],
    )


def read_jsonl(path: Path) -> list[dict]:
    rows = []
    with path.open("r", encoding="utf-8-sig") as f:
        for lineno, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"{path} 第 {lineno} 行 JSON 解析失败: {exc}") from exc
    return rows


def build_state_from_snapshot(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(
            f"找不到完整本地抓取文件:\n{path}\n"
            "需要 protocol_full_LOCAL_ONLY.jsonl，而不是 SHARE_SAFE 文件。"
        )

    records = read_jsonl(path)

    request_by_session: dict[int, str] = {}
    response_by_cmd: dict[str, Any] = {}
    pushes: list[dict] = []

    for rec in records:
        kind = rec.get("kind")
        msg = rec.get("message")
        if not isinstance(msg, dict):
            continue

        if kind == "send":
            session = msg.get("session")
            cmd = msg.get("cmd")
            if isinstance(session, int) and isinstance(cmd, str):
                request_by_session[session] = cmd

        elif kind == "recv":
            cmd = msg.get("cmd")
            session = msg.get("session")
            if isinstance(cmd, str):
                pushes.append(copy.deepcopy(msg))
            elif isinstance(session, int):
                req_cmd = request_by_session.get(session)
                if req_cmd:
                    response_by_cmd[req_cmd] = copy.deepcopy(msg.get("data", {}))

    early = [p for p in pushes if p.get("cmd") in EARLY_PUSH_CMDS]
    sync = [p for p in pushes if p.get("cmd") not in EARLY_PUSH_CMDS]

    if not sync:
        raise RuntimeError("快照里没有初始化 server push。")

    state = {
        "format_version": 2,
        "created_at": int(time.time()),
        "source_snapshot": str(path),
        "responses": response_by_cmd,
        "early_pushes": early,
        "sync_pushes": sync,
    }
    return state


def atomic_write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def backup_state(*, force: bool = False) -> None:
    global _LAST_BACKUP_AT
    if not STATE_FILE.exists():
        return
    now = time.monotonic()
    if not force and _LAST_BACKUP_AT is not None and now - _LAST_BACKUP_AT < BACKUP_INTERVAL_SECONDS:
        return
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    dest = BACKUP_DIR / f"state_{stamp}.json"
    dest.write_bytes(STATE_FILE.read_bytes())
    _LAST_BACKUP_AT = now


def save_state(state: dict, reason: str) -> None:
    if GALLERY_MODE:
        normalize_gallery_resources(state)
    backup_state()
    atomic_write_json(STATE_FILE, state)
    logging.info("STATE SAVED reason=%s file=%s", reason, STATE_FILE)


def load_or_create_state(snapshot: Path, reset: bool = False) -> dict:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if GALLERY_MODE and not STATE_FILE.exists():
        raise FileNotFoundError("图鉴版存档不存在；先运行 tools/build_gallery_state.py")

    if reset and STATE_FILE.exists():
        backup_state(force=True)
        STATE_FILE.unlink()
        logging.warning("按 --reset-state 要求重建本地 state。")

    if STATE_FILE.exists():
        with STATE_FILE.open("r", encoding="utf-8") as f:
            state = json.load(f)
        logging.info("加载本地可写存档: %s", STATE_FILE)
        return state

    state = build_state_from_snapshot(snapshot)
    atomic_write_json(STATE_FILE, state)
    logging.info("首次启动：已从原始抓取导入可写存档: %s", STATE_FILE)
    return state


def find_push(state: dict, cmd: str) -> dict | None:
    for msg in state.get("sync_pushes", []):
        if msg.get("cmd") == cmd:
            return msg
    for msg in state.get("early_pushes", []):
        if msg.get("cmd") == cmd:
            return msg
    return None


def get_clover_parts(state: dict):
    role_push = find_push(state, "client.load_role")
    field_push = find_push(state, "clover.load_clovers")

    if not role_push:
        raise RuntimeError("state 中找不到 client.load_role")
    if not field_push:
        raise RuntimeError("state 中找不到 clover.load_clovers")

    role_data = role_push.setdefault("data", {})
    res = role_data.setdefault("res", {})

    field = field_push.get("data")
    if not isinstance(field, list):
        raise RuntimeError("clover.load_clovers.data 不是数组")

    return res, field


def clover_is_collectable(info: dict, now: int) -> bool:
    last = int(info.get("last_harvest", 0) or 0)
    span = int(info.get("rebirth_span", 0) or 0)

    # 与客户端 CloverFarm.initData() 的显示判定保持一致：
    # -1 表示客户端本轮已经摘掉；正数且尚未到 last+span 表示仍在生长。
    if last == -1:
        return False
    if last > 0 and last + span > now:
        return False
    return True


def get_decoration_parts(state: dict) -> dict:
    push = find_push(state, "client.load_decorate")
    if not push:
        push = {"cmd": "client.load_decorate", "data": {"has_list": [], "status": 0, "put_id": 0}}
        state.setdefault("sync_pushes", []).append(push)
    data = push.setdefault("data", {})
    if not isinstance(data, dict):
        data = {"has_list": [], "status": 0, "put_id": 0}
        push["data"] = data
    if not isinstance(data.get("has_list"), list):
        data["has_list"] = []
    data.setdefault("status", 0)
    data.setdefault("put_id", 0)
    return data


def decoration_id_from_clover_sprite(sprite: int) -> int:
    sprite = int(sprite)
    if sprite < 10000:
        return sprite
    stage = sprite // 1000000
    return sprite - stage * 1000000


def add_decoration(state: dict, decoration_id: int, count: int = 1) -> int:
    data = get_decoration_parts(state)
    rows = data["has_list"]
    for row in rows:
        if isinstance(row, dict) and int(row.get("id", -1)) == int(decoration_id):
            row["num"] = int(row.get("num", 0) or 0) + int(count)
            return int(row["num"])
    rows.append({"id": int(decoration_id), "num": int(count)})
    return int(count)


def vase_policy(policy: dict | None = None) -> dict:
    if policy is None:
        policy = load_offline_policy()
    return policy.get("vase", {}) if isinstance(policy, dict) else {}


def advance_vase_decoration(state: dict, now: int | None = None, policy: dict | None = None, *, save: bool = True) -> bool:
    """Advance the courtyard vase through fresh, wilted and empty states."""
    if now is None:
        now = int(time.time())
    data = get_decoration_parts(state)
    decoration_id = int(data.get("put_id", 0) or 0)
    meta = state.setdefault("offline_meta", {}).setdefault("vase", {})
    if decoration_id <= 0:
        if meta:
            meta.clear()
            if save:
                save_state(state, "vase.clear")
            return True
        return False

    if int(meta.get("decoration_id", 0) or 0) != decoration_id:
        meta.clear()
        meta.update({"decoration_id": decoration_id, "inserted_at": int(now)})
        data["status"] = 1
        if save:
            save_state(state, f"vase.fresh:{decoration_id}")
        return True

    vp = vase_policy(policy)
    fresh_seconds = max(1, int(vp.get("fresh_seconds", 172800) or 172800))
    withered_seconds = max(1, int(vp.get("withered_seconds", 86400) or 86400))
    inserted_at = int(meta.get("inserted_at", now) or now)
    age = max(0, int(now) - inserted_at)
    if age < fresh_seconds:
        if int(data.get("status", 0) or 0) == 1:
            return False
        data["status"] = 1
        if save:
            save_state(state, f"vase.fresh:{decoration_id}")
        return True
    if age < fresh_seconds + withered_seconds:
        if int(data.get("status", 0) or 0) == 2:
            return False
        data["status"] = 2
        if save:
            save_state(state, f"vase.withered:{decoration_id}")
        return True

    rows = data.get("has_list", [])
    if isinstance(rows, list):
        for index, row in enumerate(rows):
            if isinstance(row, dict) and int(row.get("id", -1)) == decoration_id:
                row["num"] = int(row.get("num", 0) or 0) - 1
                if int(row["num"]) <= 0:
                    rows.pop(index)
                break
    data.update({"put_id": 0, "status": 0})
    meta.clear()
    if save:
        save_state(state, f"vase.expired:{decoration_id}")
    return True


def frog_arrange_vase_on_return(state: dict, now: int, policy: dict) -> int | None:
    """Give the returning frog one chance to arrange an available flower."""
    advance_vase_decoration(state, now=now, policy=policy, save=False)
    data = get_decoration_parts(state)
    if int(data.get("put_id", 0) or 0) > 0:
        return None
    percent = max(0, min(100, int(vase_policy(policy).get("frog_return_arrange_percent", 35) or 0)))
    if percent <= 0 or deterministic_roll(state, f"vase.frog_return:{now}", 100) >= percent:
        return None
    candidates = [
        int(row.get("id", 0) or 0)
        for row in data.get("has_list", [])
        if isinstance(row, dict) and int(row.get("id", 0) or 0) > 0 and int(row.get("num", 0) or 0) > 0
    ]
    if not candidates:
        return None
    decoration_id = candidates[deterministic_roll(state, f"vase.frog_pick:{now}", len(candidates))]
    data.update({"put_id": decoration_id, "status": 1})
    vase = state.setdefault("offline_meta", {}).setdefault("vase", {})
    vase.clear()
    vase.update({"decoration_id": decoration_id, "inserted_at": int(now)})
    return decoration_id


def _reset_clover_net_cursor(state: dict, clover_id: int, now: int) -> None:
    meta = state.setdefault("offline_meta", {})
    net = meta.setdefault("clover_net", {})
    cursors = net.setdefault("cursor", {})
    cursors[str(int(clover_id))] = int(now)


def _roll_next_clover_kind(state: dict, clover_id: int, policy: dict | None = None) -> tuple[int, int]:
    if policy is None:
        policy = load_offline_policy()
    cp = policy.get("clover", {})
    four_pct = max(0.0, min(100.0, float(cp.get("four_leaf_percent", 0.5) or 0.0)))
    if deterministic_roll(state, f"four_leaf:{clover_id}", 10000) < int(round(four_pct * 100)):
        return 1, 1
    wild_pct = max(0.0, min(100.0, float(cp.get("wild_plant_percent", 0.0) or 0.0)))
    if deterministic_roll(state, f"wild_plant:{clover_id}", 10000) < int(round(wild_pct * 100)):
        ids = [int(x) for x in cp.get("wild_decoration_ids", [1001,1002,1003,1004,1005])]
        if ids:
            did = ids[deterministic_roll(state, f"wild_plant_id:{clover_id}", len(ids))]
            # stage 1 format is simply decoration id (<10000)
            return 3, did
    return 0, 1 + deterministic_roll(state, f"clover_sprite:{clover_id}", 2)


def apply_normal_clover_harvest(
    state: dict,
    clover_id: int,
    *,
    now: int | None = None,
    policy: dict | None = None,
) -> tuple[bool, str, dict[str, Any] | None]:
    """Persist one courtyard harvest for element 0/1/2/3.

    Official client behavior:
      0 normal clover -> local +1 clover
      1 four-leaf -> local +1 item 1000
      2 flower/item -> local +1 sprite item
      3 courtyard decoration/weed -> no local inventory mutation; production
        pushes client.load_decorate.  Captured sprite 2001001 decodes to
        decoration 1001 (狗尾草).
    """
    if now is None:
        now = int(time.time())
    if policy is None:
        policy = load_offline_policy()

    res, field = get_clover_parts(state)
    info = next((x for x in field if int(x.get("clover_id", -1)) == int(clover_id)), None)
    if info is None:
        return False, "unknown_clover_id", None
    if not clover_is_collectable(info, now):
        return False, "not_ready", None

    element = int(info.get("element", 0) or 0)
    sprite = int(info.get("sprite", 0) or 0)
    result: dict[str, Any] = {"clover_id": int(clover_id), "element": element, "sprite": sprite}
    _, house, _, _ = get_item_parts(state)

    if element == 0:
        new_total = int(res.get("clover_point", 0) or 0) + 1
        res["clover_point"] = new_total
        result["clover_total"] = new_total
        task = calendar_record_clover_gain(state, 1)
        if task is not None:
            result["calendar_task"] = task
    elif element == 1:
        item_id = 1000
        count = get_house_count(house, item_id) + 1
        set_house_count(house, item_id, count)
        result.update({"item_id": item_id, "item_count": count})
    elif element == 2:
        item_id = sprite
        if item_id <= 0:
            return False, "bad_element2_sprite", None
        count = get_house_count(house, item_id) + 1
        set_house_count(house, item_id, count)
        result.update({"item_id": item_id, "item_count": count})
    elif element == 3:
        decoration_id = decoration_id_from_clover_sprite(sprite)
        if decoration_id <= 0:
            return False, "bad_decoration_sprite", None
        num = add_decoration(state, decoration_id, 1)
        result.update({"decoration_id": decoration_id, "decoration_num": num})
    else:
        return False, f"unsupported_element_{element}", None

    # Production capture shows a harvested element=3 slot returning later as a
    # normal element=0 plant. Apply the same reset rule to all harvested slots.
    next_element, next_sprite = _roll_next_clover_kind(state, int(clover_id), policy)
    info["element"] = next_element
    info["sprite"] = next_sprite
    info["last_harvest"] = int(now)
    if int(info.get("rebirth_span", 0) or 0) <= 0:
        info["rebirth_span"] = 7200
    _reset_clover_net_cursor(state, int(clover_id), int(now))

    meta = state.setdefault("offline_meta", {})
    receipts = meta.setdefault("clover_receipts", {})
    guide = meta.setdefault("guide_task", {})
    guide["harvest_count"] = int(guide.get("harvest_count", len(receipts))) + 1
    receipts[str(int(clover_id))] = {
        "server_time": int(now),
        "element": element,
        "sprite": sprite,
        "result": copy.deepcopy(result),
    }
    state["format_version"] = max(int(state.get("format_version", 2) or 2), 10)
    save_state(state, f"clover.harvest:{clover_id}:element{element}")
    return True, "ok", result


def current_clover_field(state: dict) -> list[dict]:
    _, field = get_clover_parts(state)
    return copy.deepcopy(field)


def get_item_parts(state: dict):
    push = find_push(state, "item.load_items")
    if not push:
        raise RuntimeError("state 中找不到 item.load_items")

    data = push.setdefault("data", {})
    if not isinstance(data, dict):
        raise RuntimeError("item.load_items.data 不是对象")

    house = data.setdefault("house", [])
    bag = data.setdefault("bag", [-1, -1, -1, -1])
    desk = data.setdefault("desk", [-1, -1, -1, -1, -1, -1, -1, -1])

    if not isinstance(house, list):
        raise RuntimeError("item.load_items.data.house 不是数组")
    if not isinstance(bag, list):
        raise RuntimeError("item.load_items.data.bag 不是数组")
    if not isinstance(desk, list):
        raise RuntimeError("item.load_items.data.desk 不是数组")

    return data, house, bag, desk


def get_house_count(house: list[dict], item_id: int) -> int:
    for row in house:
        if isinstance(row, dict) and int(row.get("item_id", -1)) == item_id:
            return int(row.get("count", 0) or 0)
    return 0


def set_house_count(house: list[dict], item_id: int, count: int) -> None:
    if GALLERY_MODE and item_id in gallery_repeatable_item_ids():
        count = 99
    count = max(0, int(count))
    for i, row in enumerate(house):
        if isinstance(row, dict) and int(row.get("item_id", -1)) == item_id:
            if count <= 0:
                house.pop(i)
            else:
                row["count"] = count
            return
    if count > 0:
        house.append({"item_id": item_id, "count": count})


def get_handcraft_data(state: dict) -> dict:
    """Return the server-owned state behind HandCraftModel.pray_load_grays."""
    push = find_push(state, "pray.load_grays")
    if not push:
        push = {"cmd": "pray.load_grays", "data": {}}
        state.setdefault("sync_pushes", []).append(push)
    data = push.setdefault("data", {})
    if not isinstance(data, dict):
        data = {}
        push["data"] = data
    for key in ("wishs", "stamps"):
        if not isinstance(data.get(key), list):
            data[key] = []
    if not isinstance(data.get("boxes"), (list, dict)):
        data["boxes"] = {}
    return data


def handcraft_policy(policy: dict | None = None) -> dict:
    if policy is None:
        policy = load_offline_policy()
    value = policy.get("handcraft", {}) if isinstance(policy, dict) else {}
    return value if isinstance(value, dict) else {}


def _handcraft_job(state: dict) -> dict | None:
    value = state.setdefault("offline_meta", {}).setdefault("handcraft", {}).get("job")
    return value if isinstance(value, dict) else None


def handcraft_is_active(state: dict, now: int | None = None) -> bool:
    if now is None:
        now = int(time.time())
    job = _handcraft_job(state)
    return bool(job and int(job.get("ready_at", 0) or 0) > now)


def _handcraft_stage(rows: list[dict], craft_id: int) -> int:
    return max(
        [
            int(row.get("state", 0) or 0)
            for row in rows
            if isinstance(row, dict) and int(row.get("id", -1) or -1) == craft_id
        ]
        or [0]
    )


def _next_handcraft_u_id(rows: list[dict]) -> int:
    return max(
        [int(row.get("u_id", 0) or 0) for row in rows if isinstance(row, dict)]
        or [0]
    ) + 1


def _select_handcraft_job(state: dict, data: dict, kind: str) -> tuple[int, int, int] | None:
    if kind == "pray":
        rows = data["wishs"]
        craft_ids = HANDCRAFT_PRAY_IDS
        stage_costs = HANDCRAFT_PRAY_STAGE_COSTS
    else:
        rows = data["stamps"]
        craft_ids = HANDCRAFT_STAMP_IDS
        stage_costs = HANDCRAFT_STAMP_STAGE_COSTS
    maximum = len(stage_costs)
    progressed = [(craft_id, _handcraft_stage(rows, craft_id)) for craft_id in craft_ids]
    in_progress = [(craft_id, stage) for craft_id, stage in progressed if 0 < stage < maximum]
    candidates = in_progress or [(craft_id, stage) for craft_id, stage in progressed if stage < maximum]
    if not candidates:
        return None
    smallest = min(stage for _, stage in candidates)
    candidates = [(craft_id, stage) for craft_id, stage in candidates if stage == smallest]
    craft_id, stage = candidates[deterministic_roll(state, f"handcraft:{kind}:pick:{smallest}", len(candidates))]
    next_stage = stage + 1
    return craft_id, next_stage, stage_costs[next_stage - 1]


def _make_handcraft_pray_record(state: dict, data: dict, job: dict, now: int) -> dict:
    craft_id = int(job["id"])
    stage = int(job["state"])
    body = craft_id * 100 + (1 if stage == 1 else 2)
    paper = 0 if stage < 3 else craft_id * 1000 + 1
    if stage >= 4:
        body += deterministic_roll(state, f"handcraft:pray:body:{craft_id}:{now}", 5)
        paper += deterministic_roll(state, f"handcraft:pray:paper:{craft_id}:{now}", 4)
    completed_stamps = [
        row for row in data["stamps"]
        if isinstance(row, dict) and int(row.get("state", 0) or 0) > 0
    ]
    stamp = 0
    stamp_state = 0
    if completed_stamps:
        row = completed_stamps[deterministic_roll(state, f"handcraft:pray:stamp:{craft_id}:{now}", len(completed_stamps))]
        stamp = int(row.get("id", 0) or 0)
        stamp_state = max(1, min(3, int(row.get("state", 1) or 1)))
    note = craft_id * 10 - 9 + deterministic_roll(state, f"handcraft:pray:note:{craft_id}:{now}", 5)
    return {
        "id": craft_id,
        "u_id": _next_handcraft_u_id(data["wishs"]),
        "state": stage,
        "body": body,
        "paper": paper,
        "stamp": stamp,
        "stamp_state": stamp_state,
        "make_time": now,
        "stamp_time": now if stamp else 0,
        "content": note,
    }


def _make_handcraft_stamp_record(data: dict, job: dict, now: int) -> dict:
    return {
        "id": int(job["id"]),
        "u_id": _next_handcraft_u_id(data["stamps"]),
        "state": int(job["state"]),
        "time": now,
    }


def advance_handcraft_production(state: dict, policy: dict | None = None, now: int | None = None) -> bool:
    """Advance the server-owned handcraft job when the frog is home."""
    if now is None:
        now = int(time.time())
    if furniture_job_pending(state):
        return False
    hp = handcraft_policy(policy)
    if not bool(hp.get("enabled", True)):
        return False
    _, frog, _, _ = get_role_parts(state)
    if int(frog.get("status", FROG_HOME) or FROG_HOME) != FROG_HOME:
        return False
    data = get_handcraft_data(state)
    meta = state.setdefault("offline_meta", {}).setdefault("handcraft", {})
    job = _handcraft_job(state)
    changed = False
    if job is not None:
        if int(job.get("ready_at", 0) or 0) > now:
            if int(frog.get("motion", -1) or -1) != 3:
                frog["motion"] = 3
                changed = True
        else:
            kind = str(job.get("kind", ""))
            if kind == "pray":
                record = _make_handcraft_pray_record(state, data, job, now)
                data["wishs"].append(record)
                data["wish_new"] = copy.deepcopy(record)
            elif kind == "stamp":
                record = _make_handcraft_stamp_record(data, job, now)
                data["stamps"].append(record)
                data["stamp_new"] = copy.deepcopy(record)
            elif kind == "showcase":
                pass
            else:
                return False
            meta.pop("job", None)
            recover_furniture_workbench(state, policy=policy)
            changed = True
    else:
        _, house, _, _ = get_item_parts(state)
        if get_house_count(house, HANDCRAFT_TOOL_ID) <= 0:
            return False
        preferred = str(meta.get("next_kind", "pray"))
        for kind in (preferred, "stamp" if preferred == "pray" else "pray"):
            selected = _select_handcraft_job(state, data, kind)
            if selected is None:
                if not GALLERY_MODE:
                    continue
                craft_ids = HANDCRAFT_PRAY_IDS if kind == "pray" else HANDCRAFT_STAMP_IDS
                stage_costs = HANDCRAFT_PRAY_STAGE_COSTS if kind == "pray" else HANDCRAFT_STAMP_STAGE_COSTS
                craft_id = craft_ids[deterministic_roll(state, f"handcraft:showcase:{kind}", len(craft_ids))]
                stage = len(stage_costs)
                cost = stage_costs[-1]
                job_kind = "showcase"
            else:
                craft_id, stage, cost = selected
                job_kind = kind
            if get_house_count(house, HANDCRAFT_MATERIAL_ID) < cost:
                continue
            if not GALLERY_MODE:
                set_house_count(
                    house,
                    HANDCRAFT_MATERIAL_ID,
                    get_house_count(house, HANDCRAFT_MATERIAL_ID) - cost,
                )
            duration = max(60, int(hp.get("job_seconds", 1800) or 1800))
            meta["job"] = {
                "kind": job_kind,
                "id": craft_id,
                "state": stage,
                "material_cost": cost,
                "started_at": now,
                "ready_at": now + duration,
            }
            meta["next_kind"] = "stamp" if kind == "pray" else "pray"
            frog["motion"] = 3
            changed = True
            break
    if changed:
        save_state(state, "handcraft.advance")
    return changed


def apply_pray_compose(state: dict, craft_id: int) -> tuple[bool, str, list[int], int, int]:
    """Consume one of each wooden fragment and create a real wooden amulet."""
    if int(craft_id) != HANDCRAFT_COMPOSE_ID:
        return False, "unknown_compose", [], 0, 0
    _, house, _, _ = get_item_parts(state)
    consumed: list[int] = []
    for candidates in HANDCRAFT_COMPONENTS:
        item_id = next((iid for iid in candidates if get_house_count(house, iid) > 0), 0)
        if item_id <= 0:
            return False, "missing_component", [], 0, 0
        consumed.append(item_id)
    for item_id in consumed:
        set_house_count(house, item_id, get_house_count(house, item_id) - 1)
    amulet_id = HANDCRAFT_AMULET_IDS[deterministic_roll(state, "handcraft:amulet", len(HANDCRAFT_AMULET_IDS))]
    total = get_house_count(house, amulet_id) + 1
    set_house_count(house, amulet_id, total)
    save_state(state, f"pray.compose:{amulet_id}")
    return True, "ok", consumed, amulet_id, total


def apply_pray_confirm_make_box(state: dict) -> tuple[bool, list[tuple[int, int]]]:
    """Move the pending handcraft-box contents into the house inventory."""
    data = get_handcraft_data(state)
    boxes = data.get("boxes", [])
    source = list(boxes.values()) if isinstance(boxes, dict) else list(boxes)
    rewards: list[int] = []
    for entry in source:
        if isinstance(entry, dict):
            item_id = int(entry.get("item_id", entry.get("id", 0)) or 0)
            count = max(1, int(entry.get("count", 1) or 1))
            rewards.extend([item_id] * count)
        else:
            item_id = int(entry or 0)
            if item_id > 0:
                rewards.append(item_id)
    if not rewards:
        return False, []
    _, house, _, _ = get_item_parts(state)
    totals: dict[int, int] = {}
    for item_id in rewards:
        if item_id <= 0:
            continue
        total = get_house_count(house, item_id) + 1
        set_house_count(house, item_id, total)
        totals[item_id] = total
    data["boxes"] = {}
    save_state(state, f"pray.confirm_make_box:{len(rewards)}")
    return True, sorted(totals.items())


def get_guide_task_parts(state: dict) -> tuple[dict, list[dict], list[dict], list[dict]]:
    task_push = find_push(state, "task.load")
    list_push = find_push(state, "task.load_list")
    if not task_push or not list_push:
        raise RuntimeError("state 中缺少 GuideTask 推送")
    task_data = task_push.setdefault("data", {})
    task_rows = task_data.setdefault("tasks", [])
    progress_rows = task_data.setdefault("list", [])
    reward_rows = list_push.setdefault("data", {}).setdefault("reward", [])
    if not all(isinstance(rows, list) for rows in (task_rows, progress_rows, reward_rows)):
        raise RuntimeError("GuideTask 数据格式错误")
    return task_data, task_rows, progress_rows, reward_rows


def grant_guide_task_item(state: dict, item_id: int, count: int) -> dict:
    item_id = int(item_id)
    count = int(count)
    res, _ = get_clover_parts(state)
    if item_id == LOTTERY_CLOVER_ITEM_ID:
        total = 9999 if GALLERY_MODE else int(res.get("clover_point", 0) or 0) + count
        res["clover_point"] = total
        return {"kind": "clover", "total": total}
    if item_id == LOTTERY_TICKET_ITEM_ID:
        total = 999 if GALLERY_MODE else int(res.get("ticket", 0) or 0) + count
        res["ticket"] = total
        return {"kind": "ticket", "total": total}

    _, house, _, _ = get_item_parts(state)
    total = get_house_count(house, item_id) + count
    set_house_count(house, item_id, total)
    return {"kind": "item", "item_id": item_id, "total": get_house_count(house, item_id)}


def refresh_guide_task_progress(state: dict) -> bool:
    """Reconcile guide progress against persisted gameplay facts."""
    if not find_push(state, "task.load") or not find_push(state, "task.load_list"):
        return False
    if GALLERY_MODE:
        return complete_gallery_guide_tasks(state)
    _, tasks, plans, _ = get_guide_task_parts(state)
    meta = state.setdefault("offline_meta", {})
    history = meta.get("travel_history", [])
    _, house, _, desk = get_item_parts(state)
    inventory = {int(row["item_id"]): int(row.get("count", 0)) for row in house}
    item_config = {int(row["id"]): row for row in _item_rows()}
    item_types = {item_id: row.get("type") for item_id, row in item_config.items()}
    collections, specialties = get_handbook_parts(state)
    notes = get_travel_note_state(state)["note_list"]
    note_config = load_static_json("Note_json.json")
    observations = sum(note_config.get(str(row["id"]), {}).get("type") == 1 for row in notes)
    friend_notes = len(notes) - observations
    pictures = max(len(meta.get("album_full", {})),
                   sum(len(row.get("picture_ids", [])) for row in history))
    craft = get_handcraft_data(state)
    furniture = _furniture_production_data(state)
    furniture_config = _cfg_dict("furnitureData_json.json")
    blueprints = sum(count > 0 and item_types.get(item_id) == 13 for item_id, count in inventory.items())
    event_history = meta.get("travel_event_history", [])
    furniture_events = sum(int(row.get("evt_type", -1)) == 21 for row in [*get_event_list(state), *event_history])
    drawing = (find_push(state, "guest.load_drawing") or {}).get("data", {})
    guide = meta.get("guide_task", {})
    season_rewards = int(guide.get("season_rewards", 0))
    received_friend_gift = (
        bool(guide.get("received_friend_gift")) or bool(get_visit_state(state).get("acquire"))
        or any(int(event.get("evt_type", -1)) == 16 for event in event_history)
    )
    resources = get_role_parts(state)[2]
    progress = {
        1: len(history), 2: len(history),
        3: len(specialties), 4: sum(count for item_id, count in inventory.items() if item_types.get(item_id) == ITEM_SPECIALTY),
        5: len(collections), 6: len(collections), 7: pictures, 8: pictures, 9: int(received_friend_gift),
        101: observations, 102: observations, 103: friend_notes,
        104: len(get_story_state(state)["stories"]),
        105: sum(meta.get("giftbox_friend", {}).get("visit_counts", {}).values()),
        201: inventory.get(HANDCRAFT_MATERIAL_ID, 0),
        202: len(craft["wishs"]), 203: len(craft["wishs"]),
        204: len(craft["stamps"]), 205: len(craft["stamps"]),
        301: furniture_events, 302: blueprints,
        303: int(meta.get("furniture", {}).get("manual_replace_count", 0)),
        401: max(int(drawing.get("state", 0) > DRAW_WAIT), len(meta.get("party_history", []))),
        402: len(drawing.get("colls", [])),
        403: len(meta.get("party_history", [])),
        901: int(resources.get("clover_point", 0)),
        902: int(resources.get("ticket", 0)),
        903: season_rewards, 904: inventory.get(9000, 0),
    }
    owned_furniture = set(furniture["has_fur"])
    themes = {}
    for row in furniture_config.values():
        if 2 <= int(row.get("style", 0)) <= 14:
            themes.setdefault(int(row["style"]), set()).add(int(row["id"]))
    progress[304] = int(any(ids <= owned_furniture for ids in themes.values()))
    foods = {int(row.get("food_id", -1)) for row in history}
    food_names = {item_config[food_id]["name"] for food_id in foods if food_id in item_config}
    fed = meta.get("guest_serve_history", [])
    plan_progress = {
        101: int(0 in foods), 102: len(history), 201: len(fed),
        202: int(guide.get("harvest_count", 0)),
        203: int(meta.get("vase", {}).get("decoration_id", 0) > 0),
        204: 1,  # Official-server redemption is completed locally in offline play.
        205: int(1 in foods),
        301: sum(count > 0 and item_types.get(item_id) == ITEM_TOOL for item_id, count in inventory.items()),
        302: int("沙拉皮塔饼" in food_names), 303: int("茄汁蛋包饭" in food_names),
        304: int(any(len(row.get("bag_snapshot", [])) >= 4 and
                     all(int(item) >= 0 for item in row["bag_snapshot"][:4]) for row in history)),
        305: int(bool(desk) and all(int(item) >= 0 for item in desk)),
        # The story's remote-player heart action has no offline counterpart.
        401: 1,
        402: int(_giftbox_has_contents(state) or bool(meta.get("giftbox_friend", {}).get("last_offer"))),
        403: inventory.get(7001, 0),
        407: season_rewards,
        408: int("香葱烤包子" in food_names), 409: int("海苔煎豆腐" in food_names),
        410: int("桂花蒸米糕" in food_names), 411: int("彩椒烙蛋饼" in food_names),
        412: int("水果沙拉" in food_names),
        501: int(any(int(row.get("amulet_id", -1)) in MUSEUM_DAY_TICKET_BY_ID.values() for row in history)),
        601: sum(item_types.get(int(item_id)) == 11 for row in history for item_id in row.get("item_ids", [])),
    }
    for guest_id in (0, 1, 2):
        plan_progress[404 + guest_id] = int(any(int(row.get("guest_id", -1)) == guest_id for row in fed))
    for stamp_type in range(3):
        plan_progress[502 + stamp_type] = int(any(
            int(row.get("id", 0)) // 100 == stamp_type and int(row.get("state", 0)) >= 3
            for row in craft["stamps"]))
    for craft_id in HANDCRAFT_PRAY_IDS:
        plan_progress[504 + craft_id] = int(any(
            int(row.get("id", 0)) == craft_id and int(row.get("state", 0)) >= 4
            for row in craft["wishs"]))
    for furniture_type in range(1, 28):
        plan_progress[601 + furniture_type] = int(any(
            int(row.get("type", -1)) == furniture_type and inventory.get(int(row.get("drawing", -1)), 0) > 0
            for row in furniture_config.values()))
    plan_progress[629] = inventory.get(10401, 0)
    plan_progress[630] = inventory.get(10501, 0)
    config = load_task_static_data()
    changed = False
    for rows, definitions, values in ((tasks, config["task_list"], progress), (plans, config["list_map"], plan_progress)):
        for row in rows:
            task_id = int(row["id"])
            definition = definitions.get(str(task_id))
            if definition and task_id in values:
                target = min(int(definition["count"]), int(values[task_id]))
                if target > int(row.get("pro", 0)):
                    row["pro"] = target
                    changed = True
    return changed


def apply_guide_task_reward(state: dict, task_id: int) -> tuple[bool, str, dict | None]:
    refresh_guide_task_progress(state)
    _, task_rows, _, _ = get_guide_task_parts(state)
    config = load_task_static_data().get("task_list", {})
    definition = config.get(str(int(task_id))) if isinstance(config, dict) else None
    row = next((entry for entry in task_rows if isinstance(entry, dict) and int(entry.get("id", -1)) == int(task_id)), None)
    if not isinstance(definition, dict) or row is None:
        return False, "unknown_task", None
    if bool(row.get("is_reward", False)):
        return False, "already_claimed", None
    if int(row.get("pro", 0) or 0) < int(definition.get("count", 0) or 0):
        return False, "not_complete", None

    reward = grant_guide_task_item(state, int(definition["reward_id"]), int(definition["num"]))
    row["is_reward"] = True
    save_state(state, f"task.get_reward:{int(task_id)}")
    return True, "ok", reward


def apply_guide_task_list_reward(state: dict, reward_id: int) -> tuple[bool, str, dict | None]:
    refresh_guide_task_progress(state)
    task_type, reward_index = divmod(int(reward_id), 100)
    if task_type <= 0 or reward_index <= 0:
        return False, "bad_reward_id", None

    _, _, progress_rows, reward_rows = get_guide_task_parts(state)
    config = load_task_static_data()
    list_type = config.get("list_type", {}) if isinstance(config, dict) else {}
    list_map = config.get("list_map", {}) if isinstance(config, dict) else {}
    definition = list_type.get(str(task_type)) if isinstance(list_type, dict) else None
    if not isinstance(definition, dict):
        return False, "unknown_list_type", None
    rewards = definition.get("reward", [])
    targets = definition.get("target", [])
    if reward_index > len(rewards) or reward_index > len(targets):
        return False, "unknown_list_reward", None

    reward_row = next((entry for entry in reward_rows if isinstance(entry, dict) and int(entry.get("id", -1)) == task_type), None)
    claimed = int(reward_row.get("pro", 0) or 0) if reward_row else 0
    if reward_index != claimed + 1:
        return False, "reward_out_of_order", None

    progress = {
        int(entry.get("id", -1)): int(entry.get("pro", 0) or 0)
        for entry in progress_rows if isinstance(entry, dict)
    }
    completed = sum(
        1 for entry in list_map.values()
        if isinstance(entry, dict)
        and int(entry.get("type", 0) or 0) == task_type
        and progress.get(int(entry.get("id", -1)), 0) >= int(entry.get("count", 0) or 0)
    )
    if completed < int(targets[reward_index - 1]):
        return False, "list_not_complete", None

    reward = grant_guide_task_item(state, int(rewards[reward_index - 1]), 1)
    if reward_row is None:
        reward_rows.append({"id": task_type, "pro": reward_index})
    else:
        reward_row["pro"] = reward_index
    save_state(state, f"task.get_list_reward:{int(reward_id)}")
    return True, "ok", reward


def record_guide_task_client_progress(state: dict, param: Any) -> bool:
    value = str(param or "").strip()
    if not value:
        return False
    events = state.setdefault("offline_meta", {}).setdefault("guide_task", {}).setdefault("client_progress", [])
    changed = False
    if value not in events:
        events.append(value)
        changed = True

    list_id = GUIDE_CLIENT_PROGRESS_LIST_IDS.get(value)
    if list_id is not None:
        _, _, progress_rows, _ = get_guide_task_parts(state)
        list_map = load_task_static_data().get("list_map", {})
        definition = list_map.get(str(list_id)) if isinstance(list_map, dict) else None
        if isinstance(definition, dict):
            target = int(definition.get("count", 1) or 1)
            row = next(
                (entry for entry in progress_rows if isinstance(entry, dict) and int(entry.get("id", -1)) == list_id),
                None,
            )
            if row is None:
                progress_rows.append({"id": list_id, "pro": target})
                changed = True
            elif int(row.get("pro", 0) or 0) < target:
                row["pro"] = target
                changed = True

    if changed:
        save_state(state, f"task.client_pro:{value}")
    return changed


def home_motion_policy(policy: dict | None = None) -> dict:
    if policy is None:
        policy = load_offline_policy()
    value = policy.get("home_motion", {}) if isinstance(policy, dict) else {}
    return value if isinstance(value, dict) else {}


def _home_motion_plan(state: dict, day: datetime, policy: dict) -> dict:
    """Return the persisted bedtime and watermelon window for one local day."""
    hp = home_motion_policy(policy)
    home = state.setdefault("offline_meta", {}).setdefault("home_frog_motion", {})
    plans = home.setdefault("daily_plans", {})
    if not isinstance(plans, dict):
        plans = {}
        home["daily_plans"] = plans
    key = day.strftime("%Y-%m-%d")
    plan = plans.get(key)
    if isinstance(plan, dict):
        return plan

    sleep_min_hour = max(0, min(23, int(hp.get("sleep_start_min_hour", 20) or 20)))
    sleep_max_hour = max(sleep_min_hour, min(23, int(hp.get("sleep_start_max_hour", 23) or 23)))
    sleep_hour = sleep_min_hour + deterministic_roll(
        state, f"home_motion:sleep_hour:{key}", sleep_max_hour - sleep_min_hour + 1
    )
    sleep_minute = deterministic_roll(state, f"home_motion:sleep_minute:{key}", 60)
    sleep_min_seconds = max(3600, int(hp.get("sleep_min_seconds", 25200) or 25200))
    sleep_max_seconds = max(sleep_min_seconds, int(hp.get("sleep_max_seconds", 32400) or 32400))
    sleep_duration = sleep_min_seconds + deterministic_roll(
        state, f"home_motion:sleep_duration:{key}", sleep_max_seconds - sleep_min_seconds + 1
    )
    day_start = int(day.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
    melon_min_hour = max(0, min(23, int(hp.get("summer_watermelon_start_min_hour", 11) or 11)))
    melon_max_hour = max(melon_min_hour, min(23, int(hp.get("summer_watermelon_start_max_hour", 17) or 17)))
    melon_hour = melon_min_hour + deterministic_roll(
        state, f"home_motion:watermelon_hour:{key}", melon_max_hour - melon_min_hour + 1
    )
    plan = {
        "sleep_at": day_start + sleep_hour * 3600 + sleep_minute * 60,
        "wake_at": day_start + sleep_hour * 3600 + sleep_minute * 60 + sleep_duration,
        "sleep_motion": 10 + deterministic_roll(state, f"home_motion:sleep_pose:{key}", 4),
        "watermelon_at": day_start + melon_hour * 3600 + deterministic_roll(
            state, f"home_motion:watermelon_minute:{key}", 60
        ) * 60,
        "watermelon_end": 0,
    }
    plan["watermelon_end"] = int(plan["watermelon_at"]) + max(
        300, int(hp.get("summer_watermelon_duration_seconds", 5400) or 5400)
    )
    plans[key] = plan
    # Only the present and preceding day can still affect a current home view.
    keep = {key, datetime.fromtimestamp(day_start - 1).strftime("%Y-%m-%d")}
    for old_key in list(plans):
        if old_key not in keep:
            plans.pop(old_key, None)
    return plan


def _home_sleep_motion(state: dict, now: int, policy: dict) -> int | None:
    local_now = datetime.fromtimestamp(now)
    for day in (local_now, datetime.fromtimestamp(now - 86400)):
        plan = _home_motion_plan(state, day, policy)
        if int(plan["sleep_at"]) <= now < int(plan["wake_at"]):
            return int(plan["sleep_motion"])
    return None


def _home_motion_has_table_food(state: dict) -> bool:
    _, _, _, desk = get_item_parts(state)
    return any(int(item_id) >= 0 and item_type(int(item_id)) == ITEM_LUNCHBOX for item_id in desk)


def _home_motion_can_idle_make(state: dict) -> bool:
    _, house, _, _ = get_item_parts(state)
    return (
        get_house_count(house, HANDCRAFT_TOOL_ID) > 0
        and get_house_count(house, HANDCRAFT_MATERIAL_ID) > 0
    )


def _home_motion_refresh_delay(state: dict, now: int, policy: dict) -> int:
    hp = home_motion_policy(policy)
    minimum = max(300, int(hp.get("refresh_min_seconds", 3600) or 3600))
    maximum = max(minimum, int(hp.get("refresh_max_seconds", 10800) or 10800))
    return minimum + deterministic_roll(
        state, f"home_motion:refresh_delay:{now}", maximum - minimum + 1
    )


def summer_watermelon_is_active(state: dict, now: int, policy: dict) -> bool:
    """Watermelon is a summer EasterEgg appearance, never the normal eat clip."""
    hp = home_motion_policy(policy)
    months = {int(month) for month in _to_list(hp.get("summer_watermelon_months", [6, 7, 8]))}
    if datetime.fromtimestamp(now).month not in months:
        return False
    if furniture_production_motion(state, now) is not None or handcraft_is_active(state, now):
        return False
    plan = _home_motion_plan(state, datetime.fromtimestamp(now), policy)
    return int(plan["watermelon_at"]) <= now < int(plan["watermelon_end"])


def sync_home_easter_egg(state: dict, now: int | None = None, policy: dict | None = None) -> bool:
    if now is None:
        now = int(time.time())
    if policy is None:
        policy = load_offline_policy()
    push = find_push(state, "easteregg.load")
    if push is None:
        push = {"cmd": "easteregg.load", "data": {"egg_list": []}}
        state.setdefault("early_pushes", []).append(push)
    data = push.setdefault("data", {})
    if not isinstance(data, dict):
        data = {}
        push["data"] = data
    active = []
    if _frog_is_home(state) and summer_watermelon_is_active(state, now, policy):
        active = [5]
    if isinstance(data.get("egg_list"), list) and data["egg_list"] == active:
        return False
    data["egg_list"] = active
    save_state(state, "frog.home_easter_egg")
    return True


def select_home_frog_motion(state: dict, now: int | None = None, policy: dict | None = None) -> int:
    """Choose an eligible at-home animation from the client-defined idle patterns."""
    if now is None:
        now = int(time.time())
    if policy is None:
        policy = load_offline_policy()
    furniture_motion = furniture_production_motion(state, now)
    if furniture_motion is not None:
        return furniture_motion
    if handcraft_is_active(state, now):
        return 3
    sleep_motion = _home_sleep_motion(state, now, policy)
    if sleep_motion is not None:
        return sleep_motion

    # Frogpattern and FrogMotionNum are current client constants. Exterior
    # workshop clips (saw/brush/knock/knit/cut, 5..9) are selected only by
    # an active furniture-production job above, never as idle animation.
    _, frog, _, _ = get_role_parts(state)
    patterns = (
        ("eat", "eat", "eat", "eat", "doku", "doku", "doku", "doku", "doku_s", "doku_s", "make", "make", "make"),
        ("eat", "eat", "doku", "doku", "doku", "doku", "doku_s", "doku_s", "write", "write", "write", "write", "write"),
        ("write", "write", "write", "write", "eat", "eat", "make", "make", "make", "make", "write", "write", "write"),
    )
    pattern = patterns[int(frog.get("today_step", 0) or 0) % len(patterns)]
    eligible = [
        name for name in pattern
        if name != "eat" or _home_motion_has_table_food(state)
        if name != "make" or _home_motion_can_idle_make(state)
    ]
    if not eligible:
        eligible = ["doku", "doku_s", "write"]
    motion_by_name = {"doku": 0, "doku_s": 1, "write": 2, "make": 3, "eat": 4}
    hour = datetime.fromtimestamp(now).hour
    period = "day" if hour < 18 else "evening"
    name = eligible[deterministic_roll(state, f"home_motion:{period}:{now}", len(eligible))]
    return motion_by_name[name]


def refresh_home_frog_motion(state: dict, now: int | None = None, policy: dict | None = None) -> bool:
    """Refresh an eligible home animation on its own persisted random schedule."""
    if now is None:
        now = int(time.time())
    if policy is None:
        policy = load_offline_policy()
    _, frog, _, _ = get_role_parts(state)
    if int(frog.get("status", FROG_HOME) or FROG_HOME) != FROG_HOME:
        return False
    meta = state.setdefault("offline_meta", {}).setdefault("home_frog_motion", {})
    plans = meta.get("daily_plans")
    today_key = datetime.fromtimestamp(now).strftime("%Y-%m-%d")
    needs_daily_plan = not isinstance(plans, dict) or today_key not in plans
    if int(meta.get("next_refresh_at", 0) or 0) > now and not needs_daily_plan:
        return False
    motion = int(frog.get("motion", -1))
    selected = select_home_frog_motion(state, now, policy=policy)
    meta["next_refresh_at"] = now + _home_motion_refresh_delay(state, now, policy)
    meta.pop("period", None)
    if motion == selected:
        save_state(state, "frog.home_motion.schedule")
        return True
    frog["motion"] = selected
    save_state(state, f"frog.home_motion:{frog['motion']}")
    return True


def apply_item_slot_change(
    state: dict,
    *,
    area: str,
    action: str,
    pos: int,
    item_id: int | None = None,
) -> tuple[bool, str, int | None, int | None]:
    data, house, bag, desk = get_item_parts(state)

    if area == "bag":
        slots = bag
        conflict_key = "bag_conflict"
    elif area == "desk":
        slots = desk
        conflict_key = "desk_conflict"
    else:
        return False, "bad_area", None, None

    idx = int(pos) - 1
    if idx < 0 or idx >= len(slots):
        data[conflict_key] = True
        return False, "bad_pos", None, None

    current = int(slots[idx]) if slots[idx] is not None else -1

    if action == "takeout":
        if current == -1:
            data[conflict_key] = False
            return True, "already_empty", None, None

        new_count = get_house_count(house, current) + 1
        set_house_count(house, current, new_count)
        slots[idx] = -1
        data[conflict_key] = False
        state["format_version"] = max(int(state.get("format_version", 2) or 2), 4)
        save_state(state, f"item.takeout_{area}:{pos}:{current}")
        return True, "ok", current, new_count

    if action == "putin":
        if item_id is None:
            data[conflict_key] = True
            return False, "missing_item_id", None, None

        item_id = int(item_id)

        if current == item_id:
            data[conflict_key] = False
            return True, "already_same", item_id, get_house_count(house, item_id)

        # 正常客户端在替换已有格子时会先发 takeout，再发 putin。
        if current != -1:
            data[conflict_key] = True
            return False, "slot_not_empty", None, None

        old_count = get_house_count(house, item_id)
        if old_count <= 0:
            data[conflict_key] = True
            return False, "not_in_house", item_id, old_count

        new_count = old_count - 1
        set_house_count(house, item_id, new_count)
        slots[idx] = item_id
        data[conflict_key] = False
        state["format_version"] = max(int(state.get("format_version", 2) or 2), 4)
        save_state(state, f"item.putin_{area}:{pos}:{item_id}")
        return True, "ok", item_id, new_count

    data[conflict_key] = True
    return False, "bad_action", None, None


def persist_bag_completed(state: dict, completed: Any) -> None:
    data, _, _, _ = get_item_parts(state)
    value = bool(completed)
    if bool(data.get("bag_completed", False)) != value:
        data["bag_completed"] = value
        state["format_version"] = max(int(state.get("format_version", 2) or 2), 4)
        save_state(state, f"item.set_bag_completed:{value}")



def get_shop_state_parts(state: dict):
    role_push = find_push(state, "client.load_role")
    item_push = find_push(state, "item.load_items")
    shop_push = find_push(state, "item.load_shop_info")

    if not role_push:
        raise RuntimeError("state 中找不到 client.load_role")
    if not item_push:
        raise RuntimeError("state 中找不到 item.load_items")
    if not shop_push:
        raise RuntimeError("state 中找不到 item.load_shop_info")

    role_data = role_push.setdefault("data", {})
    res = role_data.setdefault("res", {})

    item_data = item_push.setdefault("data", {})
    house = item_data.setdefault("house", [])
    if not isinstance(house, list):
        raise RuntimeError("item.load_items.data.house 不是数组")

    shop_data = shop_push.setdefault("data", {})
    purchased = shop_data.setdefault("purchased", [])
    if not isinstance(purchased, list):
        raise RuntimeError("item.load_shop_info.data.purchased 不是数组")

    return res, house, purchased


def get_purchased_count(purchased: list[dict], shop_id: int) -> int:
    for row in purchased:
        if not isinstance(row, dict):
            continue
        if int(row.get("item_id", -1)) == shop_id:
            return int(row.get("count", 0) or 0)
    return 0


def set_purchased_count(purchased: list[dict], shop_id: int, count: int) -> None:
    count = max(0, int(count))
    for row in purchased:
        if not isinstance(row, dict):
            continue
        if int(row.get("item_id", -1)) == shop_id:
            row["count"] = count
            return
    purchased.append({"item_id": shop_id, "count": count})


def apply_shop_purchase(
    state: dict,
    *,
    shop_id: int,
    item_id: int,
    price: int,
) -> tuple[bool, str, int | None, int | None, int | None]:
    """
    Local-only Stage 5/6 local purchase transaction.

    The patched local client adds item_id + price to item.buy.
    The official protocol only carries shop_id, so these extra fields are
    strictly for the user's offline preservation build.
    """
    shop_id = int(shop_id)
    item_id = int(item_id)
    price = int(price)

    if shop_id < 0:
        return False, "bad_shop_id", None, None, None
    if item_id < 0:
        return False, "bad_item_id", None, None, None
    if price < 0:
        return False, "bad_price", None, None, None
    if price > 10_000_000:
        return False, "price_out_of_range", None, None, None

    res, house, purchased = get_shop_state_parts(state)

    old_clover = int(res.get("clover_point", 0) or 0)
    if not GALLERY_MODE and old_clover < price:
        return False, "not_enough_clover", old_clover, None, None

    old_item_count = get_house_count(house, item_id)
    old_buy_count = get_purchased_count(purchased, shop_id)

    new_clover = 9999 if GALLERY_MODE else old_clover - price
    new_item_count = 99 if GALLERY_MODE and item_id in gallery_repeatable_item_ids() else old_item_count + 1
    new_buy_count = old_buy_count + 1

    res["clover_point"] = new_clover
    set_house_count(house, item_id, new_item_count)
    set_purchased_count(purchased, shop_id, new_buy_count)
    reason = "ok"
    if item_id == 204002:
        # The purchased bag opens immediately into the furniture collection.
        set_house_count(house, item_id, old_item_count)
        furniture_id = _grant_wishing_pool_reward(state, item_id, duplicate_refund=price)
        new_item_count = get_house_count(house, item_id)
        new_clover = int(res["clover_point"])
        reason = "wishing_furniture" if furniture_id is not None else "wishing_duplicate_refunded"
    if item_id == ANIM_PICTURE_PAGE_ITEM_ID:
        # The frame is a permanent expansion, not a consumable album slot.
        anim = get_animpicture_state(state)
        anim["page_num"] = int(anim["page_num"]) + 1

    meta = state.setdefault("offline_meta", {})
    receipts = meta.setdefault("shop_receipts", [])
    receipts.append({
        "time": int(time.time()),
        "shop_id": shop_id,
        "item_id": item_id,
        "price": price,
        "clover_after": new_clover,
        "house_count_after": new_item_count,
        "purchased_count_after": new_buy_count,
    })
    # Avoid unbounded growth in a very long-running local save.
    if len(receipts) > 1000:
        del receipts[:-1000]

    state["format_version"] = max(int(state.get("format_version", 2) or 2), 5)
    save_state(state, f"item.buy:{shop_id}:{item_id}:{price}")

    return True, reason, new_clover, new_item_count, new_buy_count



def get_role_parts(state: dict):
    push = find_push(state, "client.load_role")
    if not push:
        raise RuntimeError("state 中找不到 client.load_role")
    role = push.setdefault("data", {})
    frog = role.setdefault("frog", {})
    res = role.setdefault("res", {})
    misc = role.setdefault("misc", {})
    return role, frog, res, misc


def tutorial_policy(policy: dict | None = None) -> dict:
    if policy is None:
        policy = load_offline_policy()
    value = policy.get("tutorial", {}) if isinstance(policy, dict) else {}
    return value if isinstance(value, dict) else {}


def _grant_tutorial_completion_reward(state: dict, policy: dict | None = None) -> None:
    rewards = tutorial_policy(policy).get("completion_rewards", {})
    if not isinstance(rewards, dict):
        rewards = {}
    _, _, res, _ = get_role_parts(state)
    res["clover_point"] = int(res.get("clover_point", 0) or 0) + max(0, int(rewards.get("clover", 0) or 0))
    res["ticket"] = int(res.get("ticket", 0) or 0) + max(0, int(rewards.get("ticket", 0) or 0))
    _, house, _, _ = get_item_parts(state)
    for row in rewards.get("items", []):
        if not isinstance(row, dict):
            continue
        item_id = int(row.get("item_id", 0) or 0)
        count = max(0, int(row.get("count", 0) or 0))
        if item_id > 0 and count > 0:
            set_house_count(house, item_id, get_house_count(house, item_id) + count)


def tutorial_open_door(state: dict) -> None:
    meta = state.setdefault("offline_meta", {}).setdefault("tutorial", {})
    if not bool(meta.get("door_opened", False)):
        meta["door_opened"] = True
        save_state(state, "tutorial.open_door")


def tutorial_claim_completion_reward(state: dict, policy: dict | None = None) -> bool:
    meta = state.setdefault("offline_meta", {}).setdefault("tutorial", {})
    if not bool(meta.get("door_opened", False)) or bool(meta.get("completion_reward_claimed", False)):
        return bool(meta.get("completion_reward_claimed", False))
    _grant_tutorial_completion_reward(state, policy)
    meta["completion_reward_claimed"] = True
    meta["claimed_at"] = int(time.time())
    save_state(state, "tutorial.ask_award")
    return True


def get_drawing_parts(state: dict):
    push = find_push(state, "guest.load_drawing")
    if not push:
        raise RuntimeError("state 中找不到 guest.load_drawing")
    data = push.setdefault("data", {})
    bag = data.setdefault("bag", [-1, -1, -1, -1])
    if not isinstance(bag, list):
        bag = [-1, -1, -1, -1]
        data["bag"] = bag
    while len(bag) < 4:
        bag.append(-1)
    return data, bag


def get_mail_list(state: dict) -> list[dict]:
    push = find_push(state, "mail.load")
    if not push:
        # Older/local snapshots should normally have mail.load. Keep the
        # failure non-destructive if one does not.
        return []
    data = push.setdefault("data", [])
    if not isinstance(data, list):
        data = []
        push["data"] = data
    return data


def get_event_list(state: dict) -> list[dict]:
    push = find_push(state, "client.load_events")
    if not push:
        return []
    data = push.get("data")
    if isinstance(data, list):
        return data
    # Official empty payload is frequently {} rather than []. Normalize only
    # when we actually need to mutate it.
    data = []
    push["data"] = data
    return data


def get_new_album_data(state: dict) -> dict:
    push = find_push(state, "album.load_new")
    if not push:
        raise RuntimeError("state 中找不到 album.load_new")
    data = push.setdefault("data", {})
    if not isinstance(data, dict):
        data = {"pictures": [], "visted_pic": [], "has_ads": False, "is_share": False}
        push["data"] = data
    return data


def get_handbook_parts(state: dict) -> tuple[list[int], list[int]]:
    push = find_push(state, "item.load_handbook")
    if not push:
        return [], []
    data = push.setdefault("data", {})
    if not isinstance(data, dict):
        return [], []
    collections = [int(x) for x in _to_list(data.get("collections"))]
    specialtys = [int(x) for x in _to_list(data.get("specialtys"))]
    return collections, specialtys


def unlock_travel_encyclopedia_items(state: dict, item_ids: list[int]) -> list[int]:
    """Unlock the official travel-encyclopedia rows for carried trip items."""
    cfg = load_static_json("encytravel_json.json")
    entries = cfg.get("list", {}) if isinstance(cfg, dict) else {}
    if not isinstance(entries, dict):
        return []

    wanted = {int(item_id) for item_id in item_ids if int(item_id) >= 0}
    if not wanted:
        return []
    rows = [
        row for row in entries.values()
        if isinstance(row, dict) and int(row.get("item_id", -1)) in wanted
    ]
    if not rows:
        return []

    push = find_push(state, "encytravel.load")
    if not push:
        push = {"cmd": "encytravel.load", "data": {"unlock_list": [], "unlock_desc": [], "show_sub": []}}
        state.setdefault("sync_pushes", []).append(push)
    data = push.setdefault("data", {})
    if not isinstance(data, dict):
        data = {"unlock_list": [], "unlock_desc": [], "show_sub": []}
        push["data"] = data
    unlock_list, unlock_desc = _encyclopedia_lists(data)

    known = {int(value) for value in unlock_list if str(value).lstrip("-").isdigit()}
    unlocked: list[int] = []
    for row in rows:
        long_id = int(row.get("long_id", 0) or 0)
        parent_id = int(row.get("id", 0) or 0)
        sub_id = int(row.get("sub_id", 0) or 0)
        if long_id <= 0 or parent_id <= 0:
            continue
        if long_id not in known:
            unlock_list.append(long_id)
            known.add(long_id)
            unlocked.append(long_id)
        desc = next(
            (value for value in unlock_desc if isinstance(value, dict) and int(value.get("id", -1)) == parent_id),
            None,
        )
        if desc is None:
            desc = {"id": parent_id, "list": []}
            unlock_desc.append(desc)
        desc_list = desc.setdefault("list", [])
        if isinstance(desc_list, list) and sub_id > 0 and sub_id not in desc_list:
            desc_list.append(sub_id)
    _fill_encyclopedia_selections(data, entries)
    return unlocked


def _encyclopedia_lists(data: dict) -> tuple[list, list]:
    for key in ("unlock_list", "unlock_desc", "show_sub"):
        if not isinstance(data.get(key), list):
            data[key] = _to_list(data.get(key))
    return data["unlock_list"], data["unlock_desc"]


def _fill_encyclopedia_selections(data: dict, entries: dict) -> bool:
    unlocked, _ = _encyclopedia_lists(data)
    selected = {row["id"] for row in data["show_sub"] if isinstance(row, dict) and "id" in row}
    changed = False
    for long_id in unlocked:
        row = entries.get(str(long_id))
        if row and row["id"] not in selected:
            data["show_sub"].append({"id": row["id"], "sub_id": row["long_id"]})
            selected.add(row["id"])
            changed = True
    return changed


def refresh_plant_encyclopedia(state: dict) -> bool:
    _, house, _, _ = get_item_parts(state)
    icons = set()
    for row in house:
        if not isinstance(row, dict) or int(row.get("count", 0)) <= 0:
            continue
        item_id = int(row["item_id"])
        item = item_cfg(item_id)
        if item:
            icons.add(item_id)
            icons.add(item.get("img", {}).get("index"))
    if not icons:
        return False
    config = load_static_json("encyclopedia_json.json")
    entries = config["list"]
    variants = {(row["id"], row["sub_id"]) for row in entries.values()
                if row["pic_id"] == 1 and row["icon"] in icons}
    if not variants:
        return False
    push = find_push(state, "encyclopedia.load")
    if not push:
        push = {"cmd": "encyclopedia.load", "data": {}}
        state.setdefault("sync_pushes", []).append(push)
    data = push["data"]
    unlocked, descriptions = _encyclopedia_lists(data)
    known = set(unlocked)
    parents = {row["id"] for row in descriptions if isinstance(row, dict) and "id" in row}
    changed = False
    for row in entries.values():
        parent_id = row["id"]
        if (parent_id, row["sub_id"]) not in variants:
            continue
        if row["long_id"] not in known:
            unlocked.append(row["long_id"])
            known.add(row["long_id"])
            changed = True
        if parent_id not in parents:
            description = config["desc"].get(str(parent_id), {})
            descriptions.append({"id": parent_id, "list": [int(key) for key in description]})
            parents.add(parent_id)
            changed = True
    return _fill_encyclopedia_selections(data, entries) or changed


def achievement_rows() -> list[dict]:
    return [row for row in load_static_json("Achieve_json.json") if isinstance(row, dict)]


SPECIAL_TITLE_TOKEN_TO_ID = {
    **{203000 + offset: 900 for offset in range(1, 8)},
    **{203010 + offset: 901 for offset in range(1, 8)},
    **{203020 + offset: 902 for offset in range(1, 8)},
}
PERMANENT_ACTIVITY_TITLE_IDS = {78, *SPECIAL_TITLE_TOKEN_TO_ID.values()}


def special_title_policy(policy: dict | None = None) -> dict:
    if policy is None:
        policy = load_offline_policy()
    value = policy.get("special_titles", {}) if isinstance(policy, dict) else {}
    return value if isinstance(value, dict) else {}


def permanent_special_title_id(item_id: int) -> int | None:
    return SPECIAL_TITLE_TOKEN_TO_ID.get(int(item_id))


def grant_permanent_special_title(state: dict, achievement_id: int) -> bool:
    """Add an event title permanently and clear any retained expiry value."""
    achievement_id = int(achievement_id)
    if achievement_id not in PERMANENT_ACTIVITY_TITLE_IDS:
        return False
    _, frog, _, _ = get_role_parts(state)
    owned = [int(value) for value in _to_list(frog.get("achieves")) if str(value).lstrip("-").isdigit()]
    changed = False
    if achievement_id not in owned:
        frog["achieves"] = [*owned, achievement_id]
        changed = True
    expires = frog.get("achieves_time")
    if isinstance(expires, dict):
        if str(achievement_id) in expires:
            expires.pop(str(achievement_id), None)
            changed = True
        if achievement_id in expires:
            expires.pop(achievement_id, None)
            changed = True
    if changed:
        pending = state.setdefault("offline_meta", {}).setdefault("achievement_notices", [])
        if isinstance(pending, list) and achievement_id not in pending:
            pending.append(achievement_id)
    return changed


def claim_children_day_login_reward(state: dict) -> bool:
    """Give the June 1 login gift annually, without duplicating the title."""
    today = datetime.fromtimestamp(time.time()).date()
    if (today.month, today.day) != (6, 1):
        return False
    meta = state.setdefault("offline_meta", {})
    claims = meta.setdefault("limited_activity_claims", {})
    if not isinstance(claims, dict):
        claims = {}
        meta["limited_activity_claims"] = claims
    claimed_year = int(claims.get("children_day_year", 2026 if claims.get("children_day") else 0) or 0)
    if claimed_year == today.year:
        return False
    _, house, _, _ = get_item_parts(state)
    set_house_count(house, 17, get_house_count(house, 17) + 1)
    grant_permanent_special_title(state, 78)
    claims["children_day"] = True
    claims["children_day_year"] = today.year
    return True


def refresh_achievements(state: dict, now: int | None = None, *, save: bool = True) -> list[int]:
    """Apply title conditions that are fully represented by the local save."""
    if now is None:
        now = int(time.time())
    _, frog, res, misc = get_role_parts(state)
    _, house, _, _ = get_item_parts(state)
    collections, specialtys = get_handbook_parts(state)
    history = state.setdefault("offline_meta", {}).get("travel_history", [])
    if not isinstance(history, list):
        history = []

    item_ids_by_name: dict[str, list[int]] = {}
    for row in static_rows("Item_json.json"):
        name = str(row.get("name", "") or "")
        item_id = int(row.get("id", -1) or -1)
        if name and item_id >= 0:
            item_ids_by_name.setdefault(name, []).append(item_id)

    raw_owned = _to_list(frog.get("achieves"))
    owned_list = [int(value) for value in raw_owned if str(value).lstrip("-").isdigit()]
    default_missing = 0 not in raw_owned
    if 0 not in owned_list:
        owned_list.insert(0, 0)
    owned = set(owned_list)
    targets: set[int] = set()
    create_time = int(misc.get("create_time", now) or now)
    login_days = max(1, (int(now) - create_time) // 86400 + 1)

    for row in achievement_rows():
        achievement_id = int(row.get("id", -1))
        if achievement_id < 0 or row.get("is_special") not in ("", None):
            continue
        info = str(row.get("info", "") or "")
        match = re.fullmatch(r"旅行达到(\d+)次", info)
        if match:
            target = 60 if achievement_id == 3 else int(match.group(1))
            if len(history) >= target:
                targets.add(achievement_id)
            continue
        match = re.fullmatch(r"登录达到(\d+)天", info)
        if match and login_days >= int(match.group(1)):
            targets.add(achievement_id)
            continue
        match = re.fullmatch(r"获得(\d+)种纪念品", info)
        if match and len(set(collections)) >= int(match.group(1)):
            targets.add(achievement_id)
            continue
        match = re.fullmatch(r"获得(\d+)种特产", info)
        if match and len(set(specialtys)) >= int(match.group(1)):
            targets.add(achievement_id)
            continue
        if info == "获得所有特产食材" and len(set(specialtys)) >= 18:
            targets.add(achievement_id)
            continue
        if info == "拥有超过10万棵三叶草" and int(res.get("clover_point", 0) or 0) > 100000:
            targets.add(achievement_id)
            continue
        match = re.fullmatch(r"(.+)超过(\d+)个", info)
        if match:
            item_name, threshold = match.groups()
            if any(get_house_count(house, item_id) >= int(threshold) for item_id in item_ids_by_name.get(item_name, [])):
                targets.add(achievement_id)
            continue
        if info == "连续4次带“果汁”出发":
            juice_ids = set(item_ids_by_name.get("果汁", []))
            last_foods = [int(plan.get("food_id", -1) or -1) for plan in history[-4:] if isinstance(plan, dict)]
            if len(last_foods) == 4 and juice_ids and all(food_id in juice_ids for food_id in last_foods):
                targets.add(achievement_id)
        elif info == "出发不到30分钟就回家":
            if any(int(plan.get("duration_seconds", 0) or 0) < 1800 for plan in history if isinstance(plan, dict)):
                targets.add(achievement_id)
        elif info == "出发超过24小时还没回家":
            activity = state.setdefault("offline_meta", {}).get("activity", {})
            started_at = int(activity.get("started_at", now) or now) if isinstance(activity, dict) else now
            if int(frog.get("status", FROG_HOME) or FROG_HOME) == FROG_TRAVEL and int(now) - started_at > 86400:
                targets.add(achievement_id)

    vase = state.setdefault("offline_meta", {}).get("vase", {})
    if isinstance(vase, dict):
        decoration_id = int(vase.get("decoration_id", 0) or 0)
        inserted_at = int(vase.get("inserted_at", now) or now)
        decoration = load_static_json("decoration_json.json").get(str(decoration_id), {})
        decoration_name = str(decoration.get("name", "") or "") if isinstance(decoration, dict) else ""
        vase_days = max(0, (int(now) - inserted_at) // 86400)
        if decoration_name == "腊梅":
            targets.add(73)
            if vase_days >= 4:
                targets.add(74)
        elif decoration_name == "木槿":
            targets.add(79)
            if vase_days >= 6:
                targets.add(80)

    unlocked = sorted(targets - owned)
    if not unlocked:
        if default_missing:
            frog["achieves"] = owned_list
            if save:
                save_state(state, "achievement.default_title")
        return []
    frog["achieves"] = [*owned_list, *unlocked]
    pending = state.setdefault("offline_meta", {}).setdefault("achievement_notices", [])
    if not isinstance(pending, list):
        pending = []
        state.setdefault("offline_meta", {})["achievement_notices"] = pending
    pending.extend(unlocked)
    if save:
        save_state(state, f"achievement.unlock:{','.join(map(str, unlocked))}")
    return unlocked


def take_achievement_notice(state: dict) -> list[int]:
    meta = state.setdefault("offline_meta", {})
    pending = [int(value) for value in _to_list(meta.pop("achievement_notices", [])) if str(value).lstrip("-").isdigit()]
    if pending:
        save_state(state, "achievement.notice_delivered")
    return pending


def get_album_index(state: dict) -> list[dict]:
    push = find_push(state, "album.load_all")
    if not push:
        return []
    data = push.setdefault("data", {})
    if not isinstance(data, dict):
        return []
    return [x for x in _to_list(data.get("id_list")) if isinstance(x, dict)]


def get_friend_taste(guest_id: int, item_id: int) -> tuple[str, int]:
    cfg = load_static_json("Character_json.json")
    rows = cfg.get("data", []) if isinstance(cfg, dict) else []
    item_ids = cfg.get("rowItemId", []) if isinstance(cfg, dict) else []
    guest = next((x for x in rows if int(x.get("id", -1)) == int(guest_id)), None)
    if guest is None:
        raise ValueError(f"unknown guest id: {guest_id}")
    try:
        idx = [int(x) for x in item_ids].index(int(item_id))
    except ValueError as exc:
        raise ValueError(f"item {item_id} is not in Character.rowItemId") from exc
    taste = guest.get("taste", [])
    if idx >= len(taste):
        raise ValueError(f"taste index out of range guest={guest_id} item={item_id}")
    return str(guest.get("name", guest_id)), int(taste[idx])


def taste_feedback_tier(feeling: int) -> str:
    # Exact client thresholds from friendFeedBack().
    if feeling >= 80:
        return "love"
    if feeling >= 60:
        return "like"
    if feeling >= 20:
        return "neutral"
    return "dislike"


def is_rare_specialty(item_id: int) -> bool:
    """Config-derived local classification.

    Regional/museum specialties use the 3000-series and generic food
    specialties use the 4000/4100-series in the preserved Item/Specialty data.
    The client contains NORMAL/RARE friend-gift tables but no active call site;
    this mapping is therefore explicitly a local compatibility inference.
    """
    return 3000 <= int(item_id) < 4000


def next_mail_id(state: dict) -> int:
    ids = [int(m.get("id", 0) or 0) for m in get_mail_list(state) if isinstance(m, dict)]
    meta = state.setdefault("offline_meta", {})
    remembered = int(meta.get("next_mail_id", 0) or 0)
    value = max([0, remembered - 1, *ids]) + 1
    meta["next_mail_id"] = value + 1
    return value


def create_friend_return_mail(
    state: dict,
    guest_id: int,
    item_id: int,
    feeling: int,
    *,
    policy: dict | None = None,
    append: bool = True,
) -> dict:
    if policy is None:
        policy = load_offline_policy()
    guest_policy = policy.get("guest", {}) if isinstance(policy, dict) else {}
    rare = is_rare_specialty(item_id)
    weights = guest_policy.get("rare_weights" if rare else "normal_weights", {})
    reward_kind = weighted_choice(state, f"guest_gift_kind:{guest_id}:{item_id}", weights)
    guest_name, _ = get_friend_taste(guest_id, item_id)

    resource = {
        "clover_point": 0,
        "share_id": "",
        "ticket": 0,
        "reward_gacha": 0,
        # Offline preservation policy: never generate ads_with_gift.
        "ads_id": "",
    }
    items: list[dict] = []

    if reward_kind == "four_clover":
        # Tabikaeru.Define.FourLeafCloverID == 1000 and FRIEND_GIFTFIX=1.
        items.append({"item_id": 1000, "count": 1})
    elif reward_kind == "ticket":
        lo = int(guest_policy.get("ticket_min", 1) or 1)
        hi = max(lo, int(guest_policy.get("ticket_max", 3) or 3))
        resource["ticket"] = lo + deterministic_roll(
            state, f"guest_ticket_count:{guest_id}:{item_id}", hi - lo + 1
        )
    else:
        lo = int(guest_policy.get("clover_min", 20) or 20)
        hi = max(lo, int(guest_policy.get("clover_max", 60) or 60))
        resource["clover_point"] = lo + deterministic_roll(
            state, f"guest_clover_count:{guest_id}:{item_id}", hi - lo + 1
        )

    mail = {
        "expire": 0,
        "timestamp": int(time.time()),
        "message": "",
        "type": 3,  # captured normal friend return-gift mail type
        "id": next_mail_id(state),
        "items": items,
        "sender": int(guest_id),
        "auto_open": False,
        "resource": resource,
        "title": f"{guest_name}的回礼",
        "pictures": [],
        "read": False,
    }
    if append:
        get_mail_list(state).append(mail)
    return mail


def create_party_invitation(state: dict, guest_id: int) -> None:
    drawing, bag = get_drawing_parts(state)
    # Keep accumulated friendship pages/colls; reset only the invitation phase.
    drawing["guest"] = int(guest_id)
    drawing["state"] = DRAW_INVITE
    drawing["bag"] = [-1, -1, -1, -1]
    bag[:] = [-1, -1, -1, -1]
    drawing.setdefault("pages", [])
    drawing.setdefault("colls", [])
    drawing.setdefault("show_coll", 0)
    drawing.setdefault("pen_motion", "write")


def resolve_guest_after_serving(
    state: dict,
    guest_id: int,
    item_id: int,
    feeling: int,
    *,
    policy: dict | None = None,
) -> dict:
    if policy is None:
        policy = load_offline_policy()
    guest_policy = policy.get("guest", {}) if isinstance(policy, dict) else {}
    invite_percent = max(0, min(100, int(guest_policy.get("party_invite_percent", 15) or 0)))
    roll = deterministic_roll(state, f"party_invite:{guest_id}:{item_id}", 100)

    meta = state.setdefault("offline_meta", {})
    now = int(time.time())
    visitor_policy = policy.get("visitor_schedule", {}) if isinstance(policy, dict) else {}
    stay_min = max(60, int(visitor_policy.get("served_departure_min_seconds", 7200) or 7200))
    stay_max = max(stay_min, int(visitor_policy.get("served_departure_max_seconds", 14400) or 14400))
    departure_at = now + stay_min + deterministic_roll(
        state, f"visitor_served_stay:{guest_id}:{item_id}", stay_max - stay_min + 1
    )
    guest_push = find_push(state, "guest.load")
    if guest_push and isinstance(guest_push.get("data"), dict):
        guest_push["data"]["expire_time"] = departure_at
    if roll < invite_percent:
        pending = {
            "kind": "invite",
            "guest_id": int(guest_id),
            "created_at": now,
            "departure_at": departure_at,
        }
        outcome = {
            "kind": "invite",
            "roll": roll,
            "invite_percent": invite_percent,
            "departure_at": departure_at,
        }
    else:
        # Decide the reward now so the later departure cannot reroll it, but
        # keep it out of mail.load until the visitor's stay has finished.
        mail = create_friend_return_mail(
            state, guest_id, item_id, feeling, policy=policy, append=False
        )
        pending = {
            "kind": "gift",
            "guest_id": int(guest_id),
            "created_at": now,
            "departure_at": departure_at,
            "mail": copy.deepcopy(mail),
        }
        outcome = {
            "kind": "gift",
            "roll": roll,
            "invite_percent": invite_percent,
            "mail_id": mail["id"],
            "departure_at": departure_at,
        }

    meta["pending_guest_departure"] = pending
    return outcome


def settle_pending_guest_departure(state: dict) -> dict | None:
    """Materialize the fed visitor's outcome on the next full login sync.

    Feeding immediately marks the courtyard visitor as served. The visitor
    stays for its scheduled post-meal interval; the normal return gift (or
    invitation) becomes visible only after that departure.
    """
    meta = state.setdefault("offline_meta", {})
    pending = meta.get("pending_guest_departure")
    if not isinstance(pending, dict):
        return None
    departure_at = int(pending.get("departure_at", 0) or 0)
    if departure_at > int(time.time()):
        return None

    guest_id = int(pending.get("guest_id", -1))
    guest_push = find_push(state, "guest.load")
    if guest_push:
        guest_push["data"] = {"id": -1}

    kind = str(pending.get("kind", ""))
    result: dict[str, Any] = {"kind": kind, "guest_id": guest_id}
    if kind == "gift":
        mail = copy.deepcopy(pending.get("mail") or {})
        if mail:
            mail["timestamp"] = int(time.time())
            mail["read"] = False
            # Defensive dedupe in case a previous process settled the same
            # pending record immediately before a crash during save.
            mail_id = int(mail.get("id", -1) or -1)
            if not any(
                isinstance(m, dict) and int(m.get("id", -2)) == mail_id
                for m in get_mail_list(state)
            ):
                get_mail_list(state).append(mail)
            result["mail_id"] = mail_id
    elif kind == "invite":
        create_party_invitation(state, guest_id)
    else:
        logging.warning("未知 pending_guest_departure kind=%r", kind)

    try:
        garden_result = garden_guest_departure(state, guest_id)
        result["garden"] = garden_result
        logging.info("GARDEN GUEST GROWTH guest=%d result=%s", guest_id, garden_result)
    except Exception:
        logging.exception("访客花圃成长结算失败")

    meta.pop("pending_guest_departure", None)
    if kind == "gift":
        schedule_next_guest(state, int(time.time()))
    save_state(state, f"guest.depart.settle:{guest_id}:{kind}")
    return result


def sync_museum_progress(state: dict) -> list[dict]:
    """Build the small server-owned museum progress view from authoritative save data."""
    museum_cfg = load_static_json("museumData_json.json")
    owned_collections, _ = get_handbook_parts(state)
    owned_collections_set = set(owned_collections)
    owned_pic_ids = {
        int(x.get("pic_id", -1))
        for x in get_album_index(state)
        if int(x.get("pic_id", -1)) >= 0
    }

    rows: list[dict] = []
    if isinstance(museum_cfg, dict):
        for key, cfg in sorted(museum_cfg.items(), key=lambda kv: int(kv[0])):
            if not isinstance(cfg, dict):
                continue
            mid = int(cfg.get("id", key))
            pic_ids = [int(x) for x in _to_list(cfg.get("pic_id"))]
            col_ids = [int(x) for x in str(cfg.get("collection_id", "")).split(",") if str(x).strip()]
            rows.append(
                {
                    "id": mid,
                    "pic_list": [x for x in pic_ids if x in owned_pic_ids],
                    "collections": [x for x in col_ids if x in owned_collections_set],
                }
            )

    state.setdefault("offline_meta", {})["museum_list"] = copy.deepcopy(rows)
    return rows


def _to_list(value: Any) -> list:
    return value if isinstance(value, list) else []



# ---------------------------------------------------------------------------
# Stage 6C: sustainable visitor scheduler + ordinary travel generator
# ---------------------------------------------------------------------------

ITEM_LUNCHBOX = 0
ITEM_AMULET = 1
ITEM_TOOL = 2
ITEM_SPECIALTY = 3

# GoalNumber place -> Collection/Specialty regional label. This is a local
# geographic compatibility mapping because current config no longer ships the
# old server TravelData/Area table.
PLACE_REGION = {
    1:"华北地区", 2:"西南地区", 3:"西南地区", 4:"华南地区", 5:"华南地区",
    6:"华东地区", 7:"华东地区", 8:"华北地区", 9:"台湾地区", 10:"台湾地区",
    11:"港澳地区", 12:"东北地区", 13:"华中地区", 14:"华中地区", 15:"华中地区",
    16:"西北地区", 17:"西北地区", 18:"西南地区", 19:"西南地区", 20:"华东地区",
    21:"华南地区", 22:"西南地区", 23:"西北地区", 24:"华东地区", 25:"华东地区",
    26:"华东地区", 27:"东北地区", 28:"华东地区", 29:"华北地区", 30:"东北地区",
    31:"港澳地区", 32:"西北地区", 33:"华北地区", 34:"西北地区", 35:"西北地区",
}

DIRECTION_PLACES = {
    "east":  {6,7,9,10,20,24,25,26,28},
    "west":  {2,3,16,17,18,19,22,23,32,34,35},
    "south": {4,5,9,10,11,18,21,24,31},
    "north": {1,8,12,15,16,17,23,27,28,29,30,32,33,34,35},
}

FOOD_AFFINITY = {
    # exact effects from encytravel descriptions; the concrete city groups are
    # local geographic realization of those descriptions.
    4: DIRECTION_PLACES["west"],                  # 香葱烤包子 -> 西部
    5: {4,6,7,9,10,11,20,21,24,28,31},           # 海苔煎豆腐 -> 沿海
    15:{6,7,20,25,26},                            # 桂花蒸米糕 -> 江南水乡
    16:DIRECTION_PLACES["north"],                 # 彩椒烙蛋饼 -> 北方
    33:{2,3,5,13,15,16,17,18,19,22,23,29,32,34,35}, # 水果沙拉 -> 山区
}

AMULET_DIRECTION = {
    1002:"east", 1003:"west", 1004:"south", 1005:"north", # 铃铛
    1007:"east", 1008:"west", 1009:"south", 1010:"north", # 千纸鹤
    1013:"east", 1014:"west", 1015:"south", 1016:"north", # 帆船
    1100:"south",                                                  # 心意风铃
}

UNSEEN_AMULETS = {1000,1001,1006,1011}
MUSEUM_TICKET_TO_ID = {1017:1,1018:2,1019:3,1020:4,1021:5}
MUSEUM_PLACE_BY_ID = {1:100,2:101,3:102,4:103,5:104}

# Museum Day's four destinations and their directed museum tickets are official
# item/config relations. The map layouts below only drive the preserved client
# presentation; the original server-side route generator is no longer shipped.
MUSEUM_DAY_TICKET_BY_ID = {1: 1017, 2: 1018, 3: 1019, 4: 1020}
MUSEUM_DAY_PATHS = {
    1: (1, 2, 3, 4, 5, 12, 19, 26),
    2: (8, 9, 10, 11, 12, 19, 26, 33),
    3: (15, 22, 29, 30, 31, 32, 33, 34),
    4: (3, 10, 17, 24, 25, 26, 27, 28),
}
MUSEUM_DAY_TILE_STYLES = ((1, 1), (2, 1), (3, 1), (4, 1), (1, 2), (2, 2), (3, 2), (4, 3))
MUSEUM_DAY_END_DESC_IDS = (401, 402)


def museum_day_policy(policy: dict | None = None) -> dict:
    if policy is None:
        policy = load_offline_policy()
    value = policy.get("museum_day", {}) if isinstance(policy, dict) else {}
    return value if isinstance(value, dict) else {}


def museum_day_state(state: dict) -> dict:
    meta = state.setdefault("offline_meta", {})
    value = meta.get("museum_day")
    if not isinstance(value, dict):
        value = {}
        meta["museum_day"] = value
    return value


def _museum_day_path(museum_id: int) -> list[dict]:
    grids = MUSEUM_DAY_PATHS[int(museum_id)]
    return [
        {"grid": int(grid), "type": int(MUSEUM_DAY_TILE_STYLES[index][0]), "style": int(MUSEUM_DAY_TILE_STYLES[index][1])}
        for index, grid in enumerate(grids)
    ]


def _museum_day_empty_payload(end_time: int) -> dict:
    return {
        "end_time": int(end_time), "inspire_num": 0, "inspire_time": 0,
        "museum_list": [], "cur_museum": 0, "compass": 0,
        "task_num": 0, "task_week": "", "task_login_claimed": False,
        "task_share_claimed": False, "frog": 0, "next": 0, "left_num": 0,
        "desc_id": 0, "pic_id": 0, "items": [], "get_items": [],
        "log_list": [], "path": [],
    }


def museum_day_payload(state: dict, now: int | None = None, policy: dict | None = None) -> tuple[dict, bool]:
    """Return the Museum Day wire payload and initialize durable local state."""
    if now is None:
        now = int(time.time())
    mp = museum_day_policy(policy)
    data = museum_day_state(state)
    changed = False
    end_time = int(data.get("end_time", 0) or 0)
    renewed = always_on_end_time(end_time, now) if bool(mp.get("enabled", True)) else 0
    if end_time != renewed:
        end_time = renewed
        data["end_time"] = end_time
        changed = True

    defaults = _museum_day_empty_payload(end_time)
    for key, value in defaults.items():
        if key not in data or not isinstance(data.get(key), type(value)):
            data[key] = copy.deepcopy(value)
            changed = True
    data["end_time"] = end_time
    task_week = time.strftime("%Y-W%W", time.localtime(now))
    if data.get("task_week") != task_week:
        data["task_week"] = task_week
        data["task_num"] = 0
        data["task_login_claimed"] = False
        data["task_share_claimed"] = False
        changed = True
    active_id = int(data.get("cur_museum", 0) or 0)
    if active_id not in MUSEUM_DAY_TICKET_BY_ID:
        if active_id != 0:
            data["cur_museum"] = 0
            changed = True
        active_id = 0
    if active_id:
        path = data.get("path")
        if not isinstance(path, list) or len(path) != len(MUSEUM_DAY_PATHS[active_id]):
            data["path"] = _museum_day_path(active_id)
            changed = True
        path_len = len(data["path"])
        for key in ("frog", "next"):
            value = max(1, min(path_len, int(data.get(key, 1) or 1)))
            if int(data.get(key, 0) or 0) != value:
                data[key] = value
                changed = True
    return copy.deepcopy(data), changed


def museum_day_record_task_progress(
    state: dict,
    source: str,
    now: int | None = None,
    policy: dict | None = None,
) -> tuple[bool, bool]:
    """Record the weekly Museum Day task source and award an inspiration at eight."""
    if now is None:
        now = int(time.time())
    _, changed = museum_day_payload(state, now=now, policy=policy)
    target = museum_day_state(state)
    if source == "login":
        if bool(target.get("task_login_claimed", False)):
            return changed, False
        target["task_login_claimed"] = True
    elif source == "share":
        if bool(target.get("task_share_claimed", False)):
            return changed, False
        target["task_share_claimed"] = True
    elif source != "travel":
        return changed, False

    previous = max(0, min(8, int(target.get("task_num", 0) or 0)))
    current = min(8, previous + 1)
    target["task_num"] = current
    changed = True
    inspiration_awarded = previous < 8 and current == 8
    if inspiration_awarded:
        target["inspire_num"] = int(target.get("inspire_num", 0) or 0) + 1
        target["inspire_time"] = int(now)
    return changed, inspiration_awarded


def _museum_day_begin(state: dict, museum_id: int, now: int, policy: dict, *, cost: int = 0) -> tuple[bool, str]:
    if museum_id not in MUSEUM_DAY_TICKET_BY_ID:
        return False, "bad_museum"
    data, _ = museum_day_payload(state, now=now, policy=policy)
    target = museum_day_state(state)
    if int(data.get("cur_museum", 0) or 0) != 0:
        return False, "already_exploring"
    completed = {int(row.get("id", 0) or 0) for row in data.get("museum_list", []) if isinstance(row, dict)}
    if museum_id in completed and len(completed) < len(MUSEUM_DAY_TICKET_BY_ID):
        return False, "museum_already_completed"
    if cost > 0:
        res, _ = get_clover_parts(state)
        total = int(res.get("clover_point", 0) or 0)
        if total < cost:
            return False, "not_enough_clover"
        res["clover_point"] = total - int(cost)

    path = _museum_day_path(museum_id)
    target.update({
        "cur_museum": int(museum_id), "compass": len(path) - 1,
        "frog": 1, "next": 1,
        "left_num": max(0, int(museum_day_policy(policy).get("refreshes_per_exploration", 3) or 3)),
        "desc_id": 0, "pic_id": 0, "items": [], "get_items": [],
        "log_list": [], "path": path, "reward_items": [],
    })
    return True, "ok"


def museum_day_refresh(state: dict, now: int | None = None, policy: dict | None = None) -> tuple[bool, int, str]:
    if now is None:
        now = int(time.time())
    if policy is None:
        policy = load_offline_policy()
    data, _ = museum_day_payload(state, now=now, policy=policy)
    active = int(data.get("cur_museum", 0) or 0)
    if active and int(data.get("next", 0) or 0) > 1:
        return False, -1, "route_already_started"
    if active:
        left = int(data.get("left_num", 0) or 0)
        if left <= 0:
            return False, -1, "no_refreshes"
        museum_day_state(state)["left_num"] = left - 1

    completed = {int(row.get("id", 0) or 0) for row in data.get("museum_list", []) if isinstance(row, dict)}
    candidates = [museum_id for museum_id in MUSEUM_DAY_TICKET_BY_ID if museum_id not in completed]
    if not candidates:
        return False, -1, "all_completed"
    if active in candidates and len(candidates) > 1:
        index = (candidates.index(active) + 1) % len(candidates)
        museum_id = candidates[index]
    else:
        museum_id = candidates[0]
    ok, reason = _museum_day_begin(state, museum_id, now, policy)
    if ok and active:
        museum_day_state(state)["left_num"] = left - 1
    return ok, int(museum_day_state(state).get("left_num", 0) or 0) if ok else -1, reason


def _museum_day_direction(current_grid: int, next_grid: int) -> int:
    current = int(current_grid) - 1
    following = int(next_grid) - 1
    current_x, current_y = current % 7, current // 7
    next_x, next_y = following % 7, following // 7
    if next_x > current_x:
        return 4
    if next_x < current_x:
        return 3
    if next_y > current_y:
        return 2
    if next_y < current_y:
        return 1
    return 0


def museum_day_advance(state: dict, now: int | None = None, policy: dict | None = None, *, direction: int | None = None, use_inspire: bool = False) -> tuple[bool, dict]:
    if now is None:
        now = int(time.time())
    if policy is None:
        policy = load_offline_policy()
    data, _ = museum_day_payload(state, now=now, policy=policy)
    if int(data.get("cur_museum", 0) or 0) <= 0:
        return False, data
    path = data.get("path", [])
    next_index = int(data.get("next", 0) or 0)
    if next_index <= 0 or next_index >= len(path):
        return False, data
    if direction is not None:
        required = _museum_day_direction(path[next_index - 1]["grid"], path[next_index]["grid"])
        if int(direction) != required:
            return False, data
    target = museum_day_state(state)
    if use_inspire:
        if int(target.get("inspire_num", 0) or 0) <= 0 or now < int(target.get("inspire_time", 0) or 0):
            return False, data
        target["inspire_num"] = int(target.get("inspire_num", 0) or 0) - 1
    else:
        compass = int(target.get("compass", 0) or 0)
        if compass <= 0:
            return False, data
        target["compass"] = compass - 1

    target["next"] = next_index + 1
    target.setdefault("log_list", []).append({
        "time": int(now), "desc": 101 + (next_index % 5),
        "item_id": 200002, "item_num": 1,
    })
    if int(target["next"]) >= len(path):
        museum_id = int(target["cur_museum"])
        target["frog"] = len(path)
        target["desc_id"] = MUSEUM_DAY_END_DESC_IDS[(museum_id - 1) % len(MUSEUM_DAY_END_DESC_IDS)]
        target["pic_id"] = 0
        rewards = [{"item_id": MUSEUM_DAY_TICKET_BY_ID[museum_id], "num": 1}]
        target["items"] = rewards
        target["reward_items"] = copy.deepcopy(rewards)
    return True, copy.deepcopy(target)


def museum_day_get_items(state: dict, now: int | None = None, policy: dict | None = None) -> tuple[bool, list[dict]]:
    payload, _ = museum_day_payload(state, now=now, policy=policy)
    target = museum_day_state(state)
    items = [copy.deepcopy(row) for row in payload.get("items", []) if isinstance(row, dict)]
    if not items:
        return True, []
    _, house, _, _ = get_item_parts(state)
    received = target.setdefault("get_items", [])
    for row in items:
        item_id = int(row.get("item_id", -1) or -1)
        count = max(0, int(row.get("num", 0) or 0))
        if item_id < 0 or count <= 0:
            continue
        set_house_count(house, item_id, get_house_count(house, item_id) + count)
        received.append({"item_id": item_id, "num": count})
    target["items"] = []
    return True, items


def museum_day_arrive(state: dict, now: int | None = None, policy: dict | None = None) -> tuple[bool, str]:
    if now is None:
        now = int(time.time())
    data, _ = museum_day_payload(state, now=now, policy=policy)
    museum_id = int(data.get("cur_museum", 0) or 0)
    path = data.get("path", [])
    if museum_id <= 0 or not path or int(data.get("frog", 0) or 0) != len(path):
        return False, "not_arrived"
    target = museum_day_state(state)
    history = target.setdefault("museum_list", [])
    history.append({
        "id": museum_id, "desc_id": int(data.get("desc_id", 401) or 401),
        "pic_id": int(data.get("pic_id", 0) or 0), "time": int(now),
        "items": copy.deepcopy(target.get("reward_items", target.get("get_items", []))),
    })
    completed = {int(row.get("id", 0) or 0) for row in history if isinstance(row, dict)}
    if set(MUSEUM_DAY_TICKET_BY_ID).issubset(completed):
        grant_permanent_special_title(
            state,
            int(special_title_policy(policy).get("museum_day_collection", 901) or 901),
        )
    target.update({
        "cur_museum": 0, "compass": 0, "frog": 0,
        "next": 0, "left_num": 0, "desc_id": 0, "pic_id": 0,
        "items": [], "get_items": [], "log_list": [], "path": [],
        "reward_items": [],
    })
    return True, "ok"


# Mid-Autumn greeting-card data is retained in the official greetCard_json.
GREET_CARD_BACKGROUND_IDS = (1, 2, 3, 4, 5)
GREET_CARD_TAG_IDS = tuple(range(101, 126))
GREET_CARD_MOONCAKE_IDS = (47, 48, 49, 50, 51, 52, 53)
GREET_CARD_RANDOM_REWARD_IDS = (1000, 1, 8001, 5101, 20001, 18, 1201, 1105, 11001, 8002, 10105)


def greet_card_policy(policy: dict | None = None) -> dict:
    if policy is None:
        policy = load_offline_policy()
    value = policy.get("greet_card", {}) if isinstance(policy, dict) else {}
    return value if isinstance(value, dict) else {}


def _activity_item_count(items: list[dict], item_id: int) -> int:
    return next((int(row.get("num", 0) or 0) for row in items if isinstance(row, dict) and int(row.get("item_id", -1) or -1) == int(item_id)), 0)


def _activity_set_item_count(items: list[dict], item_id: int, count: int) -> None:
    item_id = int(item_id)
    count = max(0, int(count))
    for index, row in enumerate(items):
        if isinstance(row, dict) and int(row.get("item_id", -1) or -1) == item_id:
            if count:
                row["num"] = count
            else:
                items.pop(index)
            return
    if count:
        items.append({"item_id": item_id, "num": count})


def _greet_card_default(end_time: int) -> dict:
    return {
        "end_time": int(end_time), "card_info": {"bg": 0, "bless": 0, "tags": [0, 0, 0]},
        "send_list": [], "get_list": [], "items": [], "task_login": True,
        "task_share": False, "task_item": [], "task_day": "",
        "can_reward": False, "global_num": 0, "new_index": 0, "stock_num": 0,
        "background_buy_num": 0,
    }


def greet_card_payload(state: dict, now: int | None = None, policy: dict | None = None) -> tuple[dict, bool]:
    if now is None:
        now = int(time.time())
    gp = greet_card_policy(policy)
    meta = state.setdefault("offline_meta", {})
    data = meta.get("greet_card")
    changed = False
    if not isinstance(data, dict):
        data = {}
        meta["greet_card"] = data
        changed = True
    # The two cards in the captured original save are preserved as the local
    # festival anecdote history.  They are not regenerated or replaced by
    # offline player-interaction traffic.
    if int(data.get("history_version", 0) or 0) < 1:
        source = find_push(state, "greetcard.load")
        source_data = source.get("data", {}) if isinstance(source, dict) else {}
        if isinstance(source_data, dict):
            for key in ("send_list", "get_list"):
                value = source_data.get(key)
                if isinstance(value, list):
                    data[key] = copy.deepcopy(value)
        data["history_version"] = 1
        changed = True
    background_count_missing = not isinstance(data.get("background_buy_num"), int)
    _, end_time, _ = seasonal_activity_window("greet_card", now)
    if GALLERY_MODE:
        end_time = always_on_end_time(int(data.get("end_time", 0) or 0), now)
    if not bool(gp.get("enabled", True)):
        end_time = 0
    if int(data.get("end_time", 0) or 0) != end_time:
        data["end_time"] = end_time
        changed = True
    defaults = _greet_card_default(end_time)
    for key, value in defaults.items():
        if key not in data or not isinstance(data.get(key), type(value)):
            data[key] = copy.deepcopy(value)
            changed = True
    if background_count_missing:
        used_backgrounds = sum(
            1 for card_row in data.get("send_list", [])
            if isinstance(card_row, dict) and int(card_row.get("bg", 0) or 0) in GREET_CARD_BACKGROUND_IDS
        )
        current_background = int(data.get("card_info", {}).get("bg", 0) or 0)
        held_backgrounds = sum(
            _activity_item_count(data.get("items", []), background_id)
            for background_id in GREET_CARD_BACKGROUND_IDS
        )
        data["background_buy_num"] = used_backgrounds + held_backgrounds + int(current_background in GREET_CARD_BACKGROUND_IDS)
        changed = True
    task_day = time.strftime("%Y-%m-%d", time.localtime(now))
    if data["task_day"] != task_day:
        data["task_day"] = task_day
        data["task_login"] = True
        data["task_share"] = False
        data["task_item"] = [
            GREET_CARD_TAG_IDS[
                deterministic_roll(state, f"greetcard.login:{task_day}", len(GREET_CARD_TAG_IDS))
            ]
        ]
        changed = True
    card = data["card_info"]
    if not isinstance(card.get("tags"), list) or len(card["tags"]) != 3:
        card["tags"] = [0, 0, 0]
        changed = True
    for key in ("bg", "bless"):
        card[key] = int(card.get(key, 0) or 0)
    card["tags"] = [int(value or 0) for value in card["tags"]]
    task_items = [int(value) for value in data["task_item"] if int(value) in GREET_CARD_TAG_IDS]
    if data["task_item"] != task_items:
        data["task_item"] = task_items
        changed = True
    data["global_num"] = len(data["send_list"])
    return copy.deepcopy(data), changed


def _greet_card_state(state: dict) -> dict:
    value = state.setdefault("offline_meta", {}).get("greet_card")
    if not isinstance(value, dict):
        greet_card_payload(state)
        value = state["offline_meta"]["greet_card"]
    return value


def greet_card_buy(state: dict, item_id: int, policy: dict | None = None) -> tuple[bool, str, int | None]:
    data, _ = greet_card_payload(state, policy=policy)
    target = _greet_card_state(state)
    item_id = int(item_id)
    if item_id not in GREET_CARD_BACKGROUND_IDS and item_id not in GREET_CARD_TAG_IDS:
        return False, "bad_card_item", None
    items = target["items"]
    if item_id in GREET_CARD_BACKGROUND_IDS:
        prices = [max(0, int(value)) for value in greet_card_policy(policy).get("background_prices", [0, 100, 150, 200])]
        purchase_count = max(0, int(target.get("background_buy_num", 0) or 0))
        price = 0 if purchase_count == 0 else (prices[1] if len(prices) > 1 else 100)
    else:
        price = max(0, int(greet_card_policy(policy).get("tag_price", 20) or 20))
    res, _ = get_clover_parts(state)
    clover = int(res.get("clover_point", 0) or 0)
    if clover < price:
        return False, "not_enough_clover", None
    res["clover_point"] = clover - price
    _activity_set_item_count(items, item_id, _activity_item_count(items, item_id) + 1)
    if item_id in GREET_CARD_BACKGROUND_IDS:
        target["background_buy_num"] = max(0, int(target.get("background_buy_num", 0) or 0)) + 1
    return True, "ok", int(res["clover_point"])


def greet_card_change_background(state: dict, background_id: int, policy: dict | None = None) -> tuple[bool, str]:
    greet_card_payload(state, policy=policy)
    target = _greet_card_state(state)
    background_id = int(background_id)
    if background_id not in {0, *GREET_CARD_BACKGROUND_IDS}:
        return False, "bad_background"
    card = target["card_info"]
    current = int(card.get("bg", 0) or 0)
    if background_id == current:
        return True, "ok"
    items = target["items"]
    if background_id and _activity_item_count(items, background_id) <= 0:
        return False, "background_not_owned"
    if background_id:
        _activity_set_item_count(items, background_id, _activity_item_count(items, background_id) - 1)
    if current:
        _activity_set_item_count(items, current, _activity_item_count(items, current) + 1)
    card["bg"] = background_id
    return True, "ok"


def greet_card_change_bless(state: dict, bless_id: int, policy: dict | None = None) -> tuple[bool, str]:
    greet_card_payload(state, policy=policy)
    bless_id = int(bless_id)
    if bless_id not in range(0, 7):
        return False, "bad_bless"
    _greet_card_state(state)["card_info"]["bless"] = bless_id
    return True, "ok"


def greet_card_put_tag(state: dict, pos: int, tag_id: int, policy: dict | None = None) -> tuple[bool, str]:
    greet_card_payload(state, policy=policy)
    target = _greet_card_state(state)
    pos = int(pos) - 1
    tag_id = int(tag_id)
    if pos not in range(3) or tag_id not in {0, *GREET_CARD_TAG_IDS}:
        return False, "bad_tag"
    tags = target["card_info"]["tags"]
    current = int(tags[pos] or 0)
    if tag_id == current:
        return True, "ok"
    items = target["items"]
    if tag_id and _activity_item_count(items, tag_id) <= 0:
        return False, "tag_not_owned"
    if tag_id:
        _activity_set_item_count(items, tag_id, _activity_item_count(items, tag_id) - 1)
    if current:
        _activity_set_item_count(items, current, _activity_item_count(items, current) + 1)
    tags[pos] = tag_id
    return True, "ok"


def greet_card_send(state: dict, now: int | None = None, policy: dict | None = None) -> tuple[bool, str]:
    if now is None:
        now = int(time.time())
    greet_card_payload(state, now=now, policy=policy)
    target = _greet_card_state(state)
    card = target["card_info"]
    if int(card.get("bg", 0) or 0) <= 0 or any(int(value or 0) <= 0 for value in card.get("tags", [])):
        return False, "card_incomplete"
    target["send_list"].append(copy.deepcopy(card))
    target["card_info"] = {"bg": 0, "bless": 0, "tags": [0, 0, 0]}
    target["can_reward"] = True
    if not bool(target.get("task_share", False)):
        task_day = str(target.get("task_day", ""))
        target["task_share"] = True
        target["task_item"].append(
            GREET_CARD_TAG_IDS[
                deterministic_roll(state, f"greetcard.share:{task_day}", len(GREET_CARD_TAG_IDS))
            ]
        )
    target["global_num"] = len(target["send_list"])
    return True, "ok"


def greet_card_get_reward(state: dict, mooncake_id: int, policy: dict | None = None) -> tuple[bool, list[int]]:
    greet_card_payload(state, policy=policy)
    target = _greet_card_state(state)
    mooncake_id = int(mooncake_id)
    if not bool(target.get("can_reward", False)):
        return False, []
    if mooncake_id not in GREET_CARD_MOONCAKE_IDS:
        return False, []
    random_id = GREET_CARD_RANDOM_REWARD_IDS[deterministic_roll(state, f"greetcard.reward:{len(target['send_list'])}", len(GREET_CARD_RANDOM_REWARD_IDS))]
    rewards = [mooncake_id, random_id]
    _, house, _, _ = get_item_parts(state)
    for item_id in rewards:
        set_house_count(house, item_id, get_house_count(house, item_id) + 1)
    target["can_reward"] = False
    return True, rewards


def greet_card_get_task_items(state: dict, policy: dict | None = None) -> list[int]:
    greet_card_payload(state, policy=policy)
    return [int(value) for value in _greet_card_state(state).get("task_item", []) if int(value) in GREET_CARD_TAG_IDS]


def greet_card_claim_task_items(state: dict, policy: dict | None = None) -> tuple[bool, list[int]]:
    items = greet_card_get_task_items(state, policy=policy)
    if not items:
        return True, []
    target = _greet_card_state(state)
    for item_id in items:
        _activity_set_item_count(target["items"], item_id, _activity_item_count(target["items"], item_id) + 1)
    target["task_item"] = []
    return True, items


# Spring Card (春节贺卡) cards use the official 5 backgrounds and 12 sticker
# identifiers from springCard_json. Sticker acquisition and card state are local
# because the original event service is no longer available.
SPRING_CARD_BACKGROUND_IDS = (1, 2, 3, 4, 5)
SPRING_CARD_TAG_IDS = tuple(range(101, 113))

# Local reconstruction from the TapTap "贺卡攻略" (2024-02-17), matched to
# springCard.json and the shipped sticker sprites. Sticker order is irrelevant.
# The guide identifies the five 100%-gold combinations; the other tier names
# are qualitative, so their numeric rates below are local estimates, not the
# original server's unpublished probabilities.
SPRING_CARD_GOLD_TIERS = (
    (100, (
        (1, (101, 109, 110)), (2, (102, 105, 112)), (3, (103, 106, 112)),
        (4, (103, 109, 110)), (5, (103, 107, 112)),
    )),
    (80, (
        (1, (101, 104, 112)), (2, (102, 108, 111)), (3, (103, 109, 112)),
        (4, (103, 106, 110)), (5, (103, 106, 112)),
    )),
    (70, (
        (1, (101, 105, 107)), (2, (105, 107, 111)),
        (3, (103, 108, 112)), (4, (104, 109, 110)),
    )),
    (50, (
        (1, (103, 109, 112)), (2, (102, 105, 111)),
        (3, (101, 105, 107)), (4, (103, 107, 110)),
    )),
    (0, (
        (1, (101, 106, 110)), (3, (104, 107, 110)),
    )),
)
SPRING_CARD_GOLD_CHANCES = {
    (background, tags): chance
    for chance, combinations in SPRING_CARD_GOLD_TIERS
    for background, tags in combinations
}
SPRING_CARD_BLESSING_IDS = tuple(range(1, 7))
SPRING_CARD_BOX_BLESSING_IDS = tuple(range(7, 16))
# Captured springcard.load histories show these physical box outcomes.  The
# retired event service kept the precise table server-side; the local pool
# retains every observed reward while the gold box uses the same reward set.
SPRING_CARD_BOX_REWARDS = (
    (200000, 10),  # 三叶草
    (200001, 2),   # 抽奖券
    (1000, 1),     # 四叶草
    (20001, 1),    # 一号水溶肥
    (5101, 1),     # 种子盲盒
    (19, 1),       # 饺子
    (0, 1),        # 奶油华夫饼
)


def spring_card_policy(policy: dict | None = None) -> dict:
    if policy is None:
        policy = load_offline_policy()
    value = policy.get("spring_card", {}) if isinstance(policy, dict) else {}
    return value if isinstance(value, dict) else {}


def _spring_card_default(end_time: int, policy: dict | None = None) -> dict:
    sp = spring_card_policy(policy)
    return {
        "end_time": int(end_time), "card_info": {"bg": 1, "bless": 0, "tags": [0, 0, 0]},
        "items": [], "task_harvest": 0, "buy_num": 0, "shop_day": "",
        "can_buy_num": max(1, int(sp.get("bag_limit", 6) or 6)),
        "task_item": [], "share_num": 0, "share_get": 0, "box_id": 0,
        "share_code": "", "reward_list": [], "global_num": 0, "sent_count": 0,
    }


def spring_card_payload(state: dict, now: int | None = None, policy: dict | None = None) -> tuple[dict, bool]:
    if now is None:
        now = int(time.time())
    sp = spring_card_policy(policy)
    meta = state.setdefault("offline_meta", {})
    data = meta.get("spring_card")
    changed = False
    if not isinstance(data, dict):
        data = {}
        meta["spring_card"] = data
        changed = True
    _, end_time, _ = seasonal_activity_window("spring_card", now)
    if GALLERY_MODE:
        end_time = always_on_end_time(int(data.get("end_time", 0) or 0), now)
    if not bool(sp.get("enabled", True)):
        end_time = 0
    if int(data.get("end_time", 0) or 0) != end_time:
        data["end_time"] = end_time
        changed = True
    defaults = _spring_card_default(end_time, policy)
    for key, value in defaults.items():
        if key not in data or not isinstance(data.get(key), type(value)):
            data[key] = copy.deepcopy(value)
            changed = True
    card = data["card_info"]
    if not isinstance(card.get("tags"), list) or len(card["tags"]) != 3:
        card["tags"] = [0, 0, 0]
        changed = True
    card["bg"] = int(card.get("bg", 1) or 1)
    card["bless"] = int(card.get("bless", 0) or 0)
    card["tags"] = [int(value or 0) for value in card["tags"]]
    shop_day = time.strftime("%Y-%m-%d", time.localtime(now))
    if data["shop_day"] != shop_day:
        data["shop_day"] = shop_day
        data["buy_num"] = 0
        changed = True
    data["can_buy_num"] = max(1, int(sp.get("bag_limit", 6) or 6))
    return copy.deepcopy(data), changed


def _spring_card_state(state: dict) -> dict:
    value = state.setdefault("offline_meta", {}).get("spring_card")
    if not isinstance(value, dict):
        spring_card_payload(state)
        value = state["offline_meta"]["spring_card"]
    return value


def _spring_card_next_tag(state: dict, label: str) -> int:
    target = _spring_card_state(state)
    items = target["items"]
    counts = {item_id: _activity_item_count(items, item_id) for item_id in SPRING_CARD_TAG_IDS}
    lowest = min(counts.values())
    candidates = [item_id for item_id, count in counts.items() if count == lowest]
    return candidates[deterministic_roll(state, label, len(candidates))]


def spring_card_buy(state: dict, policy: dict | None = None) -> tuple[bool, int]:
    payload, _ = spring_card_payload(state, policy=policy)
    target = _spring_card_state(state)
    buy_num = int(payload.get("buy_num", 0) or 0)
    if buy_num >= int(payload.get("can_buy_num", 0) or 0):
        return False, 0
    price = 0 if buy_num == 0 else max(0, int(spring_card_policy(policy).get("bag_price", 20) or 20))
    res, _ = get_clover_parts(state)
    clover = int(res.get("clover_point", 0) or 0)
    if clover < price:
        return False, 0
    tag_id = _spring_card_next_tag(state, f"springcard.buy:{buy_num}")
    res["clover_point"] = clover - price
    _activity_set_item_count(target["items"], tag_id, _activity_item_count(target["items"], tag_id) + 1)
    target["buy_num"] = buy_num + 1
    return True, tag_id


def spring_card_change_background(state: dict, background_id: int, policy: dict | None = None) -> tuple[bool, str]:
    spring_card_payload(state, policy=policy)
    background_id = int(background_id)
    if background_id not in SPRING_CARD_BACKGROUND_IDS:
        return False, "bad_background"
    _spring_card_state(state)["card_info"]["bg"] = background_id
    return True, "ok"


def spring_card_change_bless(state: dict, bless_id: int, policy: dict | None = None) -> tuple[bool, str]:
    spring_card_payload(state, policy=policy)
    bless_id = int(bless_id)
    if bless_id not in {0, *SPRING_CARD_BLESSING_IDS}:
        return False, "bad_bless"
    _spring_card_state(state)["card_info"]["bless"] = bless_id
    return True, "ok"


def spring_card_put_tag(state: dict, pos: int, tag_id: int, policy: dict | None = None) -> tuple[bool, str]:
    spring_card_payload(state, policy=policy)
    target = _spring_card_state(state)
    pos = int(pos) - 1
    tag_id = int(tag_id)
    if pos not in range(3) or tag_id not in {0, *SPRING_CARD_TAG_IDS}:
        return False, "bad_tag"
    tags = target["card_info"]["tags"]
    current = int(tags[pos] or 0)
    if tag_id == current:
        return True, "ok"
    if tag_id and _activity_item_count(target["items"], tag_id) <= 0:
        return False, "tag_not_owned"
    if tag_id:
        _activity_set_item_count(target["items"], tag_id, _activity_item_count(target["items"], tag_id) - 1)
    if current:
        _activity_set_item_count(target["items"], current, _activity_item_count(target["items"], current) + 1)
    tags[pos] = tag_id
    return True, "ok"


def spring_card_send(state: dict, policy: dict | None = None) -> tuple[bool, int]:
    payload, _ = spring_card_payload(state, policy=policy)
    target = _spring_card_state(state)
    card = target["card_info"]
    if int(card.get("bg", 0) or 0) <= 0 or any(int(value or 0) <= 0 for value in card.get("tags", [])):
        return False, 0
    target["sent_count"] = int(target.get("sent_count", 0) or 0) + 1
    target["global_num"] = int(target.get("sent_count", 0) or 0)
    background = int(card["bg"])
    tags = tuple(sorted(int(value) for value in card["tags"]))
    chance = SPRING_CARD_GOLD_CHANCES.get((background, tags), 20)
    roll = deterministic_roll(state, f"springcard.gold:{background}:{tags}:{target['sent_count']}", 100)
    target["box_id"] = 200010 if roll < chance else 200009
    target["card_info"] = {"bg": 1, "bless": 0, "tags": [0, 0, 0]}
    return True, int(target["box_id"])


def spring_card_open_box(state: dict, policy: dict | None = None) -> tuple[bool, int, int]:
    spring_card_payload(state, policy=policy)
    target = _spring_card_state(state)
    box_id = int(target.get("box_id", 0) or 0)
    if box_id not in {200009, 200010}:
        return False, 0, 0

    roll_label = f"springcard.box:{box_id}:{target.get('sent_count', 0)}"
    # The red chest can still reveal a greeting; the gold chest always carries
    # an item.  Both chest types otherwise draw from the observed item pool.
    if box_id == 200009 and deterministic_roll(state, roll_label, len(SPRING_CARD_BOX_REWARDS) + 1) == len(SPRING_CARD_BOX_REWARDS):
        blessing_id = SPRING_CARD_BOX_BLESSING_IDS[deterministic_roll(state, f"{roll_label}:blessing", len(SPRING_CARD_BOX_BLESSING_IDS))]
        target["box_id"] = 0
        return True, blessing_id, 0

    reward_id, count = SPRING_CARD_BOX_REWARDS[
        deterministic_roll(state, f"{roll_label}:reward", len(SPRING_CARD_BOX_REWARDS))
    ]
    res, _ = get_clover_parts(state)
    if reward_id == LOTTERY_CLOVER_ITEM_ID:
        res["clover_point"] = int(res.get("clover_point", 0) or 0) + count
        calendar_record_clover_gain(state, count)
    elif reward_id == LOTTERY_TICKET_ITEM_ID:
        res["ticket"] = int(res.get("ticket", 0) or 0) + count
    else:
        _, house, _, _ = get_item_parts(state)
        set_house_count(house, reward_id, get_house_count(house, reward_id) + count)
    target.setdefault("reward_list", []).append({"item_id": reward_id, "num": count})
    target["box_id"] = 0
    return True, reward_id, count


def spring_card_queue_tag_reward(state: dict, label: str, policy: dict | None = None) -> int:
    spring_card_payload(state, policy=policy)
    target = _spring_card_state(state)
    tag_id = _spring_card_next_tag(state, label)
    pending = target.setdefault("task_item", [])
    if tag_id not in pending:
        pending.append(tag_id)
    return tag_id


def spring_card_claim_task_items(state: dict, policy: dict | None = None) -> tuple[bool, list[int]]:
    spring_card_payload(state, policy=policy)
    target = _spring_card_state(state)
    tags = [int(value) for value in target.get("task_item", []) if int(value) in SPRING_CARD_TAG_IDS]
    for tag_id in tags:
        _activity_set_item_count(target["items"], tag_id, _activity_item_count(target["items"], tag_id) + 1)
    target["task_item"] = []
    return True, tags


def spring_card_share_tag(state: dict, tag_id: int, policy: dict | None = None) -> tuple[bool, str, list[int]]:
    spring_card_payload(state, policy=policy)
    target = _spring_card_state(state)
    tag_id = int(tag_id)
    if tag_id not in SPRING_CARD_TAG_IDS or _activity_item_count(target["items"], tag_id) <= 0:
        return False, "", []
    limit = max(1, int(spring_card_policy(policy).get("share_limit", 3) or 3))
    if int(target.get("share_num", 0) or 0) >= limit:
        return False, "", []
    target["share_num"] = int(target.get("share_num", 0) or 0) + 1
    target["share_code"] = f"SPRING-{int(target['share_num'])}-{tag_id}"
    reward_tag = spring_card_queue_tag_reward(state, f"springcard.share:{tag_id}:{target['share_num']}", policy)
    return True, str(target["share_code"]), [reward_tag]


def spring_card_exchange_tag(state: dict, code: str, policy: dict | None = None) -> tuple[bool, int]:
    spring_card_payload(state, policy=policy)
    target = _spring_card_state(state)
    if not str(code or "").strip():
        return False, 0
    limit = max(1, int(spring_card_policy(policy).get("share_limit", 3) or 3))
    if int(target.get("share_get", 0) or 0) >= limit:
        return False, 0
    tag_id = _spring_card_next_tag(state, f"springcard.exchange:{target.get('share_get', 0)}")
    _activity_set_item_count(target["items"], tag_id, _activity_item_count(target["items"], tag_id) + 1)
    target["share_get"] = int(target.get("share_get", 0) or 0) + 1
    return True, tag_id


def spring_card_on_clover_harvest(state: dict, policy: dict | None = None) -> tuple[bool, list[int]]:
    spring_card_payload(state, policy=policy)
    target = _spring_card_state(state)
    if int(target.get("task_harvest", 0) or 0) >= 1:
        return False, []
    target["task_harvest"] = 1
    return True, [spring_card_queue_tag_reward(state, "springcard.harvest", policy)]


# PartyCakeData_json preserves the complete cake recipe.  The live activity
# service used to drive its daily task callbacks, while the client owns the
# five-part assembly, reward, Q&A and candle state machine.
PARTY_CAKE_MAKING = 0
PARTY_CAKE_MAKE_REWARD = 1
PARTY_CAKE_QA = 2
PARTY_CAKE_QA_REWARD = 3
PARTY_CAKE_LIGHT = 4
PARTY_CAKE_LIGHT_REWARD_STATE = 5
PARTY_CAKE_COMPLETE = 6
PARTY_CAKE_CREAM_ID = 200006
PARTY_CAKE_SUGAR_ID = 200007
PARTY_CAKE_LIGHT_REWARD_ITEM = (203004, 1)
PARTY_CAKE_SHARE_REWARDS = (1, 57)
# The original PartyCakeData opens the cake base immediately, the decoration
# and small decorations after one week, fondant after two weeks and the top
# layer after three weeks.  Keep those relative release points for each local
# permanent-event cycle instead of treating the old absolute event dates as
# already open forever.
PARTY_CAKE_RELEASE_OFFSETS = {
    1: 0,
    2: 7 * 86400,
    3: 7 * 86400,
    4: 14 * 86400,
    5: 21 * 86400,
}
PARTY_CAKE_PARTS = {
    1: (
        {"layer": 1, "cream": 8, "sugar": 1, "item_id": 200001, "item_num": 4},
        {"layer": 2, "cream": 8, "sugar": 1, "item_id": 57, "item_num": 2},
    ),
    2: (
        {"layer": 1, "cream": 6, "sugar": 4, "item_id": 8001, "item_num": 4},
        {"layer": 2, "cream": 6, "sugar": 4, "item_id": 57, "item_num": 2},
    ),
    3: (
        {"layer": 1, "cream": 5, "sugar": 5, "item_id": 10108, "item_num": 2},
    ),
    4: tuple(
        {"layer": index, "cream": 4, "sugar": 3, "item_id": item_id, "item_num": 2}
        for index, item_id in enumerate((5101, 57, 20001, 57, 11001, 57, 5101, 57), 1)
    ),
    5: (
        {"layer": 1, "cream": 3, "sugar": 2, "item_id": 10108, "item_num": 2},
    ),
}
PARTY_CAKE_TASK_TOTALS = (3, 2, 3, 3, 3, 2)


def party_cake_policy(policy: dict | None = None) -> dict:
    if policy is None:
        policy = load_offline_policy()
    value = policy.get("party_cake", {}) if isinstance(policy, dict) else {}
    return value if isinstance(value, dict) else {}


def _party_cake_default(end_time: int, policy: dict | None = None, start_time: int | None = None) -> dict:
    pc = party_cake_policy(policy)
    if start_time is None:
        start_time = 0
    return {
        "start_time": int(start_time),
        "end_time": int(end_time),
        "cream": 0,
        "sugar": 0,
        # PartyCakeView immediately calls partycake_get_mate when these are
        # present, retaining the official material-receipt presentation.
        "pre_cream": max(0, int(pc.get("starter_cream", 68) or 68)),
        "pre_sugar": max(0, int(pc.get("starter_sugar", 41) or 41)),
        "cur_state": PARTY_CAKE_MAKING,
        "part": 1,
        "layers": [],
        "task_list": [
            {"id": task_id, "count": total, "is_done": 1}
            for task_id, total in enumerate(PARTY_CAKE_TASK_TOTALS, 1)
        ],
        "guest": 0,
        "wrong": 0,
        "answer": [],
        "reward": [],
        # The local event keeps the first share reward only.  The second slot
        # remains claimed so the original two-page share view stays quiet.
        "share_get": [0, 1],
        "last_make_reward": None,
        "qa_correct_index": 0,
    }


def party_cake_payload(state: dict, now: int | None = None, policy: dict | None = None) -> tuple[dict, bool]:
    if now is None:
        now = int(time.time())
    pc = party_cake_policy(policy)
    meta = state.setdefault("offline_meta", {})
    data = meta.get("party_cake")
    changed = False
    start_time, end_time, season_year = seasonal_activity_window("party_cake", now)
    if GALLERY_MODE:
        start_time = int(data.get("start_time", 0) or 0) if isinstance(data, dict) else 0
        start_time = start_time or now
        end_time = always_on_end_time(int(data.get("end_time", 0) or 0), now) if isinstance(data, dict) else now + ALWAYS_ON_HORIZON
    if not bool(pc.get("enabled", True)):
        start_time, end_time, season_year = 0, 0, 0
    if not isinstance(data, dict):
        data = _party_cake_default(end_time, policy, start_time=start_time)
        data["season_year"] = season_year
        meta["party_cake"] = data
        changed = True
    elif season_year and int(data.get("season_year", 0) or 0) != season_year:
        data = _party_cake_default(end_time, policy, start_time=start_time)
        data["season_year"] = season_year
        meta["party_cake"] = data
        changed = True
    for field, value in (("start_time", start_time if season_year or GALLERY_MODE else int(data.get("start_time", 0) or 0)), ("end_time", end_time)):
        if int(data.get(field, 0) or 0) != value:
            data[field] = value
            changed = True
    defaults = _party_cake_default(end_time, policy, start_time=int(data.get("start_time", 0) or 0))
    for key, value in defaults.items():
        if key == "last_make_reward":
            if key not in data or (data[key] is not None and not isinstance(data[key], dict)):
                data[key] = copy.deepcopy(value)
                changed = True
            continue
        if key not in data or not isinstance(data.get(key), type(value)):
            data[key] = copy.deepcopy(value)
            changed = True
    data["cur_state"] = min(max(PARTY_CAKE_MAKING, int(data.get("cur_state", PARTY_CAKE_MAKING) or PARTY_CAKE_MAKING)), PARTY_CAKE_COMPLETE)
    normalized_part = (
        len(PARTY_CAKE_PARTS) + 1
        if data["cur_state"] == PARTY_CAKE_COMPLETE
        else min(max(1, int(data.get("part", 1) or 1)), len(PARTY_CAKE_PARTS))
    )
    if data.get("part") != normalized_part:
        data["part"] = normalized_part
        changed = True
    data["layers"] = [int(layer) for layer in data["layers"] if isinstance(layer, int) or str(layer).isdigit()]
    share_get = [1 if int(value or 0) else 0 for value in data["share_get"][:1]]
    while len(share_get) < 1:
        share_get.append(0)
    share_get.append(1)
    if data["share_get"] != share_get:
        data["share_get"] = share_get
        changed = True
    if GALLERY_MODE:
        for field, value in (("cream", 99), ("sugar", 99), ("pre_cream", 0), ("pre_sugar", 0)):
            if data[field] != value:
                data[field] = value
                changed = True
    return copy.deepcopy(data), changed


def _party_cake_state(state: dict, policy: dict | None = None) -> dict:
    value = state.setdefault("offline_meta", {}).get("party_cake")
    if not isinstance(value, dict):
        party_cake_payload(state, policy=policy)
        value = state["offline_meta"]["party_cake"]
    return value


def _party_cake_layer(part: int, layer: int) -> dict | None:
    return next((row for row in PARTY_CAKE_PARTS.get(int(part), ()) if int(row["layer"]) == int(layer)), None)


def party_cake_part_release_time(target: dict, part: int) -> int:
    if GALLERY_MODE:
        return 0
    return int(target.get("start_time", 0) or 0) + int(PARTY_CAKE_RELEASE_OFFSETS.get(int(part), 0))


def _party_cake_prepare_qa(state: dict, target: dict) -> None:
    target["guest"] = deterministic_roll(state, "partycake.qa.guest", 3)
    target["answer"] = [18, 20, 21]
    target["qa_correct_index"] = deterministic_roll(state, "partycake.qa.answer", 3) + 1
    target["wrong"] = 0
    target["reward"] = []


def party_cake_qa_payload(state: dict, policy: dict | None = None) -> dict:
    party_cake_payload(state, policy=policy)
    target = _party_cake_state(state, policy)
    return {
        "guest": int(target.get("guest", 0) or 0),
        "wrong": int(target.get("wrong", 0) or 0),
        "answer": copy.deepcopy(target.get("answer", [])),
        "reward": copy.deepcopy(target.get("reward", [])),
    }


def party_cake_get_mate(state: dict, policy: dict | None = None) -> tuple[bool, int, int]:
    party_cake_payload(state, policy=policy)
    target = _party_cake_state(state, policy)
    cream = max(0, int(target.get("pre_cream", 0) or 0))
    sugar = max(0, int(target.get("pre_sugar", 0) or 0))
    if cream <= 0 and sugar <= 0:
        return False, 0, 0
    target["cream"] = int(target.get("cream", 0) or 0) + cream
    target["sugar"] = int(target.get("sugar", 0) or 0) + sugar
    target["pre_cream"] = 0
    target["pre_sugar"] = 0
    return True, cream, sugar


def party_cake_make(state: dict, layer: int, policy: dict | None = None, now: int | None = None) -> tuple[bool, int, dict | None]:
    if now is None:
        now = int(time.time())
    party_cake_payload(state, now=now, policy=policy)
    target = _party_cake_state(state, policy)
    if int(target.get("cur_state", -1)) != PARTY_CAKE_MAKING:
        return False, int(target.get("cur_state", -1)), None
    if now < party_cake_part_release_time(target, int(target.get("part", 1) or 1)):
        return False, PARTY_CAKE_MAKING, None
    recipe = _party_cake_layer(int(target["part"]), int(layer))
    if recipe is None or int(layer) in target["layers"]:
        return False, PARTY_CAKE_MAKING, None
    if int(target.get("cream", 0) or 0) < int(recipe["cream"]) or int(target.get("sugar", 0) or 0) < int(recipe["sugar"]):
        return False, PARTY_CAKE_MAKING, None
    if not GALLERY_MODE:
        target["cream"] -= int(recipe["cream"])
        target["sugar"] -= int(recipe["sugar"])
    target["layers"].append(int(layer))
    target["last_make_reward"] = copy.deepcopy(recipe)
    target["cur_state"] = PARTY_CAKE_MAKE_REWARD
    return True, PARTY_CAKE_MAKE_REWARD, copy.deepcopy(recipe)


def party_cake_claim_make_reward(state: dict, policy: dict | None = None) -> tuple[bool, int, dict | None]:
    party_cake_payload(state, policy=policy)
    target = _party_cake_state(state, policy)
    recipe = target.get("last_make_reward")
    if int(target.get("cur_state", -1)) != PARTY_CAKE_MAKE_REWARD or not isinstance(recipe, dict):
        return False, int(target.get("cur_state", -1)), None
    item_id, count = int(recipe["item_id"]), int(recipe["item_num"])
    _, house, _, _ = get_item_parts(state)
    set_house_count(house, item_id, get_house_count(house, item_id) + count)
    expected_layers = {int(row["layer"]) for row in PARTY_CAKE_PARTS[int(target["part"])]}
    if expected_layers.issubset(set(target["layers"])):
        if int(target["part"]) >= len(PARTY_CAKE_PARTS):
            _party_cake_prepare_qa(state, target)
            target["cur_state"] = PARTY_CAKE_QA
        else:
            target["part"] = int(target["part"]) + 1
            target["layers"] = []
            target["cur_state"] = PARTY_CAKE_MAKING
    else:
        target["cur_state"] = PARTY_CAKE_MAKING
    target["last_make_reward"] = None
    return True, int(target["cur_state"]), {"item_id": item_id, "count": count}


def party_cake_answer(state: dict, answer_index: int, policy: dict | None = None) -> tuple[bool, int, dict]:
    party_cake_payload(state, policy=policy)
    target = _party_cake_state(state, policy)
    if int(target.get("cur_state", -1)) != PARTY_CAKE_QA or int(answer_index) not in (1, 2, 3):
        return False, int(target.get("cur_state", -1)), party_cake_qa_payload(state, policy)
    if int(answer_index) == int(target.get("qa_correct_index", 1) or 1):
        target["reward"] = [
            {"item_id": PARTY_CAKE_CREAM_ID, "count": 2},
            {"item_id": PARTY_CAKE_SUGAR_ID, "count": 2},
        ]
        target["cur_state"] = PARTY_CAKE_QA_REWARD
    else:
        target["wrong"] = int(target.get("wrong", 0) or 0) + 1
    return True, int(target["cur_state"]), party_cake_qa_payload(state, policy)


def party_cake_claim_qa_reward(state: dict, policy: dict | None = None) -> tuple[bool, int, list[dict]]:
    party_cake_payload(state, policy=policy)
    target = _party_cake_state(state, policy)
    if int(target.get("cur_state", -1)) != PARTY_CAKE_QA_REWARD:
        return False, int(target.get("cur_state", -1)), []
    rewards = copy.deepcopy(target.get("reward", []))
    _, house, _, _ = get_item_parts(state)
    for reward in rewards:
        item_id, count = int(reward.get("item_id", 0) or 0), int(reward.get("count", 0) or 0)
        if item_id > 0 and count > 0:
            set_house_count(house, item_id, get_house_count(house, item_id) + count)
    target["reward"] = []
    target["cur_state"] = PARTY_CAKE_LIGHT
    return True, PARTY_CAKE_LIGHT, rewards


def party_cake_light(state: dict, policy: dict | None = None) -> tuple[bool, int]:
    party_cake_payload(state, policy=policy)
    target = _party_cake_state(state, policy)
    if int(target.get("cur_state", -1)) != PARTY_CAKE_LIGHT:
        return False, int(target.get("cur_state", -1))
    target["cur_state"] = PARTY_CAKE_LIGHT_REWARD_STATE
    return True, PARTY_CAKE_LIGHT_REWARD_STATE


def party_cake_claim_light_reward(state: dict, policy: dict | None = None) -> tuple[bool, int, dict | None]:
    party_cake_payload(state, policy=policy)
    target = _party_cake_state(state, policy)
    if int(target.get("cur_state", -1)) != PARTY_CAKE_LIGHT_REWARD_STATE:
        return False, int(target.get("cur_state", -1)), None
    item_id, count = PARTY_CAKE_LIGHT_REWARD_ITEM
    achievement_id = permanent_special_title_id(item_id)
    if achievement_id is not None:
        achievement_id = int(special_title_policy(policy).get("party_cake_light", achievement_id) or achievement_id)
        grant_permanent_special_title(state, achievement_id)
    else:
        _, house, _, _ = get_item_parts(state)
        set_house_count(house, item_id, get_house_count(house, item_id) + count)
    target["cur_state"] = PARTY_CAKE_COMPLETE
    target["part"] = len(PARTY_CAKE_PARTS) + 1
    reward = {"item_id": item_id, "count": count}
    if achievement_id is not None:
        reward["achievement_id"] = achievement_id
    return True, PARTY_CAKE_COMPLETE, reward


def party_cake_claim_share_reward(state: dict, share_index: int, policy: dict | None = None) -> tuple[bool, int, int]:
    party_cake_payload(state, policy=policy)
    target = _party_cake_state(state, policy)
    index = int(share_index) - 1
    if index != 0 or int(target["share_get"][index]) == 1:
        return False, 0, 0
    item_id = PARTY_CAKE_SHARE_REWARDS[index]
    _, house, _, _ = get_item_parts(state)
    count_after = get_house_count(house, item_id) + 1
    set_house_count(house, item_id, count_after)
    target["share_get"][index] = 1
    return True, item_id, count_after


# capsuleData_json preserves the original draw board and every reward entry.
CAPSULE_REWARDS = {
    101: (200001, 1), 102: (1, 1), 103: (200000, 10), 104: (8001, 1),
    105: (10001, 1), 106: (10002, 1), 107: (10003, 1),
    201: (2, 1), 202: (1000, 1), 203: (8501, 1), 204: (8502, 1),
    205: (8503, 1), 206: (5101, 1), 207: (20001, 1), 208: (20004, 1),
    301: (1101, 1), 302: (1201, 1), 303: (202109, 1), 304: (11001, 1),
    305: (121, 1), 306: (124, 1), 307: (8002, 1), 308: (1010, 1),
    309: (1015, 1), 310: (20111, 1),
}
CAPSULE_MILESTONE_REWARDS = {3: (200001, 5), 7: (1011, 1), 12: (56, 1)}
CAPSULE_TASK_IDS = tuple(range(1, 9))


def capsule_policy(policy: dict | None = None) -> dict:
    if policy is None:
        policy = load_offline_policy()
    value = policy.get("capsule", {}) if isinstance(policy, dict) else {}
    return value if isinstance(value, dict) else {}


def _capsule_default(end_time: int, policy: dict | None = None) -> dict:
    cp = capsule_policy(policy)
    return {
        "end_time": int(end_time),
        "coin": 0,
        "pre_coin": max(0, int(cp.get("starter_coins", 2) or 2)),
        "reward_list": [],
        "task_list": [1, 2],
        "patch_num": max(0, int(cp.get("remaining_task_count", 14) or 14)),
        "task_cursor": 2,
        "spend_clover": 0,
        "task_day": "",
    }


def _capsule_day_key(now: int) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(int(now)))


def _capsule_allocate_daily_tasks(data: dict) -> list[int]:
    count = min(2, max(0, int(data.get("patch_num", 0) or 0)))
    tasks = []
    for _ in range(count):
        task_id = CAPSULE_TASK_IDS[int(data.get("task_cursor", 0) or 0) % len(CAPSULE_TASK_IDS)]
        data["task_cursor"] = int(data.get("task_cursor", 0) or 0) + 1
        tasks.append(task_id)
    data["task_list"] = tasks
    data["patch_num"] = max(0, int(data.get("patch_num", 0) or 0) - len(tasks))
    return tasks


def capsule_payload(state: dict, now: int | None = None, policy: dict | None = None) -> tuple[dict, bool]:
    if now is None:
        now = int(time.time())
    cp = capsule_policy(policy)
    meta = state.setdefault("offline_meta", {})
    data = meta.get("capsule")
    changed = False
    if not isinstance(data, dict):
        data = _capsule_default(always_on_end_time(0, now), policy)
        meta["capsule"] = data
        changed = True
    end_time = always_on_end_time(int(data.get("end_time", 0) or 0), now) if bool(cp.get("enabled", True)) else 0
    if int(data.get("end_time", 0) or 0) != end_time:
        data["end_time"] = end_time
        changed = True
    defaults = _capsule_default(end_time, policy)
    for key, value in defaults.items():
        if key not in data or not isinstance(data.get(key), type(value)):
            data[key] = copy.deepcopy(value)
            changed = True
    data["reward_list"] = [int(value) for value in data["reward_list"] if int(value) in CAPSULE_REWARDS][:16]
    data["task_list"] = [int(value) for value in data["task_list"] if int(value) in CAPSULE_TASK_IDS][:2]
    data["coin"] = max(0, int(data.get("coin", 0) or 0))
    data["pre_coin"] = max(0, int(data.get("pre_coin", 0) or 0))
    data["patch_num"] = max(0, int(data.get("patch_num", 0) or 0))
    data["task_cursor"] = max(0, int(data.get("task_cursor", 0) or 0))
    task_day = _capsule_day_key(now)
    if not data.get("task_day"):
        data["task_day"] = task_day
        changed = True
    elif data["task_day"] != task_day:
        data["task_day"] = task_day
        data["task_list"] = []
        _capsule_allocate_daily_tasks(data)
        changed = True
    return copy.deepcopy(data), changed


def _capsule_state(state: dict, policy: dict | None = None) -> dict:
    value = state.setdefault("offline_meta", {}).get("capsule")
    if not isinstance(value, dict):
        capsule_payload(state, policy=policy)
        value = state["offline_meta"]["capsule"]
    return value


def capsule_get_coin(state: dict, policy: dict | None = None) -> tuple[bool, int, int]:
    capsule_payload(state, policy=policy)
    target = _capsule_state(state, policy)
    pending = int(target.get("pre_coin", 0) or 0)
    if pending <= 0:
        return False, int(target.get("coin", 0) or 0), 0
    target["coin"] = int(target.get("coin", 0) or 0) + pending
    target["pre_coin"] = 0
    return True, int(target["coin"]), pending


def capsule_twist(state: dict, policy: dict | None = None) -> tuple[bool, int, list[dict]]:
    capsule_payload(state, policy=policy)
    target = _capsule_state(state, policy)
    if int(target.get("coin", 0) or 0) <= 0 or len(target["reward_list"]) >= 16:
        return False, 0, []
    used = {int(value) for value in target["reward_list"]}
    candidates = [reward_id for reward_id in CAPSULE_REWARDS if reward_id not in used] or list(CAPSULE_REWARDS)
    reward_id = candidates[deterministic_roll(state, f"capsule.twist:{len(target['reward_list'])}", len(candidates))]
    target["coin"] -= 1
    target["reward_list"].append(reward_id)
    grants = []
    item_id, count = CAPSULE_REWARDS[reward_id]
    grants.append({"item_id": item_id, "count": count})
    milestone = CAPSULE_MILESTONE_REWARDS.get(len(target["reward_list"]))
    if milestone is not None:
        grants.append({"item_id": milestone[0], "count": milestone[1]})
    _, house, _, _ = get_item_parts(state)
    for grant in grants:
        item_id, count = int(grant["item_id"]), int(grant["count"])
        set_house_count(house, item_id, get_house_count(house, item_id) + count)
    return True, reward_id, grants


def capsule_fast_task(state: dict, task_index: int, policy: dict | None = None) -> tuple[bool, int, list[int]]:
    capsule_payload(state, policy=policy)
    target = _capsule_state(state, policy)
    index = int(task_index) - 1
    if index not in range(len(target["task_list"])):
        return False, int(target.get("pre_coin", 0) or 0), copy.deepcopy(target["task_list"])
    price = max(0, int(capsule_policy(policy).get("fast_consume", 30) or 30))
    res, _ = get_clover_parts(state)
    clover = int(res.get("clover_point", 0) or 0)
    if clover < price:
        return False, int(target.get("pre_coin", 0) or 0), copy.deepcopy(target["task_list"])
    res["clover_point"] = clover - price
    target["task_list"].pop(index)
    target["pre_coin"] = int(target.get("pre_coin", 0) or 0) + 1
    return True, int(target["pre_coin"]), copy.deepcopy(target["task_list"])


def capsule_complete_task(state: dict, task_id: int, policy: dict | None = None) -> tuple[bool, int, list[int]]:
    """Complete one active capsule task from its matching in-game action."""
    capsule_payload(state, policy=policy)
    target = _capsule_state(state, policy)
    task_id = int(task_id)
    if task_id not in target["task_list"]:
        return False, int(target.get("pre_coin", 0) or 0), copy.deepcopy(target["task_list"])
    target["task_list"].remove(task_id)
    target["pre_coin"] = int(target.get("pre_coin", 0) or 0) + 1
    return True, int(target["pre_coin"]), copy.deepcopy(target["task_list"])


def capsule_record_clover_spend(
    state: dict,
    amount: int,
    policy: dict | None = None,
) -> tuple[bool, bool, int, list[int]]:
    """Track the active capsule task that asks the player to spend 20 clover."""
    capsule_payload(state, policy=policy)
    target = _capsule_state(state, policy)
    amount = max(0, int(amount))
    if amount <= 0 or 1 not in target["task_list"]:
        return False, False, int(target.get("pre_coin", 0) or 0), copy.deepcopy(target["task_list"])
    target["spend_clover"] = min(20, max(0, int(target.get("spend_clover", 0) or 0)) + amount)
    if int(target["spend_clover"]) < 20:
        return True, False, int(target.get("pre_coin", 0) or 0), copy.deepcopy(target["task_list"])
    completed, pending, tasks = capsule_complete_task(state, 1, policy=policy)
    return True, completed, pending, tasks


def capsule_food_is_prepared(state: dict) -> bool:
    """The second capsule task accepts food in the travel bag or on the desk."""
    _, _, bag, desk = get_item_parts(state)
    return any(item_type(item_id) == ITEM_LUNCHBOX for item_id in (*bag, *desk) if isinstance(item_id, int) and item_id >= 0)


def capsule_patch_tasks(state: dict, policy: dict | None = None) -> tuple[list[int], int]:
    capsule_payload(state, policy=policy)
    target = _capsule_state(state, policy)
    if target["task_list"] or int(target.get("patch_num", 0) or 0) <= 0:
        return copy.deepcopy(target["task_list"]), int(target.get("patch_num", 0) or 0)
    return [], int(target["patch_num"])


def static_rows(filename: str) -> list[dict]:
    obj = load_static_json(filename)
    return [x for x in obj if isinstance(x, dict)] if isinstance(obj, list) else []


def item_cfg(item_id: int) -> dict | None:
    iid=int(item_id)
    return next((x for x in static_rows("Item_json.json") if int(x.get("id",-1))==iid), None)


def item_type(item_id: int) -> int:
    row=item_cfg(item_id)
    return int(row.get("type",-1)) if row else -1


def item_spend(item_id: int) -> bool:
    row=item_cfg(item_id)
    return bool(int(row.get("spend",0) or 0)) if row else False


def goal_rows() -> list[dict]:
    return [x for x in static_rows("GoalNumber_json.json") if 1 <= int(x.get("id",0)) <= 35]


def picture_rows() -> list[dict]:
    return static_rows("Picture_json.json")


def picture_by_id(pic_id: int) -> dict | None:
    pid=int(pic_id)
    return next((x for x in picture_rows() if int(x.get("id",-1))==pid), None)


def picture_by_name(name: str) -> dict | None:
    return next((x for x in picture_rows() if str(x.get("name",""))==str(name)), None)


def owned_picture_ids(state: dict) -> set[int]:
    out={int(x.get("pic_id",-1)) for x in get_album_index(state) if int(x.get("pic_id",-1))>=0}
    try:
        data=get_new_album_data(state)
        for key in ("pictures","visted_pic"):
            for x in _to_list(data.get(key)):
                if isinstance(x,dict) and int(x.get("pic_id",-1))>=0:
                    out.add(int(x["pic_id"]))
    except Exception:
        pass
    return out


def visited_places(state: dict) -> set[int]:
    meta=state.setdefault("offline_meta",{})
    out={int(x) for x in _to_list(meta.get("visited_places"))}
    for pid in owned_picture_ids(state):
        row=picture_by_id(pid)
        if row is not None and str(row.get("type"))=="Goal":
            place=_picture_route_place(row)
            if 1<=place<=35: out.add(place)
    return out


def weighted_int_choice(state: dict, label: str, weights: dict[int,int]) -> int:
    rows=[(int(k),max(0,int(v))) for k,v in weights.items()]
    total=sum(v for _,v in rows)
    if total<=0: return rows[0][0]
    r=deterministic_roll(state,label,total)
    acc=0
    for k,w in rows:
        acc+=w
        if r<acc:return k
    return rows[-1][0]


def handcraft_travel_effect(amulet_id: int, policy: dict | None = None) -> dict:
    effects = handcraft_policy(policy).get("travel_effects", {})
    if not isinstance(effects, dict):
        return {}
    value = effects.get(str(amulet_id), effects.get(amulet_id, {}))
    return copy.deepcopy(value) if isinstance(value, dict) else {}


def travel_policy_with_handcraft_effect(policy: dict, amulet_id: int) -> tuple[dict, dict]:
    effective = copy.deepcopy(policy)
    effect = handcraft_travel_effect(amulet_id, effective)
    travel = effective.setdefault("travel", {})
    for policy_key, effect_key in (
        ("collection_percent", "collection_percent_bonus"),
        ("ticket_percent", "ticket_percent_bonus"),
        ("specialty_percent", "specialty_percent_bonus"),
        ("base_picture_percent", "base_picture_percent_bonus"),
        ("unique_picture_percent", "unique_picture_percent_bonus"),
        ("tool_picture_percent", "tool_picture_percent_bonus"),
        ("normal_picture_percent", "normal_picture_percent_bonus"),
    ):
        bonus = int(effect.get(effect_key, 0) or 0)
        if bonus:
            travel[policy_key] = max(0, min(100, int(travel.get(policy_key, 0) or 0) + bonus))
    return effective, effect


def choose_destination(state: dict, bag_snapshot: list[int], policy: dict) -> tuple[int,dict]:
    food=next((x for x in bag_snapshot if x>=0 and item_type(x)==ITEM_LUNCHBOX),-1)
    amulet=next((x for x in bag_snapshot if x>=0 and item_type(x)==ITEM_AMULET),-1)
    tp=policy.get("travel",{})

    # 路标生效时会改走三峡或黄河路线，并覆盖普通目的地限制。
    road_map=tp.get("road_sign_route_map",{})
    road_targets=road_map.get(str(amulet),road_map.get(amulet,[])) if isinstance(road_map,dict) else []
    road_percent=tp.get("road_sign_route_percent",{})
    road_pct=max(0,min(100,int(road_percent.get(str(amulet),road_percent.get(amulet,0)) or 0))) if isinstance(road_percent,dict) else 0
    candidates=[int(x) for x in _to_list(road_targets) if int(x) in {105,106}]
    if candidates and deterministic_roll(state,f"road_sign_route:{amulet}",100)<road_pct:
        place=candidates[deterministic_roll(state,f"road_sign_pick:{amulet}",len(candidates))]
        return place,{"reason":"road_sign","amulet":amulet}

    # Specific museum tickets are the strongest known route constraint.
    if amulet in MUSEUM_TICKET_TO_ID:
        mid=MUSEUM_TICKET_TO_ID[amulet]
        return MUSEUM_PLACE_BY_ID[mid], {"reason":"museum_ticket","museum_id":mid}
    if amulet==1104: # 博物馆通票: choose an active museum, prioritizing incomplete ones.
        museum_cfg=load_static_json("museumData_json.json")
        progress={x["id"]:x for x in sync_museum_progress(state)}
        candidates=[]
        for k,cfg in museum_cfg.items():
            if int(cfg.get("switch",0) or 0)!=1: continue
            mid=int(cfg.get("id",k)); score=1
            row=progress.get(mid,{})
            totalp=len(_to_list(cfg.get("pic_id")))
            totalc=len([x for x in str(cfg.get("collection_id","")).split(",") if x])
            if len(_to_list(row.get("pic_list")))<totalp: score+=3
            if len(_to_list(row.get("collections")))<totalc: score+=2
            candidates.extend([mid]*score)
        if candidates:
            mid=candidates[deterministic_roll(state,"museum_pass",len(candidates))]
            return MUSEUM_PLACE_BY_ID[mid], {"reason":"museum_pass","museum_id":mid}

    all_places=[int(x["id"]) for x in goal_rows()]
    weights={p:1 for p in all_places}
    visited=visited_places(state)
    unseen=set(all_places)-visited

    # Destination-directed merchant food: only mappings with strong local
    # identification are included in policy; all other foods remain generic.
    target_map=tp.get("target_food_place_map",{})
    target=target_map.get(str(food),target_map.get(food)) if isinstance(target_map,dict) else None
    if isinstance(target, list):
        candidates=[int(x) for x in target if int(x) in weights]
        target=candidates[deterministic_roll(state,f"target_food_place:{food}",len(candidates))] if candidates else None
    if target is not None and int(target) in weights:
        weights[int(target)]+=20

    affinity=FOOD_AFFINITY.get(food)
    if affinity:
        b=max(0,int(tp.get("food_affinity_weight",5)))
        for p in affinity:
            if p in weights: weights[p]+=b

    direction=AMULET_DIRECTION.get(amulet)
    if direction:
        b=max(0,int(tp.get("direction_bias_weight",5)))
        for p in DIRECTION_PLACES[direction]:
            if p in weights: weights[p]+=b

    # 心意风铃 keeps its southward bias and additionally favors its two
    # documented destinations, Jiangxi and Fujian.
    amulet_places=tp.get("amulet_place_bias_map",{})
    amulet_place_weights=tp.get("amulet_place_bias_weight",{})
    targets=amulet_places.get(str(amulet),amulet_places.get(amulet,[])) if isinstance(amulet_places,dict) else []
    bonus=max(0,int(amulet_place_weights.get(str(amulet),amulet_place_weights.get(amulet,0)) or 0)) if isinstance(amulet_place_weights,dict) else 0
    for p in _to_list(targets):
        if int(p) in weights: weights[int(p)]+=bonus

    if amulet in UNSEEN_AMULETS and unseen:
        b=max(0,int(tp.get("unseen_bias_weight",4)))
        for p in unseen: weights[p]+=b

    place=weighted_int_choice(state,f"travel_destination:{food}:{amulet}",weights)
    return place,{"reason":"weighted","food":food,"amulet":amulet,"direction":direction}


def tool_tier(item_id: int) -> int:
    iid=int(item_id)
    for lo in (2000,2003,2006,2009):
        if lo<=iid<=lo+2: return iid-lo+1
    return 0


def tool_picture_name(item_id: int) -> str | None:
    iid=int(item_id); tier=tool_tier(iid)
    if tier<=0:return None
    if 2000<=iid<=2002: return f"dry{tier}"
    if 2003<=iid<=2005: return f"wet{tier}"
    if 2006<=iid<=2008: return f"fuza{tier}"
    if 2009<=iid<=2011: return f"Town{tier}"
    return None


def tool_picture_rows_for_trip(state: dict, place: int, tools: list[int], travel_policy: dict) -> list[dict]:
    """Choose the official Tools-card pose for this trip's environment.

    Tool PictureTag groups encode pose 0 for the matching environment without
    its tool and poses 1..3 for the three carried-tool tiers.  The four town
    scenes share the scarf tier and are selected as authored scene variants.
    """
    environment_map=travel_policy.get("tool_picture_environment_by_place", {})
    raw=environment_map.get(str(place),environment_map.get(place,[])) if isinstance(environment_map,dict) else []
    environments=[str(x) for x in _to_list(raw) if str(x) in {"dry","wet","fuza","town"}]
    if not environments:
        return []
    environment=environments[deterministic_roll(state,f"tool_photo_environment:{place}",len(environments))]
    ranges={"dry":(2000,2002),"wet":(2003,2005),"fuza":(2006,2008),"town":(2009,2011)}
    lo,hi=ranges[environment]
    matching=[int(item) for item in tools if lo<=int(item)<=hi]
    tier=tool_tier(matching[deterministic_roll(state,f"tool_photo_tier:{place}:{environment}",len(matching))]) if matching else 0
    if environment=="town":
        names=[f"Town{tier}",f"Town1_{tier}",f"Town2_{tier}",f"Town3_{tier}"] if tier else ["Town","Town1_0","Town2_0","Town3_0"]
    else:
        names=[environment if tier==0 else f"{environment}{tier}"]
    return [row for name in names if (row:=picture_by_name(name)) is not None]


def resource_indexes() -> tuple[dict[str,int],dict[tuple[str,str],int]]:
    obj=load_static_json("resources_json.json")
    by_path={}; by_cat_base={}
    if isinstance(obj,dict):
        for k,v in obj.items():
            try: rid=int(k)
            except Exception: continue
            path=str(v); by_path[path]=rid
            parts=path.split('/')
            if len(parts)>=3 and parts[0]=='Picture':
                by_cat_base[(parts[1],parts[-1])]=rid
    return by_path,by_cat_base


def _resource_id(category: str, basename: str) -> int | None:
    _,idx=resource_indexes()
    rid = idx.get((category, basename))
    if rid is not None:
        return rid
    category_key = str(category).casefold()
    basename_key = str(basename).casefold()
    for (cat, base), value in idx.items():
        if str(cat).casefold() == category_key and str(base).casefold() == basename_key:
            return value
    return None


def _picture_resource_id(category: str, basename: str) -> int | None:
    rid = _resource_id(category, basename)
    if rid is not None or category != "Normal":
        return rid
    if str(basename).startswith("back_n_"):
        rid = _resource_id(category, str(basename)[5:])
        if rid is not None:
            return rid
    for part in ("mid", "front"):
        marker = f"mumianhua_{part}_"
        if str(basename).startswith(marker):
            alias = f"mumianhua_xyn_{part}{str(basename)[len(marker):]}"
            return _resource_id(category, alias)
    return None


def _picture_variant_bank() -> dict:
    obj = load_static_json("picture_official_variants.json")
    if isinstance(obj, dict):
        rows = obj.get("pic_ids", {})
        return rows if isinstance(rows, dict) else {}
    return {}


def _picture_dim_data() -> dict:
    obj = load_static_json("picture_dimensions_json.json")
    return obj if isinstance(obj, dict) else {}


def _picture_position_data() -> dict:
    obj = load_static_json("picture_resource_positions_json.json")
    if isinstance(obj, dict):
        rows = obj.get("positions", {})
        return rows if isinstance(rows, dict) else {}
    return {}


def _resource_xy_from_official(
    state: dict | None,
    rid: int,
    label: str,
) -> tuple[float, float] | None:
    row = _picture_position_data().get(str(int(rid)))
    if isinstance(row, dict):
        vals = row.get("xy")
        if isinstance(vals, list) and vals:
            if len(vals) == 1 or state is None:
                chosen = vals[0]
            else:
                chosen = vals[deterministic_roll(state, f"picture_xy:{label}:{rid}", len(vals))]
            if isinstance(chosen, list) and len(chosen) >= 2:
                return float(chosen[0]), float(chosen[1])

    # A 500x350 PNG already occupies the postcard canvas exactly. Its origin is
    # authored by the asset dimensions rather than an inferred component offset.
    if _resource_dimensions(rid, allow_estimated=False) == (500.0, 350.0):
        return 0.0, 0.0
    return None


def _resource_dimensions(rid: int, role: str | None = None, *, allow_estimated: bool = True) -> tuple[float, float] | None:
    obj = _picture_dim_data()
    dims = obj.get("dimensions", {}) if isinstance(obj, dict) else {}
    row = dims.get(str(int(rid))) if isinstance(dims, dict) else None
    if isinstance(row, dict):
        try:
            return float(row["w"]), float(row["h"])
        except Exception:
            pass
    if allow_estimated and role:
        fallback = obj.get("fallback_role_median", {}) if isinstance(obj, dict) else {}
        val = fallback.get(str(role)) if isinstance(fallback, dict) else None
        if isinstance(val, list) and len(val) >= 2:
            return float(val[0]), float(val[1])
    return None


def _picture_role_from_resource(rid: int) -> str | None:
    path = str(load_static_json("resources_json.json").get(str(int(rid)), ""))
    base = path.split("/")[-1].lower()
    if base.endswith("_bh"):
        return "bh"
    if base.endswith("_cw"):
        return "cw"
    if base.endswith("_yhc"):
        return "yhc"
    if base.endswith("qw"):
        return "qw"
    return None


def _character_layer_xy(
    rid: int,
    pos: dict,
    role: str,
    *,
    allow_estimated: bool,
) -> tuple[float, float] | None:
    wh = _resource_dimensions(rid, role, allow_estimated=allow_estimated)
    if wh is None:
        return None
    w, h = wh
    # Recovered from official full-layer samples and byte-verified PNG sizes:
    # Picture config uses a centre-origin coordinate system with +Y upward,
    # while Egret layers use top-left pixels with +Y downward.
    x = 250.0 + float(pos.get("x", 0) or 0) - w / 2.0
    y = 175.0 - float(pos.get("y", 0) or 0) - h / 2.0
    return x, y


def _resolve_random_resource_name(
    state: dict | None,
    category: str,
    raw_name: str,
    label: str,
    *,
    selection: int | None = None,
) -> str:
    """Resolve rnd_* aliases only from preserved resource names.

    Exact production weights are not available. Deterministic equal selection is
    therefore used only as a fallback for uncaptured Normal templates.
    """
    name = str(raw_name)
    if not name.startswith("rnd_"):
        return name
    stem = name[4:].lower()
    _, idx = resource_indexes()
    candidates = []
    for (cat, base), _rid in idx.items():
        if str(cat).lower() != str(category).lower():
            continue
        b = str(base)
        bl = b.lower()
        if bl.startswith(stem):
            candidates.append(b)
    candidates = sorted(set(candidates))
    if not candidates:
        return name
    if selection is not None:
        return candidates[int(selection) % len(candidates)]
    if state is None or len(candidates) == 1:
        return candidates[0]
    return candidates[deterministic_roll(state, f"picture_rnd:{label}:{name}", len(candidates))]


_GOAL_CHARACTER_PREFIXES = {
    "beijing": "BJ",
    "chengdu": "CD",
    "chongqing": "CQ",
    "guangzhou": "GZ",
    "guilin": "GL",
    "hangzhou": "HZ",
    "suzhou": "SZ",
    "tianjin": "TJ",
    "tw_shifen1": "TWSF",
    "tw_kending1": "TWKD",
    "tw_jingan": "TWJA",
    "tw_lanyu": "TWLY",
    "tw_tiandeng": "TWTD",
    "tw_zuinandian": "TWZND",
    "xianggang": "XG",
    "haerbin": "HEB",
    "zhangjiajie": "ZJJ",
    "wuhan": "WH",
    "luoyang": "LY",
    "xian": "XA",
    "jiuquan": "JQ",
    "yunnan": "YN",
    "lasa": "LS",
    "shanghai": "SH",
    "hainan": "HN",
    "fujian": "FJ",
    "jiangxi": "JX",
    "guizhou": "GUIZ",
    "ningxia": "NX",
    "anhui": "AH",
    "jiangmen": "JM",
    "liaoning": "LN",
    "qingdao": "QD",
    "bwg_jiangxi": "BWG_JX",
    "bwg_shandong": "BWG_SD",
    "bwg_nanyuewang": "BWG_NYW",
    "bwg_wuwenhua": "BWG_WWH",
    "bwg_shanxi": "BWG_SX",
}


# Guide-confirmed local scene replacements for Goal rows whose original
# resource names are absent from the preserved client bundle.  The source
# picture supplies the complete clean postcard layers; the route place is
# explicit so these rows participate in travel generation and album mapping.
_GUIDE_CONFIRMED_SCENE_REPLACEMENTS = {
    2033: {"place": 11, "source_picture_id": 2032, "resource": "g_xianggang2"},
    2035: {"place": 11, "source_picture_id": 2031, "resource": "g_xianggang1"},
    2040: {"place": 30, "source_picture_id": 2126, "resource": "g_jilin1"},
    2041: {"place": 30, "source_picture_id": 2127, "resource": "g_jilin2"},
    2042: {"place": 30, "source_picture_id": 2128, "resource": "g_jilin3"},
    2075: {"place": 21, "source_picture_id": 2074, "resource": "g_hainan2"},
    2076: {"place": 21, "source_picture_id": 2073, "resource": "g_hainan1"},
}


# These Goal rows are present in the current official Picture table but lost
# their numeric `place` field.  Their picture names still match GoalNumber
# exactly, so route them to that corresponding official destination.
_GOAL_ROUTE_PLACE_OVERRIDES = {
    2043: 13,  # zhangjiajie1
    2058: 17,  # jiuquan2
    2062: 18,  # yunnan3
    2063: 18,  # yunnan4
    2067: 19,  # lasa4
    2070: 20,  # shanghai2
    2081: 22,  # guizhou1
    2082: 22,  # guizhou2
    2083: 22,  # guizhou3
    2084: 23,  # ningxia1
    2085: 23,  # ningxia2
    2086: 23,  # ningxia3
}


def _picture_route_place(row: dict) -> int:
    place=int(row.get("place",0) or 0)
    if 1<=place<=35:
        return place
    override=_GOAL_ROUTE_PLACE_OVERRIDES.get(int(row.get("id",-1)))
    if override is not None:
        return override
    replacement=_GUIDE_CONFIRMED_SCENE_REPLACEMENTS.get(int(row.get("id",-1)))
    return int(replacement["place"]) if replacement is not None else 0


_TRAVELER_ROLE_ORDER = ("bh", "cw", "yhc")


def _picture_pose_stems(values: list[str]) -> list[str]:
    stems: list[str] = []
    for raw in values:
        pose = str(raw or "")
        if not pose:
            continue
        if pose.startswith("rnd_pose_"):
            pose = pose[len("rnd_pose_"):]
        elif pose.startswith("pose_"):
            pose = pose[len("pose_"):]
        if pose and pose.casefold() not in {x.casefold() for x in stems}:
            stems.append(pose)
    return stems


def _frog_resource_candidates(
    pic_cfg: dict,
    poses: list[str] | None = None,
    *,
    include_random_alternates: bool = False,
) -> list[str]:
    """Return asset basenames for the static picture frog definition."""
    typ = str(pic_cfg.get("type", "Goal"))
    if poses is None:
        poses = [str(pic_cfg.get("frogPose_s") or ""), str(pic_cfg.get("frogPose") or "")]
    stems = _picture_pose_stems(poses)

    candidates = []

    def add(name: str) -> None:
        if name and name.casefold() not in {x.casefold() for x in candidates}:
            candidates.append(name)

    for stem in stems:
        if typ == "Tools":
            add(stem)
            add(stem + "_qw")
        elif typ == "Normal":
            add(stem + "_pose_qw")
            if stem.endswith(("_h", "_z")):
                add(stem.rsplit("_", 1)[0] + "_pose_qw")
            add(stem + "_qw")
            add(stem + "qw")
            if include_random_alternates:
                add(stem + "_2qw")
            add(stem)
        elif typ == "Goal":
            add(stem + "_qw")
            add(stem + "_pose_qw")
            add(stem)
        elif typ == "Unique":
            add(stem + "_qw")
            add(stem)

    if typ == "Goal":
        name = str(pic_cfg.get("name", ""))
        add("g_" + name + "_qw")
        for key, prefix in _GOAL_CHARACTER_PREFIXES.items():
            if name.startswith(key):
                add(prefix + name[len(key):] + "_QW")

    return candidates


def _traveler_resource_candidates(pic_cfg: dict, traveler_index: int) -> list[str]:
    """Return known asset basenames for one configured travel companion role."""
    if not 0 <= traveler_index < len(_TRAVELER_ROLE_ORDER):
        return []
    typ = str(pic_cfg.get("type", "Goal"))
    poses = _to_list(pic_cfg.get("travelerPose"))
    raw_pose = str(poses[traveler_index] or "") if traveler_index < len(poses) else ""
    stems = _picture_pose_stems([raw_pose])
    role = _TRAVELER_ROLE_ORDER[traveler_index]
    candidates: list[str] = []

    def add(name: str) -> None:
        if name and name.casefold() not in {x.casefold() for x in candidates}:
            candidates.append(name)

    for stem in stems:
        add(stem + "_" + role)
        add(stem + "_pose_" + role)

    if typ == "Goal":
        name = str(pic_cfg.get("name", ""))
        add("g_" + name + "_" + role)
        for key, prefix in _GOAL_CHARACTER_PREFIXES.items():
            if name.startswith(key):
                add(prefix + name[len(key):] + "_" + role)
    return candidates


def _character_layer_from_candidates(
    category: str,
    candidates: list[str],
    pos: dict,
    role: str,
    state: dict | None,
    label: str,
    *,
    allow_estimated: bool,
    choose_random: bool = False,
    selection: int | None = None,
) -> dict | None:
    resolved: list[int] = []
    for base in candidates:
        name = _resolve_random_resource_name(
            state, category, base, label, selection=selection
        )
        rid = _picture_resource_id(category, name)
        if rid is not None and rid not in resolved:
            resolved.append(rid)
    if not resolved:
        return None
    exact = [
        rid
        for rid in resolved
        if _resource_dimensions(rid, role, allow_estimated=False) is not None
    ]
    if exact:
        resolved = exact
    if choose_random and selection is not None and len(resolved) > 1:
        rid = resolved[int(selection) % len(resolved)]
    elif choose_random and state is not None and len(resolved) > 1:
        rid = resolved[deterministic_roll(state, f"{label}:resource", len(resolved))]
    else:
        rid = resolved[0]
    xy = _resource_xy_from_official(state, rid, label)
    if xy is None:
        xy = _character_layer_xy(rid, pos, role, allow_estimated=allow_estimated)
    if xy is None:
        return None
    return {"layer": [rid, xy[0], xy[1]]}


def _traveler_indices_with_assets(pic_cfg: dict) -> list[int]:
    category = str(pic_cfg.get("type", "Goal"))
    out: list[int] = []
    for index in range(len(_TRAVELER_ROLE_ORDER)):
        if any(_picture_resource_id(category, base) is not None for base in _traveler_resource_candidates(pic_cfg, index)):
            out.append(index)
    return out


def _unique_uses_component_characters(pic_cfg: dict) -> bool:
    """Whether a Unique card's authored characters are separate layers."""
    if str(pic_cfg.get("type", "")) != "Unique":
        return False
    poses=[str(pic_cfg.get("frogPose_s") or ""), str(pic_cfg.get("frogPose") or "")]
    for base in _frog_resource_candidates(pic_cfg, poses):
        rid=_picture_resource_id("Unique",base)
        if rid is not None and _resource_dimensions(rid,allow_estimated=False)!=(500.0,350.0):
            return True
    for index in range(len(_TRAVELER_ROLE_ORDER)):
        for base in _traveler_resource_candidates(pic_cfg,index):
            rid=_picture_resource_id("Unique",base)
            if rid is not None and _resource_dimensions(rid,allow_estimated=False)!=(500.0,350.0):
                return True
    return False


def _configured_character_layers(
    pic_cfg: dict,
    state: dict | None,
    label: str,
    *,
    allow_estimated: bool,
    random_set: int | None = None,
) -> list[dict]:
    """Compose frog and optional travel companion from Picture_json fields."""
    category = str(pic_cfg.get("type", "Goal"))
    if category == "Unique" and not _unique_uses_component_characters(pic_cfg):
        return []

    traveler_index = None
    available = _traveler_indices_with_assets(pic_cfg)
    if available and state is not None:
        roll = deterministic_roll(state, f"picture_cast:{label}:{pic_cfg.get('id', -1)}", len(available) + 1)
        if roll:
            traveler_index = available[roll - 1]

    with_traveler = traveler_index is not None
    frog_pose = pic_cfg.get("frogPose") if with_traveler else pic_cfg.get("frogPose_s")
    frog_pos = pic_cfg.get("frogPos") if with_traveler else pic_cfg.get("frogPos_s")
    frog_layer = _character_layer_from_candidates(
        category,
        _frog_resource_candidates(
            pic_cfg,
            [str(frog_pose or pic_cfg.get("frogPose") or "")],
            include_random_alternates=bool(pic_cfg.get("randomSet")),
        ),
        frog_pos if isinstance(frog_pos, dict) else {"x": 0, "y": 0},
        "qw",
        state,
        f"{label}:frog",
        allow_estimated=allow_estimated,
        choose_random=bool(pic_cfg.get("randomSet")),
        selection=random_set,
    )

    traveler_layer = None
    if traveler_index is not None:
        positions = _to_list(pic_cfg.get("travelerPos"))
        traveler_pos = positions[traveler_index] if traveler_index < len(positions) else {"x": 0, "y": 0}
        traveler_layer = _character_layer_from_candidates(
            category,
            _traveler_resource_candidates(pic_cfg, traveler_index),
            traveler_pos if isinstance(traveler_pos, dict) else {"x": 0, "y": 0},
            _TRAVELER_ROLE_ORDER[traveler_index],
            state,
            f"{label}:traveler:{_TRAVELER_ROLE_ORDER[traveler_index]}",
            allow_estimated=allow_estimated,
        )

    layers: list[dict] = []
    if bool(pic_cfg.get("priority")):
        if traveler_layer is not None:
            layers.append(traveler_layer)
        if frog_layer is not None:
            layers.append(frog_layer)
    else:
        if frog_layer is not None:
            layers.append(frog_layer)
        if traveler_layer is not None:
            layers.append(traveler_layer)
    return layers


def _official_static_asset_layers(
    pic_cfg: dict,
    state: dict | None,
    label: str,
) -> list[dict] | None:
    """Return layers for a postcard backed by the downloaded official PNGs.

    Goal backgrounds are already 500x350 postcard canvases.  When their
    matching pose PNG and exact dimensions are available, the client can use
    those existing assets directly; no estimated character geometry is needed.
    """
    typ = str(pic_cfg.get("type", "Goal"))
    if typ not in {"Goal", "Unique"}:
        return None
    if _to_list(pic_cfg.get("frontImage")):
        return None

    background_rid = None
    for raw in _to_list(pic_cfg.get("backImage")):
        name = str(raw)
        if typ in {"Goal", "Unique"} and name.startswith("back_"):
            name = name[5:]
        name = _resolve_random_resource_name(state, typ, name, f"{label}:official_back")
        rid = _picture_resource_id(typ, name)
        if rid is None:
            continue
        dims = _resource_dimensions(rid, allow_estimated=False)
        if dims is not None and dims == (500.0, 350.0):
            background_rid = rid
            break
    if background_rid is None:
        return None

    layers = [{"layer": [background_rid, 0.0, 0.0]}]
    if typ == "Unique":
        if _unique_uses_component_characters(pic_cfg):
            layers.extend(_configured_character_layers(pic_cfg,state,label,allow_estimated=False))
        layers.append({"layer": [0, 0, 0]})
        return layers
    layers.extend(
        _configured_character_layers(
            pic_cfg, state, label, allow_estimated=False
        )
    )
    layers.append({"layer": [0, 0, 0]})
    return layers


def _guide_confirmed_scene_layers(
    pic_cfg: dict,
    state: dict | None,
    label: str,
) -> list[dict] | None:
    replacement=_GUIDE_CONFIRMED_SCENE_REPLACEMENTS.get(int(pic_cfg.get("id",-1)))
    if replacement is None:
        return None

    source_cfg=picture_by_id(int(replacement["source_picture_id"]))
    if source_cfg is not None:
        layers=_official_static_asset_layers(
            source_cfg, state, f"{label}:guide_source:{replacement['source_picture_id']}"
        )
        if layers is not None:
            return layers

    # The replacement is still a direct clean postcard resource if the source
    # row has no separately layered variant in a particular resource index.
    rid=_picture_resource_id("Goal", str(replacement["resource"]))
    if rid is None:
        return None
    return [{"layer": [rid, 0.0, 0.0]}, {"layer": [0, 0, 0]}]


def build_picture_layers(
    pic_cfg: dict,
    state: dict | None = None,
    label: str = "picture",
) -> list[dict]:
    """Build a postcard using a four-tier evidence strategy.

    1. Exact official full-layer templates rescued from production.
    2. Downloaded official static PNG bundles with exact dimensions.
    3. Static Picture/resources data plus recovered PNG dimensions/coordinates.
    4. Explicit estimated character-size fallback for still-unseen resources.

    The Stage 6G Qinghai-only x=238+pos.x / y=249.5+pos.y rule is removed.
    """
    pid = int(pic_cfg.get("id", -1))
    guide_layers=_guide_confirmed_scene_layers(pic_cfg, state, label)
    if guide_layers is not None:
        return guide_layers

    pg = load_offline_policy().get("picture_generation", {})
    unique_components=_unique_uses_component_characters(pic_cfg)
    variants = _picture_variant_bank().get(str(pid), [])
    if not unique_components and bool(pg.get("prefer_official_templates", True)) and isinstance(variants, list) and variants:
        if state is None or len(variants) == 1:
            row = variants[0]
        else:
            row = variants[deterministic_roll(state, f"picture_variant:{label}:{pid}", len(variants))]
        if isinstance(row, dict) and isinstance(row.get("layers"), list):
            return copy.deepcopy(row["layers"])

    official_layers = _official_static_asset_layers(pic_cfg, state, label)
    if official_layers is not None:
        return official_layers

    typ = str(pic_cfg.get("type", "Goal"))
    category = typ
    resource_category = category
    layers: list[dict] = []
    random_set = None
    if bool(pic_cfg.get("randomSet")):
        random_set = deterministic_roll(state, f"picture_random_set:{label}:{pid}", 2) if state is not None else 0

    # Unique postcards are overwhelmingly baked 500x350 images. For uncaptured
    # Unique rows, use the preserved back/front layers only and do not invent
    # separate character placement.
    for raw in _to_list(pic_cfg.get("backImage")):
        name = str(raw)
        if typ in {"Goal", "Unique"} and name.startswith("back_"):
            name = name[5:]
        name = _resolve_random_resource_name(
            state, category, name, f"{label}:back", selection=random_set
        )
        rid = _picture_resource_id(category, name)
        if rid is None and typ == "Unique":
            # A small set of dynamic Unique postcards intentionally reuses the
            # Normal scene assets; baked Unique postcards stay on their own
            # resource category.
            rid = _picture_resource_id("Normal", name)
            if rid is not None:
                resource_category = "Normal"
        if rid is None:
            continue
        xy = _resource_xy_from_official(state, rid, f"{label}:back")
        x, y = xy if xy is not None else (0.0, 0.0)
        layers.append({"layer": [rid, x, y]})

    if typ != "Unique" or resource_category == "Normal":
        character_cfg = pic_cfg
        if resource_category != category:
            character_cfg = dict(pic_cfg)
            character_cfg["type"] = resource_category
        layers.extend(
            _configured_character_layers(
                character_cfg,
                state,
                label,
                allow_estimated=bool(pg.get("allow_estimated_character_size", True)),
                random_set=random_set,
            )
        )

    for raw in _to_list(pic_cfg.get("frontImage")):
        name = _resolve_random_resource_name(
            state, category, str(raw), f"{label}:front", selection=random_set
        )
        rid = _picture_resource_id(resource_category, name)
        if rid is None:
            continue
        xy = _resource_xy_from_official(state, rid, f"{label}:front")
        x, y = xy if xy is not None else (0.0, 0.0)
        layers.append({"layer": [rid, x, y]})

    layers.append({"layer": [0, 0, 0]})
    return layers


def next_picture_instance_id(state: dict) -> int:
    vals=[]
    for x in get_album_index(state):
        if isinstance(x,dict): vals.append(int(x.get("id",0) or 0))
    try:
        for key in ("pictures","visted_pic"):
            for x in _to_list(get_new_album_data(state).get(key)):
                if isinstance(x,dict): vals.append(int(x.get("id",0) or 0))
    except Exception: pass
    meta=state.setdefault("offline_meta",{})
    remembered=int(meta.get("next_picture_instance_id",0) or 0)
    value=max([0,remembered-1,*vals])+1
    meta["next_picture_instance_id"]=value+1
    return value


def make_picture_info(state: dict, pic_id: int, *, for_ads: bool = False) -> dict:
    cfg=picture_by_id(pic_id)
    if cfg is None: raise ValueError(f"unknown picture id {pic_id}")
    iid=next_picture_instance_id(state)
    info={"id":iid,"pic_id":int(pic_id),"layers":build_picture_layers(cfg,state=state,label=f"new:{iid}:{pic_id}"),"for_ads":bool(for_ads)}
    state.setdefault("offline_meta",{}).setdefault("album_full",{})[str(iid)]=copy.deepcopy(info)
    return info


def get_photo_share_data(state: dict) -> dict:
    push = find_push(state, "share.load")
    if not push:
        push = {"cmd": "share.load", "data": {"pic_list": []}}
        sync = state.setdefault("sync_pushes", [])
        album_index = next((i for i, row in enumerate(sync) if row.get("cmd") == "album.load_new"), len(sync))
        sync.insert(album_index, push)
    data = push.setdefault("data", {})
    if not isinstance(data, dict):
        data = {"pic_list": []}
        push["data"] = data
    if not isinstance(data.get("pic_list"), list):
        data["pic_list"] = _to_list(data.get("pic_list"))
    return data


def register_photo_share_reward(state: dict, picture: dict, policy: dict) -> None:
    cfg = picture_by_id(int(picture.get("pic_id", -1)))
    if not cfg or int(cfg.get("share", 0) or 0) != 1:
        return
    clover = max(0, int(policy.get("travel", {}).get("photo_share_clover", 20) or 0))
    if clover <= 0:
        return
    rows = get_photo_share_data(state)["pic_list"]
    picture_id = int(picture["id"])
    if not any(isinstance(row, dict) and int(row.get("id", -1)) == picture_id for row in rows):
        rows.append({"id": picture_id, "clover": clover})


def choose_frog_back_bonus_picture_ids(state: dict, excluded: list[int], policy: dict) -> list[int]:
    travel = policy.get("travel", {}) if isinstance(policy, dict) else {}
    count = max(0, min(2, int(travel.get("frog_back_bonus_picture_count", 2) or 0)))
    percent = max(0, min(100, int(travel.get("frog_back_bonus_picture_percent", 100) or 0)))
    if count <= 0 or deterministic_roll(state, "frog_back_bonus_picture", 100) >= percent:
        return []
    excluded_ids = {int(value) for value in excluded}
    pool = [row for row in picture_rows() if str(row.get("type")) == "Normal" and int(row.get("id", -1)) not in excluded_ids]
    selected: list[int] = []
    while pool and len(selected) < count:
        index = deterministic_roll(state, f"frog_back_bonus_picture_pick:{len(selected)}", len(pool))
        selected.append(int(pool.pop(index)["id"]))
    return selected


def choose_trip_picture_ids(state: dict, place: int, food: int, amulet: int, tools: list[int], policy: dict) -> list[int]:
    tp=policy.get("travel",{})
    owned=owned_picture_ids(state)
    maxpics=max(1,min(4,int(tp.get("max_pictures",4) or 4)))
    selected=[]

    # 三峡、黄河是路标专属路线。两条路线各有六张正式照片；既然已经
    # 命中路线，就不再混入普通目的地照。
    route_map=tp.get("special_route_picture_map",{})
    raw_route_pool=route_map.get(str(place),route_map.get(place,[])) if isinstance(route_map,dict) else []
    route_pool=[picture_by_id(int(x)) for x in _to_list(raw_route_pool)]
    route_pool=[row for row in route_pool if row is not None]
    if route_pool:
        return [int(route_pool[deterministic_roll(state,f"route_photo_pick:{place}",len(route_pool))]["id"])]

    # Food-bound postcards are selected before generic travel photos.  The
    # celebration cake's three-card pool is an explicit, official-content
    # relation; future verified food pools use the same policy key.
    food_map=tp.get("food_picture_map",{})
    raw_food_pool=food_map.get(str(food),food_map.get(food,[])) if isinstance(food_map,dict) else []
    food_amulet_map=tp.get("food_amulet_picture_map",{})
    amulet_entry=food_amulet_map.get(str(food),food_amulet_map.get(food,{})) if isinstance(food_amulet_map,dict) else {}
    raw_amulet_pool=amulet_entry.get(str(amulet),amulet_entry.get(amulet,[])) if isinstance(amulet_entry,dict) else []
    if raw_amulet_pool:
        raw_food_pool=raw_amulet_pool
        percent_map=tp.get("food_amulet_picture_percent",{})
        percent_entry=percent_map.get(str(food),percent_map.get(food,{})) if isinstance(percent_map,dict) else {}
        food_pct=max(0,min(100,int(percent_entry.get(str(amulet),percent_entry.get(amulet,100)) or 0))) if isinstance(percent_entry,dict) else 100
    else:
        percent_map=tp.get("food_picture_percent",{})
        food_pct=max(0,min(100,int(percent_map.get(str(food),percent_map.get(food,100)) or 0))) if isinstance(percent_map,dict) else 100
    food_pool=[]
    for raw_id in _to_list(raw_food_pool):
        row=picture_by_id(int(raw_id))
        if row is not None:
            food_pool.append(row)
    if food_pool and deterministic_roll(state,f"food_photo:{food}:{amulet}",100)<food_pct:
        prefer_map=tp.get("food_picture_prefer_unowned_amulet",{})
        preferred=prefer_map.get(str(amulet),prefer_map.get(amulet,[])) if isinstance(prefer_map,dict) else []
        prefer_unowned=int(amulet)==1200 and food in {int(x) for x in _to_list(preferred)}
        pool=food_pool
        if prefer_unowned:
            missing=[row for row in food_pool if int(row["id"]) not in owned]
            pool=missing or food_pool
        selected.append(int(pool[deterministic_roll(state,f"food_photo_pick:{food}",len(pool))]["id"]))

    # 星星灯、猫猫信纸与幸运花各自提升对应照片家族。它们是附加照片
    # 判定，可与便当照共同带回，但共同受每次旅行四张上限约束。
    amulet_map=tp.get("amulet_picture_map",{})
    raw_amulet_pool=amulet_map.get(str(amulet),amulet_map.get(amulet,[])) if isinstance(amulet_map,dict) else []
    amulet_percent=tp.get("amulet_picture_percent",{})
    amulet_pct=max(0,min(100,int(amulet_percent.get(str(amulet),amulet_percent.get(amulet,100)) or 0))) if isinstance(amulet_percent,dict) else 100
    if 1201<=amulet<=1215:
        raw_amulet_pool=tp.get("lucky_flower_picture_pool",[])
        amulet_pct=max(0,min(100,int(tp.get("lucky_flower_picture_percent",0) or 0)))
    amulet_pool=[picture_by_id(int(x)) for x in _to_list(raw_amulet_pool)]
    amulet_pool=[row for row in amulet_pool if row is not None]
    if amulet_pool and len(selected)<maxpics and deterministic_roll(state,f"amulet_photo:{amulet}",100)<amulet_pct:
        pool=[row for row in amulet_pool if int(row["id"]) not in selected] or amulet_pool
        selected.append(int(pool[deterministic_roll(state,f"amulet_photo_pick:{amulet}",len(pool))]["id"]))

    # Static Unique rows that are neither special-food rewards nor event
    # rewards are the rare on-the-way encounter family.
    unique_pct=max(0,min(100,int(tp.get("unique_picture_percent",0) or 0)))
    unique_pool=[picture_by_id(int(x)) for x in _to_list(tp.get("unique_picture_pool",[]))]
    unique_pool=[row for row in unique_pool if row is not None]
    if unique_pool and len(selected)<maxpics and deterministic_roll(state,f"unique_photo:{place}",100)<unique_pct:
        pool=[row for row in unique_pool if int(row["id"]) not in selected] or unique_pool
        selected.append(int(pool[deterministic_roll(state,f"unique_photo_pick:{place}",len(pool))]["id"]))

    # Normal postcards are the generic on-the-way photos.  The four road-sign
    # scenes and the seven flower scenes are also Normal rows in Picture_json,
    # but each has a dedicated trigger above.  Do not leak those exclusive
    # cards into an unrelated trip's generic on-the-way draw.
    normal_pct=max(0,min(100,int(tp.get("normal_picture_percent",tp.get("base_picture_percent",70)) or 0)))
    special_route_map=tp.get("special_route_picture_map", {})
    exclusive_normal_ids={
        int(pic_id)
        for value in special_route_map.values()
        for pic_id in _to_list(value)
    } if isinstance(special_route_map, dict) else set()
    exclusive_normal_ids.update(
        int(pic_id) for pic_id in _to_list(tp.get("lucky_flower_picture_pool", []))
    )
    normal_rows=[
        row for row in picture_rows()
        if str(row.get("type"))=="Normal"
        and int(row.get("id", -1)) not in exclusive_normal_ids
    ]
    if normal_rows and len(selected)<maxpics and deterministic_roll(state,f"normal_photo:{place}",100)<normal_pct:
        selected.append(int(normal_rows[deterministic_roll(state,f"normal_photo_pick:{place}",len(normal_rows))]["id"]))

    # Museum routes use museum-specific probabilities and photo lists.
    if 100<=place<=104:
        mid=place-99; cfg=load_static_json("museumData_json.json").get(str(mid),{})
        candidates=[int(x) for x in _to_list(cfg.get("pic_id"))]
        pct=max(0,min(100,int(tp.get("museum_picture_percent",80))))
        if len(selected)<maxpics and deterministic_roll(state,f"museum_photo:{mid}",100)<pct:
            if candidates:
                selected.append(candidates[deterministic_roll(state,f"museum_photo_pick:{mid}",len(candidates))])
        return selected[:maxpics]

    goals=[x for x in picture_rows() if str(x.get("type"))=="Goal" and _picture_route_place(x)==place]
    if not goals:return selected[:maxpics]
    tier=max([tool_tier(x) for x in tools] or [0])
    bonus_map=tp.get("tool_picture_bonus",{})
    bonus=int(bonus_map.get(str(tier),bonus_map.get(tier,0)) or 0) if isinstance(bonus_map,dict) else 0
    base=max(0,min(100,int(tp.get("base_picture_percent",70) or 70)+bonus))
    count=0
    chance=base
    for i in range(maxpics-len(selected)):
        if deterministic_roll(state,f"photo_count:{place}:{i}",100)<chance: count+=1
        chance=max(5,chance//2)
    for i in range(min(count,len(goals))):
        pool=[x for x in goals if int(x['id']) not in selected]
        if not pool:break
        r=deterministic_roll(state,f"goal_photo_pick:{place}:{i}",len(pool))
        selected.append(int(pool[r]['id']))
    # Tools cards retain their official 0..3 pose variants.  A matching
    # carried tool changes the frog's visible prop; without it, the same
    # environment uses its authored tier-0 pose.
    tool_rows=tool_picture_rows_for_trip(state,place,tools,tp)
    if tool_rows and len(selected)<maxpics:
        tool_tiers=[tool_tier(item) for item in tools]
        tool_bonus=max(tool_tiers or [0])
        bonus_for_tool=int(bonus_map.get(str(tool_bonus),bonus_map.get(tool_bonus,0)) or 0) if isinstance(bonus_map,dict) else 0
        tool_pct=max(0,min(100,int(tp.get("tool_picture_percent",40) or 0)+bonus_for_tool))
        if deterministic_roll(state,"tool_photo_include",100)<tool_pct:
            pool=[row for row in tool_rows if int(row["id"]) not in selected] or tool_rows
            selected.append(int(pool[deterministic_roll(state,f"tool_photo_pick:{place}",len(pool))]["id"]))
    return selected[:maxpics]


def choose_collection(state: dict, place: int, policy: dict) -> int:
    owned,_=get_handbook_parts(state); owned=set(owned)
    tp=policy.get("travel",{})
    if 100<=place<=104:
        mid=place-99; cfg=load_static_json("museumData_json.json").get(str(mid),{})
        ids=[int(x) for x in str(cfg.get("collection_id","")).split(',') if x]
        pct=max(0,min(100,int(tp.get("museum_collection_percent",45))))
        if deterministic_roll(state,f"museum_collection:{mid}",100)>=pct:return -1
        pool=[x for x in ids if x not in owned] or ids
        return pool[deterministic_roll(state,f"museum_collection_pick:{mid}",len(pool))] if pool else -1
    pct=max(0,min(100,int(tp.get("collection_percent",25))))
    if deterministic_roll(state,f"collection:{place}",100)>=pct:return -1
    region=PLACE_REGION.get(place)
    rows=[x for x in static_rows("Collection_json.json") if int(x.get('type',0) or 0)==1 and str(x.get('place'))==region]
    pool=[int(x['id']) for x in rows if int(x['id']) not in owned] or [int(x['id']) for x in rows]
    return pool[deterministic_roll(state,f"collection_pick:{place}",len(pool))] if pool else -1


def choose_travel_items(state: dict, place: int, policy: dict) -> list[int]:
    # Museum collections are handled separately; museum routes may still bring
    # a regional specialty when the museum city maps cleanly to a region.
    if 100<=place<=104:
        place_for_region={100:25,101:28,102:4,103:7,104:29}.get(place)
    else: place_for_region=place
    region=PLACE_REGION.get(place_for_region)
    tp=policy.get("travel",{})
    pct=max(0,min(100,int(tp.get("specialty_percent",60))))
    max_items=max(0,min(10,int(tp.get("max_travel_items",10) or 10)))
    out=[]
    candidates=list(dict.fromkeys(int(x.get('itemId')) for x in static_rows("Specialty_json.json") if str(x.get('place'))==region)) if region else []
    # Production BackHome captures contain repeated specialty ids in the same
    # return. Each draw therefore samples the regional pool independently.
    for i in range(min(4,max_items)):
        if not candidates: break
        if deterministic_roll(state,f"specialty:{place}:{i}",100)>=pct: continue
        idx=deterministic_roll(state,f"specialty_pick:{place}:{i}",len(candidates))
        out.append(candidates[idx])

    hp=handcraft_policy(policy)
    if bool(hp.get("enabled", True)) and len(out)<max_items:
        material_pct=max(0,min(100,int(hp.get("material_travel_percent", 35) or 0)))
        if deterministic_roll(state, f"handcraft:material:{place}", 100)<material_pct:
            out.append(HANDCRAFT_MATERIAL_ID)
    if bool(hp.get("enabled", True)) and len(out)<max_items:
        fragment_pct=max(0,min(100,int(hp.get("fragment_travel_percent", 35) or 0)))
        if deterministic_roll(state, f"handcraft:fragment:{place}", 100)<fragment_pct:
            out.append(HANDCRAFT_FRAGMENT_IDS[
                deterministic_roll(state, f"handcraft:fragment_pick:{place}", len(HANDCRAFT_FRAGMENT_IDS))
            ])
    return out[:max_items]


def travel_duration_seconds(food_id: int, policy: dict) -> int:
    tp=policy.get("travel",{})
    forced=int(tp.get("force_duration_seconds",0) or 0)
    if forced>0:return max(1,forced)
    table=tp.get("duration_seconds_by_food",{})
    if not isinstance(table,dict):return 7200
    return max(60,int(table.get(str(food_id),table.get("default",7200)) or 7200))


def next_event_id(state: dict) -> int:
    ids=[int(x.get('id',0) or 0) for x in get_event_list(state) if isinstance(x,dict)]
    meta=state.setdefault('offline_meta',{}); remembered=int(meta.get('next_event_id',0) or 0)
    value=max([0,remembered-1,*ids])+1; meta['next_event_id']=value+1; return value


def prepare_travel_plan(state: dict, bag_snapshot: list[int], now: int, policy: dict) -> dict:
    food=next((x for x in bag_snapshot if x>=0 and item_type(x)==ITEM_LUNCHBOX),-1)
    amulet=next((x for x in bag_snapshot if x>=0 and item_type(x)==ITEM_AMULET),-1)
    tools=[x for x in bag_snapshot if x>=0 and item_type(x)==ITEM_TOOL]
    effective_policy,handcraft_effect=travel_policy_with_handcraft_effect(policy,amulet)
    history = state.setdefault("offline_meta", {}).get("travel_history", [])
    if not isinstance(history, list) or not history:
        place, route = 1, {"reason": "first_trip_beijing", "food": food, "amulet": amulet}
    else:
        place,route=choose_destination(state,bag_snapshot,effective_policy)
    tp=effective_policy.get('travel',{})
    lo=int(tp.get('clover_min',30) or 30); hi=max(lo,int(tp.get('clover_max',90) or 90))
    clover=lo+deterministic_roll(state,f"travel_clover:{place}",hi-lo+1)+max(0,int(handcraft_effect.get("clover_bonus",0) or 0))
    tmax=max(0,int(tp.get('ticket_max',2) or 2)); ticket=0
    if deterministic_roll(state,f"travel_ticket_chance:{place}",100)<max(0,min(100,int(tp.get('ticket_percent',35) or 35))):
        ticket=1+deterministic_roll(state,f"travel_ticket_count:{place}",max(1,tmax)) if tmax>0 else 0
    collection=choose_collection(state,place,effective_policy)
    items=choose_travel_items(state,place,effective_policy)
    pics=choose_trip_picture_ids(state,place,food,amulet,tools,effective_policy)
    dur=travel_duration_seconds(food,effective_policy)
    duration_multiplier=float(handcraft_effect.get("duration_multiplier",1) or 1)
    if duration_multiplier>0:
        dur=max(60,int(dur*duration_multiplier))
    return {
        'food_id':food,'amulet_id':amulet,'tool_ids':tools,'bag_snapshot':list(bag_snapshot),
        'place':place,'route':route,'started_at':now,'return_at':now+dur,'duration_seconds':dur,
        'clover':clover,'ticket':ticket,'collection_id':collection,'item_ids':items,'picture_ids':pics,
        'handcraft_effect':handcraft_effect,
    }


def add_handbook_rewards(state: dict, collection_id: int, item_ids: list[int]) -> None:
    push=find_push(state,'item.load_handbook')
    if not push:return
    data=push.setdefault('data',{}); cols=data.setdefault('collections',[]); specs=data.setdefault('specialtys',[])
    if collection_id>=0 and collection_id not in cols: cols.append(collection_id)
    specset={int(x.get('itemId')) for x in static_rows('Specialty_json.json')}
    for iid in item_ids:
        if iid in specset and iid not in specs: specs.append(iid)


def auto_prepare_from_desk(state: dict) -> None:
    data,_,bag,desk=get_item_parts(state)
    bag[:]=[-1,-1,-1,-1]
    # Food + amulet: first compatible item from left, matching observed desk behavior.
    for slot,typ in ((0,ITEM_LUNCHBOX),(1,ITEM_AMULET)):
        for i,iid in enumerate(list(desk)):
            if int(iid)>=0 and item_type(int(iid))==typ:
                bag[slot]=int(iid); desk[i]=-1; break
    # Tools: observed return moved the rightmost prepared tools into the bag in
    # reverse desk order (2010 then 2009). Preserve that behavior locally.
    tool_indices=[i for i,iid in enumerate(desk) if int(iid)>=0 and item_type(int(iid))==ITEM_TOOL]
    for slot,i in zip((2,3),reversed(tool_indices)):
        bag[slot]=int(desk[i]); desk[i]=-1
    data['bag_completed']=False


def settle_due_travel_return(state: dict, now: int | None=None, policy: dict | None=None) -> dict | None:
    if now is None:now=int(time.time())
    if policy is None:policy=load_offline_policy()
    _,frog,res,_=get_role_parts(state)
    if int(frog.get('status',FROG_HOME))!=FROG_TRAVEL:return None
    act=state.setdefault('offline_meta',{}).setdefault('activity',{})
    if act.get('kind')!='travel':return None
    plan=act.get('plan')
    if not isinstance(plan,dict):return None
    if not bool(policy.get('travel',{}).get('auto_return',True)):return None
    if now<int(plan.get('return_at',0) or 0):return None

    # Return every durable bag item to house; consumed spend=1 items were
    # removed on departure and therefore are not restored.
    data,house,bag,_=get_item_parts(state)
    for iid in list(bag):
        iid=int(iid)
        if iid>=0:
            set_house_count(house,iid,get_house_count(house,iid)+1)
    bag[:]=[-1,-1,-1,-1]

    reward_items=[int(x) for x in _to_list(plan.get('item_ids'))][:10]
    for iid in reward_items:
        set_house_count(house,iid,get_house_count(house,iid)+1)
    collection=int(plan.get('collection_id',-1) or -1)
    add_handbook_rewards(state,collection,reward_items)
    encyclopedia_unlocked = unlock_travel_encyclopedia_items(
        state,
        [
            int(plan.get("food_id", -1)),
            int(plan.get("amulet_id", -1) or -1),
            *[int(item_id) for item_id in _to_list(plan.get("tool_ids"))],
        ],
    )

    clover=int(plan.get('clover',0) or 0); ticket=int(plan.get('ticket',0) or 0)
    res['clover_point']=int(res.get('clover_point',0) or 0)+clover
    res['ticket']=int(res.get('ticket',0) or 0)+ticket
    calendar_record_clover_gain(state, clover)

    new_data=get_new_album_data(state); pics=_to_list(new_data.get('pictures'))
    regular_pictures=[]
    for pid in _to_list(plan.get('picture_ids')):
        try:
            picture=make_picture_info(state,int(pid))
            pics.append(picture); regular_pictures.append(picture)
            register_photo_share_reward(state,picture,policy)
        except Exception:logging.exception('生成旅行照片失败 pic_id=%s',pid)
    bonus_pictures=[]
    bonus_ids=choose_frog_back_bonus_picture_ids(state,[int(x.get('pic_id',-1)) for x in regular_pictures],policy)
    for pid in bonus_ids:
        try:
            picture=make_picture_info(state,pid,for_ads=True)
            pics.append(picture); bonus_pictures.append(picture)
        except Exception:logging.exception('生成返程奖励照片失败 pic_id=%s',pid)
    if bonus_pictures:
        batches=state.setdefault('offline_meta',{}).setdefault('frog_back_picture_batches',[])
        batches.append({'picture_ids':[int(x['id']) for x in bonus_pictures],'created_at':now,'claimed':False})
        if len(batches)>100:del batches[:-100]
    new_data['pictures']=pics; new_data.setdefault('visted_pic',[])
    unclaimed_bonus_ids={
        int(value)
        for batch in _to_list(state.setdefault('offline_meta',{}).get('frog_back_picture_batches'))
        if isinstance(batch,dict) and not bool(batch.get('claimed'))
        for value in _to_list(batch.get('picture_ids'))
    }
    new_data['has_ads']=any(
        isinstance(x,dict) and bool(x.get('for_ads')) and int(x.get('id',-1)) in unclaimed_bonus_ids
        for x in pics
    )
    new_data['is_share']=False

    event={
        'id':next_event_id(state),'evt_type':2,'evt_id':int(plan.get('place',0) or 0),
        'evt_value':[0,0,clover,ticket,collection,*reward_items], 'evt_string':[], 'evt_pic':[]
    }
    get_event_list(state).append(event)
    maybe_generate_story_social(
        state,
        f"{plan.get('started_at',0)}:{plan.get('place',0)}",
        policy=policy,
        amulet_id=int(plan.get("amulet_id", -1) or -1),
    )
    place=int(plan.get('place',0) or 0)
    note_id = roll_travel_observation(state, now)
    meta=state.setdefault('offline_meta',{}); vp=set(int(x) for x in _to_list(meta.get('visited_places'))); vp.add(place); meta['visited_places']=sorted(vp)
    history=meta.setdefault('travel_history',[]); history.append(copy.deepcopy(plan)); history[-1]['returned_at']=now
    if len(history)>200:del history[:-200]
    unlocked_achievements = refresh_achievements(state, now=now, save=False)

    frog['status'] = FROG_HOME
    arranged_decoration = frog_arrange_vase_on_return(state, now, policy)
    frog['motion'] = select_home_frog_motion(state, now)
    act.clear()
    auto_prepare_from_desk(state)
    sync_museum_progress(state)
    museum_day_record_task_progress(state, "travel", now=now, policy=policy)
    state['format_version']=max(int(state.get('format_version',2) or 2),8)
    save_state(state,f"travel.return:{place}")
    result={'place':place,'clover':clover,'ticket':ticket,'collection_id':collection,'items':reward_items,'pictures':list(plan.get('picture_ids',[])), 'bonus_pictures':bonus_ids, 'encyclopedia_unlocked': encyclopedia_unlocked, 'achievements_unlocked': unlocked_achievements, 'arranged_decoration': arranged_decoration, 'note_id': note_id}
    logging.info('TRAVEL RETURN %s',result)
    return result



def _drawing_ids_for_guest(filename: str, guest_id: int) -> list[int]:
    obj = load_static_json(filename)
    rows = obj.values() if isinstance(obj, dict) else obj if isinstance(obj, list) else []
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if int(row.get("guest", -1)) == int(guest_id):
            out.append(int(row.get("id", 0) or 0))
    return sorted(x for x in out if x > 0)


def _choose_missing_drawing_reward(state: dict, guest_id: int, *, kind: str, percent: int) -> int:
    drawing, _ = get_drawing_parts(state)
    key = "pages" if kind == "page" else "colls"
    filename = "drawingPageData_json.json" if kind == "page" else "drawingCollectData_json.json"
    if deterministic_roll(state, f"party_{kind}_chance:{guest_id}", 100) >= max(0, min(100, int(percent))):
        return 0
    owned = {int(x) for x in _to_list(drawing.get(key))}
    pool = [x for x in _drawing_ids_for_guest(filename, guest_id) if x not in owned]
    if not pool:
        return 0
    return pool[deterministic_roll(state, f"party_{kind}_pick:{guest_id}", len(pool))]


def settle_due_party_return(state: dict, now: int | None=None, policy: dict | None=None) -> dict | None:
    """Finish a locally-created party and create TimerEvent PartyResult (24).

    Capture-calibrated semantics:
      * rewards are committed before client.load_events is replayed;
      * evt_value is [page, collection, clover, ticket, ...items];
      * client.confirm_event only removes the presentation event.
    Timing and reward probabilities are local policy because they are not in the
    preserved current client/static config.
    """
    if now is None:
        now = int(time.time())
    if policy is None:
        policy = load_offline_policy()
    _, frog, res, _ = get_role_parts(state)
    if int(frog.get("status", FROG_HOME)) != FROG_PARTY:
        return None
    meta = state.setdefault("offline_meta", {})
    act = meta.setdefault("activity", {})
    if act.get("kind") != "party":
        return None
    pp = policy.get("party", {}) if isinstance(policy, dict) else {}
    if not bool(pp.get("auto_return", True)):
        return None
    return_at = int(act.get("return_at", 0) or 0)
    if return_at <= 0:
        duration = max(60, int(pp.get("duration_seconds", 1800) or 1800))
        return_at = int(act.get("started_at", now) or now) + duration
        act["return_at"] = return_at
    if now < return_at:
        return None

    guest_id = int(act.get("guest_id", 0) or 0)
    drawing, dbag = get_drawing_parts(state)
    _, house, _, _ = get_item_parts(state)

    # Party-bag items were removed from house when packed. Return durable items;
    # consumables (Item.spend=1) stay consumed, matching ordinary travel policy.
    for iid in list(dbag):
        iid = int(iid)
        if iid >= 0 and not item_spend(iid):
            set_house_count(house, iid, get_house_count(house, iid) + 1)
    dbag[:] = [-1, -1, -1, -1]

    lo = int(pp.get("clover_min", 50) or 0)
    hi = max(lo, int(pp.get("clover_max", 120) or lo))
    clover = lo + deterministic_roll(state, f"party_clover:{guest_id}", hi - lo + 1) if hi >= lo else lo
    ticket = 0
    if deterministic_roll(state, f"party_ticket_chance:{guest_id}", 100) < max(0, min(100, int(pp.get("ticket_percent", 50) or 0))):
        tlo = max(1, int(pp.get("ticket_min", 1) or 1))
        thi = max(tlo, int(pp.get("ticket_max", 4) or tlo))
        ticket = tlo + deterministic_roll(state, f"party_ticket_count:{guest_id}", thi - tlo + 1)
    page = _choose_missing_drawing_reward(state, guest_id, kind="page", percent=int(pp.get("page_percent", 20) or 0))
    coll = _choose_missing_drawing_reward(state, guest_id, kind="collection", percent=int(pp.get("collection_percent", 10) or 0))
    furniture_id = 0
    furniture_data = _furniture_production_data(state)
    owned_furniture = {int(value) for value in _to_list(furniture_data.get("has_fur")) if str(value).lstrip("-").isdigit()}
    missing_furniture = [item_id for item_id in XI_SHI_PARTY_FURNITURE_IDS if item_id not in owned_furniture]
    furniture_percent = max(0, min(100, int(pp.get("furniture_percent", 100) or 0)))
    if missing_furniture and deterministic_roll(state, f"party_furniture_chance:{guest_id}", 100) < furniture_percent:
        furniture_id = missing_furniture[deterministic_roll(state, f"party_furniture_pick:{guest_id}", len(missing_furniture))]
        furniture_data["has_fur"].append(furniture_id)

    res["clover_point"] = int(res.get("clover_point", 0) or 0) + clover
    res["ticket"] = int(res.get("ticket", 0) or 0) + ticket
    calendar_record_clover_gain(state, clover)
    if page and page not in drawing.setdefault("pages", []):
        drawing["pages"].append(page)
    if coll and coll not in drawing.setdefault("colls", []):
        drawing["colls"].append(coll)

    furniture_event_id = 0
    if furniture_id:
        furniture_event = {
            "id": next_event_id(state), "evt_type": 21, "evt_id": furniture_id,
            "evt_value": [], "evt_string": [], "evt_pic": []
        }
        get_event_list(state).append(furniture_event)
        furniture_event_id = int(furniture_event["id"])

    event = {
        "id": next_event_id(state), "evt_type": 24, "evt_id": guest_id,
        "evt_value": [page, coll, clover, ticket], "evt_string": [], "evt_pic": []
    }
    get_event_list(state).append(event)
    frog["status"] = FROG_HOME
    frog["motion"] = select_home_frog_motion(state, now)
    drawing["state"] = DRAW_WAIT
    drawing["guest"] = guest_id
    act.clear()
    schedule_next_guest(state, now=now, policy=policy)
    hist = meta.setdefault("party_history", [])
    hist.append({"guest_id": guest_id, "returned_at": now, "event": copy.deepcopy(event), "furniture_id": furniture_id})
    if len(hist) > 200:
        del hist[:-200]
    state["format_version"] = max(int(state.get("format_version", 2) or 2), 14)
    save_state(state, f"party.return:{guest_id}")
    result = {"guest_id": guest_id, "page": page, "collection": coll, "clover": clover, "ticket": ticket, "furniture_id": furniture_id, "furniture_event_id": furniture_event_id, "event_id": event["id"]}
    logging.info("PARTY RETURN %s", result)
    return result


COOKING_TASK_TARGETS = {
    1: 1,   # weekly login
    2: 1,   # advertisement
    3: 1,   # feed a visiting friend
    4: 80,  # earn clover
    5: 1,   # share
    6: 1,   # finish a dynamic photo
    7: 1,   # collect a lottery reward
    8: 1,   # harvest a flowerpot
}

COOKING_MONTH_ITEMS = {
    1: 20, 2: 21, 3: 22, 4: 23, 5: 24, 6: 25,
    7: 26, 8: 27, 9: 28, 10: 29, 11: 30, 12: 31,
    13: 35, 14: 36, 15: 37, 16: 38, 17: 39, 18: 40,
    19: 41, 20: 42, 21: 43, 22: 44, 23: 45, 24: 46,
}


def get_cooking_data(state: dict) -> dict:
    push = find_push(state, "cooking.load_cooking")
    if not push:
        push = {
            "cmd": "cooking.load_cooking",
            "data": {
                "refresh_time": 0,
                "select": 1,
                "month_pro": 0,
                "month": int(datetime.now().month),
                "task_list": [],
                "complete": False,
                "week": 1,
            },
        }
        state.setdefault("sync_pushes", []).append(push)
    data = push.setdefault("data", {})
    if not isinstance(data, dict):
        data = {}
        push["data"] = data
    tasks = data.get("task_list")
    if isinstance(tasks, dict):
        tasks = list(tasks.values())
    if not isinstance(tasks, list):
        tasks = []
    data["task_list"] = [row for row in tasks if isinstance(row, dict)]
    data["month_pro"] = max(0, int(data.get("month_pro", 0) or 0))
    data["month"] = int(data.get("month", datetime.now().month) or datetime.now().month)
    data["select"] = 2 if int(data.get("select", 1) or 1) == 2 else 1
    data["complete"] = bool(data.get("complete", False))
    return data


def cooking_record_task_progress(state: dict, task_id: int, amount: int = 1) -> dict | None:
    task_id = int(task_id)
    amount = max(0, int(amount or 0))
    if amount <= 0:
        return None
    target = COOKING_TASK_TARGETS.get(task_id)
    if target is None:
        return None
    row = next(
        (
            item
            for item in get_cooking_data(state).get("task_list", [])
            if int(item.get("id", -1)) == task_id
        ),
        None,
    )
    if row is None or bool(row.get("complete", False)):
        return None
    before = max(0, int(row.get("pro", 0) or 0))
    after = min(target, before + amount)
    if after == before:
        return None
    row["pro"] = after
    return row


def cooking_complete_task(state: dict, task_id: int) -> tuple[bool, str, dict | None]:
    task_id = int(task_id)
    data = get_cooking_data(state)
    row = next(
        (item for item in data["task_list"] if int(item.get("id", -1)) == task_id),
        None,
    )
    target = COOKING_TASK_TARGETS.get(task_id)
    if row is None or target is None:
        return False, "task_missing", None
    if bool(row.get("complete", False)):
        return False, "already_complete", row
    if int(row.get("pro", 0) or 0) < target:
        return False, "progress_incomplete", row
    row["complete"] = True
    data["month_pro"] = int(data.get("month_pro", 0) or 0) + 1
    save_state(state, f"cooking.complete_task:{task_id}")
    return True, "ok", row


def cooking_refresh_task(state: dict, task_id: int) -> tuple[bool, str, dict | None]:
    task_id = int(task_id)
    data = get_cooking_data(state)
    index = next(
        (i for i, item in enumerate(data["task_list"]) if int(item.get("id", -1)) == task_id),
        -1,
    )
    if index < 0:
        return False, "task_missing", None
    old = data["task_list"][index]
    if not bool(old.get("refresh", False)) or bool(old.get("complete", False)):
        return False, "not_refreshable", old
    present = {int(item.get("id", -1)) for item in data["task_list"]}
    candidates = [value for value in range(2, 9) if value not in present]
    if not candidates:
        return False, "no_replacement", old
    pick = candidates[deterministic_roll(state, f"cooking_refresh:{task_id}", len(candidates))]
    row = {"id": pick, "refresh": True, "pro": 0, "complete": False}
    data["task_list"][index] = row
    save_state(state, f"cooking.refresh_task:{task_id}->{pick}")
    return True, "ok", row


def cooking_select_theme(state: dict, index: int) -> tuple[bool, str]:
    index = int(index)
    if index not in (1, 2):
        return False, "bad_theme"
    data = get_cooking_data(state)
    if int(data.get("select", 1) or 1) == index:
        return True, "unchanged"
    data["select"] = index
    save_state(state, f"cooking.select:{index}")
    return True, "ok"


def cooking_start(state: dict) -> tuple[bool, str, int | None, int | None]:
    data = get_cooking_data(state)
    if bool(data.get("complete", False)):
        return False, "already_complete", None, None
    if int(data.get("month_pro", 0) or 0) < 6:
        return False, "progress_incomplete", None, None
    item_id = COOKING_MONTH_ITEMS.get(int(data.get("month", 0) or 0))
    if item_id is None:
        return False, "bad_month", None, None
    _, house, _, _ = get_item_parts(state)
    count = get_house_count(house, item_id) + 1
    set_house_count(house, item_id, count)
    data["complete"] = True
    save_state(state, f"cooking.start:{int(data.get('month', 0) or 0)}:{item_id}")
    return True, "ok", item_id, count


def get_lottery_data(state: dict) -> dict:
    push = find_push(state, "lottery.load")
    if not push:
        push = {"cmd": "lottery.load", "data": {}}
        state.setdefault("sync_pushes", []).append(push)
    data = push.setdefault("data", {})
    if not isinstance(data, dict):
        data = {}
        push["data"] = data
    data.setdefault("last_phase", 0)
    data.setdefault("phase", 1)
    data.setdefault("state", LOTTERY_OPEN)
    data.setdefault("select_list", [])
    data.setdefault("answer", [])
    data.setdefault("extra_item", {"item_id": 0, "count": 0})
    data.setdefault("right_flag", [])
    data.setdefault("egg_num", 0)
    data.setdefault("reward", [])
    for key in ("select_list", "answer", "right_flag", "reward"):
        if isinstance(data.get(key), dict):
            data[key] = []
    if not isinstance(data.get("extra_item"), dict):
        data["extra_item"] = {"item_id": 0, "count": 0}
    return data


def _lottery_specialty_pool() -> list[int]:
    return sorted({int(x.get("itemId")) for x in static_rows("Specialty_json.json") if int(x.get("itemId", -1)) >= 0})


def weekend_lottery_window(now: int) -> tuple[int, bool]:
    week, elapsed = divmod(now - LOTTERY_PHASE_171_START, LOTTERY_WEEK_SECONDS)
    return max(1, 171 + week), elapsed < 2 * 86400


def ensure_lottery_select_list(state: dict, policy: dict | None=None) -> list[int]:
    if policy is None:
        policy = load_offline_policy()
    data = get_lottery_data(state)
    current = [int(x) for x in _to_list(data.get("select_list"))]
    if len(current) >= 5:
        data["select_list"] = current
        return current
    phase = int(data.get("phase", 1) or 1)
    captured = CAPTURED_LOTTERY_SELECT_LISTS.get(phase)
    if captured:
        data["select_list"] = list(captured)
        return data["select_list"]
    lp = policy.get("lottery", {}) if isinstance(policy, dict) else {}
    if not bool(lp.get("allow_local_future_phase_pool", True)):
        return current
    count = max(5, int(lp.get("candidate_count", 15) or 15))
    # Every fourth week is Dudu's plant question, not a food question.
    pool = list(CAPTURED_LOTTERY_SELECT_LISTS[172]) if phase % 4 == 0 else _lottery_specialty_pool()
    out = []
    while pool and len(out) < count:
        idx = deterministic_roll(state, f"lottery_candidate:{phase}:{len(out)}", len(pool))
        out.append(pool.pop(idx))
    data["select_list"] = out
    return out


def lottery_open(state: dict, policy: dict | None=None, *, now: int | None = None) -> tuple[bool, str, dict, int | None]:
    if policy is None:
        policy = load_offline_policy()
    now = int(time.time()) if now is None else now
    if not weekend_lottery_window(now)[1]:
        return False, "outside_weekend", {}, None
    advance_weekend_lottery(state, now, policy)
    data = get_lottery_data(state)
    if int(data.get("state", LOTTERY_OPEN) or 0) != LOTTERY_OPEN:
        return False, "bad_state", {}, None
    select_list = ensure_lottery_select_list(state, policy=policy)
    if len(select_list) < 5:
        return False, "no_candidate_pool", {}, None
    lp = policy.get("lottery", {}) if isinstance(policy, dict) else {}
    phase = int(data.get("phase", 1) or 1)
    clover_by_phase = lp.get("open_clover_by_phase", {}) if isinstance(lp.get("open_clover_by_phase", {}), dict) else {}
    clover = max(0, int(clover_by_phase.get(str(phase), lp.get("open_clover", 10)) or 0))
    _, _, res, _ = get_role_parts(state)
    res["clover_point"] = int(res.get("clover_point", 0) or 0) + clover
    calendar_record_clover_gain(state, clover)
    extra_map = lp.get("extra_item_by_phase", {}) if isinstance(lp.get("extra_item_by_phase", {}), dict) else {}
    extra = copy.deepcopy(extra_map.get(str(phase), {"item_id": 0, "count": 0}))
    extra = {"item_id": int(extra.get("item_id", 0) or 0), "count": int(extra.get("count", 0) or 0)}
    data["extra_item"] = extra
    data["state"] = LOTTERY_SELECT
    data["answer"] = []
    data["right_flag"] = []
    data["reward"] = []
    data["egg_num"] = 0
    state["format_version"] = max(int(state.get("format_version", 2) or 2), 14)
    save_state(state, f"lottery.open:{phase}")
    payload = {"open_item": {"item_id": LOTTERY_CLOVER_ITEM_ID, "count": clover}, "extra_item": copy.deepcopy(extra)}
    return True, "ok", payload, int(res.get("clover_point", 0) or 0)


def lottery_select(state: dict, selected: list[int], policy: dict | None=None, *, now: int | None = None) -> tuple[bool, str]:
    if policy is None:
        policy = load_offline_policy()
    now = int(time.time()) if now is None else now
    if not weekend_lottery_window(now)[1]:
        return False, "outside_weekend"
    advance_weekend_lottery(state, now, policy)
    data = get_lottery_data(state)
    if int(data.get("state", LOTTERY_OPEN) or 0) != LOTTERY_SELECT:
        return False, "bad_state"
    lp = policy.get("lottery", {}) if isinstance(policy, dict) else {}
    need = max(1, int(lp.get("selection_count", 5) or 5))
    selected = [int(x) for x in selected]
    if len(selected) != need or len(set(selected)) != need:
        return False, "need_exact_selection"
    allowed = {int(x) for x in _to_list(data.get("select_list"))}
    if not all(x in allowed for x in selected):
        return False, "item_not_in_select_list"
    data["answer"] = list(selected)
    data["state"] = LOTTERY_COMPLETE
    save_state(state, f"lottery.select:{int(data.get('phase',0) or 0)}")
    return True, "ok"


def settle_captured_lottery_phase172(state: dict) -> bool:
    """Apply the captured phase-171 result when the local answer matches it."""
    if state.get("offline_meta", {}).get("portable_save"):
        return False
    data = get_lottery_data(state)
    if int(data.get("phase", 0) or 0) != 171 or int(data.get("state", -1)) != LOTTERY_COMPLETE:
        return False
    answer = [int(value) for value in _to_list(data.get("answer"))]
    if answer != CAPTURED_LOTTERY_SELECT_LISTS[171][:5]:
        return False
    data.update({
        "egg_num": 3,
        "phase": 172,
        "last_phase": 171,
        "right_flag": [0, 0, 0, 1, 1],
        "state": LOTTERY_REWARD,
        "reward": [
            {"id": LOTTERY_CLOVER_ITEM_ID, "is_egg": 0, "num": 30},
            {"id": 202107, "is_egg": 0, "num": 1},
        ],
        "extra_item": {"item_id": 0, "count": 0},
        "select_list": list(CAPTURED_LOTTERY_SELECT_LISTS[172]),
    })
    save_state(state, "lottery.settle.captured_phase172")
    return True


def advance_weekend_lottery(state: dict, now: int, policy: dict | None = None) -> None:
    """Roll the weekly question forward, keeping one outstanding answer or result."""
    phase, _ = weekend_lottery_window(now)
    data = get_lottery_data(state)
    old_phase = int(data["phase"])
    old_status = int(data["state"])
    if old_status == LOTTERY_REWARD:
        return
    if old_phase == phase:
        if not data["select_list"] and ensure_lottery_select_list(state, policy):
            save_state(state, "lottery.weekly.candidates")
        return
    if old_phase > phase:
        return
    if old_status == LOTTERY_COMPLETE and data["answer"]:
        if settle_captured_lottery_phase172(state):
            return
        # Sample the host's preferences from the old question, not the new one.
        candidates = list(dict.fromkeys(data["select_list"] or data["answer"]))
        liked = set()
        while candidates and len(liked) < 5:
            i = deterministic_roll(state, f"lottery.weekly.answer:{old_phase}", len(candidates))
            liked.add(candidates.pop(i))
        flags = [int(item in liked) for item in data["answer"]]
        hits = sum(flags)
        reward = [{"id": LOTTERY_CLOVER_ITEM_ID, "is_egg": 0, "num": 10 * (1 + hits)}]
        if hits:
            reward.append({"id": LOTTERY_TICKET_ITEM_ID, "is_egg": 0, "num": min(3, hits)})
        data.update(last_phase=old_phase, state=LOTTERY_REWARD, right_flag=flags, reward=reward, egg_num=0)
    else:
        data.update(last_phase=phase-1, state=LOTTERY_OPEN, answer=[], right_flag=[], reward=[], egg_num=0)
    data.update(phase=phase, select_list=[], extra_item={"item_id": 0, "count": 0})
    ensure_lottery_select_list(state, policy)
    save_state(state, f"lottery.weekly.rollover:{old_phase}:{phase}")


def create_lottery_result_mail(state: dict, rewards: list[dict]) -> dict | None:
    items = []
    for row in rewards:
        if not isinstance(row, dict):
            continue
        item_id = int(row.get("id", 0) or 0)
        count = int(row.get("num", 0) or 0)
        if item_id > 0 and count > 0:
            items.append({"item_id": item_id, "count": count})
    if not items:
        return None
    mail = {
        "expire": 0, "timestamp": int(time.time()), "message": "", "type": 1,
        "id": next_mail_id(state), "items": items, "sender": -1, "auto_open": False,
        "resource": {"clover_point": 0, "share_id": "", "ticket": 0, "reward_gacha": 0, "ads_id": ""},
        "title": "来自小伙伴的答谢", "pictures": [], "read": False,
    }
    get_mail_list(state).append(mail)
    return mail


def lottery_confirm_reward(state: dict, *, now: int | None = None) -> tuple[bool, str, dict | None]:
    now = int(time.time()) if now is None else now
    advance_weekend_lottery(state, now)
    data = get_lottery_data(state)
    if int(data.get("state", LOTTERY_OPEN) or 0) != LOTTERY_REWARD:
        return False, "bad_state", None
    mail = create_lottery_result_mail(state, _to_list(data.get("reward")))
    data["state"] = LOTTERY_OPEN
    data["answer"] = []
    data["right_flag"] = []
    data["reward"] = []
    data["egg_num"] = 0
    data["extra_item"] = {"item_id": 0, "count": 0}
    phase, _ = weekend_lottery_window(now)
    if phase > int(data["phase"]):
        data.update(phase=phase, last_phase=phase-1, select_list=[])
    ensure_lottery_select_list(state)
    cooking_record_task_progress(state, 7)
    save_state(state, f"lottery.confirm_reward:{int(data.get('phase',0) or 0)}")
    return True, "ok", mail


def parse_adsmgr_share_type(data: Any) -> int:
    """Decode ProtocolList's adsmgr.share request argument.

    The preserved client declares the field name as `ads_type`.  `type` and `id`
    are retained only as backward-compatible aliases for older local tooling.
    """
    if isinstance(data, dict):
        return int(data.get("ads_type", data.get("type", data.get("id", 0))) or 0)
    return int(data or 0)


def create_lottery_extra_mail(state: dict, item_id: int, count: int, *, reason: str = "claim") -> dict:
    """Create the mailbox delivery used by Lottery's ad/share extra reward.

    Client source shows adsmgr.share(type=3) only clears LotteryModel.extra_item
    locally and displays “叮咚~邮箱有动静”; it does not add the item to ItemModel.
    Therefore the offline server persists a normal system mail and inventory is
    credited only when the original client opens that mail via mail.open.

    Exact production mail title/type for this specific reward was not captured;
    type=1 (System) and the title below are offline-preservation presentation
    choices, not claimed production values.
    """
    item_id = int(item_id)
    count = int(count)
    if item_id <= 0 or count <= 0:
        raise ValueError("invalid lottery extra mail item")
    mail = {
        "expire": 0,
        "timestamp": int(time.time()),
        "message": "",
        "type": 1,
        "id": next_mail_id(state),
        "items": [{"item_id": item_id, "count": count}],
        "sender": -1,
        "auto_open": False,
        "resource": {
            "clover_point": 0,
            "share_id": "",
            "ticket": 0,
            "reward_gacha": 0,
            "ads_id": "",
        },
        "title": "周末小插曲的礼物",
        "pictures": [],
        "read": False,
    }
    get_mail_list(state).append(mail)
    logging.info(
        "LOTTERY EXTRA MAIL CREATED reason=%s mail_id=%d item=%dx%d",
        reason, int(mail["id"]), item_id, count,
    )
    return mail


def lottery_claim_extra(state: dict) -> tuple[bool, str, dict | None]:
    data = get_lottery_data(state)
    extra = data.get("extra_item") if isinstance(data.get("extra_item"), dict) else {}
    item_id = int(extra.get("item_id", 0) or 0)
    count = int(extra.get("count", 0) or 0)
    if item_id <= 0 or count <= 0:
        return True, "nothing_pending", None
    mail = create_lottery_extra_mail(state, item_id, count, reason="adsmgr.share:3")
    data["extra_item"] = {"item_id": 0, "count": 0}
    state["format_version"] = max(int(state.get("format_version", 2) or 2), 16)
    save_state(state, f"lottery.extra.mail:{item_id}:{count}")
    return True, "ok", mail


def create_photo_reward_mail(
    state: dict,
    *,
    title: str,
    pictures: list[int] | None = None,
    clover: int = 0,
) -> dict:
    mail = {
        "expire": 0,
        "timestamp": int(time.time()),
        "message": "",
        "type": 1,
        "id": next_mail_id(state),
        "items": [],
        "sender": -1,
        "auto_open": False,
        "resource": {
            "clover_point": max(0, int(clover)),
            "share_id": "",
            "ticket": 0,
            "reward_gacha": 0,
            "ads_id": "",
        },
        "title": title,
        "pictures": [int(value) for value in (pictures or [])],
        "read": False,
    }
    get_mail_list(state).append(mail)
    return mail


def claim_frog_back_picture_reward(state: dict) -> tuple[bool, str, dict | None]:
    meta = state.setdefault("offline_meta", {})
    batches = meta.setdefault("frog_back_picture_batches", [])
    album = get_new_album_data(state)
    pending_pictures = [
        row for row in _to_list(album.get("pictures"))
        if isinstance(row, dict) and bool(row.get("for_ads"))
    ]
    pending_ids = {int(row.get("id", -1)) for row in pending_pictures}
    batch = next((row for row in batches if isinstance(row, dict) and not bool(row.get("claimed"))), None)
    if batch is None and pending_ids:
        batch = {"picture_ids": sorted(pending_ids), "created_at": int(time.time()), "claimed": False}
        batches.append(batch)
    if batch is None:
        return True, "nothing_pending", None
    picture_ids = [int(value) for value in _to_list(batch.get("picture_ids")) if int(value) in pending_ids]
    if not picture_ids:
        batch["claimed"] = True
        return True, "nothing_pending", None
    mail = create_photo_reward_mail(state, title="分享奖励：照片", pictures=picture_ids)
    batch["claimed"] = True
    batch["claimed_at"] = int(time.time())
    batch["mail_id"] = int(mail["id"])
    album["has_ads"] = any(
        isinstance(row, dict) and not bool(row.get("claimed"))
        for row in batches
    )
    album["is_share"] = False
    save_state(state, f"frog_back.picture_reward:{mail['id']}")
    return True, "ok", mail


def claim_photo_share_reward(state: dict, picture_id: int) -> tuple[bool, str, dict | None]:
    rows = get_photo_share_data(state)["pic_list"]
    index = next((
        i for i, row in enumerate(rows)
        if isinstance(row, dict) and int(row.get("id", -1)) == int(picture_id)
    ), -1)
    if index < 0:
        return True, "already_claimed", None
    reward = rows.pop(index)
    clover = max(0, int(reward.get("clover", 0) or 0))
    mail = create_photo_reward_mail(state, title="分享奖励：三叶草", clover=clover)
    save_state(state, f"photo.share_reward:{picture_id}:{clover}")
    return True, "ok", mail

def schedule_next_guest(state: dict, now: int | None=None, policy: dict | None=None) -> int | None:
    if now is None:now=int(time.time())
    if policy is None:policy=load_offline_policy()
    vp=policy.get('visitor_schedule',{})
    if not bool(vp.get('enabled',True)):return None
    cool=max(0,int(vp.get('cooldown_seconds',21600) or 0)); interval=max(60,int(vp.get('check_interval_seconds',1800) or 1800)); pct=max(1,min(100,int(vp.get('appearance_percent_per_check',10) or 10))); max_checks=max(1,int(vp.get('max_checks',48) or 48))
    checks=1
    while checks<max_checks and deterministic_roll(state,f"visitor_wait:{checks}",100)>=pct: checks+=1
    due=now+cool+checks*interval
    state.setdefault('offline_meta',{})['next_guest_at']=due
    return due


def advance_guest_schedule(state: dict, now: int | None=None, policy: dict | None=None) -> bool:
    if now is None:now=int(time.time())
    if policy is None:policy=load_offline_policy()
    guest=find_push(state,'guest.load')
    if not guest:return False
    gd=guest.get('data') if isinstance(guest.get('data'),dict) else {'id':-1}; guest['data']=gd
    if int(gd.get('id',-1))>=0:return False
    if isinstance(state.setdefault('offline_meta',{}).get('pending_guest_departure'),dict):return False
    _,frog,_,_=get_role_parts(state)
    drawing,_=get_drawing_parts(state)
    if int(frog.get('status',0))!=FROG_HOME or int(drawing.get('state',0) or 0)!=DRAW_WAIT:return False
    meta=state.setdefault('offline_meta',{}); due=int(meta.get('next_guest_at',0) or 0)
    if due<=0:
        schedule_next_guest(state,now,policy); save_state(state,'visitor.schedule.initial'); return False
    if now<due:return False
    gid=deterministic_roll(state,'visitor_guest_id',3); pos=deterministic_roll(state,'visitor_pos',max(1,int(policy.get('visitor_schedule',{}).get('position_count',3) or 3)))
    stay_min=max(60,int(policy.get('visitor_schedule',{}).get('stay_min_seconds',7200) or 7200)); stay_max=max(stay_min,int(policy.get('visitor_schedule',{}).get('stay_max_seconds',21600) or 21600))
    stay=stay_min+deterministic_roll(state,'visitor_stay_seconds',stay_max-stay_min+1)
    guest['data']={'id':gid,'confirmed':False,'served':False,'expire_time':now+stay,'pos':pos}
    meta.pop('next_guest_at',None); meta['last_guest_id']=gid
    save_state(state,f"visitor.arrive:{gid}:{pos}")
    logging.info('VISITOR ARRIVE guest=%d pos=%d',gid,pos)
    return True

def schedule_unconfirmed_departure(state: dict, now: int | None = None, policy: dict | None = None) -> bool:
    """Optionally schedule the observed "left without pressing ready" path.

    Production behavior was observed by the user, but its exact 2026 server
    delay/probability is not present in the preserved client. Therefore this
    path exists only behind an explicit local-policy switch and is disabled by
    default.
    """
    if now is None:
        now = int(time.time())
    if policy is None:
        policy = load_offline_policy()
    tp = policy.get("travel", {})
    if not bool(tp.get("allow_unconfirmed_auto_departure", False)):
        return False
    _, frog, _, _ = get_role_parts(state)
    if int(frog.get("status", FROG_HOME)) != FROG_HOME:
        return False
    drawing, _ = get_drawing_parts(state)
    if int(drawing.get("state", DRAW_WAIT) or 0) in {DRAW_LOCK, DRAW_VISIT}:
        return False
    data, _, bag, _ = get_item_parts(state)
    if bool(data.get("bag_completed", False)):
        return False
    if not any(int(x) >= 0 and item_type(int(x)) == ITEM_LUNCHBOX for x in bag):
        return False
    meta = state.setdefault("offline_meta", {})
    act = meta.setdefault("activity", {})
    if act.get("pending_kind") in {"travel", "travel_auto", "party"} or act.get("kind"):
        return False
    delay = max(60, int(tp.get("unconfirmed_departure_seconds", 21600) or 21600))
    act.update({"pending_kind": "travel_auto", "depart_at": now + delay, "scheduled_at": now})
    save_state(state, "schedule.travel_auto")
    logging.info("TRAVEL AUTO SCHEDULED depart_at=%d delay=%d", now + delay, delay)
    return True


def schedule_normal_departure(state: dict, now: int | None = None) -> None:
    if now is None:
        now = int(time.time())
    _, frog, _, _ = get_role_parts(state)
    drawing, _ = get_drawing_parts(state)
    if int(frog.get("status", FROG_HOME)) != FROG_HOME:
        return
    if int(drawing.get("state", DRAW_WAIT) or 0) in {DRAW_LOCK, DRAW_VISIT}:
        return
    data, _, _, _ = get_item_parts(state)
    if not bool(data.get("bag_completed", False)):
        return
    meta = state.setdefault("offline_meta", {})
    # Stage 6C.1 migration safety: only an explicit local click of
    # item.set_bag_completed(true) arms a normal trip. Imported/stale
    # bag_completed=true must never launch the frog on login.
    if int(meta.get("travel_ready_armed_at", 0) or 0) <= 0:
        return
    act = meta.setdefault("activity", {})
    if act.get("pending_kind") != "travel":
        act["pending_kind"] = "travel"
        act["depart_at"] = now + DEPART_DELAY_SECONDS
        act["scheduled_at"] = now
        save_state(state, "schedule.travel.explicit")


def schedule_party_departure(state: dict, now: int | None = None) -> None:
    if now is None:
        now = int(time.time())
    _, frog, _, _ = get_role_parts(state)
    if int(frog.get("status", FROG_HOME)) != FROG_HOME:
        return
    meta = state.setdefault("offline_meta", {})
    act = meta.setdefault("activity", {})
    # Party has priority over an already prepared normal travel bag, matching
    # the official capture where bag_completed stayed true while status became 3.
    act["pending_kind"] = "party"
    act["depart_at"] = now + DEPART_DELAY_SECONDS
    act["scheduled_at"] = now
    save_state(state, "schedule.party")


def cancel_pending_activity(state: dict, kind: str | None = None) -> bool:
    meta = state.setdefault("offline_meta", {})
    act = meta.setdefault("activity", {})
    current = act.get("pending_kind")
    if current is None:
        return False
    if kind is not None and current != kind:
        return False
    act.pop("pending_kind", None)
    act.pop("depart_at", None)
    act.pop("scheduled_at", None)
    return True


def advance_activity_state(state: dict, now: int | None = None, policy: dict | None=None) -> bool:
    if now is None:
        now = int(time.time())
    if policy is None:
        policy=load_offline_policy()
    _, frog, _, _ = get_role_parts(state)
    if int(frog.get("status", FROG_HOME)) != FROG_HOME:
        return False
    meta = state.setdefault("offline_meta", {})
    act = meta.setdefault("activity", {})
    kind = act.get("pending_kind")
    due = int(act.get("depart_at", 0) or 0)
    if not kind or due <= 0 or now < due:
        return False

    if kind == "party":
        drawing, _ = get_drawing_parts(state)
        if int(drawing.get("state", DRAW_WAIT) or 0) != DRAW_LOCK:
            cancel_pending_activity(state, "party")
            save_state(state, "cancel.party.invalid_state")
            return False
        frog["status"] = FROG_PARTY
        frog["motion"] = -1
        drawing["state"] = DRAW_VISIT
        guest = find_push(state, "guest.load")
        if guest:
            guest["data"] = {"id": -1}
        guest_id = int(drawing.get("guest", 0) or 0)
        party_policy = policy.get("party", {}) if isinstance(policy, dict) else {}
        duration = max(60, int(party_policy.get("duration_seconds", 1800) or 1800))
        act.clear(); act.update({
            "kind": "party", "started_at": now, "return_at": now + duration,
            "guest_id": guest_id, "bag_snapshot": [int(x) for x in _to_list(drawing.get("bag"))[:4]],
        })
        state["format_version"] = max(int(state.get("format_version", 2) or 2), 14)
        save_state(state, "activity.party_depart")
        logging.info("ACTIVITY PARTY AWAY status=3 drawing.state=4")
        return True

    if kind in {"travel", "travel_auto"}:
        item_data, _, bag, _ = get_item_parts(state)
        if kind == "travel" and not bool(item_data.get("bag_completed", False)):
            cancel_pending_activity(state, "travel")
            save_state(state, "cancel.travel.not_ready")
            return False
        if kind == "travel_auto" and not bool(policy.get("travel", {}).get("allow_unconfirmed_auto_departure", False)):
            cancel_pending_activity(state, "travel_auto")
            save_state(state, "cancel.travel_auto.disabled")
            return False
        bag_snapshot=[int(x) for x in bag]
        if not any(x>=0 and item_type(x)==ITEM_LUNCHBOX for x in bag_snapshot):
            cancel_pending_activity(state,"travel")
            save_state(state,"cancel.travel.no_food")
            logging.warning("TRAVEL BLOCK: prepared bag has no lunchbox")
            return False
        plan=prepare_travel_plan(state,bag_snapshot,now,policy)
        # Consumables disappear from the away snapshot; durable amulets/tools
        # remain in the bag and are returned to the house on homecoming.
        for i,iid in enumerate(list(bag)):
            iid=int(iid)
            if iid>=0 and item_spend(iid): bag[i]=-1
        frog["status"] = FROG_TRAVEL; frog["motion"] = -1
        item_data["bag_completed"] = False
        meta.pop("travel_ready_armed_at", None)
        act.clear(); act.update({"kind":"travel","started_at":now,"plan":plan})

        # Official client shows the departure toast from TimerEvent.GoTravel (1),
        # not merely from frog.status becoming TRAVEL.  Stage 6I.1 changed the
        # role state but forgot this presentation event, so the room went dark
        # without "{frog}出去旅行了".  Put the new event first so it is shown
        # immediately on the login that performs the departure.
        go_event = {
            "id": next_event_id(state),
            "evt_type": 1,
            "evt_id": int(plan.get("place", 0) or 0),
            "evt_value": [0, 0],
            "evt_string": [],
            "evt_pic": [],
        }
        get_event_list(state).insert(0, go_event)

        state["format_version"] = max(int(state.get("format_version", 2) or 2), 15)
        save_state(state, "activity.travel_depart")
        logging.info(
            "ACTIVITY TRAVEL AWAY status=1 place=%s return_at=%s event_id=%s plan=%s",
            plan.get('place'), plan.get('return_at'), go_event["id"], plan
        )
        return True

    cancel_pending_activity(state)
    save_state(state, "cancel.activity.unknown")
    return False


def apply_drawing_accept(state: dict, accepted: bool) -> None:
    drawing, _ = get_drawing_parts(state)
    drawing["state"] = DRAW_ACCEPT if accepted else DRAW_WAIT
    if not accepted:
        cancel_pending_activity(state, "party")
        schedule_next_guest(state)
    state["format_version"] = max(int(state.get("format_version", 2) or 2), 8)
    save_state(state, f"guest.accept_invit:{accepted}")


def apply_drawing_item(
    state: dict, *, action: str, pos: int, item_id: int | None = None
) -> tuple[bool, str, int | None, int | None]:
    drawing, dbag = get_drawing_parts(state)
    _, house, _, _ = get_item_parts(state)
    if int(drawing.get("state", DRAW_WAIT) or 0) != DRAW_ACCEPT:
        return False, "drawing_not_accept", None, None
    idx = int(pos) - 1
    if idx < 0 or idx >= 4:
        return False, "bad_pos", None, None

    if action == "takeout":
        current = int(dbag[idx])
        if current < 0:
            return True, "already_empty", None, None
        new_count = get_house_count(house, current) + 1
        set_house_count(house, current, new_count)
        dbag[idx] = -1
        save_state(state, f"guest.takeout_bag:{pos}:{current}")
        return True, "ok", current, new_count

    if action == "putin":
        if item_id is None:
            return False, "missing_item", None, None
        item_id = int(item_id)
        if int(dbag[idx]) >= 0:
            return False, "slot_not_empty", None, None
        old_count = get_house_count(house, item_id)
        if old_count <= 0:
            return False, "not_in_house", item_id, old_count
        new_count = old_count - 1
        set_house_count(house, item_id, new_count)
        dbag[idx] = item_id
        save_state(state, f"guest.putin_bag:{pos}:{item_id}")
        return True, "ok", item_id, new_count

    return False, "bad_action", None, None


def toggle_drawing_lock(state: dict) -> tuple[bool, str, int]:
    drawing, _ = get_drawing_parts(state)
    cur = int(drawing.get("state", DRAW_WAIT) or 0)
    if cur == DRAW_ACCEPT:
        drawing["state"] = DRAW_LOCK
        save_state(state, "guest.lock_bag:lock")
        schedule_party_departure(state)
        return True, "locked", DRAW_LOCK
    if cur == DRAW_LOCK:
        drawing["state"] = DRAW_ACCEPT
        cancel_pending_activity(state, "party")
        save_state(state, "guest.lock_bag:unlock")
        # If a normal bag was already prepared, it becomes eligible again.
        schedule_normal_departure(state)
        return True, "unlocked", DRAW_ACCEPT
    return False, "bad_state", cur


def persist_moment_unlock(state: dict, moment_id: int) -> None:
    push = find_push(state, "misc.moment_load")
    if not push:
        raise RuntimeError("state 中找不到 misc.moment_load")
    data = push.setdefault("data", {})
    lst = data.setdefault("list", [])
    if not isinstance(lst, list):
        lst = []
        data["list"] = lst
    if moment_id not in lst:
        lst.append(moment_id)
        save_state(state, f"misc.moment_unlock:{moment_id}")


def apply_guest_serve(
    state: dict,
    guest_id: int,
    item_id: int,
    *,
    policy: dict | None = None,
) -> tuple[bool, int | None, dict[str, Any]]:
    guest = find_push(state, "guest.load")
    if not guest or not isinstance(guest.get("data"), dict):
        return False, None, {"reason": "no_guest"}
    g = guest["data"]
    if int(g.get("id", -1)) != int(guest_id):
        return False, None, {"reason": "guest_mismatch"}
    if bool(g.get("served", False)):
        return False, None, {"reason": "already_served"}

    try:
        guest_name, feeling = get_friend_taste(guest_id, item_id)
    except ValueError as exc:
        return False, None, {"reason": "invalid_specialty", "detail": str(exc)}

    _, house, _, _ = get_item_parts(state)
    old = get_house_count(house, item_id)
    if old <= 0:
        return False, old, {"reason": "not_in_house"}

    new = old - 1
    set_house_count(house, item_id, new)
    g["served"] = True

    outcome = resolve_guest_after_serving(
        state, guest_id, item_id, feeling, policy=policy
    )
    record = {
        "time": int(time.time()),
        "guest_id": int(guest_id),
        "guest_name": guest_name,
        "item_id": int(item_id),
        "feeling": int(feeling),
        "feedback_tier": taste_feedback_tier(feeling),
        "rare_specialty": is_rare_specialty(item_id),
        "outcome": copy.deepcopy(outcome),
    }
    history = state.setdefault("offline_meta", {}).setdefault("guest_serve_history", [])
    history.append(record)
    if len(history) > 500:
        del history[:-500]

    state["format_version"] = max(int(state.get("format_version", 2) or 2), 7)
    save_state(state, f"guest.serve:{guest_id}:{item_id}:{outcome.get('kind')}")
    return True, new, record


def apply_guest_confirm(state: dict, guest_id: int) -> bool:
    guest = find_push(state, "guest.load")
    if not guest or not isinstance(guest.get("data"), dict):
        return False
    g = guest["data"]
    if int(g.get("id", -1)) != int(guest_id):
        return False
    if not bool(g.get("confirmed", False)):
        g["confirmed"] = True
        save_state(state, f"guest.confirm:{guest_id}")
    return True


def apply_mail_open(state: dict, mail_id: int) -> tuple[bool, dict[str, Any]]:
    mails = get_mail_list(state)
    idx = next((i for i, m in enumerate(mails) if isinstance(m, dict) and int(m.get("id", -1)) == mail_id), -1)
    if idx < 0:
        return False, {}
    mail = mails.pop(idx)
    _, _, res, _ = get_role_parts(state)
    _, house, _, _ = get_item_parts(state)
    pushes: dict[str, Any] = {}
    resource = mail.get("resource") or {}
    if isinstance(resource, dict):
        dc = int(resource.get("clover_point", 0) or 0)
        dt = int(resource.get("ticket", 0) or 0)
        if dc:
            res["clover_point"] = int(res.get("clover_point", 0) or 0) + dc
            pushes["clover"] = res["clover_point"]
            task = calendar_record_clover_gain(state, dc)
            if task is not None:
                pushes["calendar_task"] = task
        if dt:
            res["ticket"] = int(res.get("ticket", 0) or 0) + dt
            pushes["ticket"] = res["ticket"]
    item_updates = []
    for row in _to_list(mail.get("items")):
        if not isinstance(row, dict):
            continue
        iid = int(row.get("item_id", -1))
        cnt = int(row.get("count", 0) or 0)
        if iid >= 0 and cnt:
            if iid == LOTTERY_CLOVER_ITEM_ID:
                res["clover_point"] = int(res.get("clover_point", 0) or 0) + cnt
                pushes["clover"] = res["clover_point"]
                task = calendar_record_clover_gain(state, cnt)
                if task is not None:
                    pushes["calendar_task"] = task
                continue
            if iid == 200001:
                res["ticket"] = int(res.get("ticket", 0) or 0) + cnt
                pushes["ticket"] = res["ticket"]
                continue
            new = get_house_count(house, iid) + cnt
            set_house_count(house, iid, new)
            item_updates.append((iid, new))
    pushes["items"] = item_updates
    save_state(state, f"mail.open:{mail_id}")
    return True, pushes


def apply_confirm_event(state: dict, event_id: int) -> bool:
    events = get_event_list(state)
    matched = next((copy.deepcopy(e) for e in events if isinstance(e, dict) and int(e.get("id", -1)) == event_id), None)
    before = len(events)
    events[:] = [e for e in events if not (isinstance(e, dict) and int(e.get("id", -1)) == event_id)]
    if len(events) != before:
        if matched is not None:
            # VisitFriend postcards are first previewed in evt_pic.  The original
            # client then waits for album.load_new.visted_pic after confirmation.
            if int(matched.get("evt_type", -1)) == 16:
                pics = [copy.deepcopy(x) for x in _to_list(matched.get("evt_pic")) if isinstance(x, dict)]
                if pics:
                    new_album = get_new_album_data(state)
                    normal = _to_list(new_album.get("pictures"))
                    visited = _to_list(new_album.get("visted_pic"))
                    known = {int(x.get("id", -1)) for x in normal + visited if isinstance(x, dict)}
                    known.update(int(x.get("id", -1)) for x in get_album_index(state) if isinstance(x, dict))
                    for pic in pics:
                        pid = int(pic.get("id", -1))
                        if pid >= 0 and pid not in known:
                            pic["visit"] = True
                            visited.append(pic); known.add(pid)
                    new_album["pictures"] = normal
                    new_album["visted_pic"] = visited
                    new_album.setdefault("has_ads", False); new_album.setdefault("is_share", False)
            history = state.setdefault("offline_meta", {}).setdefault("travel_event_history", [])
            if not any(isinstance(x, dict) and int(x.get("id", -1)) == int(event_id) for x in history):
                history.append(matched)
        save_state(state, f"client.confirm_event:{event_id}")
        return True
    return False


def apply_new_album_action(state: dict, picture_id: int, save: bool) -> bool:
    data = get_new_album_data(state)
    pictures = _to_list(data.get("pictures"))
    visited = _to_list(data.get("visted_pic"))
    target = None
    source = None
    for seq in (pictures, visited):
        for i, pic in enumerate(seq):
            if isinstance(pic, dict) and int(pic.get("id", -1)) == picture_id:
                target = seq.pop(i)
                source = seq
                break
        if target is not None:
            break
    if target is None:
        # Idempotent: official client may retry after local UI already removed it.
        return True
    data["pictures"] = pictures
    data["visted_pic"] = visited
    unclaimed_bonus_ids = {
        int(value)
        for batch in _to_list(state.setdefault("offline_meta", {}).get("frog_back_picture_batches"))
        if isinstance(batch, dict) and not bool(batch.get("claimed"))
        for value in _to_list(batch.get("picture_ids"))
    }
    data["has_ads"] = any(
        isinstance(row, dict) and bool(row.get("for_ads")) and int(row.get("id", -1)) in unclaimed_bonus_ids
        for row in pictures
    )
    if save:
        album = find_push(state, "album.load_all")
        if album:
            adata = album.setdefault("data", {})
            ids = adata.setdefault("id_list", [])
            if isinstance(ids, list) and not any(isinstance(x, dict) and int(x.get("id", -1)) == picture_id for x in ids):
                ids.append({"id": picture_id, "pic_id": int(target.get("pic_id", 0) or 0)})
        _, _, _, misc = get_role_parts(state)
        misc["picture_cnt"] = len(get_album_index(state))
        state.setdefault("offline_meta", {}).setdefault("album_full", {})[str(picture_id)] = target
    else:
        # Returned-but-not-saved pictures share the same six-slot recycle bin
        # as pictures explicitly deleted from the saved album.
        push_album_recover(state, target)
        # Preserve the full record only in the recycle entry, not the active album cache.
        state.setdefault("offline_meta", {}).setdefault("album_full", {}).pop(str(picture_id), None)
        share_push = find_push(state, "share.load")
        if share_push and isinstance(share_push.get("data"), dict):
            rewards = _to_list(share_push["data"].get("pic_list"))
            share_push["data"]["pic_list"] = [
                row for row in rewards
                if not (isinstance(row, dict) and int(row.get("id", -1)) == picture_id)
            ]
    save_state(state, f"album.{'save' if save else 'delete'}_new:{picture_id}")
    return True


def persist_client_setting(state: dict, cmd: str, data: Any) -> bool:
    role_push = find_push(state, "client.load_role")
    if not role_push:
        logging.error("无法写入 %s：state 中找不到 client.load_role", cmd)
        return False

    role = role_push.setdefault("data", {})
    frog = role.setdefault("frog", {})
    settings = role.setdefault("settings", {})

    if cmd == "client.set_client":
        if not isinstance(data, dict) or "client" not in data:
            logging.warning("client.set_client 缺少 data.client")
            return False
        new_value = data["client"]
        if settings.get("client") == new_value:
            logging.info("client.set_client 与当前存档一致，无需写盘")
            return True
        settings["client"] = new_value
        save_state(state, "client.set_client")
        logging.info("已持久化 client.set_client")
        return True

    if cmd == "client.set_achieve":
        if not isinstance(data, dict) or "id" not in data:
            return False
        if frog.get("cur_achieve") != data["id"]:
            frog["cur_achieve"] = data["id"]
            save_state(state, "client.set_achieve")
        logging.info("已持久化 client.set_achieve")
        return True

    if cmd == "client.set_name":
        if not isinstance(data, dict) or not isinstance(data.get("name"), str):
            return False
        if frog.get("name") != data["name"]:
            frog["name"] = data["name"]
            save_state(state, "client.set_name")
        logging.info("已持久化 client.set_name")
        return True

    if cmd == "client.set_icon":
        if not isinstance(data, dict) or "id" not in data:
            return False
        if frog.get("icon") != data["id"]:
            frog["icon"] = data["id"]
            save_state(state, "client.set_icon")
        logging.info("已持久化 client.set_icon")
        return True

    return False


def append_unknown(req: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    safe = {
        "time": int(time.time()),
        "cmd": req.get("cmd"),
        "session": req.get("session"),
        "data": req.get("data"),
    }
    with UNKNOWN_LOG.open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(safe, ensure_ascii=False, separators=(",", ":")) + "\n")


async def send_json(ws: Any, payload: dict) -> None:
    if GALLERY_MODE and payload.get("cmd") == "clover.update":
        payload = copy.deepcopy(payload)
        payload["data"]["clover"] = 9999
    elif GALLERY_MODE and payload.get("cmd") == "item.update_ticket":
        payload = copy.deepcopy(payload)
        payload["data"]["ticket"] = 999
    elif GALLERY_MODE and payload.get("cmd") == "item.update":
        item = payload.get("data", {}).get("item", {})
        if int(item.get("item_id", -1)) in gallery_repeatable_item_ids():
            payload = copy.deepcopy(payload)
            payload["data"]["item"]["count"] = 99
    await ws.send(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


async def send_response(ws: Any, session: Any, data: Any) -> None:
    if session is not None:
        await send_json(ws, {"session": session, "data": data})



def migrate_stage6c1_state(state: dict, now: int | None = None) -> bool:
    """One-time repair for Stage 6C's stale bag_completed auto-depart bug."""
    if now is None:
        now = int(time.time())
    meta = state.setdefault("offline_meta", {})
    if meta.get("stage6c1_migrated"):
        return False
    changed = False
    act = meta.setdefault("activity", {})
    item_data, _, bag, _ = get_item_parts(state)
    _, frog, _, _ = get_role_parts(state)

    if act.get("kind") == "travel" and isinstance(act.get("plan"), dict):
        snap = act["plan"].get("bag_snapshot")
        if isinstance(snap, list) and len(snap) >= 4:
            bag[:] = [int(x) for x in snap[:len(bag)]]
            frog["status"] = FROG_HOME
            frog["motion"] = select_home_frog_motion(state, now)
            item_data["bag_completed"] = False
            act.clear()
            changed = True
            logging.warning("STAGE6C1 REPAIR: restored accidental Stage6C travel to HOME; bag restored")
    elif act.get("pending_kind") in {"travel", "travel_auto"}:
        act.clear()
        item_data["bag_completed"] = False
        changed = True
        logging.warning("STAGE6C1 REPAIR: cancelled stale pre-6C.1 pending travel")
    elif int(frog.get("status", FROG_HOME)) == FROG_HOME and bool(item_data.get("bag_completed", False)):
        # Imported readiness is display state, not proof of a local arm action.
        item_data["bag_completed"] = False
        changed = True
        logging.warning("STAGE6C1 REPAIR: cleared stale imported bag_completed=true")

    meta.pop("travel_ready_armed_at", None)
    # Initialize the net cursor at migration time: no retroactive windfall.
    try:
        _, field = get_clover_parts(state)
        cursors = meta.setdefault("clover_net", {}).setdefault("cursor", {})
        for row in field:
            if isinstance(row, dict):
                cursors.setdefault(str(int(row.get("clover_id", -1))), int(now))
    except Exception:
        logging.exception("STAGE6C1: initialize clover net cursors failed")
    meta["stage6c1_migrated"] = int(now)
    state["format_version"] = max(int(state.get("format_version", 2) or 2), 10)
    save_state(state, "migrate.stage6c1")
    return changed



def migrate_stage6d1_orphaned_remote_activity(state: dict, now: int | None = None) -> bool:
    """Repair imported official-server away states that have no local plan.

    A preserved production snapshot may legitimately have frog.status == 1 (travel)
    or == 3 (party).  The local server cannot finish such a remote activity because
    there is no locally generated activity plan/return timestamp.  Leaving it as-is
    strands the frog away forever.

    This migration is deliberately conservative: if a local activity or pending
    activity exists, nothing is changed.  Only an orphaned imported away state is
    normalized back to HOME.
    """
    if now is None:
        now = int(time.time())
    meta = state.setdefault('offline_meta', {})
    if meta.get('stage6d1_orphan_activity_migrated'):
        return False

    act = meta.setdefault('activity', {})
    local_owned = bool(act.get('kind') or act.get('pending_kind') or isinstance(act.get('plan'), dict))
    item_data, house, bag, _desk = get_item_parts(state)
    _, frog, _, _ = get_role_parts(state)
    status = int(frog.get('status', FROG_HOME) or FROG_HOME)
    changed = False

    if not local_owned and status == FROG_TRAVEL:
        # Match the official return shape we captured: durable items still present
        # in the away bag are returned to the house, then the desk auto-prepares
        # the next bag.  Consumed food/amulets are already absent from the away bag
        # and therefore are not recreated.
        returned = []
        for iid in list(bag):
            try:
                iid = int(iid)
            except Exception:
                iid = -1
            if iid >= 0:
                set_house_count(house, iid, get_house_count(house, iid) + 1)
                returned.append(iid)
        bag[:] = [-1 for _ in bag]
        frog['status'] = FROG_HOME
        frog['motion'] = select_home_frog_motion(state, now)
        item_data['bag_completed'] = False
        meta.pop('travel_ready_armed_at', None)
        act.clear()
        auto_prepare_from_desk(state)
        meta['imported_remote_activity_repair'] = {
            'time': int(now), 'from_status': FROG_TRAVEL, 'returned_bag_items': returned
        }
        changed = True
        logging.warning(
            'STAGE6D1 REPAIR: orphan imported TRAVEL -> HOME; returned_bag_items=%s; desk auto-prepared',
            returned,
        )

    elif not local_owned and status == FROG_PARTY:
        # Preserve friendship pages/colls, but end the unfinishable imported visit.
        # Party hand-gifts were already consumed when the official visit was locked,
        # so clear that transient four-slot drawing bag rather than refunding it.
        drawing, dbag = get_drawing_parts(state)
        drawing['state'] = DRAW_WAIT
        drawing['guest'] = -1
        dbag[:] = [-1 for _ in dbag]
        frog['status'] = FROG_HOME
        frog['motion'] = select_home_frog_motion(state, now)
        item_data['bag_completed'] = False
        meta.pop('travel_ready_armed_at', None)
        act.clear()
        meta['imported_remote_activity_repair'] = {
            'time': int(now), 'from_status': FROG_PARTY
        }
        changed = True
        logging.warning('STAGE6D1 REPAIR: orphan imported PARTY -> HOME; drawing visit reset')

    meta['stage6d1_orphan_activity_migrated'] = int(now)
    state['format_version'] = max(int(state.get('format_version', 2) or 2), 12)
    save_state(state, 'migrate.stage6d1.orphan_activity')
    return changed


# ---------------------------------------------------------------------------
# Stage 6D: courtyard garden / compost / pocket / vase preservation engine
# ---------------------------------------------------------------------------

GARDEN_SEEDS = tuple(range(20101, 20112))
GARDEN_FERTILIZERS = tuple(range(20001, 20007))
GARDEN_FLOWER_FAMILIES = {1, 2, 4, 5, 6, 11}
GARDEN_CROP_REWARD = {3: 4101, 7: 4102, 8: 4103, 9: 4006, 10: 4104}
FURNITURE_FEATURE_OPEN_AT = 1640995200
FURNITURE_TOOL_SLOTS = 5
FURNITURE_BENCH_SLOTS = 10
FURNITURE_ORDINARY_MATERIAL_IDS = tuple(range(10001, 10008))
# Main material IDs are Item.json data. Their matching furniture styles are
# the published in-game furniture guide: 27 pieces per theme, with red paper,
# palm leaves and ribbons choosing one eligible theme at random.
FURNITURE_MATERIAL_STYLES = {
    10101: (2,),       # 岩纹石 → 石作
    10102: (3, 10, 12),# 正丹纸 → 壬寅、癸卯、甲辰迎春
    10103: (4, 13),   # 棕榈叶 → 雨林、夏凉
    10104: (5,),       # 海螺贝 → 海洋
    10105: (6,),       # 紫檀木 → 博古
    10106: (7,),       # 细篷布 → 露营
    10107: (8,),       # 秋芦荻 → 秋收
    10108: (9, 11, 14), # 织彩带 → 欢聚、田园、彩陶庆典
}

# The three component drawings and their finished objects are from Item.json,
# pocketData_json.json and compostData_json.json.  Tumbler output IDs are the
# complete tumblerData_json catalogue.  Plant fruit alone makes the original
# four forms; the four documented paired materials unlock their themed forms.
FURNITURE_SPECIAL_MATERIAL_RECIPES = {
    11001: {
        "drawing_id": 10401, "kind": "tumbler",
        "variants": {
            # 胡桃、尖栗、兰栗、橡子；每种均有 3 个正式成品变体。
            0: tuple(range(20011, 20014)) + tuple(range(20021, 20024))
               + tuple(range(20031, 20034)) + tuple(range(20041, 20044)),
            10101: tuple(range(20111, 20114)),  # 岩纹石：石头不倒
            10105: tuple(range(20121, 20124)),  # 紫檀木：蹴鞠不倒
            10104: tuple(range(20151, 20154)) + tuple(range(20161, 20164)),  # 海螺贝
            10107: tuple(range(20131, 20134)) + tuple(range(20141, 20144))
               + tuple(range(20171, 20174)) + tuple(range(20181, 20184)),  # 秋芦荻
        },
    },
    11101: {
        "drawing_id": 10501, "kind": "compost",
        "variants": {
            0: tuple(range(21001, 21005)) + tuple(range(21011, 21015)) + tuple(range(21021, 21025)),
            10101: (21031,),  # 岩纹石堆肥箱
            10106: (21041,),  # 细篷布堆肥箱
            10102: (21051,),  # 正丹纸堆肥箱
        },
    },
    11102: {
        "drawing_id": 10601, "kind": "pocket",
        "variants": {
            0: (22011, 22012, 22013),
            10103: (22021,),  # 棕榈叶挂兜
            10104: (22031,),  # 海螺贝挂兜
            10102: (22041,),  # 正丹纸挂兜
        },
    },
}
FURNITURE_SPECIAL_MATERIAL_IDS = frozenset(FURNITURE_SPECIAL_MATERIAL_RECIPES)


def _cfg_dict(name: str) -> dict:
    obj = load_static_json(name)
    return obj if isinstance(obj, dict) else {}


def _item_rows() -> list[dict]:
    obj = load_static_json("Item_json.json")
    if isinstance(obj, list):
        return [x for x in obj if isinstance(x, dict)]
    if isinstance(obj, dict):
        return [x for x in obj.values() if isinstance(x, dict)]
    return []


def item_cfg(item_id: int) -> dict | None:
    for row in _item_rows():
        if isinstance(row, dict) and int(row.get("id", -1)) == int(item_id):
            return row
    return None


_GALLERY_REPEATABLE_IDS: set[int] | None = None


def gallery_repeatable_item_ids() -> set[int]:
    global _GALLERY_REPEATABLE_IDS
    if _GALLERY_REPEATABLE_IDS is None:
        _GALLERY_REPEATABLE_IDS = {
            int(row["id"]) for row in _item_rows()
            if int(row.get("spend", 0) or 0) == 1
            and int(row.get("id", -1)) >= 0
            and int(row["id"]) not in HANDCRAFT_FRAGMENT_IDS
            and not (
                int(row.get("type", -1)) == 14
                and int(row.get("sub_type", 0) or 0) in {1, 2, 3, 4, 5}
            )
        }
    return _GALLERY_REPEATABLE_IDS


def normalize_gallery_resources(state: dict) -> None:
    _, _, res, _ = get_role_parts(state)
    res["clover_point"] = 9999
    res["ticket"] = 999
    _, house, _, _ = get_item_parts(state)
    for item_id in gallery_repeatable_item_ids():
        set_house_count(house, item_id, 99)
    decorations = get_decoration_parts(state)["has_list"]
    by_id = {int(row["id"]): row for row in decorations if isinstance(row, dict)}
    for decoration_id in GALLERY_DECORATION_IDS:
        if decoration_id in by_id:
            by_id[decoration_id]["num"] = 99
        else:
            decorations.append({"id": decoration_id, "num": 99})
    cake = state.setdefault("offline_meta", {}).get("party_cake")
    if isinstance(cake, dict):
        cake.update({"cream": 99, "sugar": 99, "pre_cream": 0, "pre_sugar": 0})


def complete_gallery_guide_tasks(state: dict) -> bool:
    """Mark every GuideTask entry complete and its reward claimed in gallery mode."""
    _, task_rows, progress_rows, reward_rows = get_guide_task_parts(state)
    config = load_task_static_data()
    task_list = config.get("task_list", {})
    list_map = config.get("list_map", {})
    list_type = config.get("list_type", {})
    changed = False

    for row in task_rows:
        definition = task_list.get(str(int(row.get("id", -1))))
        if not isinstance(definition, dict):
            continue
        target = int(definition.get("count", 0) or 0)
        if int(row.get("pro", 0) or 0) != target:
            row["pro"] = target
            changed = True
        if not bool(row.get("is_reward", False)):
            row["is_reward"] = True
            changed = True

    for row in progress_rows:
        definition = list_map.get(str(int(row.get("id", -1))))
        if not isinstance(definition, dict):
            continue
        target = int(definition.get("count", 0) or 0)
        if int(row.get("pro", 0) or 0) != target:
            row["pro"] = target
            changed = True

    by_type = {int(row.get("id", -1)): row for row in reward_rows if isinstance(row, dict)}
    for type_id, definition in list_type.items():
        if not isinstance(definition, dict):
            continue
        task_type = int(type_id)
        complete_count = len(definition.get("reward", []))
        row = by_type.get(task_type)
        if row is None:
            reward_rows.append({"id": task_type, "pro": complete_count})
            changed = True
        elif int(row.get("pro", 0) or 0) != complete_count:
            row["pro"] = complete_count
            changed = True
    return changed


def complete_gallery_moments(state: dict) -> bool:
    """Fill the official Beautiful Moments collection for gallery mode."""
    push = find_push(state, "misc.moment_load")
    if push is None:
        push = {"cmd": "misc.moment_load", "data": {"list": []}}
        state.setdefault("sync_pushes", []).append(push)
    data = push.setdefault("data", {})
    if not isinstance(data, dict):
        data = {}
        push["data"] = data
    moments = data.get("list")
    if not isinstance(moments, list):
        moments = []
        data["list"] = moments
    merged = sorted({int(value) for value in moments if isinstance(value, (int, float, str))} | set(GALLERY_MOMENT_IDS))
    if moments == merged:
        return False
    data["list"] = merged
    return True


def get_furniture_data(state: dict, cmd: str, default: dict) -> dict:
    push = find_push(state, cmd)
    if not push:
        push = {"cmd": cmd, "data": copy.deepcopy(default)}
        state.setdefault("sync_pushes", []).append(push)
    data = push.get("data")
    if not isinstance(data, dict):
        data = copy.deepcopy(default)
        push["data"] = data
    return data


def wishing_pool_policy(policy: dict | None = None) -> dict:
    if policy is None:
        policy = load_offline_policy()
    value = policy.get("wishing_pool", {}) if isinstance(policy, dict) else {}
    return value if isinstance(value, dict) else {}


def get_wishing_pool_state(state: dict, policy: dict | None = None) -> dict:
    """Return the permanent local Wishing Pool payload used by the client."""
    wp = wishing_pool_policy(policy)
    push = find_push(state, "wishingpool.load")
    if push is None:
        push = {"cmd": "wishingpool.load", "data": {}}
        state.setdefault("sync_pushes", []).append(push)
    data = push.setdefault("data", {})
    if not isinstance(data, dict):
        data = {}
        push["data"] = data

    meta = state.setdefault("offline_meta", {}).setdefault("wishing_pool", {})
    now = int(time.time())
    if not bool(meta.get("initialized", False)):
        data["coin"] = max(0, int(wp.get("starter_coins", 4) or 4))
        configured = wp.get("items", [])
        data["items"] = copy.deepcopy(configured if isinstance(configured, list) else [])
        meta["initialized"] = True
    if not isinstance(data.get("items"), list):
        data["items"] = []
    data["coin"] = max(0, int(data.get("coin", 0) or 0))
    data["end_time"] = always_on_end_time(int(data.get("end_time", 0) or 0), now) if bool(wp.get("enabled", True)) else 0
    return data


def _grant_wishing_pool_reward(state: dict, item_id: int, *, duplicate_refund: int = 100) -> int | None:
    """Apply the actual collection grant behind a Wishing Pool result."""
    furniture = _furniture_production_data(state)
    owned = furniture["has_fur"]
    if item_id in WISHING_POOL_FURNITURE_TARGETS:
        furniture_id = WISHING_POOL_FURNITURE_TARGETS[item_id]
        if furniture_id not in owned:
            owned.append(furniture_id)
        return furniture_id
    if item_id == 204002:
        candidates = [value for value in WISHING_POOL_GIFT_BAG_TARGETS if value not in owned]
        if not candidates:
            _, _, res, _ = get_role_parts(state)
            res["clover_point"] = 9999 if GALLERY_MODE else int(res.get("clover_point", 0) or 0) + duplicate_refund
            return
        furniture_id = candidates[deterministic_roll(state, "wishing_pool:gift_bag", len(candidates))]
        if furniture_id not in owned:
            owned.append(furniture_id)
        return furniture_id
    _, house, _, _ = get_item_parts(state)
    set_house_count(house, item_id, get_house_count(house, item_id) + 1)


def apply_wishing_pool_wish(state: dict, policy: dict | None = None) -> tuple[bool, int]:
    data = get_wishing_pool_state(state, policy)
    if int(data.get("end_time", 0) or 0) <= int(time.time()):
        return False, 0
    if int(data.get("coin", 0) or 0) <= 0:
        return False, 0
    available = [
        row for row in data["items"]
        if isinstance(row, dict) and int(row.get("id", 0) or 0) > 0 and int(row.get("limit", 0) or 0) > 0
    ]
    if not available:
        return False, 0
    row = available[deterministic_roll(state, "wishing_pool:wish", len(available))]
    item_id = int(row["id"])
    row["limit"] = int(row.get("limit", 0) or 0) - 1
    data["coin"] = int(data["coin"]) - 1
    _grant_wishing_pool_reward(state, item_id)
    save_state(state, f"wishingpool.wish:{item_id}")
    return True, item_id


def furniture_policy(policy: dict | None = None) -> dict:
    if policy is None:
        policy = load_offline_policy()
    value = policy.get("furniture", {}) if isinstance(policy, dict) else {}
    return value if isinstance(value, dict) else {}


def furniture_job_pending(state: dict) -> bool:
    value = state.setdefault("offline_meta", {}).setdefault("furniture", {}).get("job")
    return isinstance(value, dict)


def _furniture_job(state: dict) -> dict | None:
    value = state.setdefault("offline_meta", {}).setdefault("furniture", {}).get("job")
    return value if isinstance(value, dict) else None


def _normalize_furniture_bench(data: dict) -> list[int]:
    bench = data.get("bench")
    if not isinstance(bench, list):
        bench = []
        data["bench"] = bench
    while len(bench) < FURNITURE_BENCH_SLOTS:
        bench.append(-1)
    if len(bench) > FURNITURE_BENCH_SLOTS:
        del bench[FURNITURE_BENCH_SLOTS:]
    for index, value in enumerate(bench):
        try:
            bench[index] = int(value)
        except (TypeError, ValueError):
            bench[index] = -1
    return bench


def _furniture_production_data(state: dict) -> dict:
    data = get_furniture_data(state, "furniture.load_furniture", {
        "shop": {"start_time": 0, "leave_time": 0, "shop_list": []},
        "replace_fur": [], "put_fur": [], "bench_lock": False,
        "bench": [-1] * FURNITURE_BENCH_SLOTS, "mate_list": [], "has_fur": [],
    })
    _normalize_furniture_bench(data)
    if not isinstance(data.get("has_fur"), list):
        data["has_fur"] = []
    if not isinstance(data.get("mate_list"), list):
        data["mate_list"] = []
    return data


def furniture_production_motion(state: dict, now: int | None = None) -> int | None:
    if now is None:
        now = int(time.time())
    job = _furniture_job(state)
    if job is None or int(job.get("ready_at", 0) or 0) <= now:
        return None
    motion = int(job.get("motion", 5) or 5)
    return motion if 5 <= motion <= 9 else 5


def _furniture_meta(state: dict) -> dict:
    return state.setdefault("offline_meta", {}).setdefault("furniture", {})


def _furniture_random_seconds(
    state: dict,
    policy: dict,
    label: str,
    min_key: str,
    max_key: str,
    default_min: int,
    default_max: int,
) -> int:
    lower = max(60, int(policy.get(min_key, default_min) or default_min))
    upper = max(lower, int(policy.get(max_key, default_max) or default_max))
    return lower + deterministic_roll(state, label, upper - lower + 1)


def _furniture_queued_job_is_valid(state: dict, bench: list[int], queued: dict) -> bool:
    try:
        tool_index = int(queued["tool_index"])
        tool_id = int(queued["tool_id"])
        drawing_id = int(queued["drawing_id"])
        material_slots = [int(value) for value in queued.get("material_slots", [])]
        material_ids = [int(value) for value in queued.get("material_ids", [])]
        resources = [int(value) for value in queued.get("reserved_resources", [])]
    except (KeyError, TypeError, ValueError):
        return False
    if not (0 <= tool_index < FURNITURE_TOOL_SLOTS) or bench[tool_index] != tool_id:
        return False
    if len(material_slots) != len(material_ids):
        return False
    if any(
        slot < FURNITURE_TOOL_SLOTS or slot >= FURNITURE_BENCH_SLOTS or bench[slot] != item_id
        for slot, item_id in zip(material_slots, material_ids)
    ):
        return False
    _, house, _, _ = get_item_parts(state)
    if get_house_count(house, drawing_id) <= 0:
        return False
    return all(get_house_count(house, item_id) >= resources.count(item_id) for item_id in set(resources))


def furniture_workbench_is_messy(state: dict) -> bool:
    return bool(_furniture_meta(state).get("workbench_mess", False))


def _owned_furniture_of_type(data: dict, furniture_type: int) -> list[int]:
    owned = {int(value) for value in data["has_fur"] if isinstance(value, (int, float, str))}
    candidates = []
    for key, row in _cfg_dict("furnitureData_json.json").items():
        if not isinstance(row, dict):
            continue
        try:
            furniture_id = int(row.get("id", key))
            item_type = int(row.get("type", -1) or -1)
        except (TypeError, ValueError):
            continue
        if item_type == furniture_type and furniture_id in owned:
            candidates.append(furniture_id)
    return sorted(candidates)


def _replace_home_furniture(data: dict, furniture_type: int, furniture_id: int) -> bool:
    put = data.get("put_fur")
    if not isinstance(put, list):
        put = _to_list(put)
        data["put_fur"] = put
    current = next((int(row.get("id", -1)) for row in put if isinstance(row, dict) and int(row.get("type", -1)) == furniture_type), -1)
    if current == furniture_id:
        return False
    put[:] = [row for row in put if not (isinstance(row, dict) and int(row.get("type", -1)) == furniture_type)]
    put.append({"type": furniture_type, "id": furniture_id})
    return True


def note_manual_furniture_replace(state: dict, data: dict, policy: dict | None = None) -> bool:
    """Track repeated away-from-home redecorating and possibly mess the bench."""
    fp = furniture_policy(policy)
    meta = _furniture_meta(state)
    if furniture_workbench_is_messy(state):
        return False
    count = int(meta.get("manual_replace_count", 0) or 0) + 1
    meta["manual_replace_count"] = count
    threshold = max(1, int(fp.get("manual_replace_mess_after", 12) or 12))
    chance = max(0, min(100, int(fp.get("manual_replace_mess_percent", 35) or 0)))
    if count < threshold or deterministic_roll(state, f"furniture:workbench_mess:{count}", 100) >= chance:
        return False
    meta["workbench_mess"] = True
    data["bench_lock"] = True
    return True


def recover_furniture_workbench(state: dict, policy: dict | None = None) -> bool:
    """Travel and finished handcrafts calm the frog after a workbench mess."""
    fp = furniture_policy(policy)
    meta = _furniture_meta(state)
    if not furniture_workbench_is_messy(state):
        meta["manual_replace_count"] = max(0, int(meta.get("manual_replace_count", 0) or 0) - 1)
        return False
    progress = int(meta.get("mess_recovery_progress", 0) or 0) + 1
    meta["mess_recovery_progress"] = progress
    required = max(1, int(fp.get("mess_recovery_trips", 3) or 3))
    if progress < required:
        return False
    meta["workbench_mess"] = False
    meta["mess_recovery_progress"] = 0
    meta["manual_replace_count"] = 0
    data = _furniture_production_data(state)
    if not furniture_job_pending(state):
        data["bench_lock"] = False
    return True


def apply_furniture_home_return(state: dict, policy: dict | None = None) -> dict:
    """Resolve workbench recovery and the frog's possible home redecoration."""
    fp = furniture_policy(policy)
    data = _furniture_production_data(state)
    recovered = recover_furniture_workbench(state, policy)
    result = {"changed": recovered, "recovered": recovered, "replaced": False}
    if furniture_workbench_is_messy(state):
        save_state(state, "furniture.workbench_recovery_progress")
        return result
    chance = max(0, min(100, int(fp.get("home_auto_replace_percent", 35) or 0)))
    meta = _furniture_meta(state)
    visit = int(meta.get("home_return_count", 0) or 0) + 1
    meta["home_return_count"] = visit
    if deterministic_roll(state, f"furniture:home_replace:{visit}", 100) >= chance:
        save_state(state, "furniture.home_return_checked")
        return result
    replaced_types = [int(value) for value in _to_list(data.get("replace_fur")) if str(value).lstrip("-").isdigit()]
    choices: list[tuple[int, list[int], int]] = []
    for furniture_type in sorted(set(replaced_types)):
        current = next((int(row.get("id", -1)) for row in _to_list(data.get("put_fur")) if isinstance(row, dict) and int(row.get("type", -1)) == furniture_type), -1)
        alternatives = [furniture_id for furniture_id in _owned_furniture_of_type(data, furniture_type) if furniture_id != current]
        if alternatives:
            choices.append((furniture_type, alternatives, current))
    if not choices:
        save_state(state, "furniture.home_return_checked")
        return result
    furniture_type, alternatives, _ = choices[deterministic_roll(state, f"furniture:home_replace_type:{visit}", len(choices))]
    furniture_id = alternatives[deterministic_roll(state, f"furniture:home_replace_item:{furniture_type}:{visit}", len(alternatives))]
    if _replace_home_furniture(data, furniture_type, furniture_id):
        rep = _to_list(data.get("replace_fur"))
        data["replace_fur"] = [value for value in rep if int(value) != furniture_type]
        get_event_list(state).append({
            "id": next_event_id(state), "evt_type": 22, "evt_id": 0,
            "evt_value": [], "evt_string": [], "evt_pic": [],
        })
        result.update({"changed": True, "replaced": True})
    save_state(state, "furniture.home_return")
    return result


def _special_furniture_owned_ids(state: dict, kind: str) -> set[int]:
    cmd, key = {
        "tumbler": ("furniture.load_tumbler", "tumbler_list"),
        "compost": ("furniture.load_compost", "compost_list"),
        "pocket": ("furniture.load_pocket", "list"),
    }[kind]
    push = find_push(state, cmd)
    source = push.get("data", {}) if isinstance(push, dict) else {}
    values = source.get(key, []) if isinstance(source, dict) else []
    if not isinstance(values, list):
        return set()
    if kind == "tumbler":
        return {int(row.get("id", 0) or 0) for row in values if isinstance(row, dict)}
    return {int(value) for value in values if isinstance(value, (int, float, str))}


def _select_special_furniture_production_target(state: dict, bench: list[int]) -> dict | None:
    _, house, _, _ = get_item_parts(state)
    bench_materials = [
        (slot, item_id)
        for slot, item_id in enumerate(bench[FURNITURE_TOOL_SLOTS:], start=FURNITURE_TOOL_SLOTS)
        if item_id > 0
    ]
    choices: list[dict] = []
    for special_slot, special_id in bench_materials:
        recipe = FURNITURE_SPECIAL_MATERIAL_RECIPES.get(special_id)
        if recipe is None or get_house_count(house, int(recipe["drawing_id"])) <= 0:
            continue
        kind = str(recipe["kind"])
        owned = _special_furniture_owned_ids(state, kind)

        def available_ids(primary_id: int) -> tuple[int, ...]:
            ids = tuple(int(value) for value in recipe["variants"].get(primary_id, ()))
            if GALLERY_MODE:
                return ids
            if kind != "tumbler":
                return tuple(value for value in ids if value not in owned)
            unowned = tuple(value for value in ids if value not in owned)
            return unowned or ids

        paired = [
            (slot, item_id, available_ids(item_id))
            for slot, item_id in bench_materials
            if slot != special_slot and item_id in recipe["variants"] and item_id != 0 and available_ids(item_id)
        ]
        if paired:
            for primary_slot, primary_id, ids in paired:
                choices.append({
                    "kind": kind, "drawing_id": int(recipe["drawing_id"]), "target_ids": ids,
                    "material_slots": [special_slot, primary_slot], "material_ids": [special_id, primary_id],
                    "style": primary_id,
                })
            continue
        ids = available_ids(0)
        if ids:
            choices.append({
                "kind": kind, "drawing_id": int(recipe["drawing_id"]), "target_ids": ids,
                "material_slots": [special_slot], "material_ids": [special_id], "style": 0,
            })
    if not choices:
        return None
    choice = choices[deterministic_roll(state, "furniture:special_recipe", len(choices))]
    target_ids = sorted(choice.pop("target_ids"))
    choice["target_id"] = target_ids[deterministic_roll(
        state,
        f"furniture:special_target:{choice['kind']}:{choice['style']}",
        len(target_ids),
    )]
    return choice


def _select_furniture_production_target(state: dict, data: dict, bench: list[int]) -> dict | None:
    _, house, _, _ = get_item_parts(state)
    owned = {int(value) for value in data["has_fur"] if isinstance(value, (int, float, str))}
    candidates_by_style: dict[int, list[tuple[int, int]]] = {}
    for key, row in _cfg_dict("furnitureData_json.json").items():
        if not isinstance(row, dict):
            continue
        try:
            furniture_id = int(row.get("id", key))
            drawing_id = int(row.get("drawing", 0) or 0)
            style = int(row.get("style", 0) or 0)
        except (TypeError, ValueError):
            continue
        if (furniture_id not in owned or GALLERY_MODE) and drawing_id > 0 and get_house_count(house, drawing_id) > 0:
            candidates_by_style.setdefault(style, []).append((furniture_id, drawing_id))

    materials: list[tuple[int, int, list[int]]] = []
    for slot, item_id in enumerate(bench[FURNITURE_TOOL_SLOTS:], start=FURNITURE_TOOL_SLOTS):
        styles = [style for style in FURNITURE_MATERIAL_STYLES.get(item_id, ()) if candidates_by_style.get(style)]
        if styles:
            materials.append((slot, item_id, styles))
    if not materials:
        # 素风格没有主题主材料；备齐图纸、工具和普通材料即可制作。
        candidates = sorted(candidates_by_style.get(1, ()))
        if not candidates:
            return None
        furniture_id, drawing_id = candidates[deterministic_roll(
            state, "furniture:production_target:1", len(candidates)
        )]
        return {
            "kind": "furniture", "target_id": furniture_id, "drawing_id": drawing_id,
            "material_slots": [], "material_ids": [], "style": 1,
        }
    slot, material_id, styles = materials[deterministic_roll(state, "furniture:production_material", len(materials))]
    style = styles[deterministic_roll(state, f"furniture:production_style:{material_id}", len(styles))]
    candidates = sorted(candidates_by_style[style])
    furniture_id, drawing_id = candidates[deterministic_roll(state, f"furniture:production_target:{style}", len(candidates))]
    return {
        "kind": "furniture", "target_id": furniture_id, "drawing_id": drawing_id,
        "material_slots": [slot], "material_ids": [material_id], "style": style,
    }


def _select_furniture_ordinary_materials(state: dict, count: int) -> list[int]:
    _, house, _, _ = get_item_parts(state)
    selected: list[int] = []
    for index in range(max(1, count)):
        available = [
            item_id for item_id in FURNITURE_ORDINARY_MATERIAL_IDS
            if selected.count(item_id) < get_house_count(house, item_id)
        ]
        if not available:
            return []
        selected.append(available[deterministic_roll(state, f"furniture:ordinary_material:{index}", len(available))])
    return selected


def _record_furniture_product(state: dict, data: dict, kind: str, target_id: int) -> None:
    if kind == "furniture":
        if target_id not in data["has_fur"]:
            data["has_fur"].append(target_id)
        return
    if kind == "tumbler":
        tumbler = get_furniture_data(state, "furniture.load_tumbler", {
            "show_index": 1, "replace_index": 0, "tumbler_list": [],
        })
        tumbler_list = tumbler.setdefault("tumbler_list", [])
        if not isinstance(tumbler_list, list):
            tumbler_list = []
            tumbler["tumbler_list"] = tumbler_list
        tumbler_list.append({"id": target_id, "layers": []})
        tumbler["show_index"] = len(tumbler_list)
        return
    if kind == "compost":
        compost = get_furniture_data(state, "furniture.load_compost", {
            "show_index": 1, "replace_index": 0, "compost_list": [21000],
            "state": 0, "box_index": 0, "box_list": [0, 0, 0, 0, 0, 0],
        })
        compost_list = compost.setdefault("compost_list", [21000])
        if target_id not in compost_list:
            compost_list.append(target_id)
        compost["show_index"] = compost_list.index(target_id) + 1
        return
    if kind == "pocket":
        pocket = get_furniture_data(state, "furniture.load_pocket", {
            "show_index": 1, "replace_index": 0, "list": [22001], "clover": 0,
        })
        pocket_list = pocket.setdefault("list", [22001])
        if target_id not in pocket_list:
            pocket_list.append(target_id)
        pocket["show_index"] = pocket_list.index(target_id) + 1


def advance_furniture_production(state: dict, policy: dict | None = None, now: int | None = None) -> dict:
    """Queue, start, or finish a workbench job while the frog is at home."""
    if now is None:
        now = int(time.time())
    result = {"changed": False, "completed": False, "consumed_resources": [], "product_kind": ""}
    fp = furniture_policy(policy)
    if not bool(fp.get("enabled", True)) or _handcraft_job(state) is not None:
        return result
    push = find_push(state, "furniture.load_furniture")
    raw_data = push.get("data") if isinstance(push, dict) else None
    mate_list_was_invalid = not isinstance(raw_data, dict) or not isinstance(raw_data.get("mate_list"), list)
    data = _furniture_production_data(state)
    if mate_list_was_invalid:
        save_state(state, "furniture.normalize_mate_list")
    _, frog, _, _ = get_role_parts(state)
    if int(frog.get("status", FROG_HOME) or FROG_HOME) != FROG_HOME:
        return result
    bench = data["bench"]
    meta = state.setdefault("offline_meta", {}).setdefault("furniture", {})
    job = _furniture_job(state)
    if job is not None:
        if int(job.get("ready_at", 0) or 0) > now:
            data["bench_lock"] = True
            frog["motion"] = furniture_production_motion(state, now)
            return result
        _, house, _, _ = get_item_parts(state)
        consumed = [int(item_id) for item_id in job.get("reserved_resources", [])]
        if not GALLERY_MODE:
            for item_id in consumed:
                set_house_count(house, item_id, max(0, get_house_count(house, item_id) - 1))
        material_slots = job.get("material_slots")
        if not isinstance(material_slots, list):
            material_slots = [int(job.get("material_slot", -1) or -1)]
        for material_slot in {int(slot) for slot in material_slots if isinstance(slot, (int, float, str))}:
            if FURNITURE_TOOL_SLOTS <= material_slot < FURNITURE_BENCH_SLOTS:
                bench[material_slot] = -1
        data["mate_list"] = []
        data["bench_lock"] = False
        target_id = int(job.get("target_id", job.get("furniture_id", 0)) or 0)
        product_kind = str(job.get("product_kind", "furniture") or "furniture")
        already_owned = (
            target_id in data["has_fur"] if product_kind == "furniture"
            else target_id in _special_furniture_owned_ids(state, product_kind)
        )
        awarded = not (GALLERY_MODE and already_owned)
        if awarded:
            _record_furniture_product(state, data, product_kind, target_id)
            if product_kind == "furniture":
                grant_permanent_special_title(
                    state,
                    int(special_title_policy(policy).get("furniture_creation", 902) or 902),
                )
            get_event_list(state).append({
                "id": next_event_id(state), "evt_type": 21, "evt_id": target_id,
                "evt_value": [], "evt_string": [], "evt_pic": [],
            })
        meta.pop("job", None)
        result.update({"changed": True, "completed": True, "awarded": awarded, "consumed_resources": consumed, "product_kind": product_kind})
        save_state(state, f"furniture.production.finish:{target_id}")
        return result

    if furniture_workbench_is_messy(state):
        data["bench_lock"] = True
        return result

    queued = meta.get("queued_job")
    if isinstance(queued, dict):
        if not _furniture_queued_job_is_valid(state, bench, queued):
            meta.pop("queued_job", None)
            result["changed"] = True
        elif int(queued.get("start_at", 0) or 0) > now:
            return result
        else:
            job = copy.deepcopy(queued)
            job["started_at"] = int(job.get("start_at", now) or now)
            job["ready_at"] = max(job["started_at"] + 60, int(job.get("ready_at", now) or now))
            meta["job"] = job
            meta.pop("queued_job", None)
            if job["ready_at"] > now:
                data["mate_list"] = list(job.get("reserved_resources", []))
                data["bench_lock"] = True
                frog["motion"] = furniture_production_motion(state, now)
                result["changed"] = True
                save_state(state, f"furniture.production.start:{int(job.get('target_id', 0) or 0)}")
                return result
            return advance_furniture_production(state, policy=policy, now=now)

    tool_index = next((index for index, item_id in enumerate(bench[:FURNITURE_TOOL_SLOTS]) if item_id > 0), None)
    if tool_index is None:
        if result["changed"]:
            save_state(state, "furniture.production.queue_cancel")
        return result
    special_material_present = any(item_id in FURNITURE_SPECIAL_MATERIAL_IDS for item_id in bench[FURNITURE_TOOL_SLOTS:])
    target = _select_special_furniture_production_target(state, bench)
    if target is None and not special_material_present:
        target = _select_furniture_production_target(state, data, bench)
    if target is None:
        if result["changed"]:
            save_state(state, "furniture.production.queue_cancel")
        return result
    materials = _select_furniture_ordinary_materials(state, int(fp.get("ordinary_material_count", 3) or 3))
    if not materials:
        if result["changed"]:
            save_state(state, "furniture.production.queue_cancel")
        return result
    target_id = int(target["target_id"])
    product_kind = str(target["kind"])
    start_delay = _furniture_random_seconds(
        state, fp, f"furniture:queue_delay:{target_id}",
        "start_delay_min_seconds", "start_delay_max_seconds", 7200, 172800,
    )
    duration = _furniture_random_seconds(
        state, fp, f"furniture:job_duration:{target_id}",
        "job_min_seconds", "job_max_seconds", 3600, 43200,
    )
    meta["queued_job"] = {
        "furniture_id": target_id, "target_id": target_id, "product_kind": product_kind,
        "drawing_id": int(target["drawing_id"]), "material_slots": list(target["material_slots"]),
        "material_ids": list(target["material_ids"]), "style": int(target["style"]),
        "reserved_resources": materials, "tool_index": tool_index, "tool_id": int(bench[tool_index]),
        "queued_at": now, "start_at": now + start_delay, "ready_at": now + start_delay + duration,
        "motion": 5 + tool_index,
    }
    result["changed"] = True
    save_state(state, f"furniture.production.queue:{target_id}")
    return result


def get_flowerpot_data(state: dict) -> dict:
    data = get_furniture_data(state, "furniture.load_flowerpot", {
        "show_index": 1, "replace_index": 0, "list": [23001],
        "plant_list": [{"index": 1, "id": 0, "stage": 0}, {"index": 2, "id": 0, "stage": 0}],
    })
    data.setdefault("show_index", 1); data.setdefault("replace_index", 0)
    data.setdefault("list", [23001]); data.setdefault("plant_list", [])
    if not isinstance(data["plant_list"], list): data["plant_list"] = []
    # Official first flowerpot has two positions. Keep existing indices and add
    # missing empty positions defensively.
    existing={int(x.get("index",-1)) for x in data["plant_list"] if isinstance(x,dict)}
    for pos in (1,2):
        if pos not in existing: data["plant_list"].append({"index":pos,"id":0,"stage":0})
    return data


def get_compost_data(state: dict) -> dict:
    data = get_furniture_data(state, "furniture.load_compost", {
        "show_index": 1, "replace_index": 0, "compost_list": [21000],
        "state": 0, "box_index": 0, "box_list": [0,0,0,0,0,0],
    })
    data.setdefault("show_index",1); data.setdefault("replace_index",0)
    data.setdefault("compost_list",[21000]); data.setdefault("state",0); data.setdefault("box_index",0)
    box=data.setdefault("box_list",[0,0,0,0,0,0])
    if not isinstance(box,list): box=[]; data["box_list"]=box
    while len(box)<6: box.append(0)
    if len(box)>6: del box[6:]
    return data


def get_pocket_data(state: dict) -> dict:
    data=get_furniture_data(state,"furniture.load_pocket",{
        "show_index":1,"replace_index":0,"list":[22001],"clover":0,
    })
    data.setdefault("show_index",1); data.setdefault("replace_index",0)
    data.setdefault("list",[22001]); data["clover"]=max(0,int(data.get("clover",0) or 0))
    return data


def garden_policy(policy: dict | None=None) -> dict:
    if policy is None: policy=load_offline_policy()
    return policy.get("garden",{}) if isinstance(policy,dict) else {}


def garden_meta(state: dict) -> dict:
    return state.setdefault("offline_meta",{}).setdefault("garden",{})


def plant_family(plant_id: int) -> int:
    if int(plant_id)<2010000: return 0
    return (int(plant_id)//100)%100


def seed_for_family(family: int) -> int:
    return 20100 + int(family)


def plant_variants_for_seed(seed_id: int) -> list[int]:
    family=int(seed_id)-20100
    if family<1 or family>11: return []
    plants=_cfg_dict("flowerpotData_json.json").get("plant",{})
    out=[]
    for k,row in plants.items() if isinstance(plants,dict) else []:
        try: pid=int(row.get("id",k))
        except Exception: continue
        if plant_family(pid)==family: out.append(pid)
    return sorted(out)


def decorative_item_for_plant(plant_id: int) -> int | None:
    family=plant_family(plant_id)
    variant=int(plant_id)%100
    prefix={1:202210,2:202220,4:202230,5:202240,6:202250,11:202260}.get(family)
    return prefix+variant if prefix else None


def decoration_id_for_plant(plant_id: int) -> int | None:
    family=plant_family(plant_id); variant=int(plant_id)%100
    prefix={1:10010,2:10020,4:10030,5:10040,6:10050,11:10060}.get(family)
    return prefix+variant if prefix else None


def harvest_items_for_plant(plant_id: int) -> list[dict]:
    fam=plant_family(plant_id)
    if fam in GARDEN_CROP_REWARD:
        return [{"item_id":GARDEN_CROP_REWARD[fam],"num":1}]
    iid=decorative_item_for_plant(plant_id)
    return [{"item_id":iid,"num":1}] if iid else []


def _garden_stage_for_progress(progress: float, gp: dict) -> int:
    raw=gp.get("stage_points",[0,100,200,300])
    vals=[float(x) for x in raw] if isinstance(raw,list) and len(raw)>=4 else [0,100,200,300]
    if progress>=vals[3]: return 3
    if progress>=vals[2]: return 2
    if progress>=vals[1]: return 1
    return 0


def sync_garden_meta_from_wire(state: dict, now: int | None=None, policy: dict | None=None) -> dict:
    if now is None: now=int(time.time())
    gp=garden_policy(policy); meta=garden_meta(state); plants_meta=meta.setdefault("plants",{})
    fp=get_flowerpot_data(state)
    for row in fp["plant_list"]:
        if not isinstance(row,dict): continue
        index=int(row.get("index",0) or 0); pid=int(row.get("id",0) or 0); stage=int(row.get("stage",0) or 0)
        if index<=0: continue
        pm=plants_meta.get(str(index))
        if pid<=0:
            if not isinstance(pm,dict) or int(pm.get("plant_id",0) or 0)<=0:
                plants_meta[str(index)]={"plant_id":0,"seed_id":0,"progress":0.0,"last_update":int(now)}
            continue
        if not isinstance(pm,dict) or int(pm.get("plant_id",0) or 0)!=pid:
            points=gp.get("stage_points",[0,100,200,300]); base=float(points[min(max(stage,0),3)])
            plants_meta[str(index)]={"plant_id":pid,"seed_id":seed_for_family(plant_family(pid)),"progress":base,"last_update":int(now)}
    meta.setdefault("fertility_until",0); meta.setdefault("fertility_bonus_points",0)
    return meta


def _compost_meta(state: dict) -> dict:
    meta = garden_meta(state).setdefault("compost", {})
    if not isinstance(meta, dict):
        meta = {}
        garden_meta(state)["compost"] = meta
    slots = meta.setdefault("slots", {})
    if not isinstance(slots, dict):
        meta["slots"] = {}
    return meta


def _compost_item_points(item_id: int, policy: dict | None = None) -> float:
    gp = garden_policy(policy)
    fertilizer_points = gp.get("fertilizer_points", {})
    if item_id in GARDEN_FERTILIZERS:
        return float(fertilizer_points.get(str(item_id), 30) or 30)
    cfg = item_cfg(item_id) or {}
    if int(cfg.get("type", -1)) == 3:
        return float(gp.get("specialty_compost_points", 18) or 18)
    return 0.0


def _compost_item_duration(item_id: int, policy: dict | None = None) -> int:
    gp = garden_policy(policy)
    durations = gp.get("fertilizer_duration_seconds", {})
    if item_id in GARDEN_FERTILIZERS:
        return max(1, int(durations.get(str(item_id), gp.get("compost_absorb_seconds", 7200)) or 1))
    return max(1, int(gp.get("specialty_compost_duration_seconds", 7200) or 1))


def _expire_garden_fertility(meta: dict, now: int) -> bool:
    if int(meta.get("fertility_until", 0) or 0) > int(now):
        return False
    if float(meta.get("fertility_bonus_points", 0) or 0) <= 0:
        return False
    meta["fertility_bonus_points"] = 0
    meta["fertility_until"] = 0
    return True


def _fertility_state_from_points(points: float) -> int:
    if points <= 0:
        return 0
    # The original meter has three flowers with thresholds 2, 3 and 4.
    if points < 80:
        return 2
    if points < 180:
        return 3
    return 4


def _advance_garden_plants(state: dict, now: int, policy: dict) -> bool:
    """Integrate growth only over the interval where the current fertility exists."""
    gp = garden_policy(policy)
    meta = garden_meta(state)
    points = float(meta.get("fertility_bonus_points", 0) or 0)
    until = int(meta.get("fertility_until", 0) or 0)
    level = _fertility_state_from_points(points)
    multiplier = float(gp.get("fertility_multiplier_by_state", {}).get(str(level), 1.0))
    hourly = float(gp.get("passive_growth_points_per_hour", 10) or 0)
    changed = False
    for row in get_flowerpot_data(state)["plant_list"]:
        if not isinstance(row, dict) or int(row.get("id", 0) or 0) <= 0:
            continue
        plant = meta["plants"][str(int(row["index"]))]
        last = int(plant.get("last_update", now))
        if now <= last:
            continue
        boosted = max(0, min(now, until) - last)
        gain = hourly * ((now - last) + boosted * (multiplier - 1.0)) / 3600.0
        plant["progress"] = float(plant.get("progress", 0) or 0) + gain
        plant["last_update"] = now
        row["stage"] = _garden_stage_for_progress(plant["progress"], gp)
        changed = True
    return changed


def _advance_compost_fermentation(state: dict, now: int, policy: dict | None=None) -> bool:
    """Keep the formal one-slot fermenting queue in sync with local time."""
    gp = garden_policy(policy)
    comp = get_compost_data(state)
    meta = _compost_meta(state)
    slots = meta["slots"]
    delay = max(0, int(gp.get("compost_lock_delay_seconds", 300) or 0))
    absorb = max(1, int(gp.get("compost_absorb_seconds", 7200) or 1))
    changed = False

    # Imported slots predate local timestamps. Treat their first observable
    # refresh as ready to ferment, so they do not become newly reclaimable.
    for pos, raw_id in enumerate(comp["box_list"], start=1):
        item_id = int(raw_id or 0)
        key = str(pos)
        if item_id <= 0:
            if key in slots:
                del slots[key]
                changed = True
            continue
        entry = slots.get(key)
        if not isinstance(entry, dict):
            slots[key] = {"item_id": item_id, "placed_at": now - delay}
            changed = True
        elif int(entry.get("item_id", 0) or 0) != item_id:
            slots[key] = {"item_id": item_id, "placed_at": now}
            changed = True

    active = int(comp.get("box_index", 0) or 0)
    if active < 1 or active > 6 or int(comp["box_list"][active - 1] or 0) <= 0:
        if active != 0:
            comp["box_index"] = 0
            changed = True
        active = 0

    cursor = int(meta.get("settled_at", 0) or 0)
    while True:
        if not active:
            candidates = [
                (max(cursor, int(slots[str(pos)]["placed_at"]) + delay), pos)
                for pos, raw_id in enumerate(comp["box_list"], start=1)
                if int(raw_id or 0) > 0
            ]
            if not candidates:
                break
            started_at, active = min(candidates)
            if started_at > now:
                active = 0
                break
            slots[str(active)]["ferment_started_at"] = started_at
            comp["box_index"] = active
            changed = True
        entry = slots.get(str(active), {})
        started_at = int(entry.get("ferment_started_at", now) or now) if isinstance(entry, dict) else now
        if not isinstance(entry, dict) or "ferment_started_at" not in entry:
            slots[str(active)] = {"item_id": int(comp["box_list"][active - 1]), "placed_at": now - delay, "ferment_started_at": started_at}
            changed = True
        completed_at = started_at + absorb
        if completed_at <= now:
            changed = _advance_garden_plants(state, completed_at, policy) or changed
            item_id = int(comp["box_list"][active - 1] or 0)
            points = _compost_item_points(item_id, policy)
            if points > 0:
                garden = garden_meta(state)
                # Back-to-back inputs preserve the pool at the shared boundary.
                _expire_garden_fertility(garden, completed_at - 1)
                garden["fertility_bonus_points"] = float(garden.get("fertility_bonus_points", 0) or 0) + points
                garden["fertility_until"] = max(
                    int(garden.get("fertility_until", 0) or 0),
                    completed_at,
                ) + _compost_item_duration(item_id, policy)
            comp["box_list"][active - 1] = 0
            slots.pop(str(active), None)
            comp["box_index"] = 0
            active = 0
            cursor = completed_at
            changed = True
        else:
            break
    if now >= cursor and meta.get("settled_at") != now:
        meta["settled_at"] = now
        changed = True
    return changed


def _compost_state_from_box(state: dict, now: int | None = None) -> int:
    garden = garden_meta(state)
    if now is None:
        now = int(time.time())
    _expire_garden_fertility(garden, now)
    return _fertility_state_from_points(float(garden.get("fertility_bonus_points", 0) or 0))


def advance_garden_growth(state: dict, now: int | None=None, policy: dict | None=None, *, save: bool=True) -> bool:
    if now is None: now=int(time.time())
    if policy is None: policy=load_offline_policy()
    gp=garden_policy(policy)
    if not bool(gp.get("enabled",True)): return False
    meta=sync_garden_meta_from_wire(state,now,policy); compost=get_compost_data(state)
    changed = _advance_compost_fermentation(state, now, policy)
    changed = _advance_garden_plants(state, now, policy) or changed
    changed = _expire_garden_fertility(meta, now) or changed
    level = _compost_state_from_box(state, now)
    if compost["state"] != level:
        compost["state"] = level
        changed = True
    if changed and save: save_state(state,"garden.growth")
    return changed


def garden_guest_departure(state: dict, guest_id: int, now: int | None=None, policy: dict | None=None) -> dict:
    if now is None: now=int(time.time())
    if policy is None: policy=load_offline_policy()
    gp=garden_policy(policy); advance_garden_growth(state,now,policy,save=False)
    fp=get_flowerpot_data(state); meta=sync_garden_meta_from_wire(state,now,policy)
    planted=[]; grew=[]
    # Official item descriptions explicitly say neighbours are interested in seeds;
    # exact server selection/growth amount is not exposed, so selection/value are policy.
    if bool(gp.get("auto_plant_on_guest_departure",True)):
        _,house,_,_=get_item_parts(state)
        seeds=[sid for sid in GARDEN_SEEDS if get_house_count(house,sid)>0]
        empties=[r for r in fp["plant_list"] if isinstance(r,dict) and int(r.get("id",0) or 0)==0]
        count=min(int(gp.get("max_auto_plants_per_guest",1) or 1),len(empties),len(seeds))
        for n in range(count):
            sid=seeds[deterministic_roll(state,f"garden_seed:{guest_id}:{n}",len(seeds))]
            variants=plant_variants_for_seed(sid)
            if not variants: continue
            pid=variants[deterministic_roll(state,f"garden_variant:{guest_id}:{sid}:{n}",len(variants))]
            row=empties[n]; idx=int(row["index"])
            set_house_count(house,sid,get_house_count(house,sid)-1)
            row.update({"id":pid,"stage":1})
            points=max(float(gp.get("stage_points",[0,100,200,300])[1]),1.0)
            meta["plants"][str(idx)]={"plant_id":pid,"seed_id":sid,"progress":points,"last_update":int(now)}
            planted.append({"index":idx,"seed_id":sid,"plant_id":pid})
            seeds=[x for x in seeds if get_house_count(house,x)>0]
    guest_pts=float(gp.get("guest_growth_points",60) or 0)
    if guest_pts>0:
        for row in fp["plant_list"]:
            if not isinstance(row,dict) or int(row.get("id",0) or 0)<=0 or int(row.get("stage",0) or 0)>=3: continue
            idx=int(row["index"]); pm=meta["plants"].setdefault(str(idx),{"plant_id":int(row["id"]),"seed_id":seed_for_family(plant_family(int(row["id"]))),"progress":0,"last_update":int(now)})
            pm["progress"]=float(pm.get("progress",0) or 0)+guest_pts; pm["last_update"]=int(now)
            row["stage"]=_garden_stage_for_progress(pm["progress"],gp); grew.append({"index":idx,"stage":row["stage"]})
    meta["last_guest_growth"]={"time":int(now),"guest_id":int(guest_id),"points":guest_pts,"planted":copy.deepcopy(planted)}
    return {"planted":planted,"grew":grew}


def apply_compost_put(state: dict, pos: int, item_id: int, policy: dict | None=None) -> tuple[bool,str,int]:
    if pos<1 or pos>6: return False,"bad_pos",0
    cfg=item_cfg(item_id) or {}; typ=int(cfg.get("type",-1)); sub=cfg.get("sub_type")
    if not (typ==3 or (typ==15 and int(sub or 0)==1)): return False,"not_compostable",0
    _,house,_,_=get_item_parts(state); old=get_house_count(house,item_id)
    if old<=0: return False,"not_in_house",old
    # Settle all existing growth before the compost contents change.  The new
    # material affects only the time that follows this placement.
    now=int(time.time())
    advance_garden_growth(state,now=now,policy=policy,save=False)
    comp=get_compost_data(state); idx=pos-1
    if int(comp["box_list"][idx] or 0)>0: return False,"occupied",old
    set_house_count(house,item_id,old-1); comp["box_list"][idx]=item_id
    _compost_meta(state)["slots"][str(pos)]={"item_id":item_id,"placed_at":now}
    comp["state"]=_compost_state_from_box(state,now)
    save_state(state,f"furniture.putin_box:{pos}:{item_id}")
    return True,"ok",old-1


def apply_compost_takeout(state: dict, pos: int, policy: dict | None=None) -> tuple[bool,str,int|None,int|None]:
    if pos<1 or pos>6: return False,"bad_pos",None,None
    now=int(time.time())
    advance_garden_growth(state,now=now,policy=policy,save=False)
    comp=get_compost_data(state); idx=pos-1; iid=int(comp["box_list"][idx] or 0)
    if iid<=0: return False,"empty",None,None
    if int(comp.get("box_index",0) or 0)==pos:
        return False,"composting",None,None
    _,house,_,_=get_item_parts(state)
    count=get_house_count(house,iid)+1
    set_house_count(house,iid,count)
    comp["box_list"][idx]=0
    _compost_meta(state)["slots"].pop(str(pos),None)
    comp["state"]=_compost_state_from_box(state,now)
    save_state(state,f"furniture.takeout_box:{pos}:{iid}")
    return True,"ok",iid,count


def apply_pocket_get(
    state: dict,
    *,
    now: int | None = None,
    policy: dict | None = None,
) -> int:
    """Claim the current net reservoir without leaving stale overflow debt.

    Before emptying the pocket, sweep net cycles through ``now`` while the
    current pocket occupancy is still in force.  With a full pocket this
    consumes/discards any overflow that matured while the net had no room, so
    a later login cannot replay those old cycles and instantly refill it.
    """
    if now is None:
        now = int(time.time())
    if policy is None:
        policy = load_offline_policy()

    # Important: perform the sweep *before* reading/clearing pocket.clover.
    # The fixed collector advances cursors even when capacity is already full.
    collect_clover_net_overflow(state, now=int(now), policy=policy)

    pocket = get_pocket_data(state)
    amount = int(pocket.get("clover", 0) or 0)
    if amount <= 0:
        return 0
    res, _ = get_clover_parts(state)
    res["clover_point"] = int(res.get("clover_point", 0) or 0) + amount
    calendar_record_clover_gain(state, amount)
    pocket["clover"] = 0
    save_state(state, f"furniture.pocket_get:{amount}")
    return amount


def apply_flowerpot_harvest(state: dict, index: int, policy: dict | None=None) -> tuple[bool,str,list[dict]]:
    advance_garden_growth(state,policy=policy,save=False); fp=get_flowerpot_data(state); row=next((x for x in fp["plant_list"] if isinstance(x,dict) and int(x.get("index",-1))==int(index)),None)
    if not row: return False,"bad_index",[]
    pid=int(row.get("id",0) or 0); stage=int(row.get("stage",0) or 0)
    if pid<=0: return False,"empty",[]
    if stage<3: return False,"not_mature",[]
    rewards=harvest_items_for_plant(pid); _,house,_,_=get_item_parts(state)
    for reward in rewards:
        iid=int(reward["item_id"]); num=int(reward.get("num",1)); set_house_count(house,iid,get_house_count(house,iid)+num)
    did=decoration_id_for_plant(pid)
    if did is not None: add_decoration(state,did,1)
    row.update({"id":0,"stage":0}); garden_meta(state).setdefault("plants",{})[str(int(index))]={"plant_id":0,"seed_id":0,"progress":0.0,"last_update":int(time.time())}
    cooking_record_task_progress(state, 8)
    save_state(state,f"furniture.flowerpot_harvest:{index}:{pid}"); return True,"ok",rewards


def apply_change_decoration(state: dict, decoration_id: int) -> tuple[bool,str]:
    if _frog_is_home(state):
        return False,"frog_home"
    now=int(time.time())
    advance_vase_decoration(state, now=now, save=False)
    data=get_decoration_parts(state); new_id=int(decoration_id); rows=data["has_list"]
    if new_id>0 and not any(isinstance(r,dict) and int(r.get("id",-1))==new_id and int(r.get("num",0) or 0)>0 for r in rows): return False,"not_owned"
    old=int(data.get("put_id",0) or 0)
    # Exact client semantics: only the previously displayed stem is consumed
    # when replaced; the newly selected stem remains counted until next replace.
    if old>0 and old!=new_id:
        for i,r in enumerate(list(rows)):
            if isinstance(r,dict) and int(r.get("id",-1))==old:
                r["num"]=int(r.get("num",0) or 0)-1
                if r["num"]<=0: rows.pop(i)
                break
    data["put_id"]=new_id; data["status"]=1 if new_id>0 else 0
    vase=state.setdefault("offline_meta",{}).setdefault("vase",{})
    vase.clear()
    if new_id>0: vase.update({"decoration_id":new_id,"inserted_at":now})
    save_state(state,f"client.change_decorate:{new_id}"); return True,"ok"


def _dudu_gift_is_owned(state: dict, item_id: int, furniture: dict | None=None) -> bool:
    target = DUDU_FURNITURE_GIFT_TARGETS.get(int(item_id))
    if target is None:
        return False
    kind, target_id = target
    if kind == "furniture":
        data = furniture if isinstance(furniture, dict) else _furniture_production_data(state)
        return int(target_id) in {int(value) for value in _to_list(data.get("has_fur")) if str(value).lstrip("-").isdigit()}
    return int(target_id) in {int(value) for value in _to_list(get_flowerpot_data(state).get("list")) if str(value).lstrip("-").isdigit()}


def _dudu_one_time_tool_row(row: dict) -> bool:
    """Whether a Dudu shelf row is a single-acquisition crafting tool."""
    if not isinstance(row, dict) or int(row.get("limit", 0) or 0) <= 0:
        return False
    item = item_cfg(int(row.get("item_id", 0) or 0))
    return bool(item and int(item.get("type", -1) or -1) == 12 and int(item.get("own_num", 0) or 0) == 1)


# GiftData 5001 and 5002 are the two one-clover starter purchases.
DUDU_STARTER_KITS = {1: tuple(range(10201, 10206)), 2: (*range(10001, 10008), 10101)}


def _dudu_starter_offers(state: dict, house: list) -> list[int]:
    claimed = state.get("offline_meta", {}).get("dudu_starter_kits", [])
    missing_tools = any(not get_house_count(house, item_id) for item_id in DUDU_STARTER_KITS[1])
    if not missing_tools and 1 not in claimed:
        return []
    return [shop_id for shop_id in DUDU_STARTER_KITS if shop_id not in claimed]


def _grant_dudu_furniture_gift(state: dict, item_id: int) -> str | None:
    target = DUDU_FURNITURE_GIFT_TARGETS.get(int(item_id))
    if target is None:
        return None
    kind, target_id = target
    if kind == "furniture":
        data = _furniture_production_data(state)
        if int(target_id) not in data["has_fur"]:
            data["has_fur"].append(int(target_id))
    else:
        data = get_flowerpot_data(state)
        if int(target_id) not in data["list"]:
            data["list"].append(int(target_id))
            data["show_index"] = data["list"].index(int(target_id)) + 1
    return kind


def refresh_dudu_garden_shop(state: dict, now: int | None=None, policy: dict | None=None) -> bool:
    if now is None: now=int(time.time())
    gp=garden_policy(policy); furn=get_furniture_data(state,"furniture.load_furniture",{"shop":{"start_time":0,"leave_time":0,"shop_list":[]},"mood":0,"bench_lock":False,"bench":[],"put_fur":[],"has_fur":[],"mate_list":[],"replace_fur":[]})
    shop=furn.setdefault("shop",{}); start=int(shop.get("start_time",0) or 0); leave=int(shop.get("leave_time",0) or 0)
    slot_limit=max(1,int(gp.get("dudu_total_slots",10) or 10))
    db=_cfg_dict("furnitureShopData_json.json"); _,house,_,_=get_item_parts(state)
    starter_ids = _dudu_starter_offers(state, house)
    spring_offer = seasonal_activity_window("spring_card", now)[1] > 0 and bool(spring_card_policy(policy).get("enabled", True))
    existing=shop.get("shop_list") if isinstance(shop.get("shop_list"),list) else []
    def no_longer_sell(entry):
        if not isinstance(entry,dict): return False
        row=db.get(str(int(entry.get("shop_id",-1))))
        if not isinstance(row,dict): return False
        item_id=int(row.get("item_id",0) or 0)
        target=DUDU_FURNITURE_GIFT_TARGETS.get(item_id)
        gift_sold_out=bool(target and target[0] != "flowerpot" and _dudu_gift_is_owned(state,item_id,furn))
        tool_sold_out=_dudu_one_time_tool_row(row) and get_house_count(house,item_id)>0
        return (int(row.get("type",0))==4 and get_house_count(house,item_id)>0) or tool_sold_out or gift_sold_out
    filtered=[]
    for entry in existing:
        if no_longer_sell(entry):
            continue
        entry_out=copy.deepcopy(entry)
        item_id=int(entry_out.get("item_id",0) or 0)
        target=DUDU_FURNITURE_GIFT_TARGETS.get(item_id)
        if target and target[0] == "flowerpot" and _dudu_gift_is_owned(state,item_id,furn):
            entry_out["num"]=0
        filtered.append(entry_out)
    inventory_changed=filtered!=existing
    if inventory_changed: shop["shop_list"]=filtered
    local_time=time.localtime(now); midnight=now-(local_time.tm_hour*3600+local_time.tm_min*60+local_time.tm_sec)
    arrive=midnight+max(0,min(23,int(gp.get("dudu_arrive_hour",12) or 12)))*3600
    leave_hour=max(1,min(24,int(gp.get("dudu_leave_hour",24) or 24)))
    departure=midnight+leave_hour*3600
    meta=garden_meta(state); window=meta.get("dudu_window")
    if not isinstance(window,dict) or int(window.get("day",0) or 0)!=midnight:
        min_stay=max(60,int(gp.get("dudu_min_stay_minutes",360) or 360))*60
        max_stay=max(min_stay,int(gp.get("dudu_max_stay_minutes",480) or 480)*60)
        latest_arrive=max(arrive,departure-min_stay)
        start_time=arrive+deterministic_roll(state,f"dudu:window_start:{midnight//86400}",latest_arrive-arrive+1)
        longest_stay=min(max_stay,departure-start_time)
        stay_time=min_stay+deterministic_roll(state,f"dudu:window_stay:{midnight//86400}",longest_stay-min_stay+1)
        window={"day":midnight,"start_time":start_time,"leave_time":start_time+stay_time}
        meta["dudu_window"]=window
    arrive=int(window["start_time"]); departure=int(window["leave_time"])
    if now<arrive or now>=departure:
        changed=(inventory_changed or int(shop.get("start_time",0) or 0)!=FURNITURE_FEATURE_OPEN_AT
                 or bool(shop.get("leave_time") or shop.get("shop_list")))
        if changed:
            # Furniture stays unlocked after the first feature release; only
            # 嘟嘟's timed storefront closes outside its daily visit window.
            shop.update({"start_time":FURNITURE_FEATURE_OPEN_AT,"leave_time":0,"shop_list":[]})
            save_state(state,"garden.dudu_departure")
        return changed
    sold_out=shop.get("sold_out_specials")
    if not isinstance(sold_out,list):
        sold_out=[]
        shop["sold_out_specials"]=sold_out
    # Preserve a bought flowerpot in this visit's shelf as sold out.  The
    # current save predates this field, so an owned flowerpot in the active
    # visit is restored once as the same sold-out row.
    flowerpot_item_id=201009
    flowerpot_shop_id=20
    active_window=start==arrive and leave==departure
    if active_window:
        listed = {int(row["shop_id"]) for row in filtered if isinstance(row, dict)}
        starter_rows = [{"shop_id": sid, "item_id": int(db[str(sid)]["item_id"]), "num": 1}
                        for sid in starter_ids if sid not in listed]
        filtered = starter_rows + filtered
    if active_window and _dudu_gift_is_owned(state,flowerpot_item_id,furn):
        record=next((row for row in sold_out if isinstance(row,dict) and int(row.get("shop_id",0) or 0)==flowerpot_shop_id and int(row.get("start_time",0) or 0)==start),None)
        if record is None:
            record={"shop_id":flowerpot_shop_id,"item_id":flowerpot_item_id,"start_time":start}
            sold_out.append(record)
        if not any(int(row.get("shop_id",-1) or -1)==flowerpot_shop_id for row in filtered if isinstance(row,dict)):
            filtered.append({"shop_id":flowerpot_shop_id,"item_id":flowerpot_item_id,"num":0})
    if filtered!=existing:
        inventory_changed=True
        shop["shop_list"]=filtered
    if len(filtered)>slot_limit:
        sold_out=sorted(
            (entry for entry in filtered if int(entry.get("num",0) or 0)<=0),
            key=lambda entry: int(entry.get("shop_id",0) or 0)!=flowerpot_shop_id,
        )
        available=[entry for entry in filtered if int(entry.get("num",0) or 0)>0]
        filtered=sold_out[:slot_limit]+available[:max(0,slot_limit-len(sold_out))]
        inventory_changed=True
        shop["shop_list"]=filtered
    if active_window and spring_offer and not any(int(row.get("shop_id", -1) or -1) == 28 for row in filtered):
        filtered = [row for row in filtered if int(row.get("item_id", 0) or 0) != 10102 or int(row.get("num", 0) or 0) <= 0]
        if len(filtered) >= slot_limit:
            available = [i for i, row in enumerate(filtered) if int(row.get("num", 0) or 0) > 0]
            filtered.pop(available[-1] if available else -1)
        filtered.append({"shop_id": 28, "item_id": 10102, "num": 1})
        shop["shop_list"] = filtered
        inventory_changed = True
    if start==arrive and leave==departure and isinstance(shop.get("shop_list"),list) and shop["shop_list"]:
        if inventory_changed: save_state(state,"garden.dudu_remove_owned_blueprint")
        return inventory_changed
    rows=[r for r in db.values() if isinstance(r,dict)]
    seed_rows=[r for r in rows if int(r.get("item_id",0)) in GARDEN_SEEDS and int(r.get("type",0))==6]
    fert_rows=[r for r in rows if int(r.get("item_id",0)) in GARDEN_FERTILIZERS and int(r.get("type",0))==6]
    wood_rows=[r for r in rows if int(r.get("item_id",0))==8001 and int(r.get("type",0))==6]
    def pick(rows,count,label):
        rows=sorted(rows,key=lambda r:int(r["id"])); out=[]
        pool=rows[:]
        for n in range(min(count,len(pool))):
            j=deterministic_roll(state,f"dudu:{label}:{midnight//86400}:{n}",len(pool)); out.append(pool.pop(j))
        return out
    featured=[]
    for shop_type in (1,2,3,4,5,9):
        candidates=[r for r in rows if int(r.get("type",0))==shop_type]
        if shop_type==4:
            candidates=[r for r in candidates if get_house_count(house,int(r.get("item_id",0)))<=0]
        if shop_type==1:
            candidates=[r for r in candidates if not (_dudu_one_time_tool_row(r) and get_house_count(house,int(r.get("item_id",0)))>0)]
        featured.extend(pick(candidates,1,f"type{shop_type}"))
    featured.extend(pick([r for r in rows if int(r.get("type",0))==8],int(gp.get("dudu_general_slots",3) or 3),"type8"))
    gift_rows=[r for r in rows if int(r.get("type",0))==999 and int(r.get("item_id",0)) in DUDU_FURNITURE_GIFT_TARGETS and not _dudu_gift_is_owned(state,int(r.get("item_id",0)),furn)]
    featured.extend(pick(gift_rows,int(gp.get("dudu_special_gift_slots",1) or 1),"special_gift"))
    selected=(featured+pick(wood_rows,1,"wood")+
              pick(seed_rows,int(gp.get("dudu_seed_slots",3) or 3),"seed")+
              pick(fert_rows,int(gp.get("dudu_fertilizer_slots",2) or 2),"fert"))
    if spring_offer:
        selected = [row for row in selected if int(row.get("item_id", 0) or 0) != 10102]
    regular_slots = max(0, slot_limit - int(spring_offer) - len(starter_ids))
    if len(selected) > regular_slots:
        selected = pick(selected, regular_slots, "total")
    selected = [db[str(sid)] for sid in starter_ids[:slot_limit-int(spring_offer)]] + selected
    if spring_offer:
        selected.append(db["28"])
    shop["shop_list"]=[{"shop_id":int(r["id"]),"item_id":int(r["item_id"]),"num":1} for r in selected]
    shop["start_time"]=arrive; shop["leave_time"]=departure
    save_state(state,"garden.dudu_refresh"); return True


def apply_furniture_shop_buy(state: dict, shop_id: int) -> tuple[bool,str,int|None,int|None,int|None,bool]:
    db=_cfg_dict("furnitureShopData_json.json"); row=db.get(str(int(shop_id)))
    if not isinstance(row,dict): return False,"unknown_shop",None,None,None,False
    furn=get_furniture_data(state,"furniture.load_furniture",{"shop":{"start_time":0,"leave_time":0,"shop_list":[]}}); shop=furn.setdefault("shop",{}); entry=next((x for x in shop.get("shop_list",[]) if isinstance(x,dict) and int(x.get("shop_id",-1))==int(shop_id)),None)
    if not entry or int(entry.get("num",0) or 0)<=0: return False,"not_in_current_shop",None,None,None,False
    price=int(row.get("price",0) or 0); iid=int(row.get("item_id",0) or 0); res,_=get_clover_parts(state); total=int(res.get("clover_point",0) or 0)
    _,house,_,_=get_item_parts(state); owned=get_house_count(house,iid)
    if shop_id in DUDU_STARTER_KITS:
        if shop_id not in _dudu_starter_offers(state, house):
            return False, "starter_already_owned", total, iid, None, False
        if total < price:
            return False, "not_enough_clover", total, iid, None, False
        for item_id in DUDU_STARTER_KITS[shop_id]:
            count = get_house_count(house, item_id)
            set_house_count(house, item_id, max(count, 1) if shop_id == 1 else count + 1)
        res["clover_point"] = total - price
        entry["num"] = 0
        state.setdefault("offline_meta", {}).setdefault("dudu_starter_kits", []).append(shop_id)
        save_state(state, f"furniture.buy_starter:{shop_id}")
        return True, "starter_kit", res["clover_point"], iid, None, False
    if _dudu_one_time_tool_row(row) and owned>0:
        entry["num"]=0
        save_state(state,f"furniture.duplicate_one_time_tool_refund:{shop_id}:{iid}")
        return True,"duplicate_one_time_tool_refunded",total,iid,owned,True
    if int(row.get("type",0))==4 and owned>0:
        entry["num"]=0
        save_state(state,f"furniture.duplicate_blueprint_refund:{shop_id}:{iid}")
        return True,"duplicate_blueprint_refunded",total,iid,owned,True
    if iid in DUDU_FURNITURE_GIFT_TARGETS:
        if _dudu_gift_is_owned(state,iid,furn):
            entry["num"]=0
            save_state(state,f"furniture.duplicate_gift_refund:{shop_id}:{iid}")
            return True,"duplicate_furniture_refunded",total,iid,None,True
        if total<price: return False,"not_enough_clover",total,iid,None,False
        res["clover_point"]=total-price
        entry["num"]=int(entry.get("num",0) or 0)-1
        gift_kind=_grant_dudu_furniture_gift(state,iid)
        if gift_kind=="flowerpot":
            sold_out=shop.setdefault("sold_out_specials",[])
            if not any(isinstance(row,dict) and int(row.get("shop_id",0) or 0)==int(shop_id) and int(row.get("start_time",0) or 0)==int(shop.get("start_time",0) or 0) for row in sold_out):
                sold_out.append({"shop_id":int(shop_id),"item_id":iid,"start_time":int(shop.get("start_time",0) or 0)})
        save_state(state,f"furniture.buy_special_gift:{shop_id}:{iid}")
        return True,"ok",total-price,iid,None,False
    if total<price: return False,"not_enough_clover",total,iid,None,False
    new=owned+1; set_house_count(house,iid,new); res["clover_point"]=total-price; entry["num"]=int(entry.get("num",0) or 0)-1; save_state(state,f"furniture.buy_shop:{shop_id}:{iid}")
    return True,"ok",total-price,iid,new,False


def apply_furniture_bench_change(state: dict, action: str, pos: int, item_id: int | None = None) -> tuple[bool, str, int | None, int | None]:
    if pos < 1 or pos > 10:
        return False, "bad_pos", None, None
    furn = _furniture_production_data(state)
    if bool(furn.get("bench_lock", False)):
        return False, "bench_locked", None, None
    bench = furn["bench"]
    index = pos - 1
    current = int(bench[index] if bench[index] is not None else -1)

    if action == "takeout":
        if current == -1:
            return True, "already_empty", None, None
        bench[index] = -1
        if pos <= FURNITURE_TOOL_SLOTS:
            save_state(state, f"furniture.takeout_bench:{pos}:{current}")
            return True, "ok", None, None
        _, house, _, _ = get_item_parts(state)
        total = get_house_count(house, current) + 1
        set_house_count(house, current, total)
        save_state(state, f"furniture.takeout_bench:{pos}:{current}")
        return True, "ok", current, total

    if action != "putin" or item_id is None or int(item_id) <= 0:
        return False, "bad_item", None, None
    if current != -1:
        return False, "slot_not_empty", None, None
    item_id = int(item_id)
    if pos <= FURNITURE_TOOL_SLOTS:
        bench[index] = item_id
        save_state(state, f"furniture.putin_bench:{pos}:{item_id}")
        return True, "ok", None, None

    _, house, _, _ = get_item_parts(state)
    owned = get_house_count(house, item_id)
    if owned <= 0:
        return False, "not_in_house", item_id, owned
    total = owned - 1
    set_house_count(house, item_id, total)
    bench[index] = item_id
    save_state(state, f"furniture.putin_bench:{pos}:{item_id}")
    return True, "ok", item_id, total


def collect_clover_net_overflow(state: dict, now: int | None = None, policy: dict | None = None) -> int:
    """Accumulate overflow clovers into the furniture pocket, not wallet.

    A mature courtyard slot keeps one visible clover in the field.  Additional
    full rebirth cycles may be caught by the net.  Crucially, cycles that occur
    while the net is full are *consumed and discarded*, not queued as debt.
    Otherwise emptying a full net and logging in again can replay old cycles
    and refill it instantly.

    Special plants are never auto-collected.
    """
    if now is None:
        now = int(time.time())
    if policy is None:
        policy = load_offline_policy()

    cp = policy.get("clover", {})
    gp = garden_policy(policy)
    if not bool(cp.get("net_enabled", True)):
        return 0

    limit = max(0, int(cp.get("net_max_credit_per_login", 100) or 0))
    capacity = max(1, int(gp.get("pocket_capacity", 50) or 50))
    if limit <= 0:
        return 0

    _, field = get_clover_parts(state)
    meta = state.setdefault("offline_meta", {})
    net_meta = meta.setdefault("clover_net", {})
    cursors = net_meta.setdefault("cursor", {})
    pocket = get_pocket_data(state)

    pocket_before = max(0, int(pocket.get("clover", 0) or 0))
    room = max(0, capacity - pocket_before)
    credit = 0
    eligible_cycles = 0
    discarded_cycles = 0
    cursor_changed = False

    for row in field:
        if not isinstance(row, dict):
            continue

        cid = int(row.get("clover_id", -1))
        if cid < 0:
            continue
        key = str(cid)
        element = int(row.get("element", 0) or 0)

        # Non-normal plants are not net-collected.  Keep their cursor current so
        # time spent as a special plant can never become normal-clover backlog.
        if element != 0:
            prev = int(cursors.get(key, now) or now)
            nxt = max(prev, int(now))
            if cursors.get(key) != nxt:
                cursors[key] = nxt
                cursor_changed = True
            continue

        last = int(row.get("last_harvest", 0) or 0)
        span = max(1, int(row.get("rebirth_span", 7200) or 7200))
        if last <= 0:
            if cursors.get(key) != int(now):
                cursors[key] = int(now)
                cursor_changed = True
            continue

        mature = last + span
        if int(now) < mature:
            continue

        cursor = max(int(cursors.get(key, mature) or mature), mature)
        cycles = max(0, (int(now) - cursor) // span)
        if cycles <= 0:
            continue

        # Always consume every elapsed overflow cycle, even if the pocket is
        # already full or the per-login safety cap has been reached.  This is
        # the key Stage 6F.3 fix: excess cycles expire instead of becoming debt.
        eligible_cycles += cycles
        new_cursor = cursor + cycles * span
        if int(cursors.get(key, cursor) or cursor) != new_cursor:
            cursors[key] = new_cursor
            cursor_changed = True

        allowance = max(0, min(limit - credit, room - credit))
        take = min(cycles, allowance)
        credit += take
        discarded_cycles += cycles - take

    if credit:
        pocket["clover"] = pocket_before + credit
        logging.info(
            "CLOVER POCKET CREDIT count=%d pocket=%d discarded=%d",
            credit,
            int(pocket["clover"]),
            discarded_cycles,
        )

    if cursor_changed or credit:
        net_meta["last_sweep"] = {
            "time": int(now),
            "eligible_cycles": int(eligible_cycles),
            "credited": int(credit),
            "discarded": int(discarded_cycles),
            "pocket_before": int(pocket_before),
            "pocket_after": int(pocket.get("clover", 0) or 0),
        }
        if credit:
            net_meta["last_credit"] = {"time": int(now), "count": int(credit)}
            save_state(state, f"clover.pocket:{credit}")
        elif discarded_cycles > 0:
            save_state(state, f"clover.pocket.discard:{discarded_cycles}")
        else:
            # Cursor initialization/reset only.  Persist it so a server restart
            # cannot resurrect old history.
            save_state(state, "clover.pocket.cursor")

    return credit


def migrate_stage6f3_clover_net_cursor(state: dict, now: int | None = None) -> bool:
    """One-time repair for stale net cursors produced before Stage 6F.3.

    Preserve the currently stored pocket amount, but reset every per-slot net
    cursor to the upgrade time.  This intentionally discards only historical
    overflow debt left by the old bug; it does not alter courtyard harvest
    timestamps or rebirth spans.
    """
    if now is None:
        now = int(time.time())
    meta = state.setdefault("offline_meta", {})
    if meta.get("stage6f3_clover_net_cursor_repaired"):
        return False

    _, field = get_clover_parts(state)
    net_meta = meta.setdefault("clover_net", {})
    cursors = net_meta.setdefault("cursor", {})
    repaired = 0
    for row in field:
        if not isinstance(row, dict):
            continue
        cid = int(row.get("clover_id", -1))
        if cid < 0:
            continue
        cursors[str(cid)] = int(now)
        repaired += 1

    pocket = get_pocket_data(state)
    meta["stage6f3_clover_net_cursor_repaired"] = {
        "at": int(now),
        "slots": int(repaired),
        "pocket_preserved": int(pocket.get("clover", 0) or 0),
    }
    state["format_version"] = max(int(state.get("format_version", 2) or 2), 17)
    save_state(state, "migrate.stage6f3.clover_net_cursor")
    logging.warning(
        "STAGE6F3 CLOVER NET REPAIR: reset %d cursors at %d; pocket_preserved=%d",
        repaired,
        int(now),
        int(pocket.get("clover", 0) or 0),
    )
    return True


def album_picture_by_instance_id(state: dict, instance_id: int) -> dict | None:
    full = state.setdefault("offline_meta", {}).setdefault("album_full", {})
    cached = full.get(str(int(instance_id)))
    if isinstance(cached, dict):
        pic = copy.deepcopy(cached)
        if int(pic.get("pic_id", -1)) in (100, 101):
            positions = _picture_position_data()
            for layer in _to_list(pic.get("layers")):
                values = layer.get("layer") if isinstance(layer, dict) else None
                if isinstance(values, list) and len(values) >= 3:
                    row = positions.get(str(values[0]), {})
                    xy = row.get("xy", []) if isinstance(row, dict) else []
                    if xy:
                        values[1:3] = xy[0][:2]
        return pic
    row = next((x for x in get_album_index(state) if int(x.get("id", -1)) == int(instance_id)), None)
    if not isinstance(row, dict):
        return None
    pic_id = int(row.get("pic_id", -1))
    cfg = picture_by_id(pic_id)
    if cfg is None:
        return None
    # We cannot reconstruct every historical random companion choice, but the
    # preserved official Picture/resources tables can rebuild a valid postcard
    # layer set for every known pic_id. Preserve the original instance id.
    return {"id": int(instance_id), "pic_id": pic_id, "layers": build_picture_layers(cfg,state=state,label=f"album:{instance_id}:{pic_id}"), "for_ads": False}


def album_full_index_payload(state: dict) -> dict:
    if GALLERY_MODE:
        return {"id_list": [
            album_picture_by_instance_id(state, int(row["id"]))
            for row in get_album_index(state)
        ]}
    push = find_push(state, "album.load_all")
    if push and isinstance(push.get("data"), dict):
        return copy.deepcopy(push["data"])
    return {"id_list": copy.deepcopy(get_album_index(state))}


def album_page_payload(state: dict, start: int, count: int) -> dict:
    rows = get_album_index(state)
    start = max(1, int(start))
    count = max(0, int(count))
    selected = rows[start - 1:start - 1 + count]
    pictures = []
    for row in selected:
        pic = album_picture_by_instance_id(state, int(row.get("id", -1)))
        if pic is not None:
            pictures.append(pic)
    return {"start": start, "total": len(rows), "pictures": pictures}


# ---------------------------------------------------------------------------
# Stage 6H: exact historical album rescue + dynamic-photo state machine
# ---------------------------------------------------------------------------

def migrate_stage6h_album_official_layers(state: dict) -> bool:
    """Import the exact 200 historical full-layer records rescued from production.

    Only records whose instance id AND pic_id still match the current album index
    are applied. This prevents stale official data from being attached to a
    picture the user later replaced or moved.
    """
    meta = state.setdefault("offline_meta", {})
    if meta.get("stage6h_album_full_rescued"):
        return False

    obj = load_static_json("album_official_full_20260912.json")
    rows = obj.get("pictures", []) if isinstance(obj, dict) else []
    current = {
        int(r.get("id", -1)): int(r.get("pic_id", -1))
        for r in get_album_index(state)
        if isinstance(r, dict)
    }
    full = meta.setdefault("album_full", {})
    matched = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        iid = int(row.get("id", -1))
        pid = int(row.get("pic_id", -1))
        if iid < 0 or current.get(iid) != pid:
            continue
        full[str(iid)] = {
            "id": iid,
            "pic_id": pid,
            "layers": copy.deepcopy(row.get("layers", [])),
            "for_ads": bool(row.get("for_ads", False)),
        }
        matched += 1

    meta["stage6h_album_full_rescued"] = {
        "at": int(time.time()),
        "matched": int(matched),
        "source_count": len(rows),
    }
    state["format_version"] = max(int(state.get("format_version", 2) or 2), 18)
    save_state(state, f"migrate.stage6h.album_full:{matched}")
    logging.warning(
        "STAGE6H ALBUM RESCUE matched=%d source=%d",
        matched,
        len(rows),
    )
    return True


def get_animpicture_state(state: dict) -> dict:
    push = find_push(state, "animpicture.load")
    if not push:
        push = {
            "cmd": "animpicture.load",
            "data": {
                "page_num": 1,
                "item_num": 0,
                "guide": 5,
                "pic_list": [],
                "making_index": 0,
            },
        }
        state.setdefault("sync_pushes", []).append(push)
    data = push.setdefault("data", {})
    if not isinstance(data, dict):
        data = {
            "page_num": 1,
            "item_num": 0,
            "guide": 5,
            "pic_list": [],
            "making_index": 0,
        }
        push["data"] = data

    pics = data.get("pic_list", [])
    if isinstance(pics, dict):
        # Utils.convertArrayAll() accepts old object-like arrays; normalize local
        # persistence to a stable numeric list.
        try:
            keys = sorted(pics, key=lambda x: int(x))
            pics = [pics[k] for k in keys]
        except Exception:
            pics = list(pics.values())
        data["pic_list"] = pics
    elif not isinstance(pics, list):
        data["pic_list"] = []

    data["page_num"] = max(1, int(data.get("page_num", 1) or 1))
    data["item_num"] = max(0, int(data.get("item_num", 0) or 0))
    data["guide"] = max(0, int(data.get("guide", 0) or 0))
    data["making_index"] = max(0, int(data.get("making_index", 0) or 0))

    for row in data["pic_list"]:
        if not isinstance(row, dict):
            continue
        if not isinstance(row.get("pictures"), list):
            row["pictures"] = _to_list(row.get("pictures"))
        if not isinstance(row.get("exp_pic"), list):
            row["exp_pic"] = _to_list(row.get("exp_pic"))
        row["put_num"] = max(0, int(row.get("put_num", len(row["pictures"])) or 0))
        row["phase"] = max(0, int(row.get("phase", 0) or 0))
        row["exp"] = max(0, int(row.get("exp", 0) or 0))
    return data


def animpicture_config() -> dict:
    obj = load_static_json("animpictureData_json.json")
    return obj if isinstance(obj, dict) else {}


def _animpicture_template(template_id: int) -> dict | None:
    rows = animpicture_config().get("list", {})
    if not isinstance(rows, dict):
        return None
    row = rows.get(str(int(template_id)))
    return row if isinstance(row, dict) else None


def _animpicture_template_for_pic(pic_id: int) -> int | None:
    mp = animpicture_config().get("pic_map", {})
    if not isinstance(mp, dict):
        return None
    value = mp.get(str(int(pic_id)))
    try:
        return int(value)
    except Exception:
        return None


ANIMPICTURE_GUIDE_COMPLETE = 6


def unlock_animpicture_guide(state: dict) -> bool:
    """Start the local introduction once a compatible photo is in the album."""
    data = get_animpicture_state(state)
    if GALLERY_MODE:
        if data["guide"] >= ANIMPICTURE_GUIDE_COMPLETE:
            return False
        data["guide"] = ANIMPICTURE_GUIDE_COMPLETE
        save_state(state, "animpicture.gallery_ready")
        return True
    if data["guide"] != 0:
        return False
    if not any(_animpicture_template_for_pic(int(row.get("pic_id", -1))) is not None for row in get_album_index(state)):
        return False
    data["guide"] = 1
    save_state(state, "animpicture.unlock_guide")
    return True


def _animpicture_entry(data: dict, anim_index: int) -> tuple[int, dict | None]:
    idx = int(anim_index) - 1
    pics = data.get("pic_list", [])
    if idx < 0 or idx >= len(pics):
        return idx, None
    row = pics[idx]
    return idx, row if isinstance(row, dict) else None


def _album_pop_picture_no_save(state: dict, instance_id: int) -> dict | None:
    rows = _album_index_mutable(state)
    idx = next(
        (
            i
            for i, r in enumerate(rows)
            if isinstance(r, dict) and int(r.get("id", -1)) == int(instance_id)
        ),
        -1,
    )
    if idx < 0:
        return None
    pic = album_picture_by_instance_id(state, int(instance_id))
    if pic is None:
        return None
    rows.pop(idx)
    state.setdefault("offline_meta", {}).setdefault("album_full", {}).pop(
        str(int(instance_id)), None
    )
    try:
        _, _, _, misc = get_role_parts(state)
        misc["picture_cnt"] = max(0, int(misc.get("picture_cnt", 0) or 0) - 1)
    except Exception:
        pass
    return pic


def _album_append_picture_no_save(state: dict, pic: dict) -> bool:
    if not isinstance(pic, dict):
        return False
    iid = int(pic.get("id", -1))
    pid = int(pic.get("pic_id", -1))
    if iid <= 0 or pid < 0:
        return False
    rows = _album_index_mutable(state)
    if not any(
        isinstance(r, dict) and int(r.get("id", -1)) == iid
        for r in rows
    ):
        rows.append({"id": iid, "pic_id": pid})
        try:
            _, _, _, misc = get_role_parts(state)
            misc["picture_cnt"] = int(misc.get("picture_cnt", 0) or 0) + 1
        except Exception:
            pass
    state.setdefault("offline_meta", {}).setdefault("album_full", {})[str(iid)] = copy.deepcopy(pic)
    return True


def animpicture_select_pic(state: dict, instance_id: int) -> tuple[bool, str]:
    data = get_animpicture_state(state)
    if len(data["pic_list"]) >= int(data.get("page_num", 1) or 1):
        return False, "page_full"

    pic = album_picture_by_instance_id(state, int(instance_id))
    if pic is None:
        return False, "picture_missing"
    template_id = _animpicture_template_for_pic(int(pic.get("pic_id", -1)))
    if template_id is None:
        return False, "not_dynamic_source"
    if any(
        isinstance(x, dict) and int(x.get("id", -1)) == template_id
        for x in data["pic_list"]
    ):
        return False, "already_made"

    cfg = _animpicture_template(template_id)
    if cfg is None:
        return False, "template_missing"
    popped = _album_pop_picture_no_save(state, int(instance_id))
    if popped is None:
        return False, "picture_missing"

    phase_list = _to_list(cfg.get("phase_list"))
    entry = {
        "id": int(template_id),
        "put_num": 0,
        "pictures": [],
        "phase": 0 if len(phase_list) <= 1 else 1,
        "exp": 0,
        "exp_pic": [],
    }
    data["pic_list"].append(entry)
    # The mobile client converts this persisted one-based value to its
    # zero-based active-page index when it receives animpicture.load.
    data["making_index"] = len(data["pic_list"])
    state.setdefault("offline_meta", {}).setdefault("animpicture_source", {})[
        str(int(template_id))
    ] = copy.deepcopy(popped)
    state["format_version"] = max(int(state.get("format_version", 2) or 2), 18)
    save_state(state, f"animpicture.select:{instance_id}:template{template_id}")
    return True, "ok"


def _animpicture_related_pic_ids(entry: dict) -> set[int]:
    cfg = _animpicture_template(int(entry.get("id", -1)))
    if not cfg:
        return set()
    return {int(x) for x in _to_list(cfg.get("pic_list"))}


def animpicture_add_pics(state: dict, anim_index: int, ids: list[int]) -> tuple[bool, str]:
    data = get_animpicture_state(state)
    _, entry = _animpicture_entry(data, anim_index)
    if entry is None or int(entry.get("phase", 0) or 0) <= 0:
        return False, "bad_anim_index"
    related = _animpicture_related_pic_ids(entry)
    ids = [int(x) for x in ids]
    if len(entry["exp_pic"]) + len(ids) > 5:
        return False, "too_many"
    staged = []
    for iid in ids:
        pic = album_picture_by_instance_id(state, iid)
        if pic is None:
            return False, "invalid_picture"
        if int(pic.get("pic_id", -1)) not in related:
            return False, "invalid_picture"
        staged.append(pic)
    for pic in staged:
        popped = _album_pop_picture_no_save(state, int(pic["id"]))
        if popped is None:
            return False, "picture_missing"
        entry["exp_pic"].append(popped)
    save_state(state, f"animpicture.add_pic:{anim_index}:{ids}")
    return True, "ok"


def animpicture_remove_exp_pic(
    state: dict,
    anim_index: int,
    pic_index: int,
    is_delete: bool,
) -> tuple[bool, str]:
    data = get_animpicture_state(state)
    _, entry = _animpicture_entry(data, anim_index)
    if entry is None:
        return False, "bad_anim_index"
    idx = int(pic_index) - 1
    if idx < 0 or idx >= len(entry["exp_pic"]):
        return False, "bad_pic_index"
    pic = entry["exp_pic"].pop(idx)
    if not bool(is_delete):
        _album_append_picture_no_save(state, pic)
    save_state(
        state,
        f"animpicture.remove_pic:{anim_index}:{pic_index}:delete{int(bool(is_delete))}",
    )
    return True, "ok"


def _animpicture_advance_phase(
    entry: dict,
    gain: int,
) -> tuple[int, int, bool]:
    cfg = _animpicture_template(int(entry.get("id", -1)))
    if not cfg:
        return int(entry.get("phase", 0) or 0), int(entry.get("exp", 0) or 0), False
    phases = _to_list(cfg.get("phase_list"))
    phase = int(entry.get("phase", 0) or 0)
    exp = int(entry.get("exp", 0) or 0)
    gain = max(0, int(gain))
    if phase <= 0:
        return 0, exp, False

    completed = False
    safety = 0
    while phase > 0 and safety < len(phases) + 3:
        safety += 1
        if phase > len(phases):
            phase = 0
            completed = True
            break
        threshold = int(phases[phase - 1].get("exp", 0) or 0)

        # A zero threshold is a free/terminal stage in the preserved static
        # table. Advance without inventing an EXP requirement.
        if threshold <= 0:
            phase += 1
            exp = 0
            if phase > len(phases):
                phase = 0
                completed = True
                break
            continue

        exp += gain
        gain = 0
        if exp < threshold:
            break

        overflow = exp - threshold
        phase += 1
        exp = 0
        if phase > len(phases):
            phase = 0
            completed = True
            break
        gain = overflow

    entry["phase"] = int(phase)
    entry["exp"] = int(exp)
    return int(phase), int(exp), completed


def animpicture_use_item(
    state: dict,
    anim_index: int,
    item_id: int,
    policy: dict | None = None,
) -> tuple[bool, str, dict]:
    if policy is None:
        policy = load_offline_policy()
    item_id = int(item_id)
    if item_id not in ANIM_PICTURE_DEVELOPER_IDS:
        return False, "bad_item", {"phase": -1, "exp": 0}

    data = get_animpicture_state(state)
    _, entry = _animpicture_entry(data, anim_index)
    if entry is None or int(entry.get("phase", 0) or 0) <= 0:
        return False, "bad_anim_index", {"phase": -1, "exp": 0}

    _, house, _, _ = get_item_parts(state)
    old_count = get_house_count(house, item_id)
    if old_count <= 0:
        return False, "no_item", {"phase": int(entry.get("phase", 0)), "exp": int(entry.get("exp", 0))}

    ap = policy.get("animpicture", {})
    gains = ap.get("developer_exp", {})
    gain = int(gains.get(str(item_id), gains.get(item_id, 0)) or 0) if isinstance(gains, dict) else 0
    gain += int(ap.get("extra_photo_bonus_per_photo", 0) or 0) * len(entry.get("exp_pic", []))
    if gain <= 0:
        return False, "uncalibrated_gain", {"phase": int(entry.get("phase", 0)), "exp": int(entry.get("exp", 0))}

    set_house_count(house, item_id, old_count - 1)
    phase, exp, completed = _animpicture_advance_phase(entry, gain)

    if completed:
        for pic in list(entry.get("exp_pic", [])):
            _album_append_picture_no_save(state, pic)
        entry["exp_pic"] = []
        data["item_num"] = int(data.get("item_num", 0) or 0) + 1
        data["making_index"] = 0
        cooking_record_task_progress(state, 6)

    save_state(
        state,
        f"animpicture.use_item:{anim_index}:{item_id}:phase{phase}:exp{exp}",
    )
    return True, "ok", {
        "phase": int(phase),
        "exp": int(exp),
        "item_id": item_id,
        "item_count": get_house_count(house, item_id),
        "completed": bool(completed),
    }


def animpicture_get_item(state: dict) -> tuple[bool, int, int]:
    data = get_animpicture_state(state)
    pending = int(data.get("item_num", 0) or 0)
    if pending <= 0:
        return True, 0, get_house_count(get_item_parts(state)[1], ANIM_PICTURE_ALBUM_ITEM_ID)
    _, house, _, _ = get_item_parts(state)
    total = get_house_count(house, ANIM_PICTURE_ALBUM_ITEM_ID) + pending
    set_house_count(house, ANIM_PICTURE_ALBUM_ITEM_ID, total)
    data["item_num"] = 0
    save_state(state, f"animpicture.get_item:{pending}")
    return True, pending, total


def animpicture_guide(state: dict) -> int:
    data = get_animpicture_state(state)
    data["guide"] = int(data.get("guide", 0) or 0) + 1
    save_state(state, f"animpicture.guide:{data['guide']}")
    return int(data["guide"])


def animpicture_open_album(state: dict, anim_index: int) -> tuple[bool, str, int]:
    data = get_animpicture_state(state)
    _, entry = _animpicture_entry(data, anim_index)
    if entry is None or int(entry.get("phase", 0) or 0) != 0:
        return False, "bad_anim_index", -1

    base = animpicture_config().get("base_info", {})
    max_slots = max(0, int(base.get("album_num", 4) or 4))
    if len(entry["pictures"]) >= max_slots:
        return False, "slot_full", -1

    _, house, _, _ = get_item_parts(state)
    old = get_house_count(house, ANIM_PICTURE_ALBUM_ITEM_ID)
    if old <= 0:
        return False, "no_item", old
    set_house_count(house, ANIM_PICTURE_ALBUM_ITEM_ID, old - 1)
    entry["pictures"].append({"id": 0, "pic_id": 0})
    entry["put_num"] = int(entry.get("put_num", 0) or 0) + 1
    save_state(state, f"animpicture.open_album:{anim_index}")
    return True, "ok", old - 1


def animpicture_album_add_pic(
    state: dict,
    anim_index: int,
    pic_index: int,
    instance_id: int,
) -> tuple[bool, str]:
    data = get_animpicture_state(state)
    _, entry = _animpicture_entry(data, anim_index)
    if entry is None or int(entry.get("phase", 0) or 0) != 0:
        return False, "bad_anim_index"
    idx = int(pic_index) - 1
    if idx < 0 or idx >= len(entry["pictures"]):
        return False, "bad_pic_index"
    if isinstance(entry["pictures"][idx], dict) and int(entry["pictures"][idx].get("id", 0) or 0) > 0:
        return False, "slot_occupied"

    pic = album_picture_by_instance_id(state, int(instance_id))
    if pic is None:
        return False, "invalid_picture"
    related = _animpicture_related_pic_ids(entry)
    if int(pic.get("pic_id", -1)) not in related:
        return False, "invalid_picture"
    popped = _album_pop_picture_no_save(state, int(instance_id))
    if popped is None:
        return False, "picture_missing"
    entry["pictures"][idx] = popped
    save_state(state, f"animpicture.album_add:{anim_index}:{pic_index}:{instance_id}")
    return True, "ok"


def animpicture_album_remove_pic(
    state: dict,
    anim_index: int,
    pic_index: int,
    is_delete: bool,
) -> tuple[bool, str]:
    data = get_animpicture_state(state)
    _, entry = _animpicture_entry(data, anim_index)
    if entry is None or int(entry.get("phase", 0) or 0) != 0:
        return False, "bad_anim_index"
    idx = int(pic_index) - 1
    if idx < 0 or idx >= len(entry["pictures"]):
        return False, "bad_pic_index"
    pic = entry["pictures"][idx]
    if not isinstance(pic, dict) or int(pic.get("id", 0) or 0) <= 0:
        return True, "ok"
    if not bool(is_delete):
        _album_append_picture_no_save(state, pic)
    entry["pictures"][idx] = {"id": 0, "pic_id": 0}
    save_state(
        state,
        f"animpicture.album_remove:{anim_index}:{pic_index}:delete{int(bool(is_delete))}",
    )
    return True, "ok"




# ---------------------------------------------------------------------------
# Stage 6E: gift box, anonymous social visitors, calendar rewards, encyclopedia
# ---------------------------------------------------------------------------

def giftbox_policy(policy: dict | None = None) -> dict:
    if policy is None:
        policy = load_offline_policy()
    return policy.get("giftbox_friend", policy.get("giftbox_social", {})) if isinstance(policy, dict) else {}


def get_giftbox_state(state: dict) -> dict:
    meta = state.setdefault("offline_meta", {})
    box = meta.setdefault("gift_box", {"pictures": [], "specialtys": []})
    if not isinstance(box, dict):
        box = {"pictures": [], "specialtys": []}
        meta["gift_box"] = box
    if not isinstance(box.get("pictures"), list):
        box["pictures"] = []
    if not isinstance(box.get("specialtys"), list):
        box["specialtys"] = []
    return box


def giftbox_payload(state: dict) -> dict:
    box = get_giftbox_state(state)
    return {
        "pictures": copy.deepcopy(box["pictures"]),
        "specialtys": copy.deepcopy(box["specialtys"]),
    }


def _giftbox_specialty_count(box: dict) -> int:
    total = 0
    for row in box.get("specialtys", []):
        if isinstance(row, dict):
            total += max(0, int(row.get("count", 0) or 0))
    return total


def _giftbox_add_specialty(box: dict, item_id: int, count: int = 1) -> None:
    for row in box["specialtys"]:
        if isinstance(row, dict) and int(row.get("item_id", -1)) == int(item_id):
            row["count"] = int(row.get("count", 0) or 0) + int(count)
            return
    box["specialtys"].append({"item_id": int(item_id), "count": int(count)})


def _giftbox_take_specialty(box: dict, item_id: int, count: int = 1) -> bool:
    for i, row in enumerate(list(box["specialtys"])):
        if isinstance(row, dict) and int(row.get("item_id", -1)) == int(item_id):
            old = int(row.get("count", 0) or 0)
            if old < int(count):
                return False
            new = old - int(count)
            if new <= 0:
                box["specialtys"].pop(i)
            else:
                row["count"] = new
            return True
    return False


def _giftbox_has_contents(state: dict) -> bool:
    box = get_giftbox_state(state)
    return bool(box.get("pictures")) or _giftbox_specialty_count(box) > 0


def _schedule_giftbox_social(state: dict, now: int | None = None, policy: dict | None = None) -> None:
    if now is None:
        now = int(time.time())
    gp = giftbox_policy(policy)
    meta = state.setdefault("offline_meta", {}).setdefault("giftbox_friend", {})
    if not bool(gp.get("enabled", True)) or not _giftbox_has_contents(state):
        meta.pop("next_check", None)
        return
    if int(meta.get("next_check", 0) or 0) <= 0:
        meta["next_check"] = int(now) + max(60, int(gp.get("delay_seconds", gp.get("visit_delay_seconds", 1800)) or 1800))


def _album_index_mutable(state: dict) -> list[dict]:
    push = find_push(state, "album.load_all")
    if not push:
        push = {"cmd": "album.load_all", "data": {"id_list": []}}
        state.setdefault("sync_pushes", []).append(push)
    data = push.setdefault("data", {})
    if not isinstance(data, dict):
        data = {"id_list": []}; push["data"] = data
    rows = data.setdefault("id_list", [])
    if not isinstance(rows, list):
        rows = _to_list(rows); data["id_list"] = rows
    return rows



ALBUM_RECOVER_CAPACITY = 6


def get_album_recover_list(state: dict) -> list[dict]:
    """Newest-first recycle bin used by album.load_recover.

    The historical client renders at most six slots.  Deleted saved pictures
    and returned-but-not-saved pictures both enter this same queue.
    """
    meta = state.setdefault("offline_meta", {})
    rows = meta.setdefault("album_recover", [])
    if not isinstance(rows, list):
        rows = []
        meta["album_recover"] = rows
    # Normalize defensively and keep the newest six only.
    rows[:] = [x for x in rows if isinstance(x, dict) and "id" in x and "pic_id" in x][:ALBUM_RECOVER_CAPACITY]
    return rows


def reconcile_album_transfer_state(state: dict, *, save: bool = True) -> bool:
    """Remove recycle copies of pictures that are already in an active container."""
    active_ids = {
        int(row.get("id", -1)) for row in get_album_index(state)
        if isinstance(row, dict)
    }
    active_ids.update(
        int(row.get("id", -1)) for row in get_giftbox_state(state).get("pictures", [])
        if isinstance(row, dict)
    )
    rows = get_album_recover_list(state)
    repaired = [row for row in rows if int(row.get("id", -1)) not in active_ids]
    if repaired == rows:
        return False
    rows[:] = repaired
    if save:
        save_state(state, "album.reconcile_transfers")
    return True


def push_album_recover(state: dict, pic: dict) -> None:
    """Put one full picture into the recycle bin, newest first."""
    if not isinstance(pic, dict):
        return
    try:
        iid = int(pic.get("id", -1))
        pid = int(pic.get("pic_id", -1))
    except Exception:
        return
    if iid < 0 or pid < 0:
        return
    rows = get_album_recover_list(state)
    rows[:] = [x for x in rows if int(x.get("id", -1)) != iid]
    rows.insert(0, copy.deepcopy(pic))
    del rows[ALBUM_RECOVER_CAPACITY:]


def album_recover_payload(state: dict) -> dict:
    return {"pictures": copy.deepcopy(get_album_recover_list(state)[:ALBUM_RECOVER_CAPACITY])}


def apply_album_recover(state: dict, instance_id: int) -> tuple[bool, str]:
    rows = get_album_recover_list(state)
    idx = next(
        (i for i, p in enumerate(rows)
         if isinstance(p, dict) and int(p.get("id", -1)) == int(instance_id)),
        -1,
    )
    if idx < 0:
        # Idempotent retry after a successful restore.
        if any(
            isinstance(r, dict) and int(r.get("id", -1)) == int(instance_id)
            for r in get_album_index(state)
        ):
            return True, "already_restored"
        return False, "not_found"

    pic = rows.pop(idx)
    album = _album_index_mutable(state)
    if not any(
        isinstance(r, dict) and int(r.get("id", -1)) == int(instance_id)
        for r in album
    ):
        album.append(
            {
                "id": int(pic["id"]),
                "pic_id": int(pic.get("pic_id", 0) or 0),
            }
        )
    state.setdefault("offline_meta", {}).setdefault("album_full", {})[
        str(int(instance_id))
    ] = copy.deepcopy(pic)
    try:
        _, _, _, misc = get_role_parts(state)
        # Keep this authoritative count in lockstep with the actual index.
        misc["picture_cnt"] = len(album)
    except Exception:
        pass
    save_state(state, f"album.recover:{instance_id}")
    return True, "ok"


def apply_album_delete(state: dict, instance_id: int) -> bool:
    rows = _album_index_mutable(state)
    idx = next((i for i, r in enumerate(rows) if isinstance(r, dict) and int(r.get("id", -1)) == int(instance_id)), -1)
    if idx < 0:
        return True

    # Capture the exact full-layer picture before removing it from the album.
    pic = album_picture_by_instance_id(state, int(instance_id))
    rows.pop(idx)
    state.setdefault("offline_meta", {}).setdefault("album_full", {}).pop(str(int(instance_id)), None)
    if pic is not None:
        push_album_recover(state, pic)
    try:
        _, _, _, misc = get_role_parts(state)
        misc["picture_cnt"] = len(rows)
    except Exception:
        pass
    save_state(state, f"album.delete:{instance_id}")
    return True


def apply_album_to_gift(state: dict, instance_id: int, policy: dict | None = None) -> tuple[bool, int]:
    gp = giftbox_policy(policy)
    cap = max(1, int(gp.get("picture_capacity", 30) or 30))
    box = get_giftbox_state(state)
    if len(box["pictures"]) >= cap:
        return False, 100  # client maps code 100 to picture gift-box full
    pic = album_picture_by_instance_id(state, int(instance_id))
    if pic is None:
        return False, 1
    album = _album_index_mutable(state)
    idx = next(
        (i for i, row in enumerate(album)
         if isinstance(row, dict) and int(row.get("id", -1)) == int(instance_id)),
        -1,
    )
    if idx < 0:
        return False, 1
    # Moving a saved photo into the gift box is not deletion and must never
    # create a recycle-bin entry.
    album.pop(idx)
    state.setdefault("offline_meta", {}).setdefault("album_full", {}).pop(str(int(instance_id)), None)
    try:
        _, _, _, misc = get_role_parts(state)
        misc["picture_cnt"] = len(album)
    except Exception:
        pass
    box["pictures"].append(copy.deepcopy(pic))
    _schedule_giftbox_social(state, policy=policy)
    save_state(state, f"travel.album_to_gift:{instance_id}")
    return True, 0


def apply_gift_to_album(state: dict, instance_id: int) -> tuple[bool, int]:
    box = get_giftbox_state(state)
    idx = next((i for i, p in enumerate(box["pictures"]) if isinstance(p, dict) and int(p.get("id", -1)) == int(instance_id)), -1)
    if idx < 0:
        return True, 0
    # The historical client has its own album-full UI. Offline preservation uses
    # the original client picture count, with a generous 1000 hard guard.
    rows = _album_index_mutable(state)
    if len(rows) >= 1000:
        return False, 101
    pic = box["pictures"].pop(idx)
    already_in_album = any(
        isinstance(row, dict) and int(row.get("id", -1)) == int(pic["id"])
        for row in rows
    )
    if not already_in_album:
        rows.append({"id": int(pic["id"]), "pic_id": int(pic.get("pic_id", 0) or 0)})
    state.setdefault("offline_meta", {}).setdefault("album_full", {})[str(int(pic["id"]))] = copy.deepcopy(pic)
    try:
        _, _, _, misc = get_role_parts(state)
        misc["picture_cnt"] = len(rows)
    except Exception:
        pass
    reconcile_album_transfer_state(state, save=False)
    save_state(state, f"travel.gift_to_album:{instance_id}")
    return True, 0


def apply_gift_delete_picture(state: dict, instance_id: int) -> bool:
    box = get_giftbox_state(state)
    idx = next(
        (i for i, pic in enumerate(box["pictures"])
         if isinstance(pic, dict) and int(pic.get("id", -1)) == int(instance_id)),
        -1,
    )
    if idx >= 0:
        pic = box["pictures"].pop(idx)
        push_album_recover(state, pic)
        save_state(state, f"travel.gift_delete_album:{instance_id}")
    return True


def apply_bag_to_gift(state: dict, item_id: int, policy: dict | None = None) -> tuple[bool, int, int]:
    item_id = int(item_id)
    cfg = item_cfg(item_id) or {}
    # Official PlayerBag only exposes the gift action for ItemType.Specialty.
    if int(cfg.get("type", -1)) != 3:
        return False, 1, 0
    gp = giftbox_policy(policy)
    cap = max(1, int(gp.get("specialty_capacity", 30) or 30))
    box = get_giftbox_state(state)
    if _giftbox_specialty_count(box) >= cap:
        return False, 102, 0
    _, house, _, _ = get_item_parts(state)
    old = get_house_count(house, item_id)
    if old <= 0:
        return False, 1, old
    set_house_count(house, item_id, old - 1)
    _giftbox_add_specialty(box, item_id, 1)
    _schedule_giftbox_social(state, policy=policy)
    save_state(state, f"travel.bag_to_gift:{item_id}")
    return True, 0, old - 1


def apply_gift_to_bag(state: dict, item_id: int) -> tuple[bool, int, int]:
    item_id = int(item_id)
    box = get_giftbox_state(state)
    if not _giftbox_take_specialty(box, item_id, 1):
        return False, 1, 0
    _, house, _, _ = get_item_parts(state)
    new = get_house_count(house, item_id) + 1
    set_house_count(house, item_id, new)
    save_state(state, f"travel.gift_to_bag:{item_id}")
    return True, 0, new



def migrate_stage6f1_lottery_phase171(state: dict, now: int | None = None, policy: dict | None = None) -> bool:
    """One-time calibration to the production phase captured on 2026-09-12.

    The imported snapshot was phase 170 (captured before the weekend rollover), while
    production on Sat 2026-09-12 was independently captured as phase 171.  Stage 6F
    could therefore reopen stale phase 170 and miss the phase-171 extra item.  This
    migration repairs ONLY that observed 170 -> 171 boundary; future rollover/result
    generation remains intentionally unimplemented until more production evidence.
    """
    if now is None:
        now = int(time.time())
    if policy is None:
        policy = load_offline_policy()
    meta = state.setdefault("offline_meta", {})
    if meta.get("stage6f1_lottery_phase171_migrated"):
        return False
    # 2026-09-12 00:00:00 +08:00, the captured phase-171 weekend.
    if int(now) < 1789142400:
        return False
    data = get_lottery_data(state)
    if int(data.get("phase", 0) or 0) != 170:
        if int(data.get("phase", 0) or 0) >= 171:
            meta["stage6f1_lottery_phase171_migrated"] = {"at": int(now), "action": "already_current"}
        return False
    old_state = int(data.get("state", LOTTERY_OPEN) or 0)
    # If Stage 6F already opened stale phase 170 locally, undo exactly that known
    # +10 opening reward before resetting to the captured phase 171 Open state.
    undo = 0
    if old_state in (LOTTERY_SELECT, LOTTERY_COMPLETE):
        lp = policy.get("lottery", {}) if isinstance(policy, dict) else {}
        undo = max(0, int(lp.get("open_clover", 10) or 0))
        _, _, res, _ = get_role_parts(state)
        res["clover_point"] = max(0, int(res.get("clover_point", 0) or 0) - undo)
    data["last_phase"] = 170
    data["phase"] = 171
    data["state"] = LOTTERY_OPEN
    data["select_list"] = list(CAPTURED_LOTTERY_SELECT_LISTS[171])
    data["answer"] = []
    data["right_flag"] = []
    data["reward"] = []
    data["egg_num"] = 0
    data["extra_item"] = {"item_id": 0, "count": 0}
    meta["stage6f1_lottery_phase171_migrated"] = {"at": int(now), "from_phase": 170, "to_phase": 171, "undid_open_clover": undo}
    save_state(state, "migrate.stage6f1.lottery_phase171")
    logging.warning("STAGE6F1 LOTTERY CALIBRATION: phase 170 -> 171; stale_open_clover_undone=%d", undo)
    return True


def migrate_stage6f2_lottery_extra_mail(state: dict, now: int | None = None) -> bool:
    """Repair the single Stage 6F.1 claim that the server failed to persist.

    Stage 6F.1 parsed adsmgr.share using `type`/`id`, but ProtocolList names the
    request field `ads_type`.  In the observed 2026-09-12 local session the client
    therefore showed “邮箱有动静” and cleared its in-memory extra_item, while the
    server kept phase 171 extra_item=101x1 and no mail was created.

    This migration is intentionally narrow: it only matches that known session
    (phase 171, Complete, the captured first-row answer, pending 101x1, and the
    Stage 6F.1 phase migration marker).  It will never synthesize a reward for a
    generic future/pending Lottery state.
    """
    if now is None:
        now = int(time.time())
    meta = state.setdefault("offline_meta", {})
    if meta.get("stage6f2_lottery_extra_mail_repaired"):
        return False
    if not meta.get("stage6f1_lottery_phase171_migrated"):
        return False
    data = get_lottery_data(state)
    if int(data.get("phase", 0) or 0) != 171 or int(data.get("state", -1)) != LOTTERY_COMPLETE:
        return False
    if [int(x) for x in _to_list(data.get("answer"))] != CAPTURED_LOTTERY_SELECT_LISTS[171][:5]:
        return False
    extra = data.get("extra_item") if isinstance(data.get("extra_item"), dict) else {}
    if int(extra.get("item_id", 0) or 0) != 101 or int(extra.get("count", 0) or 0) != 1:
        return False
    # Idempotency guard in case a partially-written state already contains the mail.
    for mail in get_mail_list(state):
        if not isinstance(mail, dict):
            continue
        if mail.get("title") == "周末小插曲的礼物":
            for row in _to_list(mail.get("items")):
                if isinstance(row, dict) and int(row.get("item_id", 0) or 0) == 101 and int(row.get("count", 0) or 0) == 1:
                    data["extra_item"] = {"item_id": 0, "count": 0}
                    meta["stage6f2_lottery_extra_mail_repaired"] = {"at": int(now), "action": "mail_already_present", "mail_id": int(mail.get("id", 0) or 0)}
                    save_state(state, "migrate.stage6f2.lottery_extra_mail.already_present")
                    return True
    mail = create_lottery_extra_mail(state, 101, 1, reason="stage6f1_repair")
    data["extra_item"] = {"item_id": 0, "count": 0}
    meta["stage6f2_lottery_extra_mail_repaired"] = {"at": int(now), "mail_id": int(mail["id"]), "item_id": 101, "count": 1}
    state["format_version"] = max(int(state.get("format_version", 2) or 2), 16)
    save_state(state, "migrate.stage6f2.lottery_extra_mail")
    logging.warning(
        "STAGE6F2 LOTTERY EXTRA REPAIR: pending extra 101x1 -> mail id=%d",
        int(mail["id"]),
    )
    return True


# ---------------------------------------------------------------------------
# Stage 6E.2: split the two previously conflated social systems.
#   StoryModel      = encounters with other players' frogs (offline-anonymized)
#   TravelFriends   = 壁虎/刺猬/萤火虫 gift-box companion returns
#   VisitorModel    = province-flower courtyard visitor system (left untouched)
# ---------------------------------------------------------------------------

def story_policy(policy: dict | None = None) -> dict:
    if policy is None:
        policy = load_offline_policy()
    return policy.get("story_social", {}) if isinstance(policy, dict) else {}


def get_story_state(state: dict) -> dict:
    push = find_push(state, "story.load")
    if not push:
        push = {"cmd": "story.load", "data": {"stories": [], "new_story_id": 0}}
        state.setdefault("sync_pushes", []).append(push)
    data = push.setdefault("data", {})
    if not isinstance(data, dict):
        data = {"stories": [], "new_story_id": 0}
        push["data"] = data
    if not isinstance(data.get("stories"), list):
        data["stories"] = _to_list(data.get("stories"))
    data["new_story_id"] = int(data.get("new_story_id", 0) or 0)
    return data


def story_config_rows() -> list[dict]:
    cfg = load_static_json("story_json.json")
    rows = cfg.get("story", []) if isinstance(cfg, dict) else []
    return [x for x in rows if isinstance(x, dict)]


def story_social_payload(state: dict, policy: dict | None = None) -> dict:
    """Return StoryModel data with remote-player identity hidden in offline mode."""
    sp = story_policy(policy)
    data = copy.deepcopy(get_story_state(state))
    if not bool(sp.get("anonymize_existing_names", True)):
        return data
    generic = str(sp.get("generic_name", "旅途中的朋友") or "旅途中的朋友")
    no_return = not bool(sp.get("return_gift_required", False))
    for row in data.get("stories", []):
        if not isinstance(row, dict):
            continue
        row["name"] = generic
        # RelationshipView uses partner only as a grouping key.  A story-local
        # key preserves its layout without exposing the original player ID or
        # linking separate encounters back to the same remote account.
        row["partner"] = int(row.get("id", 0) or 0)
        # Do not rewrite the preserved source state. This outgoing-only value
        # suppresses the client button that asks the user to return a specialty.
        if no_return and int(row.get("gift", -1) or -1) == -1:
            row["gift"] = 0
    return data


def maybe_generate_story_social(state: dict, key: str, policy: dict | None = None, amulet_id: int = -1) -> dict | None:
    """Generate one missing official Story entry after an offline trip.

    The static client contains exactly 25 story definitions. Never invent duplicate
    IDs once a save already owns all of them (the user's imported save is 25/25).
    """
    if policy is None:
        policy = load_offline_policy()
    sp = story_policy(policy)
    if not bool(sp.get("enabled", True)):
        return None
    data = get_story_state(state)
    owned = {int(x.get("id", -1)) for x in data.get("stories", []) if isinstance(x, dict)}
    cfg_rows = story_config_rows()
    valid = [int(x.get("storyid", -1)) for x in cfg_rows if int(x.get("storyid", -1)) > 0]
    missing = [sid for sid in valid if sid not in owned]
    if not missing:
        logging.info("STORY SOCIAL complete=%d/%d no_new_story", len(owned & set(valid)), len(valid))
        return None
    cfg = load_static_json("story_json.json")
    official_pct = int(((cfg.get("const") or {}).get("friend_choice_percent", 25)) if isinstance(cfg, dict) else 25)
    base_pct=max(0, min(100, int(sp.get("encounter_percent", official_pct) or 0)))
    bonuses=sp.get("amulet_encounter_bonus_percent",{})
    bonus=max(0,int(bonuses.get(str(amulet_id),bonuses.get(amulet_id,0)) or 0)) if isinstance(bonuses,dict) else 0
    bonus += max(0, int(handcraft_travel_effect(amulet_id, policy).get("story_encounter_bonus_percent", 0) or 0))
    pct = max(0, min(100, base_pct+bonus))
    roll = deterministic_roll(state, f"story.social.roll:{key}", 100)
    if roll >= pct:
        logging.info("STORY SOCIAL no encounter roll=%d pct=%d missing=%d", roll, pct, len(missing))
        return None
    sid = missing[deterministic_roll(state, f"story.social.pick:{key}", len(missing))]
    generic = str(sp.get("generic_name", "旅途中的朋友") or "旅途中的朋友")
    gift = -1 if bool(sp.get("return_gift_required", False)) else 0
    row = {"id": sid, "partner": sid, "name": generic, "gift": gift, "feedback": -1}
    data["stories"].append(row)
    data["new_story_id"] = sid
    get_event_list(state).append({
        "id": next_event_id(state), "evt_type": 8, "evt_id": sid,
        "evt_value": [], "evt_string": [], "evt_pic": []
    })
    logging.info("STORY SOCIAL NEW id=%d name=%s", sid, generic)
    return row


def apply_story_read_new(state: dict) -> bool:
    data = get_story_state(state)
    if int(data.get("new_story_id", 0) or 0) != 0:
        data["new_story_id"] = 0
        save_state(state, "story.read_new_story")
    return True


def apply_story_send_gift(state: dict, story_id: int, gift_id: int) -> bool:
    """Compatibility path for historical imported stories with gift=-1."""
    data = get_story_state(state)
    row = next((x for x in data.get("stories", []) if isinstance(x, dict) and int(x.get("id", -1)) == int(story_id)), None)
    if row is None or int(row.get("gift", -1) or -1) != -1:
        return False
    _, house, _, _ = get_item_parts(state)
    if get_house_count(house, int(gift_id)) <= 0:
        return False
    set_house_count(house, int(gift_id), get_house_count(house, int(gift_id)) - 1)
    row["gift"] = int(gift_id)
    save_state(state, f"story.send_gift:{story_id}:{gift_id}")
    return True


def giftbox_friend_policy(policy: dict | None = None) -> dict:
    return giftbox_policy(policy)


def unlocked_travel_friend_ids(state: dict) -> list[int]:
    note_ids = {int(x.get("id", -1)) for x in get_travel_note_state(state).get("note_list", []) if isinstance(x, dict)}
    cfg = load_static_json("TravelFriends_json.json")
    vals = list(cfg.values()) if isinstance(cfg, dict) else []
    out = []
    for row in vals:
        if not isinstance(row, dict):
            continue
        fid = int(row.get("id", -1))
        unlock = int(row.get("visitOpen", -1) or -1)
        if fid >= 0 and unlock in note_ids:
            out.append(fid)
    return sorted(set(out))


def _giftbox_friend_pending_event(state: dict) -> bool:
    return any(isinstance(e, dict) and int(e.get("evt_type", -1)) == 16 for e in get_event_list(state))


def _apply_friend_drop_reward(state: dict, drop: list[int]) -> None:
    # drop is one DropType triple: [type,item_id,count]
    if len(drop) < 3:
        return
    typ, iid, cnt = int(drop[0]), int(drop[1]), max(0, int(drop[2]))
    if cnt <= 0:
        return
    if typ == 5 and iid == 100000:
        _, _, res, _ = get_role_parts(state)
        res["clover_point"] = int(res.get("clover_point", 0) or 0) + cnt
        calendar_record_clover_gain(state, cnt)
    elif typ == 5 and iid == 100001:
        _, _, res, _ = get_role_parts(state)
        res["ticket"] = int(res.get("ticket", 0) or 0) + cnt
    elif typ == 1:
        _, house, _, _ = get_item_parts(state)
        set_house_count(house, iid, get_house_count(house, iid) + cnt)


def _friend_reward_drop(state: dict, key: str, policy: dict) -> list[int]:
    gp = giftbox_friend_policy(policy)
    w = gp.get("normal_weights", {"clover": 80, "four_clover": 18, "ticket": 2})
    wc = max(0, int(w.get("clover", 80) or 0)); wf = max(0, int(w.get("four_clover", 18) or 0)); wt = max(0, int(w.get("ticket", 2) or 0))
    total = max(1, wc + wf + wt)
    roll = deterministic_roll(state, f"giftbox.friend.reward:{key}", total)
    if roll < wc:
        return [5, 100000, max(1, int(gp.get("clover_amount", 20) or 20))]
    if roll < wc + wf:
        return [1, 1000, 1]
    lo = max(1, int(gp.get("ticket_min", 1) or 1)); hi = max(lo, int(gp.get("ticket_max", 3) or 3))
    return [5, 100001, lo + deterministic_roll(state, f"giftbox.friend.ticket:{key}", hi-lo+1)]


def travel_friend_postcard_ids(friend_id: int) -> list[int]:
    """Return official static Unique postcard ids belonging to one TravelFriend.

    PictureTag uses the exact marker [picture_name, "_TRAVELER_ID", friend_id].
    This lets us preserve the client-defined postcard pool without inventing ids.
    """
    cfg = load_static_json("PictureTag_json.json")
    rows = cfg if isinstance(cfg, list) else list(cfg.values()) if isinstance(cfg, dict) else []
    out: list[int] = []
    for row in rows:
        if not isinstance(row, dict) or str(row.get("tagType", "")) != "Unique":
            continue
        names = row.get("picNames")
        if not isinstance(names, list) or len(names) < 3 or str(names[1]) != "_TRAVELER_ID":
            continue
        try:
            fid = int(names[2]); pid = int(row.get("id", -1))
        except Exception:
            continue
        if fid == int(friend_id) and pid >= 0 and picture_by_id(pid) is not None:
            out.append(pid)
    return sorted(set(out))


def _friend_return_postcards(state: dict, friend_id: int, key: str, policy: dict) -> list[dict]:
    """Generate returned TravelFriend postcards from official static pools.

    The exact production probability/rarity distribution is server-side and unknown.
    `postcard_percent` is therefore an explicit offline-policy value.  Once triggered,
    the actual postcard id is selected only from the official _TRAVELER_ID pool.
    """
    gp = giftbox_friend_policy(policy)
    pct = max(0, min(100, int(gp.get("postcard_percent", 100) or 0)))
    if deterministic_roll(state, f"giftbox.friend.postcard.trigger:{key}:{friend_id}", 100) >= pct:
        return []
    pool = travel_friend_postcard_ids(friend_id)
    if not pool:
        logging.warning("GIFTBOX FRIEND postcard pool empty friend_id=%s", friend_id)
        return []
    pid = pool[deterministic_roll(state, f"giftbox.friend.postcard.pick:{key}:{friend_id}", len(pool))]
    pic = make_picture_info(state, pid)
    pic["visit"] = True
    return [pic]


def _grant_travel_friend_notes(state: dict, friend_id: int, now: int) -> list[int]:
    """Apply the static TravelFriends note chain for one return visit."""
    config = load_static_json("Note_json.json")
    rows = list(config.values()) if isinstance(config, dict) else config if isinstance(config, list) else []
    notes = get_travel_note_state(state).get("note_list", [])
    owned = {int(row.get("id", -1)) for row in notes if isinstance(row, dict)}
    meta = state.setdefault("offline_meta", {}).setdefault("giftbox_friend", {})
    visits = meta.setdefault("visit_counts", {})
    visit_count = int(visits.get(str(friend_id), 0) or 0) + 1
    visits[str(friend_id)] = visit_count
    intro = next((int(row["visitOpen"]) for row in load_static_json("TravelFriends_json.json").values()
                  if int(row["id"]) == friend_id), None)
    if intro is not None and intro not in owned:
        notes.append({"id": intro, "read": False, "timestamp": now})
        return [intro]
    granted: list[int] = []

    reply_candidates = []
    for row in rows:
        if not isinstance(row, dict) or "attach" not in row:
            continue
        try:
            note_id = int(row["id"]); parent_id = int(row["attach"])
            friend_text, min_visit_text = str(row.get("factorData2", "")).split("_", 1)
            required_friend = int(friend_text); min_visits = int(min_visit_text)
        except (TypeError, ValueError):
            continue
        if note_id not in owned and parent_id in owned and required_friend == friend_id and visit_count >= min_visits:
            reply_candidates.append(note_id)
    if reply_candidates:
        note_id = sorted(reply_candidates)[deterministic_roll(state, f"giftbox.friend.reply:{friend_id}:{visit_count}", len(reply_candidates))]
        notes.append({"id": note_id, "read": False, "timestamp": now})
        owned.add(note_id); granted.append(note_id)

    base_candidates = []
    for row in rows:
        if not isinstance(row, dict) or "attach" in row or int(row.get("type", -1)) != 2:
            continue
        try:
            note_id = int(row["id"]); required_friend = int(row.get("factorData2", -1)); chance = int(row.get("factorData", 0) or 0)
        except (TypeError, ValueError):
            continue
        if note_id in owned or str(row.get("factorType", "")) != "Drop" or str(row.get("factorType2", "")) != "Friends" or required_friend != friend_id:
            continue
        if str(row.get("factorType3", "")) == "Own_Note" and int(row.get("factorData3", -1) or -1) not in owned:
            continue
        base_candidates.append((note_id, max(0, min(100, chance))))
    if base_candidates:
        note_id, chance = sorted(base_candidates)[deterministic_roll(state, f"giftbox.friend.note:{friend_id}:{visit_count}", len(base_candidates))]
        if deterministic_roll(state, f"giftbox.friend.note_drop:{note_id}:{visit_count}", 100) < chance:
            notes.append({"id": note_id, "read": False, "timestamp": now})
            granted.append(note_id)
    return granted


def advance_giftbox_friend(state: dict, now: int | None = None, policy: dict | None = None) -> dict | None:
    """Turn one gift-box offering into a TravelFriends return event.

    TravelFriends are the client-defined 壁虎/刺猬/萤火虫 system. This intentionally
    does NOT touch VisitorModel / province flowers and does NOT impersonate a player.
    """
    if now is None:
        now = int(time.time())
    if policy is None:
        policy = load_offline_policy()
    gp = giftbox_friend_policy(policy)
    if not bool(gp.get("enabled", True)) or _giftbox_friend_pending_event(state):
        return None
    if not _giftbox_has_contents(state):
        state.setdefault("offline_meta", {}).setdefault("giftbox_friend", {}).pop("next_check", None)
        return None
    meta = state.setdefault("offline_meta", {}).setdefault("giftbox_friend", {})
    due = int(meta.get("next_check", 0) or 0)
    delay = max(60, int(gp.get("delay_seconds", gp.get("visit_delay_seconds", 1800)) or 1800))
    if due <= 0:
        meta["next_check"] = now + delay
        save_state(state, "giftbox.friend.schedule")
        return None
    if now < due:
        return None
    pct = max(0, min(100, int(gp.get("trigger_percent_per_login", 25) or 0)))
    roll = deterministic_roll(state, f"giftbox.friend.trigger:{due}", 100)
    if roll >= pct:
        meta["next_check"] = now + delay
        save_state(state, "giftbox.friend.retry")
        return None
    friends = unlocked_travel_friend_ids(state)
    if not friends:
        # The first offline gift exchange introduces a friend and their opening note.
        friends = [int(row["id"]) for row in load_static_json("TravelFriends_json.json").values()]
    offer = _consume_one_giftbox_offer(state, policy)
    if offer is None:
        return None
    fid = friends[deterministic_roll(state, f"giftbox.friend.pick:{due}", len(friends))]
    drop = _friend_reward_drop(state, str(due), policy)
    _apply_friend_drop_reward(state, drop)
    postcards = _friend_return_postcards(state, fid, str(due), policy)
    notes = _grant_travel_friend_notes(state, fid, now)
    event = {
        "id": next_event_id(state), "evt_type": 16, "evt_id": 0,
        "evt_value": [fid, *drop], "evt_string": [], "evt_pic": copy.deepcopy(postcards)
    }
    get_event_list(state).append(event)
    meta["last_offer"] = copy.deepcopy(offer)
    meta["last_friend_id"] = fid
    meta["next_check"] = 0
    if _giftbox_has_contents(state):
        meta["next_check"] = now + delay
    save_state(state, f"giftbox.friend.return:{fid}")
    logging.info("GIFTBOX FRIEND RETURN friend_id=%d offer=%s drop=%s postcards=%s notes=%s event_id=%d", fid, offer, drop, [p.get("pic_id") for p in postcards], notes, event["id"])
    return event


def migrate_stage6e2_social_split(state: dict, now: int | None = None, policy: dict | None = None) -> bool:
    """Undo Stage 6E/6E.1's incorrect giftbox->province-Visitor simulation."""
    if now is None:
        now = int(time.time())
    if policy is None:
        policy = load_offline_policy()
    meta = state.setdefault("offline_meta", {})
    if meta.get("stage6e2_social_split_migrated"):
        return False
    old = meta.get("giftbox_social") if isinstance(meta.get("giftbox_social"), dict) else {}
    box = get_giftbox_state(state)
    offer = old.get("active_offer") if isinstance(old, dict) else None
    restored = None
    if isinstance(offer, dict):
        kind = str(offer.get("kind", ""))
        if kind == "specialty":
            iid = int(offer.get("item_id", -1) or -1)
            if iid >= 0:
                _giftbox_add_specialty(box, iid, 1); restored = offer
        elif kind == "picture":
            iid = int(offer.get("id", -1) or -1); pid = int(offer.get("pic_id", 0) or 0)
            if iid >= 0 and not any(isinstance(x, dict) and int(x.get("id", -1)) == iid for x in box.get("pictures", [])):
                try:
                    pic = make_picture_info(state, pid); pic["id"] = iid
                    box["pictures"].append(pic); restored = offer
                except Exception:
                    logging.exception("Stage6E2 restore old synthetic picture offer failed id=%s pic_id=%s", iid, pid)
    # Remove only the fake visitor created by our own previous build.
    visit = get_visit_state(state)
    visitor = visit.get("visitor")
    if isinstance(visitor, dict) and (str(visitor.get("name", "")) == "匿名旅蛙" or isinstance(offer, dict)):
        visit.pop("visitor", None)
    friend_meta = meta.setdefault("giftbox_friend", {})
    old_due = int(old.get("next_check", 0) or 0) if isinstance(old, dict) else 0
    if int(friend_meta.get("next_check", 0) or 0) <= 0 and old_due > 0:
        friend_meta["next_check"] = old_due
    if _giftbox_has_contents(state) and int(friend_meta.get("next_check", 0) or 0) <= 0:
        delay = max(60, int(giftbox_friend_policy(policy).get("delay_seconds", 1800) or 1800))
        friend_meta["next_check"] = now + delay
    meta["giftbox_social_retired"] = {"migrated_at": now, "restored_offer": copy.deepcopy(restored)}
    meta.pop("giftbox_social", None)
    meta["stage6e2_social_split_migrated"] = now
    state["format_version"] = max(int(state.get("format_version", 2) or 2), 14)
    save_state(state, "migrate.stage6e2.social_split")
    logging.warning("STAGE6E2 MIGRATION: retired fake province visitor; restored_offer=%s", restored)
    return True

def get_visit_state(state: dict) -> dict:
    push = find_push(state, "visit.load")
    if not push:
        push = {"cmd": "visit.load", "data": {"acquire": []}}
        state.setdefault("sync_pushes", []).append(push)
    data = push.setdefault("data", {})
    if not isinstance(data, dict):
        data = {"acquire": []}
        push["data"] = data
    if not isinstance(data.get("acquire"), list):
        data["acquire"] = _to_list(data.get("acquire"))
    return data


def _visitor_province_rows() -> list[tuple[str, dict]]:
    cfg = load_static_json("visitors_json.json")
    p = cfg.get("provinceList", {}) if isinstance(cfg, dict) else {}
    return sorted([(str(k), v) for k, v in p.items() if isinstance(v, dict)], key=lambda x: int(x[1].get("ID", 9999)))


def schedule_courtyard_traveler(state: dict, now: int, policy: dict) -> None:
    meta = state.setdefault("offline_meta", {}).setdefault("courtyard_traveler", {})
    if int(meta.get("next_arrival", 0)) > 0:
        return
    timing = policy.get("courtyard_traveler", {})
    low = max(60, int(timing.get("arrival_min_seconds", 172800)))
    high = max(low, int(timing.get("arrival_max_seconds", 432000)))
    meta["next_arrival"] = now + low + deterministic_roll(state, "courtyard_traveler.wait", high - low + 1)


def advance_courtyard_traveler(state: dict, now: int | None = None, policy: dict | None = None) -> dict | None:
    """Simulate a passing frog without online identities or gift-box consumption."""
    now = int(time.time()) if now is None else int(now)
    policy = load_offline_policy() if policy is None else policy
    role = find_push(state, "client.load_role")
    settings = role.get("data", {}).get("settings", {}).get("client", "{}") if role else "{}"
    settings = json.loads(settings) if isinstance(settings, str) else settings
    if not isinstance(settings, dict) or settings.get("guideStep") != "Complete":
        return None
    visit = get_visit_state(state)
    # An expired visit remains a claimable gift, as in the official UI.
    if isinstance(visit.get("visitor"), dict):
        return None
    meta = state.setdefault("offline_meta", {}).setdefault("courtyard_traveler", {})
    due = int(meta.get("next_arrival", 0))
    if due <= 0:
        schedule_courtyard_traveler(state, now, policy)
        save_state(state, "courtyard_traveler.schedule")
        return None
    if now < due:
        return None
    rows = _visitor_province_rows()
    actions = load_static_json("visitors_json.json").get("actionList", {})
    food_ids = sorted(int(key) for key in actions)
    if not rows or not food_ids:
        return None
    province, info = rows[deterministic_roll(state, "courtyard_traveler.province", len(rows))]
    first = province not in visit["acquire"]
    low = int(info.get("CountFloor", 1))
    high = max(low, int(info.get("CountUpper", low)))
    count = low + deterministic_roll(state, "courtyard_traveler.gift", high - low + 1)
    timing = policy.get("courtyard_traveler", {})
    duration_low = max(60, int(timing.get("stay_min_seconds", 1200)))
    duration_high = max(duration_low, int(timing.get("stay_max_seconds", 3600)))
    duration = duration_low + deterministic_roll(state, "courtyard_traveler.stay", duration_high - duration_low + 1)
    visitor = {
        "partner": 0, "name": "过路旅蛙", "title": 0,
        "city": province + "_", "expire_time": now + duration,
        "food": food_ids[deterministic_roll(state, "courtyard_traveler.food", len(food_ids))],
        "carpet": 1 + deterministic_roll(state, "courtyard_traveler.carpet", 7),
        "first": first,
        "gift": {"item_id": 0 if first else int(info["OtherGiftID"]), "count": 0 if first else count},
    }
    visit["visitor"] = visitor
    meta["next_arrival"] = 0
    save_state(state, "courtyard_traveler.arrive")
    return visitor


def _consume_one_giftbox_offer(state: dict, policy: dict) -> dict | None:
    """Consume one offered gift when an offline social visit is generated.

    Both picture and specialty gift-box entries are eligible in the original UI.
    The preserved client does not contain the production server's selection rule,
    so the offline replacement chooses deterministically among *individual slots*
    (a specialty stack of count 3 contributes three candidates). This prevents a
    gift from generating unlimited visits while keeping restart behavior stable.
    """
    box = get_giftbox_state(state)
    gp = giftbox_policy(policy)
    offers: list[tuple[str, int, int]] = []
    if bool(gp.get("consume_specialty_on_visit", True)):
        for row in box.get("specialtys", []):
            if not isinstance(row, dict):
                continue
            iid = int(row.get("item_id", -1) or -1)
            cnt = max(0, int(row.get("count", 0) or 0))
            offers.extend(("specialty", iid, 0) for _ in range(cnt))
    if bool(gp.get("consume_picture_on_visit", True)):
        for pic in box.get("pictures", []):
            if isinstance(pic, dict):
                offers.append(("picture", int(pic.get("id", -1) or -1), int(pic.get("pic_id", 0) or 0)))
    if not offers:
        return None
    kind, ident, pic_id = offers[deterministic_roll(state, "giftbox.social.offer", len(offers))]
    if kind == "specialty":
        if _giftbox_take_specialty(box, ident, 1):
            return {"kind": "specialty", "item_id": ident}
        return None
    idx = next((i for i, p in enumerate(box.get("pictures", [])) if isinstance(p, dict) and int(p.get("id", -1)) == ident), -1)
    if idx < 0:
        return None
    pic = box["pictures"].pop(idx)
    return {"kind": "picture", "id": int(pic.get("id", 0) or 0), "pic_id": int(pic.get("pic_id", pic_id) or pic_id)}


def advance_giftbox_social(state: dict, now: int | None = None, policy: dict | None = None) -> dict | None:
    """Create an anonymous single-player replacement for the retired online visitor.

    The visit data shape and province/reward ranges are official client config.
    Trigger timing and gift-box causality are local preservation policy.
    """
    if now is None:
        now = int(time.time())
    if policy is None:
        policy = load_offline_policy()
    gp = giftbox_policy(policy)
    if not bool(gp.get("enabled", True)):
        return None
    visit = get_visit_state(state)
    if isinstance(visit.get("visitor"), dict):
        return None
    if not _giftbox_has_contents(state):
        state.setdefault("offline_meta", {}).setdefault("giftbox_friend", {}).pop("next_check", None)
        return None
    meta = state.setdefault("offline_meta", {}).setdefault("giftbox_friend", {})
    next_check = int(meta.get("next_check", 0) or 0)
    if next_check <= 0:
        _schedule_giftbox_social(state, now=now, policy=policy)
        return None
    if now < next_check:
        return None
    pct = max(0, min(100, int(gp.get("trigger_percent_per_login", 25) or 0)))
    roll = deterministic_roll(state, f"giftbox.social.trigger:{next_check}", 100)
    delay = max(60, int(gp.get("delay_seconds", gp.get("visit_delay_seconds", 1800)) or 1800))
    if roll >= pct:
        meta["next_check"] = now + delay
        save_state(state, "giftbox.social.retry")
        return None
    rows = _visitor_province_rows()
    if not rows:
        return None
    province, cfg = rows[deterministic_roll(state, f"giftbox.social.province:{next_check}", len(rows))]
    acquired = {str(x) for x in _to_list(visit.get("acquire"))}
    first = province not in acquired
    # Official client semantics: the first visitor from a province awards the
    # province-flower collection (VisitorModel.acquire), not OtherGiftID.
    # Repeat visits use the configured ordinary reward.
    if first:
        reward_id = 0
        reward_count = 0
    else:
        reward_id = int(cfg.get("OtherGiftID", 100001) or 100001)
        low = max(1, int(cfg.get("CountFloor", 1) or 1))
        high = max(low, int(cfg.get("CountUpper", low) or low))
        reward_count = low + deterministic_roll(state, f"giftbox.social.reward:{next_check}", high - low + 1)
    offer = _consume_one_giftbox_offer(state, policy)
    duration = max(60, int(gp.get("visit_duration_seconds", 1800) or 1800))
    visitor = {
        "partner": 0,
        "name": "匿名旅蛙",
        "title": 0,
        "expire_time": now + duration,
        "city": province + "_",
        "food": deterministic_roll(state, f"giftbox.social.food:{next_check}", 19),
        "first": bool(first),
        "gift": {"item_id": reward_id, "count": reward_count},
        "carpet": 1 + deterministic_roll(state, f"giftbox.social.carpet:{next_check}", 7),
    }
    visit["visitor"] = visitor
    meta["active_offer"] = offer
    meta["next_check"] = 0
    save_state(state, f"giftbox.social.visit:{province}")
    if first:
        logging.info("GIFTBOX SOCIAL VISITOR province=%s first=True offer=%s reward=province_flower", province, offer)
    else:
        logging.info("GIFTBOX SOCIAL VISITOR province=%s first=False offer=%s reward=%s", province, offer, visitor["gift"])
    return visitor


def reconcile_active_social_visitor_duration(state: dict, now: int | None = None, policy: dict | None = None) -> bool:
    """Apply a shorter local visit-duration policy to an already active simulated visitor.

    Only touches visitors generated by the offline gift-box simulator (identified by
    offline_meta.giftbox_social.active_offer). This makes test-policy changes such as
    1800 -> 60 seconds take effect without hand-editing state.json. It never lengthens
    an existing visit.
    """
    if now is None:
        now = int(time.time())
    if policy is None:
        policy = load_offline_policy()
    meta = state.setdefault("offline_meta", {}).setdefault("giftbox_friend", {})
    if "active_offer" not in meta:
        return False
    visitor = get_visit_state(state).get("visitor")
    if not isinstance(visitor, dict):
        return False
    gp = giftbox_policy(policy)
    duration = max(60, int(gp.get("visit_duration_seconds", 1800) or 1800))
    current = int(visitor.get("expire_time", 0) or 0)
    target = int(now) + duration
    if current <= 0 or current > target:
        visitor["expire_time"] = target
        save_state(state, f"giftbox.social.clamp_expire:{target}")
        logging.info("GIFTBOX SOCIAL ACTIVE duration clamped expire_time=%d", target)
        return True
    return False


def apply_visit_open(state: dict, policy: dict | None = None, now: int | None = None) -> dict | None:
    now = int(time.time()) if now is None else int(now)
    policy = load_offline_policy() if policy is None else policy
    visit = get_visit_state(state)
    visitor = visit.get("visitor")
    if not isinstance(visitor, dict):
        return None
    if int(visitor.get("expire_time", 0)) <= 0 or now < int(visitor["expire_time"]):
        return None
    province = str(visitor.get("city", "")).split("_")[0]
    first = bool(visitor.get("first", False))
    reward = visitor.get("gift") if isinstance(visitor.get("gift"), dict) else {"item_id": 0, "count": 0}
    result = {"first": first, "province": province, "gift": copy.deepcopy(reward)}
    if first:
        acquire = visit.setdefault("acquire", [])
        if province and province not in acquire:
            acquire.append(province)
    else:
        iid = int(reward.get("item_id", 0) or 0); count = int(reward.get("count", 0) or 0)
        if count > 0:
            if iid == 100000:
                _, _, res, _ = get_role_parts(state)
                res["clover_point"] = int(res.get("clover_point", 0) or 0) + count
                result["clover"] = int(res["clover_point"])
                task = calendar_record_clover_gain(state, count)
                if task is not None:
                    result["calendar_task"] = task
            elif iid == 100001:
                _, _, res, _ = get_role_parts(state)
                res["ticket"] = int(res.get("ticket", 0) or 0) + count
                result["ticket"] = int(res["ticket"])
            else:
                _, house, _, _ = get_item_parts(state)
                new = get_house_count(house, iid) + count
                set_house_count(house, iid, new)
                result["item"] = [iid, new]
    visit.pop("visitor", None)
    state.setdefault("offline_meta", {}).setdefault("guide_task", {})["received_friend_gift"] = True
    meta = state.setdefault("offline_meta", {}).setdefault("giftbox_friend", {})
    meta.pop("active_offer", None)
    schedule_courtyard_traveler(state, now, policy)
    save_state(state, f"visit.open:{province}:{'first' if first else 'gift'}")
    return result


def apply_visit_set_field(state: dict, key: str, value: Any) -> bool:
    visit = get_visit_state(state)
    visitor = visit.get("visitor")
    if not isinstance(visitor, dict):
        return False
    visitor[key] = value
    save_state(state, f"visit.set_{key}")
    return True


def get_travel_note_state(state: dict) -> dict:
    push = find_push(state, "travel.load_note")
    if not push:
        push = {"cmd": "travel.load_note", "data": {"note_list": []}}
        state.setdefault("sync_pushes", []).append(push)
    data = push.setdefault("data", {})
    if not isinstance(data, dict):
        data = {"note_list": []}
        push["data"] = data
    if not isinstance(data.get("note_list"), list):
        data["note_list"] = _to_list(data.get("note_list"))
    return data


def roll_travel_observation(state: dict, now: int) -> int | None:
    """Try one eligible, uncollected observation from the official note table."""
    notes = get_travel_note_state(state)["note_list"]
    owned = {int(row["id"]) for row in notes if isinstance(row, dict) and "id" in row}
    candidates = [row for row in load_static_json("Note_json.json").values()
                  if row.get("type") == 1 and row.get("factorType") == "Drop" and not row.get("attach")
                  and int(row["id"]) not in owned
                  and (not row.get("factorType2") or
                       (row["factorType2"] == "Own_Note" and int(row["factorData2"]) in owned))]
    if not candidates:
        return None
    selected = candidates[deterministic_roll(state, "travel.observation.pick", len(candidates))]
    if deterministic_roll(state, "travel.observation.drop", 100) >= int(selected["factorData"]):
        return None
    note_id = int(selected["id"])
    notes.append({"id": note_id, "read": False, "timestamp": now})
    return note_id


def apply_travel_read_note(state: dict, ids: Any) -> int:
    idset = set()
    if isinstance(ids, list):
        for x in ids:
            try:
                idset.add(int(x))
            except Exception:
                pass
    elif isinstance(ids, dict):
        raw = ids.get("id_list", ids.get("ids", ids.get("id", [])))
        if not isinstance(raw, list):
            raw = [raw]
        for x in raw:
            try:
                idset.add(int(x))
            except Exception:
                pass
    else:
        try:
            idset.add(int(ids))
        except Exception:
            pass
    changed = 0
    for row in get_travel_note_state(state).get("note_list", []):
        if isinstance(row, dict) and int(row.get("id", -1) or -1) in idset and not bool(row.get("read", False)):
            row["read"] = True
            changed += 1
    if changed:
        save_state(state, f"travel.read_note:{changed}")
    return changed


def get_calendar_state(state: dict) -> dict:
    push = find_push(state, "calendar.load")
    if not push:
        push = {"cmd": "calendar.load", "data": {"new_flag": [], "lucky_days": [], "st_days": [], "task_list": []}}
        state.setdefault("sync_pushes", []).append(push)
    data = push.setdefault("data", {})
    if not isinstance(data, dict):
        data = {"new_flag": [], "lucky_days": [], "st_days": [], "task_list": []}
        push["data"] = data
    for key in ("new_flag", "lucky_days", "st_days", "task_list"):
        if not isinstance(data.get(key), list):
            data[key] = _to_list(data.get(key))
    flags = data["new_flag"]
    flags.extend([0] * (7 - len(flags)))
    return data


def get_calendar_task(state: dict, task_id: int) -> dict | None:
    tasks = get_calendar_state(state).get("task_list", [])
    return next((r for r in tasks if isinstance(r, dict) and int(r.get("id", -1)) == int(task_id)), None)


def calendar_record_clover_gain(state: dict, amount: int) -> dict | None:
    """Advance solar-term task 2 for *positive earned clover* only.

    The preserved UI names this task “累计获得三叶草（{0}/30）”.  We cap at
    the official displayed target 30 and never count spending/negative deltas.
    This helper mutates state but deliberately does not save by itself; callers
    already persist the enclosing authoritative action.
    """
    amount = max(0, int(amount or 0))
    if amount <= 0:
        return None
    cooking_record_task_progress(state, 4, amount)
    row = get_calendar_task(state, 2)
    if row is None or bool(row.get("complete", False)):
        return None
    old = max(0, int(row.get("pro", 0) or 0))
    new = min(CALENDAR_CLOVER_TARGET, old + amount)
    if new == old:
        return None
    row["pro"] = new
    row["complete"] = new >= CALENDAR_CLOVER_TARGET
    return copy.deepcopy(row)


def calendar_complete_gacha_task(state: dict) -> dict | None:
    """Complete task 3 only when a shop-Gacha prize is actually redeemed."""
    row = get_calendar_task(state, 3)
    if row is None:
        return None
    old_pro = max(0, int(row.get("pro", 0) or 0))
    old_complete = bool(row.get("complete", False))
    row["pro"] = max(CALENDAR_GACHA_TARGET, old_pro)
    row["complete"] = True
    if old_complete and old_pro >= CALENDAR_GACHA_TARGET:
        return None
    return copy.deepcopy(row)


def calendar_task_push(task: dict | None) -> dict | None:
    if not isinstance(task, dict):
        return None
    return {"cmd": "calendar.task_update", "data": {"task": copy.deepcopy(task)}}


def migrate_stage6g_calendar_sep2026(state: dict, now: int | None = None) -> bool:
    """Narrow calibration from this account's 2026-09-12 production capture.

    Production returned Sept. st_days including day 23 -> item 28, with task 2
    already 30/complete and task 3 1/complete.  Raise only those two task
    states when that exact monthly signature is present; never insert rewards,
    never remove st_days, and never reduce progress.
    """
    if now is None:
        now = int(time.time())
    meta = state.setdefault("offline_meta", {})
    if meta.get("stage6g_calendar_sep2026_calibrated"):
        return False
    if int(now) < STAGE6G_CALENDAR_CALIBRATION_AT:
        return False
    cal = get_calendar_state(state)
    signature = any(
        isinstance(r, dict)
        and int(r.get("day", -1) or -1) == 23
        and int(r.get("item_id", 0) or 0) == 28
        for r in cal.get("st_days", [])
    )
    if not signature:
        return False
    t2 = get_calendar_task(state, 2)
    t3 = get_calendar_task(state, 3)
    if t2 is None or t3 is None:
        return False
    t2["pro"] = max(CALENDAR_CLOVER_TARGET, int(t2.get("pro", 0) or 0))
    t2["complete"] = True
    t3["pro"] = max(CALENDAR_GACHA_TARGET, int(t3.get("pro", 0) or 0))
    t3["complete"] = True
    meta["stage6g_calendar_sep2026_calibrated"] = {
        "at": int(now), "signature": {"day": 23, "item_id": 28},
        "task2": copy.deepcopy(t2), "task3": copy.deepcopy(t3),
    }
    state["format_version"] = max(int(state.get("format_version", 2) or 2), 18)
    save_state(state, "migrate.stage6g.calendar_sep2026")
    logging.warning(
        "STAGE6G CALENDAR CALIBRATION: Sept2026 task2=%s task3=%s; st_days preserved",
        t2, t3,
    )
    return True


def get_gacha_data(state: dict) -> dict:
    data, _, _, _ = get_item_parts(state)
    gacha = data.get("gacha")
    if not isinstance(gacha, dict):
        gacha = {"color_ball": -1}
        data["gacha"] = gacha
    gacha["color_ball"] = int(gacha.get("color_ball", -1) if gacha.get("color_ball") is not None else -1)
    return gacha


def _gacha_roll_rank(state: dict) -> int:
    r = deterministic_roll(state, "shop_gacha_rank", sum(w for _, w in GACHA_RANK_WEIGHTS))
    acc = 0
    for rank, weight in GACHA_RANK_WEIGHTS:
        acc += int(weight)
        if r < acc:
            return int(rank)
    return 0


def apply_gacha_draw(state: dict, is_reward: bool = False) -> tuple[bool, str, dict]:
    """Perform the captured normal store-Gacha draw.

    Normal production draw consumes 5 tickets and returns {ticket:<ball rank>}.
    The special is_reward path has not been production-calibrated, so it is
    explicitly blocked rather than guessed.
    """
    if bool(is_reward):
        return False, "reward_draw_uncalibrated", {}
    gacha = get_gacha_data(state)
    if int(gacha.get("color_ball", -1)) >= 0:
        return False, "pending_ball", {}
    _, _, res, _ = get_role_parts(state)
    tickets = 999 if GALLERY_MODE else int(res.get("ticket", 0) or 0)
    if tickets < GACHA_NEED_TICKETS:
        return False, "not_enough_ticket", {"ticket_total": tickets}
    if not GALLERY_MODE:
        tickets -= GACHA_NEED_TICKETS
    res["ticket"] = tickets
    rank = _gacha_roll_rank(state)
    gacha["color_ball"] = rank
    state["format_version"] = max(int(state.get("format_version", 2) or 2), 18)
    save_state(state, f"item.gacha:{rank}")
    return True, "ok", {"rank": rank, "ticket_total": tickets}


def _prize_row(prize_id: int) -> dict | None:
    return next((r for r in static_rows("Prize_json.json") if int(r.get("id", -1)) == int(prize_id)), None)


def apply_gacha_redeem(state: dict, prize_id: int) -> tuple[bool, str, dict]:
    """Redeem exactly one prize compatible with the pending ball rank."""
    gacha = get_gacha_data(state)
    rank = int(gacha.get("color_ball", -1))
    if rank < 0:
        return False, "no_pending_ball", {}
    row = _prize_row(int(prize_id))
    if row is None:
        return False, "unknown_prize", {}
    if int(row.get("rank", -999)) != rank:
        return False, "rank_mismatch", {}
    stock = max(0, int(row.get("stock", 0) or 0))
    iid = int(row.get("itemId", -1) or -1)
    result: dict[str, Any] = {"rank": rank, "prize_id": int(prize_id)}
    if rank == 0 and iid == -1:
        # Client PrizeList renders White as a ticket, and the client special
        # path adds Prize.stock tickets before sending item.redeem_prize.
        _, _, res, _ = get_role_parts(state)
        res["ticket"] = 999 if GALLERY_MODE else int(res.get("ticket", 0) or 0) + stock
        result["ticket_total"] = int(res["ticket"])
    else:
        if iid < 0 or stock <= 0:
            return False, "bad_prize_config", {}
        _, house, _, _ = get_item_parts(state)
        new_count = get_house_count(house, iid) + stock
        set_house_count(house, iid, new_count)
        result["item_id"] = iid
        result["item_count"] = new_count
    gacha["color_ball"] = -1
    task = calendar_complete_gacha_task(state)
    if task is not None:
        result["calendar_task"] = task
    state["format_version"] = max(int(state.get("format_version", 2) or 2), 18)
    save_state(state, f"item.redeem_prize:{prize_id}:rank{rank}")
    return True, "ok", result


def apply_offline_calendar_policy(state: dict, policy: dict | None = None) -> bool:
    if policy is None:
        policy = load_offline_policy()
    cp = policy.get("calendar", {}) if isinstance(policy, dict) else {}
    cal = get_calendar_state(state)
    if not bool(cp.get("offline_complete_ad_share_task", True)):
        return False
    tasks = cal.get("task_list", [])
    # Solar-term task id=1 is exactly the ad/share condition in captured data.
    row = next((r for r in tasks if isinstance(r, dict) and int(r.get("id", -1)) == 1), None)
    if not row:
        return False
    changed = not bool(row.get("complete", False)) or int(row.get("pro", 0) or 0) < 1
    if changed:
        row["complete"] = True
        row["pro"] = max(1, int(row.get("pro", 0) or 0))
        save_state(state, "calendar.offline_ad_task_complete")
    return changed


def _calendar_add_item(state: dict, item_id: int, count: int = 1) -> int:
    _, house, _, _ = get_item_parts(state)
    new = get_house_count(house, int(item_id)) + int(count)
    set_house_count(house, int(item_id), new)
    return get_house_count(house, int(item_id))


def apply_calendar_st_reward(state: dict, now: int | None = None, reward_day: int | None = None) -> tuple[bool, dict]:
    if now is None:
        now = int(time.time())
    cal = get_calendar_state(state)
    tasks = [r for r in cal.get("task_list", []) if isinstance(r, dict)]
    if not tasks or any(not bool(r.get("complete", False)) for r in tasks):
        return False, {"code": 1, "offline_reason": "tasks_incomplete"}
    day = datetime.fromtimestamp(now).day
    candidates = [r for r in cal["st_days"] if isinstance(r, dict)
                  and int(r.get("item_id", 0) or 0) > 0
                  and 1 <= int(r.get("day", 0) or 0) <= day
                  and (reward_day is None or int(r["day"]) == reward_day)]
    if not candidates:
        return False, {"code": 1, "offline_reason": "no_solar_term_reward"}
    row = sorted(candidates, key=lambda r: int(r.get("day", 0) or 0))[0]
    item_id = int(row.get("item_id", 0) or 0); reward_day = int(row.get("day", 0) or 0)
    count = _calendar_add_item(state, item_id, 1)
    cal["st_days"] = [r for r in cal["st_days"] if r is not row]
    # Offline play fulfills the ad task automatically when the policy is enabled.
    auto_share = load_offline_policy().get("calendar", {}).get("offline_complete_ad_share_task", True)
    t1 = next((r for r in tasks if int(r.get("id", -1)) == 1), None)
    if t1 is not None:
        t1["complete"] = bool(auto_share)
    guide = state.setdefault("offline_meta", {}).setdefault("guide_task", {})
    guide["season_rewards"] = int(guide.get("season_rewards", 0)) + 1
    save_state(state, f"calendar.get_st_reward:{reward_day}:{item_id}")
    return True, {"code": 0, "day": reward_day, "item_id": item_id, "item_count": count, "task": t1}


def apply_calendar_lucky_reward(state: dict, now: int | None = None) -> tuple[bool, dict]:
    if now is None:
        now = int(time.time())
    cal = get_calendar_state(state); day = datetime.fromtimestamp(now).day
    row = next((r for r in cal.get("lucky_days", []) if isinstance(r, dict) and int(r.get("day", -1)) == day and int(r.get("item_id", 0) or 0) > 0), None)
    if row is None:
        return False, {"code": 1, "offline_reason": "no_lucky_reward"}
    item_id = int(row["item_id"]); count = _calendar_add_item(state, item_id, 1)
    cal["lucky_days"] = [r for r in cal["lucky_days"] if r is not row]
    save_state(state, f"calendar.get_luck_reward:{day}:{item_id}")
    return True, {"code": 0, "day": day, "item_id": item_id, "item_count": count}


def _calendar_beginner_rows() -> dict:
    cfg = load_static_json("calendarData_json.json")
    return cfg.get("beginner", {}) if isinstance(cfg, dict) else {}


def apply_calendar_beginner_reward(state: dict, code: str | None = None) -> tuple[bool, dict]:
    cal = get_calendar_state(state); rows = _calendar_beginner_rows(); flags = cal.setdefault("new_flag", [])
    target_day = None
    if code is not None:
        for k, r in rows.items():
            if isinstance(r, dict) and str(r.get("code", "")) == str(code):
                target_day = int(r.get("id", k)); break
    else:
        # Client's normal beginner button expects the current creation-day slot.
        _, _, _, misc = get_role_parts(state)
        now = int(time.time())
        create_time = int(misc.get("create_time") or now)
        # Match core.getOffsetDay, which uses midnight in China (UTC+8).
        target_day = (now + 28800) // 86400 - (create_time + 28800) // 86400 + 1
    row = rows.get(str(int(target_day))) if target_day is not None else None
    if not isinstance(row, dict):
        return False, {"day": 0, "code": 1}
    while len(flags) < int(target_day): flags.append(0)
    if int(flags[int(target_day)-1] or 0) > 0:
        return False, {"day": 0, "code": 1, "offline_reason": "already_claimed"}
    iid = int(row.get("item_id", 0) or 0); num = max(1, int(row.get("num", 1) or 1)); count = _calendar_add_item(state, iid, num)
    flags[int(target_day)-1] = 1
    save_state(state, f"calendar.beginner:{target_day}:{iid}")
    return True, {"day": int(target_day), "code": 0, "item_id": iid, "item_count": count}


def calendar_note_payload(state: dict) -> dict:
    meta = state.setdefault("offline_meta", {})
    if isinstance(meta.get("calendar_note_list"), list):
        return {"list": copy.deepcopy(meta["calendar_note_list"])}
    # If an earlier captured request/response was imported, preserve it.
    old = state.get("responses", {}).get("calendar.load_note")
    if isinstance(old, dict) and isinstance(old.get("list"), list):
        meta["calendar_note_list"] = copy.deepcopy(old["list"])
        return {"list": copy.deepcopy(old["list"])}
    return {"list": []}


def _apply_ency_show_sub_from_config(
    state: dict,
    *,
    push_cmd: str,
    config_name: str,
    long_id: int,
    save_reason: str,
) -> bool:
    push = find_push(state, push_cmd)
    if not push or not isinstance(push.get("data"), dict):
        return False
    data = push["data"]
    rows = data.setdefault("show_sub", [])
    if not isinstance(rows, list):
        rows = _to_list(rows)
        data["show_sub"] = rows

    cfg = load_static_json(config_name)
    lst = cfg.get("list", {}) if isinstance(cfg, dict) else {}
    row = None
    if isinstance(lst, dict):
        row = lst.get(str(int(long_id))) or lst.get(int(long_id))
    elif isinstance(lst, list):
        row = next(
            (x for x in lst if isinstance(x, dict) and int(x.get("long_id", -1)) == int(long_id)),
            None,
        )
    if not isinstance(row, dict):
        return False

    parent_id = int(row.get("id", 0) or 0)
    if parent_id <= 0:
        return False
    old = next(
        (r for r in rows if isinstance(r, dict) and int(r.get("id", -1)) == parent_id),
        None,
    )
    if old is None:
        rows.append({"id": parent_id, "sub_id": int(long_id)})
    else:
        old["sub_id"] = int(long_id)
    save_state(state, f"{save_reason}:{int(long_id)}")
    return True


def apply_encytravel_show_sub(state: dict, long_id: int) -> bool:
    # Protocol argument is named long_id.  Stage 6H accidentally treated it as
    # a zero-based list index; the client actually passes keys such as 10010001.
    return _apply_ency_show_sub_from_config(
        state,
        push_cmd="encytravel.load",
        config_name="encytravel_json.json",
        long_id=int(long_id),
        save_reason="encytravel.set_show_sub",
    )


def apply_encyclopedia_show_sub(state: dict, long_id: int) -> bool:
    return _apply_ency_show_sub_from_config(
        state,
        push_cmd="encyclopedia.load",
        config_name="encyclopedia_json.json",
        long_id=int(long_id),
        save_reason="encyclopedia.set_show_sub",
    )


def furniture_cfg(furniture_id: int) -> dict | None:
    obj = load_static_json("furnitureData_json.json")
    if not isinstance(obj, dict):
        return None
    row = obj.get(str(int(furniture_id)))
    return row if isinstance(row, dict) else None


def _frog_is_home(state: dict) -> bool:
    try:
        _, frog, _, _ = get_role_parts(state)
        return int(frog.get("status", FROG_HOME) or FROG_HOME) == FROG_HOME
    except Exception:
        return True


def apply_furniture_replace_fur(state: dict, furniture_id: int) -> tuple[bool, str, int]:
    data = get_furniture_data(
        state,
        "furniture.load_furniture",
        {"shop": {"start_time": 0, "leave_time": 0, "shop_list": []},
         "mood": 0, "bench_lock": False, "bench": [], "put_fur": [],
         "has_fur": [], "mate_list": [], "replace_fur": []},
    )
    if _frog_is_home(state):
        # The official client hides manual replacement while the frog is home.
        # code=1 is the client's "server did not keep manual replace" branch.
        return False, "frog_home", 1
    cfg = furniture_cfg(int(furniture_id))
    if not cfg:
        return False, "unknown_furniture", -1
    owned = {int(x) for x in _to_list(data.get("has_fur")) if str(x).lstrip("-").isdigit()}
    if int(furniture_id) not in owned:
        return False, "not_owned", -1
    typ = int(cfg.get("type", -1))
    if typ <= 0:
        return False, "bad_type", -1
    put = data.setdefault("put_fur", [])
    if not isinstance(put, list):
        put = _to_list(put); data["put_fur"] = put
    current_id = next((int(row.get("id", -1)) for row in put if isinstance(row, dict) and int(row.get("type", -1)) == typ), -1)
    put[:] = [x for x in put if not (isinstance(x, dict) and int(x.get("type", -1)) == typ)]
    put.append({"type": typ, "id": int(furniture_id)})
    rep = data.setdefault("replace_fur", [])
    if not isinstance(rep, list):
        rep = _to_list(rep); data["replace_fur"] = rep
    if typ not in [int(x) for x in rep if str(x).lstrip("-").isdigit()]:
        rep.append(typ)
    if current_id != int(furniture_id):
        note_manual_furniture_replace(state, data)
    save_state(state, f"furniture.replace_fur:{furniture_id}:type{typ}")
    return True, "ok", 0


def _apply_furniture_replace_index(
    state: dict,
    *,
    cmd: str,
    list_key: str,
    index: int,
) -> tuple[bool, str, int]:
    if _frog_is_home(state):
        return False, "frog_home", 1
    defaults = {
        "furniture.load_tumbler": {"show_index": 0, "replace_index": 0, "tumbler_list": []},
        "furniture.load_compost": {"show_index": 1, "replace_index": 0, "compost_list": [], "state": 0, "box_index": 0, "box_list": [0]*6},
        "furniture.load_pocket": {"show_index": 1, "replace_index": 0, "list": [], "clover": 0},
        "furniture.load_flowerpot": {"show_index": 1, "replace_index": 0, "list": [], "plant_list": []},
    }
    data = get_furniture_data(state, cmd, defaults[cmd])
    rows = data.get(list_key, [])
    if not isinstance(rows, list):
        rows = _to_list(rows)
        data[list_key] = rows
    index = int(index)
    if index <= 0 or index > len(rows):
        return False, "bad_index", -1
    data["show_index"] = index
    data["replace_index"] = index
    save_state(state, f"{cmd.replace('load','replace')}:{index}")
    return True, "ok", 0


def apply_furniture_replace_tumbler(state: dict, index: int) -> tuple[bool, str, int]:
    return _apply_furniture_replace_index(state, cmd="furniture.load_tumbler", list_key="tumbler_list", index=index)


def apply_furniture_replace_compost(state: dict, index: int) -> tuple[bool, str, int]:
    return _apply_furniture_replace_index(state, cmd="furniture.load_compost", list_key="compost_list", index=index)


def apply_furniture_replace_pocket(state: dict, index: int) -> tuple[bool, str, int]:
    return _apply_furniture_replace_index(state, cmd="furniture.load_pocket", list_key="list", index=index)


def apply_furniture_replace_flowerpot(state: dict, index: int) -> tuple[bool, str, int]:
    return _apply_furniture_replace_index(state, cmd="furniture.load_flowerpot", list_key="list", index=index)


ANNUAL_REVIEW_CAKE_ITEM_ID = 57  # Item.json: 庆典蛋糕
ANNUAL_REVIEW_CAKE_COUNT = 1


def get_annual_review_data(state: dict) -> dict:
    responses = state.setdefault("responses", {})
    data = responses.get("annual.load")
    if not isinstance(data, dict):
        data = {}
        responses["annual.load"] = data
    return data


def apply_annual_review_share(state: dict) -> tuple[bool, int, int]:
    """Persist the 2023 annual-review share state and grant its cake once.

    The preserved client makes annual.share a fire-and-forget request and
    locally flips is_share=true after a successful platform share.  The
    server-side state must mirror that flag or the red dot/reward resets on
    next login.

    The visible reward is a cake; the preserved Item table's matching item is
    id 57, 庆典蛋糕 ("满怀心意的蛋糕 和伙伴们一起分享吧").  We keep this
    as a one-time local restoration of the historic activity.
    """
    data = get_annual_review_data(state)
    meta = state.setdefault("offline_meta", {})
    annual = meta.setdefault("annual_review", {})

    already = bool(annual.get("share_reward_claimed", False))
    # If an imported snapshot had already recorded the share, treat it as
    # claimed unless our local marker explicitly says otherwise.
    if bool(data.get("is_share", False)) and "share_reward_claimed" not in annual:
        already = True
        annual["share_reward_claimed"] = True

    data["is_share"] = True
    reward_count_after = -1
    granted = False
    if not already:
        _, house, _, _ = get_item_parts(state)
        reward_count_after = get_house_count(house, ANNUAL_REVIEW_CAKE_ITEM_ID) + ANNUAL_REVIEW_CAKE_COUNT
        set_house_count(house, ANNUAL_REVIEW_CAKE_ITEM_ID, reward_count_after)
        annual["share_reward_claimed"] = True
        annual["reward_item_id"] = ANNUAL_REVIEW_CAKE_ITEM_ID
        annual["reward_count"] = ANNUAL_REVIEW_CAKE_COUNT
        annual["claimed_at"] = int(time.time())
        granted = True

    state["format_version"] = max(int(state.get("format_version", 2) or 2), 15)
    save_state(state, "annual.share")
    return granted, ANNUAL_REVIEW_CAKE_ITEM_ID, reward_count_after


async def serve_client(ws, state: dict) -> None:
    peer = getattr(ws, "remote_address", None)
    logging.info("客户端连接: %s", peer)

    def request_argument(data: object, *preferred_keys: str) -> object | None:
        if isinstance(data, dict):
            for key in preferred_keys:
                if data.get(key) is not None:
                    return data[key]
            if len(data) == 1:
                return next(iter(data.values()))
            return None
        if isinstance(data, (list, tuple)) and len(data) == 1:
            return data[0]
        return data

    async def complete_capsule_task_and_push(task_id: int, reason: str) -> bool:
        policy = load_offline_policy()
        completed, pending, tasks = capsule_complete_task(state, task_id, policy=policy)
        if not completed:
            return False
        save_state(state, f"capsule.complete_task:{task_id}:{reason}")
        payload, _ = capsule_payload(state, policy=policy)
        await send_json(ws, {"cmd": "capsule_load_coin", "data": {
            "coin": int(payload.get("coin", 0) or 0), "pre_coin": pending,
        }})
        await send_json(ws, {"cmd": "capsule_load_task", "data": {"task_list": tasks}})
        logging.info("CAPSULE TASK COMPLETE id=%d reason=%s pending=%d", task_id, reason, pending)
        return True

    async def record_capsule_spend_and_push(amount: int, reason: str) -> bool:
        policy = load_offline_policy()
        changed, completed, pending, tasks = capsule_record_clover_spend(state, amount, policy=policy)
        if not changed:
            return False
        save_state(state, f"capsule.spend_clover:{amount}:{reason}")
        if not completed:
            return False
        payload, _ = capsule_payload(state, policy=policy)
        await send_json(ws, {"cmd": "capsule_load_coin", "data": {
            "coin": int(payload.get("coin", 0) or 0), "pre_coin": pending,
        }})
        await send_json(ws, {"cmd": "capsule_load_task", "data": {"task_list": tasks}})
        logging.info("CAPSULE TASK COMPLETE id=1 reason=%s pending=%d", reason, pending)
        return True

    try:
        async for raw in ws:
            if isinstance(raw, bytes):
                logging.warning("忽略二进制消息: %d bytes", len(raw))
                continue

            try:
                req = json.loads(raw)
            except json.JSONDecodeError:
                logging.warning("收到非 JSON 文本，已忽略")
                continue

            cmd = req.get("cmd")
            # The shipped client uses dotted request names for the event
            # panels, while their registered protocol callbacks use the
            # underscore names below.  Normalize only this known family.
            if isinstance(cmd, str) and cmd.startswith(("museumday.", "greetcard.", "springcard.", "capsule.", "partycake.", "wishingpool.")):
                cmd = cmd.replace(".", "_")
            session = req.get("session")
            data = req.get("data")
            logging.info("RECV cmd=%s session=%s", cmd, session)

            if cmd == "offline.info":
                await send_response(ws, session, {"edition": "gallery" if GALLERY_MODE else "normal", "protocol": 1})

            elif cmd == "client.hello":
                await send_response(ws, session, {"timestamp": int(time.time())})

            elif cmd == "hall.gen_token":
                await send_response(ws, session, {"code": 0, "token": LOCAL_TOKEN})

            elif cmd == "hall.login":
                sync_home_easter_egg(state)
                for push in state.get("early_pushes", []):
                    await send_json(ws, push)
                await send_response(
                    ws, session, {"code": 0, "account": LOCAL_ACCOUNT}
                )

            elif cmd == "hall.enter_game":
                await send_response(ws, session, {"code": 0})

            elif cmd == "wishingpool_load":
                payload = get_wishing_pool_state(state, load_offline_policy())
                await send_response(ws, session, copy.deepcopy(payload))

            elif cmd == "wishingpool_wish":
                try:
                    before_clover = int(get_role_parts(state)[2].get("clover_point", 0) or 0)
                    ok, item_id = apply_wishing_pool_wish(state, load_offline_policy())
                    after_clover = int(get_role_parts(state)[2].get("clover_point", 0) or 0)
                    if after_clover != before_clover:
                        await send_json(ws, {"cmd": "clover.update", "data": {"clover": after_clover}})
                    await send_response(ws, session, {"id": item_id if ok else 0})
                except Exception:
                    logging.exception("处理 wishingpool_wish 时出错")
                    await send_response(ws, session, {"id": 0})

            elif cmd == "tutorial.step_open_door":
                try:
                    tutorial_open_door(state)
                except Exception:
                    logging.exception("处理 tutorial.step_open_door 时出错")

            elif cmd == "tutorial.step_open_door_q":
                meta = state.setdefault("offline_meta", {}).setdefault("tutorial", {})
                await send_response(ws, session, {"ok": bool(meta.get("door_opened", False))})

            elif cmd == "tutorial.step_ask_award":
                try:
                    tutorial_claim_completion_reward(state, load_offline_policy())
                except Exception:
                    logging.exception("处理 tutorial.step_ask_award 时出错")

            elif cmd == "tutorial.step_ask_award_q":
                meta = state.setdefault("offline_meta", {}).setdefault("tutorial", {})
                await send_response(ws, session, {"ok": bool(meta.get("completion_reward_claimed", False))})

            elif cmd == "annual.load":
                data_out = get_annual_review_data(state)
                await send_response(ws, session, data_out)

            elif cmd == "annual.share":
                try:
                    granted, item_id, count_after = apply_annual_review_share(state)
                    if granted:
                        await send_json(
                            ws,
                            {"cmd": "item.update", "data": {"item": {"item_id": item_id, "count": count_after}}},
                        )
                    await send_response(ws, session, {"code": 0})
                    logging.info(
                        "ANNUAL SHARE ok=True granted=%s item_id=%d count_after=%d",
                        granted, item_id, count_after
                    )
                except Exception:
                    logging.exception("处理 annual.share 时出错")
                    await send_response(ws, session, {"code": 1})

            elif cmd == "cooking.share":
                policy = load_offline_policy()
                cooking_task = cooking_record_task_progress(state, 5)
                changed, inspiration_awarded = museum_day_record_task_progress(
                    state, "share", policy=policy,
                )
                capsule_task = await complete_capsule_task_and_push(4, "share")
                if changed or cooking_task is not None:
                    save_state(state, "cooking.share")
                await send_response(ws, session, {"code": 0})
                if cooking_task is not None:
                    await send_json(ws, {"cmd": "cooking_task_update", "data": {"task": copy.deepcopy(cooking_task)}})
                if changed:
                    museum_day, _ = museum_day_payload(state, policy=policy)
                    await send_json(ws, {"cmd": "museumday_info", "data": {
                        "compass": int(museum_day.get("compass", 0) or 0),
                        "task_num": int(museum_day.get("task_num", 0) or 0),
                    }})
                    if inspiration_awarded:
                        await send_json(ws, {"cmd": "museumday_load", "data": museum_day})
                logging.info(
                    "COOKING SHARE cooking_task_changed=%s museum_task_changed=%s capsule_task_changed=%s",
                    cooking_task is not None, changed, capsule_task,
                )

            elif cmd in {"cooking.load_cooking", "cooking_load_cooking"}:
                await send_response(ws, session, copy.deepcopy(get_cooking_data(state)))

            elif cmd in {"cooking.complete_task", "cooking_complete_task"}:
                try:
                    task_id = int(request_argument(data, "id") or 0)
                    ok, reason, _ = cooking_complete_task(state, task_id)
                    await send_response(ws, session, {"code": 0} if ok else {"code": 1, "reason": reason})
                except Exception:
                    logging.exception("处理 cooking_complete_task 时出错")
                    await send_response(ws, session, {"code": 1})

            elif cmd in {"cooking.refresh_task", "cooking_refresh_task"}:
                try:
                    task_id = int(request_argument(data, "id") or 0)
                    ok, reason, task = cooking_refresh_task(state, task_id)
                    await send_response(
                        ws,
                        session,
                        {"task": copy.deepcopy(task)} if ok else {"code": 1, "reason": reason},
                    )
                except Exception:
                    logging.exception("处理 cooking_refresh_task 时出错")
                    await send_response(ws, session, {"code": 1})

            elif cmd in {"cooking.select", "cooking_select"}:
                try:
                    index = int(request_argument(data, "index") or 0)
                    ok, reason = cooking_select_theme(state, index)
                    await send_response(ws, session, {"code": 0} if ok else {"code": 1, "reason": reason})
                except Exception:
                    logging.exception("处理 cooking_select 时出错")
                    await send_response(ws, session, {"code": 1})

            elif cmd in {"cooking.start_cooking", "cooking_start_cooking"}:
                try:
                    ok, reason, item_id, count = cooking_start(state)
                    await send_response(ws, session, {"code": 0} if ok else {"code": 1, "reason": reason})
                    if ok and item_id is not None and count is not None:
                        await send_json(ws, {"cmd": "item.update", "data": {"item": {"item_id": item_id, "count": count}}})
                except Exception:
                    logging.exception("处理 cooking_start_cooking 时出错")
                    await send_response(ws, session, {"code": 1})

            elif cmd in {"cooking.look_ad", "cooking_look_ad"}:
                try:
                    task = cooking_record_task_progress(state, 2)
                    if task is not None:
                        save_state(state, "cooking.look_ad")
                        await send_json(ws, {"cmd": "cooking_task_update", "data": {"task": copy.deepcopy(task)}})
                    await send_response(ws, session, {"code": 0})
                except Exception:
                    logging.exception("处理 cooking_look_ad 时出错")
                    await send_response(ws, session, {"code": 1})

            elif cmd == "client.load_all_info":
                children_day_claimed = False
                try:
                    policy = load_offline_policy()
                    gallery_guide_changed = complete_gallery_guide_tasks(state) if GALLERY_MODE else False
                    gallery_moments_changed = complete_gallery_moments(state) if GALLERY_MODE else False
                    cooking_login_task = cooking_record_task_progress(state, 1)
                    advance_garden_growth(state, policy=policy)
                    advance_vase_decoration(state, policy=policy)
                    refresh_dudu_garden_shop(state, policy=policy)
                    collect_clover_net_overflow(state, policy=policy)
                    apply_offline_calendar_policy(state, policy=policy)
                    friend_event = advance_giftbox_friend(state, policy=policy)
                    if friend_event is not None:
                        logging.info("GIFTBOX FRIEND EVENT ACTIVE %s", friend_event)
                    advance_courtyard_traveler(state, policy=policy)
                    settled_guest = settle_pending_guest_departure(state)
                    if settled_guest is not None:
                        logging.info("GUEST DEPART SETTLED %s", settled_guest)
                    settled_party = settle_due_party_return(state, policy=policy)
                    if settled_party is not None:
                        if capsule_complete_task(state, 5, policy=policy)[0]:
                            save_state(state, "capsule.complete_task:5:party_return")
                        logging.info("PARTY RETURN SETTLED %s", settled_party)
                    settled_travel = settle_due_travel_return(state, policy=policy)
                    if settled_travel is not None:
                        if capsule_complete_task(state, 4, policy=policy)[0]:
                            save_state(state, "capsule.complete_task:4:travel_return")
                        logging.info("TRAVEL RETURN SETTLED %s", settled_travel)
                        furniture_return = apply_furniture_home_return(state, policy=policy)
                        if furniture_return["changed"]:
                            logging.info("FURNITURE HOME RETURN %s", furniture_return)
                    advance_guest_schedule(state, policy=policy)
                    schedule_unconfirmed_departure(state, policy=policy)
                    advance_activity_state(state, policy=policy)
                    advance_furniture_production(state, policy=policy)
                    advance_handcraft_production(state, policy=policy)
                    refresh_home_frog_motion(state, policy=policy)
                    sync_home_easter_egg(state, policy=policy)
                    reconcile_album_transfer_state(state)
                    unlock_animpicture_guide(state)
                    travel_ency = find_push(state, "encytravel.load")
                    travel_ency_changed = travel_ency and _fill_encyclopedia_selections(
                        travel_ency["data"], load_static_json("encytravel_json.json")["list"]
                    )
                    if refresh_plant_encyclopedia(state) or travel_ency_changed:
                        save_state(state, "encyclopedia.sync")
                    if refresh_guide_task_progress(state):
                        save_state(state, "task.sync_gameplay")
                    children_day_claimed = claim_children_day_login_reward(state)
                    refresh_achievements(state)
                    advance_weekend_lottery(state, int(time.time()), policy)
                except Exception:
                    logging.exception("推进本地登录/旅行/聚会/访客状态失败")
                pushes = state.get("sync_pushes", [])
                logging.info("开始 replay 可写存档，共 %d 条 push", len(pushes))
                for push in pushes:
                    outgoing = push
                    if isinstance(push, dict) and push.get("cmd") == "album.load_all" and GALLERY_MODE:
                        outgoing = {"cmd": "album.load_all", "data": album_full_index_payload(state)}
                    elif isinstance(push, dict) and push.get("cmd") == "story.load":
                        outgoing = copy.deepcopy(push)
                        outgoing["data"] = story_social_payload(state, policy=policy)
                    await send_json(ws, outgoing)
                    await asyncio.sleep(0)
                museum_day_task_changed, _ = museum_day_record_task_progress(state, "login", policy=policy)
                museum_day, museum_day_payload_changed = museum_day_payload(state, policy=policy)
                museum_day_changed = museum_day_task_changed or museum_day_payload_changed
                greet_card, greet_card_changed = greet_card_payload(state, policy=policy)
                spring_card, spring_card_changed = spring_card_payload(state, policy=policy)
                capsule, capsule_changed = capsule_payload(state, policy=policy)
                if capsule_food_is_prepared(state) and capsule_complete_task(state, 2, policy=policy)[0]:
                    capsule, _ = capsule_payload(state, policy=policy)
                    capsule_changed = True
                party_cake, party_cake_changed = party_cake_payload(state, policy=policy)
                if gallery_guide_changed or gallery_moments_changed or cooking_login_task is not None or children_day_claimed or museum_day_changed or greet_card_changed or spring_card_changed or capsule_changed or party_cake_changed:
                    save_state(state, "activity.local_event.initialize")
                await send_json(ws, {"cmd": "museumday_load", "data": museum_day})
                await send_json(ws, {"cmd": "greetcard_load", "data": greet_card})
                await send_json(ws, {"cmd": "greetcard_get_task_item", "data": {
                    "list": copy.deepcopy(greet_card.get("task_item", [])),
                }})
                await send_json(ws, {"cmd": "springcard_load", "data": spring_card})
                await send_json(ws, {"cmd": "capsule_load", "data": capsule})
                await send_json(ws, {"cmd": "partycake_load", "data": party_cake})
                notice_ids = take_achievement_notice(state)
                if notice_ids:
                    title_by_id = {int(row.get("id", -1)): str(row.get("name", "")) for row in achievement_rows()}
                    names = [title_by_id.get(achievement_id, str(achievement_id)) for achievement_id in notice_ids]
                    await send_json(ws, {"cmd": "client.notice", "data": {"msg": f"解锁新称号：{'、'.join(names)}"}})
                await send_response(ws, session, {"code": 0})
                logging.info("可写存档 replay 完成")

            elif cmd == "clover.harvest":
                if not isinstance(data, dict) or "clover_id" not in data:
                    logging.warning("clover.harvest 参数异常: %r", data)
                    await send_response(ws, session, {"code": 1, "offline_error": "bad_clover_id"})
                    continue
                try:
                    clover_id = int(data["clover_id"])
                    ok, reason, result = apply_normal_clover_harvest(state, clover_id)
                except Exception:
                    logging.exception("处理 clover.harvest 时出错")
                    await send_response(ws, session, {"code": 1, "offline_error": "server_error"})
                    continue
                if ok and isinstance(result, dict):
                    element = int(result.get("element", 0))
                    if "clover_total" in result:
                        await send_json(ws, {"cmd": "clover.update", "data": {"clover": result["clover_total"]}})
                    if "calendar_task" in result:
                        await send_json(ws, calendar_task_push(result["calendar_task"]))
                    if "item_id" in result:
                        await send_json(ws, {"cmd": "item.update", "data": {"item": {"item_id": result["item_id"], "count": result["item_count"]}}})
                        if refresh_plant_encyclopedia(state):
                            save_state(state, "encyclopedia.clover_harvest")
                            await send_json(ws, find_push(state, "encyclopedia.load"))
                    if "decoration_id" in result:
                        # Exact production pattern observed for element=3.
                        await send_json(ws, {"cmd": "client.load_decorate", "data": copy.deepcopy(get_decoration_parts(state))})
                    spring_task_changed, _ = spring_card_on_clover_harvest(state, policy=load_offline_policy())
                    if spring_task_changed:
                        save_state(state, "springcard.harvest_task")
                        spring_payload, _ = spring_card_payload(state)
                        await send_json(ws, {"cmd": "springcard_load_task_item", "data": {
                            "task_harvest": int(spring_payload.get("task_harvest", 0) or 0),
                            "share_num": int(spring_payload.get("share_num", 0) or 0),
                            "list": copy.deepcopy(spring_payload.get("task_item", [])),
                        }})
                    await complete_capsule_task_and_push(1, "clover_harvest")
                    if element == 1:
                        await complete_capsule_task_and_push(3, "four_leaf_harvest")
                    await send_response(ws, session, {"clover_id": clover_id})
                    logging.info("CLOVER HARVEST OK id=%d element=%d result=%s", clover_id, element, result)
                else:
                    logging.warning("CLOVER HARVEST BLOCK id=%d reason=%s", clover_id, reason)
                    await send_response(ws, session, {"code": 1, "offline_blocked": True, "reason": reason})

            elif cmd == "clover.harvest_resend":
                req_list = data.get("list", []) if isinstance(data, dict) else []
                if not isinstance(req_list, list):
                    req_list = []
                logging.info("CLOVER RESEND recovery count=%d", len(req_list))
                try:
                    calendar_task = None
                    for row in req_list:
                        if not isinstance(row, dict) or "clover_id" not in row:
                            continue
                        cid = int(row["clover_id"])
                        # If the original request already committed, the slot is
                        # regrowing and this returns not_ready => no double credit.
                        ok, reason, result = apply_normal_clover_harvest(state, cid)
                        if ok and isinstance(result, dict):
                            if "decoration_id" in result:
                                await send_json(ws, {"cmd": "client.load_decorate", "data": copy.deepcopy(get_decoration_parts(state))})
                            if "calendar_task" in result:
                                calendar_task = result["calendar_task"]
                        logging.info("CLOVER RESEND id=%d result=%s", cid, reason)
                    res, _ = get_clover_parts(state)
                    await send_json(ws, {"cmd": "clover.update", "data": {"clover": int(res.get("clover_point", 0) or 0)}})
                    if calendar_task is not None:
                        await send_json(ws, calendar_task_push(calendar_task))
                    await send_response(ws, session, current_clover_field(state))
                except Exception:
                    logging.exception("处理 clover.harvest_resend 时出错")
                    await send_response(ws, session, current_clover_field(state))

            elif cmd == "item.buy":
                if not isinstance(data, dict):
                    logging.warning("item.buy 参数异常: %r", data)
                    await send_response(ws, session, None)
                    continue

                required = ("shop_id", "item_id", "price")
                if any(k not in data for k in required):
                    logging.warning(
                        "item.buy 缺少本地扩展字段，当前客户端可能不是 Stage 6 offline JS: %r",
                        data,
                    )
                    await send_response(ws, session, None)
                    continue

                try:
                    shop_id = int(data["shop_id"])
                    item_id = int(data["item_id"])
                    price = int(data["price"])

                    ok, reason, clover_after, item_count_after, buy_count_after = (
                        apply_shop_purchase(
                            state,
                            shop_id=shop_id,
                            item_id=item_id,
                            price=price,
                        )
                    )
                except Exception:
                    logging.exception("处理 item.buy 时出错")
                    await send_response(ws, session, None)
                    continue

                if ok:
                    # Push authoritative resource state first.
                    await send_json(
                        ws,
                        {
                            "cmd": "clover.update",
                            "data": {"clover": clover_after},
                        },
                    )
                    await send_json(
                        ws,
                        {
                            "cmd": "item.update",
                            "data": {
                                "item": {
                                    "item_id": item_id,
                                    "count": item_count_after,
                                }
                            },
                        },
                    )
                    if item_id == ANIM_PICTURE_PAGE_ITEM_ID:
                        await send_json(ws, {"cmd": "animpicture.load", "data": copy.deepcopy(get_animpicture_state(state))})

                    # The client's item_buy() callback also recognizes
                    # optional ticket / ads / share / is_free fields.
                    # For normal offline purchases we intentionally return
                    # a plain, non-free success.
                    await send_response(
                        ws,
                        session,
                        {
                            "code": 0,
                            "is_free": False,
                        },
                    )
                    if item_id == 204002:
                        await send_json(ws, {"cmd": "furniture.load_furniture",
                                             "data": copy.deepcopy(_furniture_production_data(state))})
                    if not GALLERY_MODE and reason != "wishing_duplicate_refunded":
                        await record_capsule_spend_and_push(price, "shop_buy")
                    logging.info(
                        "SHOP BUY OK shop_id=%d item_id=%d price=%d "
                        "clover=%d house_count=%d purchased_count=%d",
                        shop_id,
                        item_id,
                        price,
                        clover_after,
                        item_count_after,
                        buy_count_after,
                    )
                else:
                    # Returning null prevents the client's e && (...) success
                    # branch from adding / displaying a successful purchase.
                    if clover_after is not None:
                        await send_json(
                            ws,
                            {
                                "cmd": "clover.update",
                                "data": {"clover": clover_after},
                            },
                        )
                    await send_response(ws, session, None)
                    logging.warning(
                        "SHOP BUY BLOCK shop_id=%s item_id=%s price=%s reason=%s",
                        data.get("shop_id"),
                        data.get("item_id"),
                        data.get("price"),
                        reason,
                    )

            elif cmd in {
                "item.putin_bag",
                "item.takeout_bag",
                "item.putin_desk",
                "item.takeout_desk",
            }:
                area = "bag" if cmd.endswith("_bag") else "desk"
                action = "putin" if ".putin_" in cmd else "takeout"

                if not isinstance(data, dict) or "pos" not in data:
                    logging.warning("%s 参数异常: %r", cmd, data)
                    await send_response(ws, session, {"conflict": True})
                    continue

                try:
                    pos = int(data["pos"])
                    item_id = data.get("item_id")
                    ok, reason, changed_item, new_count = apply_item_slot_change(
                        state,
                        area=area,
                        action=action,
                        pos=pos,
                        item_id=item_id,
                    )
                except Exception:
                    logging.exception("处理 %s 时出错", cmd)
                    await send_response(ws, session, {"conflict": True})
                    continue

                if ok:
                    if changed_item is not None and new_count is not None:
                        # 客户端 item_update() 以“服务器给出的绝对库存数”为准。
                        await send_json(
                            ws,
                            {
                                "cmd": "item.update",
                                "data": {
                                    "item": {
                                        "item_id": changed_item,
                                        "count": new_count,
                                    }
                                },
                            },
                        )

                    if action == "putin" and item_id is not None and item_type(int(item_id)) == ITEM_LUNCHBOX:
                        await complete_capsule_task_and_push(2, f"food_in_{area}")

                    await send_response(ws, session, {"conflict": False})
                    logging.info(
                        "ITEM SLOT OK cmd=%s pos=%d item=%s house_count=%s reason=%s",
                        cmd,
                        pos,
                        changed_item,
                        new_count,
                        reason,
                    )
                else:
                    await send_response(ws, session, {"conflict": True})
                    logging.warning(
                        "ITEM SLOT BLOCK cmd=%s pos=%d item=%s reason=%s",
                        cmd,
                        pos,
                        data.get("item_id") if isinstance(data, dict) else None,
                        reason,
                    )

            elif cmd == "item.gacha":
                try:
                    is_reward = bool(data.get("is_reward", False)) if isinstance(data, dict) else bool(data)
                    ok, reason, out = apply_gacha_draw(state, is_reward=is_reward)
                    if ok:
                        await send_json(ws, {"cmd": "item.update_ticket", "data": {"ticket": out["ticket_total"]}})
                        await complete_capsule_task_and_push(7, "gacha_draw")
                        await send_response(ws, session, {"ticket": out["rank"]})
                        logging.info("GACHA DRAW rank=%d ticket_total=%d", out["rank"], out["ticket_total"])
                    else:
                        await send_response(ws, session, {"code": 1, "offline_blocked": True, "reason": reason})
                        logging.warning("GACHA DRAW BLOCK reason=%s", reason)
                except Exception:
                    logging.exception("处理 item.gacha 时出错")
                    await send_response(ws, session, {"code": 1, "offline_error": "server_error"})

            elif cmd == "item.redeem_prize":
                try:
                    prize_id = int(data.get("prize_id", -1)) if isinstance(data, dict) else int(data)
                    ok, reason, out = apply_gacha_redeem(state, prize_id)
                    if ok:
                        if "ticket_total" in out:
                            await send_json(ws, {"cmd": "item.update_ticket", "data": {"ticket": out["ticket_total"]}})
                        if "item_id" in out:
                            await send_json(ws, {"cmd": "item.update", "data": {"item": {"item_id": out["item_id"], "count": out["item_count"]}}})
                        if "calendar_task" in out:
                            await send_json(ws, calendar_task_push(out["calendar_task"]))
                        logging.info("GACHA REDEEM OK %s", out)
                    else:
                        logging.warning("GACHA REDEEM BLOCK prize_id=%s reason=%s", prize_id, reason)
                    # ProtocolList marks item.redeem_prize as no-response; do not
                    # manufacture a session response even if a patched client sends one.
                except Exception:
                    logging.exception("处理 item.redeem_prize 时出错")

            elif cmd == "item.set_bag_completed":
                if isinstance(data, dict) and "completed" in data:
                    try:
                        completed = bool(data["completed"])
                        meta = state.setdefault("offline_meta", {})
                        if completed:
                            meta["travel_ready_armed_at"] = int(time.time())
                        else:
                            meta.pop("travel_ready_armed_at", None)
                        persist_bag_completed(state, completed)
                        if completed:
                            schedule_normal_departure(state)
                        else:
                            cancelled = cancel_pending_activity(state, "travel")
                            cancelled = cancel_pending_activity(state, "travel_auto") or cancelled
                            if cancelled:
                                save_state(state, "cancel.travel.user")
                        logging.info(
                            "BAG COMPLETED SAVED value=%s", completed
                        )
                    except Exception:
                        logging.exception("处理 item.set_bag_completed 时出错")
                else:
                    logging.warning("item.set_bag_completed 参数异常: %r", data)

            elif cmd == "guest.accept_invit":
                accepted = bool(data.get("is_accept")) if isinstance(data, dict) else False
                try:
                    apply_drawing_accept(state, accepted)
                    await send_response(ws, session, {"code": 0})
                except Exception:
                    logging.exception("处理 guest.accept_invit 时出错")
                    await send_response(ws, session, {"code": 1})

            elif cmd in {"guest.putin_bag", "guest.takeout_bag"}:
                if not isinstance(data, dict) or "pos" not in data:
                    await send_response(ws, session, {"code": 1})
                    continue
                try:
                    action = "putin" if cmd == "guest.putin_bag" else "takeout"
                    ok, reason, iid, count = apply_drawing_item(
                        state,
                        action=action,
                        pos=int(data["pos"]),
                        item_id=data.get("id"),
                    )
                    if ok and iid is not None and count is not None:
                        await send_json(ws, {"cmd": "item.update", "data": {"item": {"item_id": iid, "count": count}}})
                    await send_response(ws, session, {"code": 0 if ok else 1, "offline_reason": reason})
                except Exception:
                    logging.exception("处理 %s 时出错", cmd)
                    await send_response(ws, session, {"code": 1})

            elif cmd == "guest.lock_bag":
                try:
                    ok, reason, draw_state = toggle_drawing_lock(state)
                    await send_response(ws, session, {"code": 0 if ok else 1, "offline_reason": reason})
                    logging.info("DRAWING LOCK result=%s state=%s", reason, draw_state)
                except Exception:
                    logging.exception("处理 guest.lock_bag 时出错")
                    await send_response(ws, session, {"code": 1})

            elif cmd == "guest.confirm":
                if isinstance(data, dict):
                    gid = data.get("id")
                else:
                    gid = data
                if gid is not None:
                    try:
                        apply_guest_confirm(state, int(gid))
                    except Exception:
                        logging.exception("处理 guest.confirm 时出错")

            elif cmd == "guest.serve":
                if isinstance(data, dict) and "id" in data and "item_id" in data:
                    try:
                        ok, count, result = apply_guest_serve(
                            state, int(data["id"]), int(data["item_id"])
                        )
                        if ok and count is not None:
                            cooking_task = cooking_record_task_progress(state, 3)
                            if cooking_task is not None:
                                save_state(state, "cooking.task.guest_feed")
                                await send_json(ws, {"cmd": "cooking_task_update", "data": {"task": copy.deepcopy(cooking_task)}})
                            await send_json(ws, {"cmd": "item.update", "data": {"item": {"item_id": int(data["item_id"]), "count": count}}})
                            await complete_capsule_task_and_push(6, "guest_feed")

                            # Do not reveal the gift/invitation immediately.
                            # Production behavior observed by the user: after
                            # feeding, the visitor remains in the courtyard; on
                            # the next full login the visitor is gone and only
                            # then does the mailbox/invitation update appear.
                            logging.info(
                                "GUEST SERVE OK guest=%s item=%s taste=%s tier=%s outcome=%s",
                                result.get("guest_name"),
                                data["item_id"],
                                result.get("feeling"),
                                result.get("feedback_tier"),
                                result.get("outcome"),
                            )
                        else:
                            logging.warning("GUEST SERVE BLOCK %r", result)
                    except Exception:
                        logging.exception("处理 guest.serve 时出错")

            elif cmd == "guest.set_expire_time":
                try:
                    value = int(request_argument(data, "time") or 0)
                    guest = find_push(state, "guest.load")
                    if guest and isinstance(guest.get("data"), dict) and int(guest["data"].get("id", -1)) >= 0:
                        guest["data"]["expire_time"] = value
                        save_state(state, f"guest.set_expire_time:{value}")
                except Exception:
                    logging.exception("处理 guest.set_expire_time 时出错")

            elif cmd == "guest.finish":
                try:
                    guest = find_push(state, "guest.load")
                    current = guest.get("data") if guest and isinstance(guest.get("data"), dict) else None
                    if current and int(current.get("id", -1)) >= 0 and bool(current.get("served", False)):
                        settled = settle_pending_guest_departure(state)
                        if settled is not None:
                            logging.info("GUEST SERVED DEPART SETTLED %s", settled)
                    elif current and int(current.get("id", -1)) >= 0:
                        guest_id = int(current["id"])
                        garden_guest_departure(state, guest_id)
                        guest["data"] = {"id": -1}
                        schedule_next_guest(state)
                        save_state(state, f"guest.finish:{guest_id}")
                except Exception:
                    logging.exception("处理 guest.finish 时出错")

            elif cmd in {"task.load", "task.load_list"}:
                if refresh_guide_task_progress(state):
                    save_state(state, "task.refresh")
                push = find_push(state, cmd)
                await send_response(ws, session, copy.deepcopy(push.get("data", {})) if push else {})

            elif cmd == "pray.load_grays":
                policy = load_offline_policy()
                _, house, _, _ = get_item_parts(state)
                material_before = get_house_count(house, HANDCRAFT_MATERIAL_ID)
                advance_handcraft_production(state, policy=policy)
                material_after = get_house_count(house, HANDCRAFT_MATERIAL_ID)
                if material_after != material_before:
                    await send_json(ws, {"cmd": "item.update", "data": {"item": {"item_id": HANDCRAFT_MATERIAL_ID, "count": material_after}}})
                await send_response(ws, session, copy.deepcopy(get_handcraft_data(state)))

            elif cmd == "pray.compose":
                try:
                    craft_id = int(data.get("id", 0)) if isinstance(data, dict) else int(data or 0)
                    ok, reason, consumed, amulet_id, total = apply_pray_compose(state, craft_id)
                    if ok:
                        _, house, _, _ = get_item_parts(state)
                        for item_id in consumed:
                            await send_json(ws, {"cmd": "item.update", "data": {"item": {"item_id": item_id, "count": get_house_count(house, item_id)}}})
                        await send_json(ws, {"cmd": "item.update", "data": {"item": {"item_id": amulet_id, "count": total}}})
                    await send_response(ws, session, {"code": 0 if ok else 1, "item_list": [amulet_id] if ok else [], "offline_reason": reason})
                except Exception:
                    logging.exception("处理 pray.compose 时出错")
                    await send_response(ws, session, {"code": 1, "item_list": []})

            elif cmd == "pray.confirm_make_box":
                try:
                    ok, rewards = apply_pray_confirm_make_box(state)
                    if ok:
                        for item_id, total in rewards:
                            await send_json(ws, {"cmd": "item.update", "data": {"item": {"item_id": item_id, "count": total}}})
                        await send_json(ws, {"cmd": "pray.load_grays", "data": copy.deepcopy(get_handcraft_data(state))})
                    await send_response(ws, session, {"code": 0 if ok else 1})
                except Exception:
                    logging.exception("处理 pray.confirm_make_box 时出错")
                    await send_response(ws, session, {"code": 1})

            elif cmd in {"furniture.load_tumbler", "furniture.load_flowerpot", "furniture.load_compost", "furniture.load_pocket", "furniture.load_furniture"}:
                try:
                    policy = load_offline_policy()
                    advance_garden_growth(state, policy=policy)
                    refresh_dudu_garden_shop(state, policy=policy)
                    production = advance_furniture_production(state, policy=policy)
                    if production["completed"]:
                        _, house, _, _ = get_item_parts(state)
                        for item_id in sorted(set(production["consumed_resources"])):
                            await send_json(ws, {"cmd": "item.update", "data": {"item": {"item_id": item_id, "count": get_house_count(house, item_id)}}})
                        if production.get("awarded", True):
                            await send_json(ws, {"cmd": "client.load_events", "data": copy.deepcopy(get_event_list(state))})
                        product_cmd = {
                            "tumbler": "furniture.load_tumbler",
                            "compost": "furniture.load_compost",
                            "pocket": "furniture.load_pocket",
                        }.get(production["product_kind"])
                        if product_cmd and product_cmd != cmd:
                            product_push = find_push(state, product_cmd)
                            if product_push:
                                await send_json(ws, {"cmd": product_cmd, "data": copy.deepcopy(product_push.get("data", {}))})
                    mapper = {
                        "furniture.load_tumbler": "furniture.load_tumbler",
                        "furniture.load_flowerpot": "furniture.load_flowerpot",
                        "furniture.load_compost": "furniture.load_compost",
                        "furniture.load_pocket": "furniture.load_pocket",
                        "furniture.load_furniture": "furniture.load_furniture",
                    }
                    push = find_push(state, mapper[cmd])
                    payload = copy.deepcopy(push.get("data", {})) if push else {}
                    await send_response(ws, session, payload)
                except Exception:
                    logging.exception("处理 %s 时出错", cmd)
                    await send_response(ws, session, {"code": 1})

            elif cmd == "furniture.putin_box":
                if not isinstance(data, dict):
                    await send_response(ws, session, {"code": 1}); continue
                try:
                    ok, reason, count = apply_compost_put(state, int(data.get("pos",0)), int(data.get("id",0)))
                    if ok:
                        await send_json(ws,{"cmd":"item.update","data":{"item":{"item_id":int(data["id"]),"count":count}}})
                    await send_response(ws,session,{"code":0 if ok else 1,"offline_reason":reason})
                except Exception:
                    logging.exception("处理 furniture.putin_box 时出错"); await send_response(ws,session,{"code":1})

            elif cmd == "furniture.takeout_box":
                if not isinstance(data, dict):
                    await send_response(ws, session, {"code": 1}); continue
                try:
                    ok, reason, iid, count = apply_compost_takeout(state, int(data.get("pos",0)))
                    if ok and iid is not None:
                        await send_json(ws,{"cmd":"item.update","data":{"item":{"item_id":iid,"count":count}}})
                    await send_response(ws,session,{"code":0 if ok else 1,"offline_reason":reason})
                except Exception:
                    logging.exception("处理 furniture.takeout_box 时出错"); await send_response(ws,session,{"code":1})

            elif cmd == "furniture.pocket_get":
                try:
                    before_task = copy.deepcopy(get_calendar_task(state, 2))
                    amount=apply_pocket_get(state, policy=load_offline_policy()); res,_=get_clover_parts(state)
                    if amount>0: await send_json(ws,{"cmd":"clover.update","data":{"clover":int(res.get("clover_point",0) or 0)}})
                    after_task = get_calendar_task(state, 2)
                    if before_task != after_task and after_task is not None:
                        await send_json(ws, calendar_task_push(after_task))
                    await send_response(ws,session,{"code":0})
                    logging.info("POCKET GET amount=%d",amount)
                except Exception:
                    logging.exception("处理 furniture.pocket_get 时出错"); await send_response(ws,session,{"code":1})

            elif cmd == "furniture.flowerpot_harvest":
                if not isinstance(data,dict): await send_response(ws,session,{"code":1}); continue
                try:
                    ok,reason,rewards=apply_flowerpot_harvest(state,int(data.get("index",0)))
                    if ok:
                        _,house,_,_=get_item_parts(state)
                        for r in rewards:
                            await send_json(ws,{"cmd":"item.update","data":{"item":{"item_id":int(r["item_id"]),"count":get_house_count(house,int(r["item_id"]))}}})
                        if refresh_plant_encyclopedia(state):
                            save_state(state, "encyclopedia.flowerpot_harvest")
                            await send_json(ws, find_push(state, "encyclopedia.load"))
                        await send_json(ws,{"cmd":"client.load_decorate","data":copy.deepcopy(get_decoration_parts(state))})
                    await send_response(ws,session,{"code":0 if ok else 1,"offline_reason":reason,"item_list":rewards if ok else []})
                except Exception:
                    logging.exception("处理 furniture.flowerpot_harvest 时出错"); await send_response(ws,session,{"code":1,"item_list":[]})

            elif cmd == "client.change_decorate":
                try:
                    did=int(data.get("id",0)) if isinstance(data,dict) else int(data or 0); ok,reason=apply_change_decoration(state,did)
                    if ok: await send_json(ws,{"cmd":"client.load_decorate","data":copy.deepcopy(get_decoration_parts(state))})
                    await send_response(ws,session,{"code":0 if ok else 1,"offline_reason":reason})
                except Exception:
                    logging.exception("处理 client.change_decorate 时出错"); await send_response(ws,session,{"code":1})

            elif cmd == "furniture.buy_shop":
                try:
                    sid=int(data.get("shop_id",0)) if isinstance(data,dict) else int(data or 0); ok,reason,clover,iid,count,refunded=apply_furniture_shop_buy(state,sid)
                    is_special_gift=bool(iid in DUDU_FURNITURE_GIFT_TARGETS) if iid is not None else False
                    if ok:
                        if reason == "starter_kit":
                            await send_response(ws, session, {"code": 0, "offline_reason": reason})
                            await send_json(ws, {"cmd": "clover.update", "data": {"clover": clover}})
                            _, house, _, _ = get_item_parts(state)
                            for item_id in DUDU_STARTER_KITS[sid]:
                                await send_json(ws, {"cmd": "item.update", "data": {"item": {
                                    "item_id": item_id, "count": get_house_count(house, item_id)}}})
                            continue
                        if is_special_gift:
                            await send_response(ws,session,{"code":0,"offline_reason":reason})
                            await send_json(ws,{"cmd":"clover.update","data":{"clover":clover}})
                            gift_kind=DUDU_FURNITURE_GIFT_TARGETS[int(iid)][0]
                            push_cmd="furniture.load_furniture" if gift_kind=="furniture" else "furniture.load_flowerpot"
                            push_data=_furniture_production_data(state) if gift_kind=="furniture" else get_flowerpot_data(state)
                            await send_json(ws,{"cmd":push_cmd,"data":copy.deepcopy(push_data)})
                            continue
                        if refunded:
                            await send_response(ws,session,{"code":0,"offline_reason":reason})
                            await send_json(ws,{"cmd":"clover.update","data":{"clover":clover}})
                            await send_json(ws,{"cmd":"item.update","data":{"item":{"item_id":iid,"count":count}}})
                            continue
                        await send_json(ws,{"cmd":"clover.update","data":{"clover":clover}})
                        await send_json(ws,{"cmd":"item.update","data":{"item":{"item_id":iid,"count":count}}})
                    await send_response(ws,session,{"code":0 if ok else 1,"offline_reason":reason})
                except Exception:
                    logging.exception("处理 furniture.buy_shop 时出错"); await send_response(ws,session,{"code":1})

            elif cmd in {"furniture.putin_bench", "furniture.takeout_bench"}:
                if not isinstance(data, dict):
                    await send_response(ws, session, {"code": 1})
                    continue
                try:
                    pos = int(data.get("pos", 0) or 0)
                    if cmd == "furniture.putin_bench":
                        ok, reason, item_id, count = apply_furniture_bench_change(state, "putin", pos, int(data.get("id", 0) or 0))
                    else:
                        ok, reason, item_id, count = apply_furniture_bench_change(state, "takeout", pos)
                    if ok and item_id is not None and count is not None:
                        await send_json(ws, {"cmd": "item.update", "data": {"item": {"item_id": item_id, "count": count}}})
                    if ok:
                        production = advance_furniture_production(state, policy=load_offline_policy())
                        if production["changed"]:
                            await send_json(ws, {"cmd": "furniture.load_furniture", "data": copy.deepcopy(_furniture_production_data(state))})
                    await send_response(ws, session, {"code": 0 if ok else 1, "offline_reason": reason})
                except Exception:
                    logging.exception("处理 %s 时出错", cmd)
                    await send_response(ws, session, {"code": 1})

            elif cmd == "travel.load_gift":
                payload = giftbox_payload(state)
                if session is not None:
                    await send_response(ws, session, payload)
                else:
                    await send_json(ws, {"cmd": "travel.load_gift", "data": payload})
                logging.info("GIFTBOX LOAD pictures=%d specialty_count=%d", len(payload["pictures"]), sum(int(x.get("count",0) or 0) for x in payload["specialtys"] if isinstance(x,dict)))

            elif cmd == "travel.album_to_gift":
                try:
                    pid = int(data.get("picture_id", -1)) if isinstance(data, dict) else int(data)
                    ok, code = apply_album_to_gift(state, pid, policy=load_offline_policy())
                    await send_response(ws, session, {"code": code})
                    if ok:
                        await send_json(ws, {"cmd": "album.load_all", "data": album_full_index_payload(state)})
                    logging.info("GIFTBOX ALBUM->GIFT id=%d ok=%s code=%d", pid, ok, code)
                except Exception:
                    logging.exception("处理 travel.album_to_gift 时出错")
                    await send_response(ws, session, {"code": 1})

            elif cmd == "travel.gift_to_album":
                try:
                    pid = int(data.get("picture_id", -1)) if isinstance(data, dict) else int(data)
                    ok, code = apply_gift_to_album(state, pid)
                    await send_response(ws, session, {"code": code})
                    if ok:
                        await send_json(ws, {"cmd": "album.load_all", "data": album_full_index_payload(state)})
                    logging.info("GIFTBOX GIFT->ALBUM id=%d ok=%s code=%d", pid, ok, code)
                except Exception:
                    logging.exception("处理 travel.gift_to_album 时出错")
                    await send_response(ws, session, {"code": 1})

            elif cmd == "travel.gift_delete_album":
                try:
                    pid = int(data.get("id", -1)) if isinstance(data, dict) else int(data)
                    apply_gift_delete_picture(state, pid)
                    await send_response(ws, session, {"code": 0})
                    logging.info("GIFTBOX DELETE PICTURE id=%d", pid)
                except Exception:
                    logging.exception("处理 travel.gift_delete_album 时出错")
                    await send_response(ws, session, {"code": 1})

            elif cmd == "travel.bag_to_gift":
                try:
                    iid = int(data.get("item_id", -1)) if isinstance(data, dict) else int(data)
                    ok, code, count = apply_bag_to_gift(state, iid, policy=load_offline_policy())
                    if ok:
                        # Client's callback expects the authoritative item.update
                        # to have changed ItemModel before it updates GiftBoxModel.
                        await send_json(ws, {"cmd": "item.update", "data": {"item": {"item_id": iid, "count": count}}})
                    await send_response(ws, session, {"code": code})
                    logging.info("GIFTBOX BAG->GIFT item=%d ok=%s code=%d", iid, ok, code)
                except Exception:
                    logging.exception("处理 travel.bag_to_gift 时出错")
                    await send_response(ws, session, {"code": 1})

            elif cmd == "travel.gift_to_bag":
                try:
                    iid = int(data.get("item_id", -1)) if isinstance(data, dict) else int(data)
                    ok, code, count = apply_gift_to_bag(state, iid)
                    # Do not send item.update here: official client callback calls
                    # addHouseItem() itself after a successful response.
                    await send_response(ws, session, {"code": code})
                    logging.info("GIFTBOX GIFT->BAG item=%d ok=%s code=%d house=%d", iid, ok, code, count)
                except Exception:
                    logging.exception("处理 travel.gift_to_bag 时出错")
                    await send_response(ws, session, {"code": 1})

            elif cmd == "album.delete":
                try:
                    pid = int(data.get("id", -1)) if isinstance(data, dict) else int(data)
                    ok = apply_album_delete(state, pid)
                    await send_response(ws, session, {"code": 0 if ok else 1})
                    logging.info("ALBUM DELETE id=%d ok=%s", pid, ok)
                except Exception:
                    logging.exception("处理 album.delete 时出错")
                    await send_response(ws, session, {"code": 1})

            elif cmd == "album.load_recover":
                try:
                    payload = album_recover_payload(state)
                    await send_response(ws, session, payload)
                    logging.info("ALBUM RECOVER LOAD returned=%d", len(payload.get("pictures", [])))
                except Exception:
                    logging.exception("处理 album.load_recover 时出错")
                    await send_response(ws, session, {"pictures": []})

            elif cmd == "album.recover":
                try:
                    pid = int(data.get("id", -1)) if isinstance(data, dict) else int(data)
                    ok, reason = apply_album_recover(state, pid)
                    await send_response(ws, session, {"code": 0 if ok else 1})
                    logging.info("ALBUM RECOVER id=%d ok=%s reason=%s", pid, ok, reason)
                except Exception:
                    logging.exception("处理 album.recover 时出错")
                    await send_response(ws, session, {"code": 1})

            elif cmd == "calendar.load_note":
                await send_response(ws, session, calendar_note_payload(state))

            elif cmd == "calendar.get_st_reward":
                try:
                    day = request_argument(data, "day")
                    ok, out = apply_calendar_st_reward(state, reward_day=int(day) if day is not None else None)
                    if ok:
                        await send_json(ws, {"cmd": "item.update", "data": {"item": {"item_id": out["item_id"], "count": out["item_count"]}}})
                    await send_response(ws, session, out)
                    logging.info("CALENDAR SOLAR REWARD ok=%s out=%s", ok, out)
                except Exception:
                    logging.exception("处理 calendar.get_st_reward 时出错")
                    await send_response(ws, session, {"code": 1})

            elif cmd == "lottery.open":
                try:
                    before_task = copy.deepcopy(get_calendar_task(state, 2))
                    ok, reason, payload, clover_total = lottery_open(state, policy=load_offline_policy())
                    if ok:
                        if clover_total is not None:
                            await send_json(ws, {"cmd": "clover.update", "data": {"clover": clover_total}})
                        after_task = get_calendar_task(state, 2)
                        if before_task != after_task and after_task is not None:
                            await send_json(ws, calendar_task_push(after_task))
                        await send_response(ws, session, payload)
                        logging.info("LOTTERY OPEN phase=%s payload=%s", get_lottery_data(state).get("phase"), payload)
                    else:
                        await send_response(ws, session, {"code": 1, "offline_blocked": True, "reason": reason})
                except Exception:
                    logging.exception("处理 lottery.open 时出错")
                    await send_response(ws, session, {"code": 1, "offline_error": "server_error"})

            elif cmd == "lottery.select":
                try:
                    values = data.get("list", []) if isinstance(data, dict) else []
                    ok, reason = lottery_select(state, values, policy=load_offline_policy())
                    await send_response(ws, session, {"code": 0} if ok else {"code": 1, "offline_blocked": True, "reason": reason})
                except Exception:
                    logging.exception("处理 lottery.select 时出错")
                    await send_response(ws, session, {"code": 1, "offline_error": "server_error"})

            elif cmd == "lottery.confirm_reward":
                try:
                    ok, reason, mail = lottery_confirm_reward(state)
                    if ok:
                        await send_json(ws, {"cmd": "lottery.load", "data": copy.deepcopy(get_lottery_data(state))})
                    await send_response(ws, session, {"code": 0} if ok else {"code": 1, "offline_blocked": True, "reason": reason})
                    if mail is not None:
                        await send_json(ws, {"cmd": "notify.new_mail", "data": {"mail": copy.deepcopy(mail)}})
                except Exception:
                    logging.exception("处理 lottery.confirm_reward 时出错")
                    await send_response(ws, session, {"code": 1, "offline_error": "server_error"})

            elif cmd == "adsmgr.share":
                try:
                    # ProtocolList: adsmgr_share:[["ads_type"], true].  Stage 6F.1
                    # incorrectly looked only for type/id, so Lottery type=3 was
                    # acknowledged without creating its mailbox reward.
                    share_type = parse_adsmgr_share_type(data)
                    if share_type == 3:
                        ok, reason, mail = lottery_claim_extra(state)
                        # Do NOT send item.update here.  The original client says
                        # “邮箱有动静”; the item is credited by mail.open.
                        await send_response(ws, session, {"code": 0} if ok else {"code": 1, "reason": reason})
                        logging.info(
                            "LOTTERY EXTRA MAIL CLAIM ads_type=%d result=%s mail_id=%s",
                            share_type, reason, None if mail is None else mail.get("id"),
                        )
                    else:
                        # Existing offline behavior: share-only systems are completed
                        # without invoking third-party SDKs.
                        await send_response(ws, session, {"code": 0})
                except Exception:
                    logging.exception("处理 adsmgr.share 时出错")
                    await send_response(ws, session, {"code": 1, "offline_error": "server_error"})

            elif cmd == "adsmgr.share_ads":
                try:
                    ads_id = str(request_argument(data, "ads_id") or "")
                    if ads_id == "frog_back":
                        ok, reason, mail = claim_frog_back_picture_reward(state)
                        if mail is not None:
                            await send_json(ws, {"cmd": "notify.new_mail", "data": {"mail": copy.deepcopy(mail)}})
                        logging.info(
                            "FROG BACK PICTURE REWARD result=%s mail_id=%s pictures=%s",
                            reason,
                            None if mail is None else mail.get("id"),
                            [] if mail is None else mail.get("pictures", []),
                        )
                    else:
                        logging.info("OFFLINE SHARE ADS completed ads_id=%s", ads_id)
                except Exception:
                    logging.exception("处理 adsmgr.share_ads 时出错")

            elif cmd == "share.get_reward":
                try:
                    picture_id = int(request_argument(data, "id") or -1)
                    ok, reason, mail = claim_photo_share_reward(state, picture_id)
                    await send_response(ws, session, {"code": 0 if ok else 1})
                    if mail is not None:
                        await send_json(ws, {"cmd": "notify.new_mail", "data": {"mail": copy.deepcopy(mail)}})
                    logging.info(
                        "PHOTO SHARE REWARD picture_id=%d result=%s mail_id=%s",
                        picture_id, reason, None if mail is None else mail.get("id"),
                    )
                except Exception:
                    logging.exception("处理 share.get_reward 时出错")
                    await send_response(ws, session, {"code": 1, "offline_error": "server_error"})

            elif cmd == "calendar.get_luck_reward":
                try:
                    ok, out = apply_calendar_lucky_reward(state)
                    if ok:
                        await send_json(ws, {"cmd": "item.update", "data": {"item": {"item_id": out["item_id"], "count": out["item_count"]}}})
                    await send_response(ws, session, out)
                    logging.info("CALENDAR LUCKY REWARD ok=%s out=%s", ok, out)
                except Exception:
                    logging.exception("处理 calendar.get_luck_reward 时出错")
                    await send_response(ws, session, {"code": 1})

            elif cmd in {"calendar.get_beginer_reward", "calendar.get_code_reward"}:
                try:
                    code_text = None
                    if cmd == "calendar.get_code_reward":
                        if isinstance(data, dict):
                            code_text = data.get("code") or data.get("day") or data.get("str") or data.get("value")
                        elif data is not None:
                            code_text = str(data)
                    ok, out = apply_calendar_beginner_reward(state, code=code_text)
                    if ok:
                        await send_json(ws, {"cmd": "item.update", "data": {"item": {"item_id": out["item_id"], "count": out["item_count"]}}})
                    await send_response(ws, session, out)
                    logging.info("CALENDAR BEGINNER REWARD cmd=%s ok=%s out=%s", cmd, ok, out)
                except Exception:
                    logging.exception("处理 %s 时出错", cmd)
                    await send_response(ws, session, {"code": 1, "day": 0})

            elif cmd == "encytravel.set_show_sub":
                try:
                    sid = int(data.get("long_id", -1)) if isinstance(data, dict) else int(data)
                    ok = apply_encytravel_show_sub(state, sid)
                    logging.info("ENCYTRAVEL SHOW SUB long_id=%d ok=%s", sid, ok)
                except Exception:
                    logging.exception("处理 encytravel.set_show_sub 时出错")

            elif cmd == "encyclopedia.set_show_sub":
                try:
                    sid = int(data.get("long_id", -1)) if isinstance(data, dict) else int(data)
                    ok = apply_encyclopedia_show_sub(state, sid)
                    logging.info("ENCYCLOPEDIA SHOW SUB long_id=%d ok=%s", sid, ok)
                except Exception:
                    logging.exception("处理 encyclopedia.set_show_sub 时出错")

            elif cmd in {
                "furniture.replace_fur",
                "furniture.replace_tumbler",
                "furniture.replace_compost",
                "furniture.replace_pocket",
                "furniture.replace_flowerpot",
            }:
                try:
                    if cmd == "furniture.replace_fur":
                        value = int(data.get("id", -1)) if isinstance(data, dict) else int(data)
                        ok, reason, code = apply_furniture_replace_fur(state, value)
                    else:
                        value = int(data.get("index", -1)) if isinstance(data, dict) else int(data)
                        fn = {
                            "furniture.replace_tumbler": apply_furniture_replace_tumbler,
                            "furniture.replace_compost": apply_furniture_replace_compost,
                            "furniture.replace_pocket": apply_furniture_replace_pocket,
                            "furniture.replace_flowerpot": apply_furniture_replace_flowerpot,
                        }[cmd]
                        ok, reason, code = fn(state, value)
                    await send_response(ws, session, {"code": int(code)})
                    logging.info("FURNITURE REPLACE cmd=%s value=%d ok=%s reason=%s code=%d", cmd, value, ok, reason, code)
                except Exception:
                    logging.exception("处理 %s 时出错", cmd)
                    await send_response(ws, session, {"code": -1})

            elif cmd == "story.load":
                try:
                    await send_response(ws, session, story_social_payload(state, policy=load_offline_policy()))
                    logging.info("STORY LOAD count=%d new=%d", len(get_story_state(state).get("stories", [])), int(get_story_state(state).get("new_story_id", 0) or 0))
                except Exception:
                    logging.exception("处理 story.load 时出错")
                    await send_response(ws, session, {"stories": [], "new_story_id": 0})

            elif cmd == "story.read_new_story":
                try:
                    apply_story_read_new(state)
                    logging.info("STORY READ NEW")
                except Exception:
                    logging.exception("处理 story.read_new_story 时出错")

            elif cmd == "story.send_gift":
                try:
                    sid = int(data.get("id", -1)) if isinstance(data, dict) else -1
                    gift = int(data.get("gift", -1)) if isinstance(data, dict) else -1
                    ok = apply_story_send_gift(state, sid, gift)
                    logging.info("STORY SEND GIFT id=%d gift=%d ok=%s", sid, gift, ok)
                except Exception:
                    logging.exception("处理 story.send_gift 时出错")

            elif cmd == "story.feedback_gift":
                # Offline mode has no remote player to notify. Acknowledge locally.
                logging.info("STORY FEEDBACK ignored safely data=%r", data)

            elif cmd == "travel.load_note":
                try:
                    await send_response(ws, session, copy.deepcopy(get_travel_note_state(state)))
                    logging.info("TRAVEL NOTE LOAD count=%d", len(get_travel_note_state(state).get("note_list", [])))
                except Exception:
                    logging.exception("处理 travel.load_note 时出错")
                    await send_response(ws, session, {"note_list": []})

            elif cmd == "travel.read_note":
                try:
                    changed = apply_travel_read_note(state, data)
                    logging.info("TRAVEL NOTE READ changed=%d ids=%r", changed, data)
                except Exception:
                    logging.exception("处理 travel.read_note 时出错")

            elif cmd == "visit.set_carpet":
                try:
                    value = int(data.get("id", 0)) if isinstance(data, dict) else int(data)
                    apply_visit_set_field(state, "carpet", value)
                except Exception:
                    logging.exception("处理 visit.set_carpet 时出错")

            elif cmd == "visit.set_expire_time":
                try:
                    value = int(data.get("time", 0)) if isinstance(data, dict) else int(data)
                    apply_visit_set_field(state, "expire_time", value)
                except Exception:
                    logging.exception("处理 visit.set_expire_time 时出错")

            elif cmd == "visit.open":
                try:
                    result = apply_visit_open(state, policy=load_offline_policy())
                    if isinstance(result, dict):
                        if "clover" in result:
                            await send_json(ws, {"cmd": "clover.update", "data": {"clover": result["clover"]}})
                        if "ticket" in result:
                            await send_json(ws, {"cmd": "item.update_ticket", "data": {"ticket": result["ticket"]}})
                        if "calendar_task" in result:
                            await send_json(ws, calendar_task_push(result["calendar_task"]))
                        logging.info("VISIT OPEN settled=%s", result)
                except Exception:
                    logging.exception("处理 visit.open 时出错")

            elif cmd == "greetcard_load":
                try:
                    payload, changed = greet_card_payload(state, policy=load_offline_policy())
                    if changed:
                        save_state(state, "greetcard.load.initialize")
                    await send_response(ws, session, payload)
                except Exception:
                    logging.exception("处理 greetcard_load 时出错")
                    await send_response(ws, session, _greet_card_default(0))

            elif cmd == "greetcard_load_count":
                try:
                    payload, changed = greet_card_payload(state, policy=load_offline_policy())
                    if changed:
                        save_state(state, "greetcard.count.initialize")
                    await send_response(ws, session, {"count": int(payload.get("global_num", 0) or 0)})
                except Exception:
                    logging.exception("处理 greetcard_load_count 时出错")
                    await send_response(ws, session, {"count": 0})

            elif cmd == "greetcard_buy":
                try:
                    item_id = data.get("id") if isinstance(data, dict) else data
                    resource, _ = get_clover_parts(state)
                    clover_before = int(resource.get("clover_point", 0) or 0)
                    ok, reason, clover = greet_card_buy(state, int(item_id), policy=load_offline_policy())
                    if ok:
                        save_state(state, f"greetcard.buy:{int(item_id)}")
                    await send_response(ws, session, {"code": 0 if ok else 1})
                    if ok and clover is not None:
                        await send_json(ws, {"cmd": "clover.update", "data": {"clover": clover}})
                        await record_capsule_spend_and_push(clover_before - int(clover), "greetcard_buy")
                    logging.info("GREETCARD BUY id=%s ok=%s reason=%s", item_id, ok, reason)
                except Exception:
                    logging.exception("处理 greetcard_buy 时出错")
                    await send_response(ws, session, {"code": 1})

            elif cmd == "greetcard_change_bg":
                try:
                    background_id = data.get("id") if isinstance(data, dict) else data
                    ok, reason = greet_card_change_background(state, int(background_id), policy=load_offline_policy())
                    if ok:
                        save_state(state, f"greetcard.change_bg:{int(background_id)}")
                    await send_response(ws, session, {"code": 0 if ok else 1})
                except Exception:
                    logging.exception("处理 greetcard_change_bg 时出错")
                    await send_response(ws, session, {"code": 1})

            elif cmd == "greetcard_change_bless":
                try:
                    bless_id = data.get("id") if isinstance(data, dict) else data
                    ok, reason = greet_card_change_bless(state, int(bless_id), policy=load_offline_policy())
                    if ok:
                        save_state(state, f"greetcard.change_bless:{int(bless_id)}")
                    await send_response(ws, session, {"code": 0 if ok else 1})
                except Exception:
                    logging.exception("处理 greetcard_change_bless 时出错")
                    await send_response(ws, session, {"code": 1})

            elif cmd == "greetcard_put_tags":
                try:
                    if isinstance(data, dict):
                        pos, tag_id = data.get("pos"), data.get("id")
                    else:
                        pos, tag_id = data[0], data[1]
                    ok, reason = greet_card_put_tag(state, int(pos), int(tag_id), policy=load_offline_policy())
                    if ok:
                        save_state(state, f"greetcard.put_tag:{int(pos)}:{int(tag_id)}")
                    await send_response(ws, session, {"code": 0 if ok else 1})
                except Exception:
                    logging.exception("处理 greetcard_put_tags 时出错")
                    await send_response(ws, session, {"code": 1})

            elif cmd == "greetcard_send":
                try:
                    ok, reason = greet_card_send(state, policy=load_offline_policy())
                    if ok:
                        save_state(state, "greetcard.send")
                    await send_response(ws, session, {"code": 0 if ok else 1})
                    if ok:
                        payload, _ = greet_card_payload(state, policy=load_offline_policy())
                        await send_json(ws, {"cmd": "greetcard_load", "data": payload})
                        await send_json(ws, {"cmd": "greetcard_get_task_item", "data": {
                            "list": copy.deepcopy(payload.get("task_item", [])),
                        }})
                    logging.info("GREETCARD SEND ok=%s reason=%s", ok, reason)
                except Exception:
                    logging.exception("处理 greetcard_send 时出错")
                    await send_response(ws, session, {"code": 1})

            elif cmd == "greetcard_get_reward":
                try:
                    mooncake_id = data.get("id") if isinstance(data, dict) else data
                    ok, rewards = greet_card_get_reward(state, int(mooncake_id), policy=load_offline_policy())
                    if ok:
                        save_state(state, f"greetcard.reward:{int(mooncake_id)}")
                    await send_response(ws, session, {"list": rewards if ok else []})
                    if ok:
                        _, house, _, _ = get_item_parts(state)
                        for item_id in rewards:
                            await send_json(ws, {"cmd": "item.update", "data": {"item": {"item_id": item_id, "count": get_house_count(house, item_id)}}})
                except Exception:
                    logging.exception("处理 greetcard_get_reward 时出错")
                    await send_response(ws, session, {"list": []})

            elif cmd == "greetcard_get_task_item":
                try:
                    items = greet_card_get_task_items(state, policy=load_offline_policy())
                    await send_response(ws, session, {"list": items})
                except Exception:
                    logging.exception("处理 greetcard_get_task_item 时出错")
                    await send_response(ws, session, {"list": []})

            elif cmd == "greetcard_get_task_reward":
                try:
                    ok, items = greet_card_claim_task_items(state, policy=load_offline_policy())
                    if items:
                        save_state(state, "greetcard.task_reward")
                    await send_response(ws, session, {"code": 0 if ok else 1})
                except Exception:
                    logging.exception("处理 greetcard_get_task_reward 时出错")
                    await send_response(ws, session, {"code": 1})

            elif cmd == "greetcard_stock":
                try:
                    payload, _ = greet_card_payload(state, policy=load_offline_policy())
                    target = _greet_card_state(state)
                    prices = [max(0, int(value)) for value in greet_card_policy().get("background_prices", [0, 100, 150, 200])]
                    stock_num = int(payload.get("stock_num", 0) or 0)
                    price = prices[min(stock_num + 1, len(prices) - 1)] if prices else 0
                    res, _ = get_clover_parts(state)
                    if stock_num >= 2 or int(res.get("clover_point", 0) or 0) < price:
                        ok = False
                    else:
                        res["clover_point"] = int(res.get("clover_point", 0) or 0) - price
                        target["stock_num"] = stock_num + 1
                        save_state(state, "greetcard.stock")
                        ok = True
                    await send_response(ws, session, {"code": 0 if ok else 1})
                    if ok:
                        await send_json(ws, {"cmd": "clover.update", "data": {"clover": int(res.get("clover_point", 0) or 0)}})
                except Exception:
                    logging.exception("处理 greetcard_stock 时出错")
                    await send_response(ws, session, {"code": 1})

            elif cmd in {"greetcard_send_gift", "greetcard_feedback_gift", "greetcard_read_new"}:
                # Offline mode has no remote player/card recipient. Preserve the
                # local card history and acknowledge the client interaction.
                await send_response(ws, session, {"code": 0})

            elif cmd == "springcard_load":
                try:
                    payload, changed = spring_card_payload(state, policy=load_offline_policy())
                    if changed:
                        save_state(state, "springcard.load.initialize")
                    await send_response(ws, session, payload)
                except Exception:
                    logging.exception("处理 springcard_load 时出错")
                    await send_response(ws, session, _spring_card_default(0))

            elif cmd == "springcard_load_count":
                try:
                    payload, changed = spring_card_payload(state, policy=load_offline_policy())
                    if changed:
                        save_state(state, "springcard.count.initialize")
                    await send_response(ws, session, {"count": int(payload.get("global_num", 0) or 0)})
                except Exception:
                    logging.exception("处理 springcard_load_count 时出错")
                    await send_response(ws, session, {"count": 0})

            elif cmd == "springcard_buy":
                try:
                    resource, _ = get_clover_parts(state)
                    clover_before = int(resource.get("clover_point", 0) or 0)
                    ok, tag_id = spring_card_buy(state, policy=load_offline_policy())
                    if ok:
                        save_state(state, f"springcard.buy:{tag_id}")
                    await send_response(ws, session, {"tags_id": tag_id if ok else 0})
                    if ok:
                        res, _ = get_clover_parts(state)
                        await send_json(ws, {"cmd": "clover.update", "data": {"clover": int(res.get("clover_point", 0) or 0)}})
                        await record_capsule_spend_and_push(
                            clover_before - int(res.get("clover_point", 0) or 0),
                            "springcard_buy",
                        )
                except Exception:
                    logging.exception("处理 springcard_buy 时出错")
                    await send_response(ws, session, {"tags_id": 0})

            elif cmd == "springcard_change_bg":
                try:
                    background_id = data.get("id") if isinstance(data, dict) else data
                    ok, reason = spring_card_change_background(state, int(background_id), policy=load_offline_policy())
                    if ok:
                        save_state(state, f"springcard.change_bg:{int(background_id)}")
                    await send_response(ws, session, {"code": 0 if ok else 1})
                except Exception:
                    logging.exception("处理 springcard_change_bg 时出错")
                    await send_response(ws, session, {"code": 1})

            elif cmd == "springcard_change_bless":
                try:
                    bless_id = data.get("id") if isinstance(data, dict) else data
                    ok, reason = spring_card_change_bless(state, int(bless_id), policy=load_offline_policy())
                    if ok:
                        save_state(state, f"springcard.change_bless:{int(bless_id)}")
                    await send_response(ws, session, {"code": 0 if ok else 1})
                except Exception:
                    logging.exception("处理 springcard_change_bless 时出错")
                    await send_response(ws, session, {"code": 1})

            elif cmd == "springcard_put_tags":
                try:
                    if isinstance(data, dict):
                        pos, tag_id = data.get("pos"), data.get("id")
                    else:
                        pos, tag_id = data[0], data[1]
                    ok, reason = spring_card_put_tag(state, int(pos), int(tag_id), policy=load_offline_policy())
                    if ok:
                        save_state(state, f"springcard.put_tag:{int(pos)}:{int(tag_id)}")
                    await send_response(ws, session, {"code": 0 if ok else 1})
                except Exception:
                    logging.exception("处理 springcard_put_tags 时出错")
                    await send_response(ws, session, {"code": 1})

            elif cmd == "springcard_send":
                try:
                    ok, box_id = spring_card_send(state, policy=load_offline_policy())
                    if ok:
                        save_state(state, f"springcard.send:{box_id}")
                    await send_response(ws, session, {"box_id": box_id if ok else 0})
                except Exception:
                    logging.exception("处理 springcard_send 时出错")
                    await send_response(ws, session, {"box_id": 0})

            elif cmd == "springcard_get_reward":
                try:
                    ok, reward_id, count = spring_card_open_box(state, policy=load_offline_policy())
                    if ok:
                        save_state(state, "springcard.open_box")
                    await send_response(ws, session, {"id": reward_id, "num": count})
                    if ok and count > 0:
                        if reward_id == LOTTERY_CLOVER_ITEM_ID:
                            res, _ = get_clover_parts(state)
                            await send_json(ws, {"cmd": "clover.update", "data": {"clover": int(res.get("clover_point", 0) or 0)}})
                        elif reward_id == LOTTERY_TICKET_ITEM_ID:
                            res, _ = get_clover_parts(state)
                            await send_json(ws, {"cmd": "item.update_ticket", "data": {"ticket": int(res.get("ticket", 0) or 0)}})
                        else:
                            _, house, _, _ = get_item_parts(state)
                            await send_json(ws, {"cmd": "item.update", "data": {"item": {"item_id": reward_id, "count": get_house_count(house, reward_id)}}})
                except Exception:
                    logging.exception("处理 springcard_get_reward 时出错")
                    await send_response(ws, session, {"id": 0, "num": 0})

            elif cmd == "springcard_get_task_item":
                try:
                    payload, changed = spring_card_payload(state, policy=load_offline_policy())
                    if changed:
                        save_state(state, "springcard.task_item.initialize")
                    await send_response(ws, session, {
                        "task_harvest": int(payload.get("task_harvest", 0) or 0),
                        "share_num": int(payload.get("share_num", 0) or 0),
                        "list": copy.deepcopy(payload.get("task_item", [])),
                    })
                except Exception:
                    logging.exception("处理 springcard_get_task_item 时出错")
                    await send_response(ws, session, {"task_harvest": 0, "share_num": 0, "list": []})

            elif cmd == "springcard_share_tags":
                try:
                    tag_id = request_argument(data, "id", "tag_id")
                    ok, share_code, pending = spring_card_share_tag(state, int(tag_id), policy=load_offline_policy())
                    if ok:
                        save_state(state, f"springcard.share:{int(tag_id)}")
                        payload, _ = spring_card_payload(state)
                        await send_json(ws, {"cmd": "springcard_load_task_item", "data": {
                            "task_harvest": int(payload.get("task_harvest", 0) or 0),
                            "share_num": int(payload.get("share_num", 0) or 0),
                            "list": pending,
                        }})
                    await send_response(ws, session, {"share_code": share_code if ok else ""})
                except Exception:
                    logging.exception("处理 springcard_share_tags 时出错")
                    await send_response(ws, session, {"share_code": ""})

            elif cmd == "springcard_get_share_tags":
                try:
                    code = data.get("code") if isinstance(data, dict) else data
                    ok, tag_id = spring_card_exchange_tag(state, str(code or ""), policy=load_offline_policy())
                    if ok:
                        save_state(state, f"springcard.exchange:{tag_id}")
                    await send_response(ws, session, {"tags_id": tag_id if ok else 0, "code": 0 if ok else 1})
                except Exception:
                    logging.exception("处理 springcard_get_share_tags 时出错")
                    await send_response(ws, session, {"tags_id": 0, "code": 1})

            elif cmd == "springcard_get_task_reward":
                try:
                    ok, tags = spring_card_claim_task_items(state, policy=load_offline_policy())
                    if tags:
                        save_state(state, "springcard.task_reward")
                    await send_response(ws, session, {"code": 0 if ok else 1})
                except Exception:
                    logging.exception("处理 springcard_get_task_reward 时出错")
                    await send_response(ws, session, {"code": 1})

            elif cmd == "capsule_load":
                try:
                    payload, changed = capsule_payload(state, policy=load_offline_policy())
                    if capsule_food_is_prepared(state) and capsule_complete_task(state, 2, policy=load_offline_policy())[0]:
                        payload, _ = capsule_payload(state, policy=load_offline_policy())
                        changed = True
                    if changed:
                        save_state(state, "capsule.load.initialize")
                    await send_response(ws, session, payload)
                except Exception:
                    logging.exception("处理 capsule_load 时出错")
                    await send_response(ws, session, _capsule_default(0))

            elif cmd == "capsule_load_coin":
                try:
                    payload, changed = capsule_payload(state, policy=load_offline_policy())
                    if changed:
                        save_state(state, "capsule.load_coin.initialize")
                    await send_response(ws, session, {
                        "coin": int(payload.get("coin", 0) or 0),
                        "pre_coin": int(payload.get("pre_coin", 0) or 0),
                    })
                except Exception:
                    logging.exception("处理 capsule_load_coin 时出错")
                    await send_response(ws, session, {"coin": 0, "pre_coin": 0})

            elif cmd == "capsule_load_task":
                try:
                    payload, changed = capsule_payload(state, policy=load_offline_policy())
                    if changed:
                        save_state(state, "capsule.load_task.initialize")
                    await send_response(ws, session, {"task_list": copy.deepcopy(payload.get("task_list", []))})
                except Exception:
                    logging.exception("处理 capsule_load_task 时出错")
                    await send_response(ws, session, {"task_list": []})

            elif cmd == "capsule_get_coin":
                try:
                    ok, coin, pending = capsule_get_coin(state, policy=load_offline_policy())
                    if ok:
                        save_state(state, "capsule.get_coin")
                    await send_response(ws, session, {"code": 0 if ok else 1})
                    logging.info("CAPSULE GET COIN ok=%s coin=%d received=%d", ok, coin, pending)
                except Exception:
                    logging.exception("处理 capsule_get_coin 时出错")
                    await send_response(ws, session, {"code": 1})

            elif cmd == "capsule_twist":
                try:
                    ok, reward_id, grants = capsule_twist(state, policy=load_offline_policy())
                    if ok:
                        save_state(state, f"capsule.twist:{reward_id}")
                    await send_response(ws, session, {"reward_id": reward_id if ok else 0})
                    if ok:
                        _, house, _, _ = get_item_parts(state)
                        for grant in grants:
                            item_id = int(grant["item_id"])
                            await send_json(ws, {"cmd": "item.update", "data": {"item": {
                                "item_id": item_id,
                                "count": get_house_count(house, item_id),
                            }}})
                except Exception:
                    logging.exception("处理 capsule_twist 时出错")
                    await send_response(ws, session, {"reward_id": 0})

            elif cmd == "capsule_fast_task":
                try:
                    task_index = request_argument(data, "id", "index", "task_index")
                    if task_index is None:
                        task_index = 1
                    ok, pending, tasks = capsule_fast_task(state, int(task_index), policy=load_offline_policy())
                    if ok:
                        save_state(state, f"capsule.fast_task:{int(task_index)}")
                    await send_response(ws, session, {"code": 0 if ok else 1})
                    if ok:
                        payload, _ = capsule_payload(state)
                        res, _ = get_clover_parts(state)
                        await send_json(ws, {"cmd": "clover.update", "data": {"clover": int(res.get("clover_point", 0) or 0)}})
                        await send_json(ws, {"cmd": "capsule_load_coin", "data": {
                            "coin": int(payload.get("coin", 0) or 0), "pre_coin": pending,
                        }})
                        await send_json(ws, {"cmd": "capsule_load_task", "data": {"task_list": tasks}})
                except Exception:
                    logging.exception("处理 capsule_fast_task 时出错")
                    await send_response(ws, session, {"code": 1})

            elif cmd == "capsule_patch":
                try:
                    tasks, remaining = capsule_patch_tasks(state, policy=load_offline_policy())
                    if tasks:
                        save_state(state, "capsule.patch")
                    await send_response(ws, session, {"task_list": tasks})
                    logging.info("CAPSULE PATCH tasks=%s remaining=%d", tasks, remaining)
                except Exception:
                    logging.exception("处理 capsule_patch 时出错")
                    await send_response(ws, session, {"task_list": []})

            elif cmd == "partycake_load":
                try:
                    payload, changed = party_cake_payload(state, policy=load_offline_policy())
                    if changed:
                        save_state(state, "partycake.load.initialize")
                    await send_response(ws, session, payload)
                except Exception:
                    logging.exception("处理 partycake_load 时出错")
                    await send_response(ws, session, _party_cake_default(0))

            elif cmd == "partycake_load_mate":
                try:
                    payload, changed = party_cake_payload(state, policy=load_offline_policy())
                    if changed:
                        save_state(state, "partycake.load_mate.initialize")
                    await send_response(ws, session, {
                        "pre_cream": int(payload.get("pre_cream", 0) or 0),
                        "pre_sugar": int(payload.get("pre_sugar", 0) or 0),
                    })
                except Exception:
                    logging.exception("处理 partycake_load_mate 时出错")
                    await send_response(ws, session, {"pre_cream": 0, "pre_sugar": 0})

            elif cmd == "partycake_load_task":
                try:
                    payload, changed = party_cake_payload(state, policy=load_offline_policy())
                    if changed:
                        save_state(state, "partycake.load_task.initialize")
                    task_id = data.get("id") if isinstance(data, dict) else data
                    rows = payload.get("task_list", [])
                    row = next((item for item in rows if int(item.get("id", 0) or 0) == int(task_id or 0)), {})
                    await send_response(ws, session, {"task": copy.deepcopy(row)})
                except Exception:
                    logging.exception("处理 partycake_load_task 时出错")
                    await send_response(ws, session, {"task": {}})

            elif cmd == "partycake_load_qa":
                try:
                    payload, changed = party_cake_payload(state, policy=load_offline_policy())
                    if changed:
                        save_state(state, "partycake.load_qa.initialize")
                    await send_response(ws, session, party_cake_qa_payload(state, policy=load_offline_policy()))
                except Exception:
                    logging.exception("处理 partycake_load_qa 时出错")
                    await send_response(ws, session, {"guest": 0, "wrong": 0, "answer": [], "reward": []})

            elif cmd == "partycake_get_mate":
                try:
                    ok, cream, sugar = party_cake_get_mate(state, policy=load_offline_policy())
                    if ok:
                        save_state(state, "partycake.get_mate")
                    await send_response(ws, session, {"code": 0 if ok else 1})
                    logging.info("PARTYCAKE GET MATE ok=%s cream=%d sugar=%d", ok, cream, sugar)
                except Exception:
                    logging.exception("处理 partycake_get_mate 时出错")
                    await send_response(ws, session, {"code": 1})

            elif cmd == "partycake_make":
                try:
                    layer = data.get("layer") if isinstance(data, dict) else data
                    ok, next_state, recipe = party_cake_make(state, int(layer), policy=load_offline_policy())
                    if ok:
                        save_state(state, f"partycake.make:{int(layer)}")
                    await send_response(ws, session, {"state": next_state})
                    logging.info("PARTYCAKE MAKE ok=%s layer=%s recipe=%s", ok, layer, recipe)
                except Exception:
                    logging.exception("处理 partycake_make 时出错")
                    await send_response(ws, session, {"state": PARTY_CAKE_MAKING})

            elif cmd == "partycake_reward_make":
                try:
                    ok, next_state, reward = party_cake_claim_make_reward(state, policy=load_offline_policy())
                    if ok:
                        save_state(state, "partycake.reward_make")
                    if ok and next_state in {PARTY_CAKE_QA, PARTY_CAKE_QA_REWARD}:
                        await send_json(ws, {"cmd": "partycake_load_qa", "data": party_cake_qa_payload(state)})
                    await send_response(ws, session, {"state": next_state})
                    if reward is not None:
                        _, house, _, _ = get_item_parts(state)
                        await send_json(ws, {"cmd": "item.update", "data": {"item": {
                            "item_id": int(reward["item_id"]),
                            "count": get_house_count(house, int(reward["item_id"])),
                        }}})
                except Exception:
                    logging.exception("处理 partycake_reward_make 时出错")
                    await send_response(ws, session, {"state": PARTY_CAKE_MAKE_REWARD})

            elif cmd == "partycake_answer":
                try:
                    answer_index = request_argument(data, "id", "answer_index", "index", "answer")
                    ok, next_state, qa_data = party_cake_answer(state, int(answer_index), policy=load_offline_policy())
                    if ok:
                        save_state(state, f"partycake.answer:{int(answer_index)}")
                        await send_json(ws, {"cmd": "partycake_load_qa", "data": qa_data})
                    await send_response(ws, session, {"state": next_state})
                except Exception:
                    logging.exception("处理 partycake_answer 时出错")
                    await send_response(ws, session, {"state": PARTY_CAKE_QA})

            elif cmd == "partycake_reward_qa":
                try:
                    ok, next_state, rewards = party_cake_claim_qa_reward(state, policy=load_offline_policy())
                    if ok:
                        save_state(state, "partycake.reward_qa")
                    await send_response(ws, session, {"state": next_state})
                    if ok:
                        _, house, _, _ = get_item_parts(state)
                        for reward in rewards:
                            item_id = int(reward["item_id"])
                            await send_json(ws, {"cmd": "item.update", "data": {"item": {
                                "item_id": item_id,
                                "count": get_house_count(house, item_id),
                            }}})
                except Exception:
                    logging.exception("处理 partycake_reward_qa 时出错")
                    await send_response(ws, session, {"state": PARTY_CAKE_QA_REWARD})

            elif cmd == "partycake_light":
                try:
                    ok, next_state = party_cake_light(state, policy=load_offline_policy())
                    if ok:
                        save_state(state, "partycake.light")
                    await send_response(ws, session, {"state": next_state})
                except Exception:
                    logging.exception("处理 partycake_light 时出错")
                    await send_response(ws, session, {"state": PARTY_CAKE_LIGHT})

            elif cmd == "partycake_reward_light":
                try:
                    ok, next_state, reward = party_cake_claim_light_reward(state, policy=load_offline_policy())
                    if ok:
                        save_state(state, "partycake.reward_light")
                        await send_json(ws, {"cmd": "partycake_load", "data": copy.deepcopy(_party_cake_state(state))})
                    await send_response(ws, session, {"state": next_state})
                    if reward is not None:
                        achievement_id = reward.get("achievement_id")
                        if achievement_id is not None:
                            title_by_id = {int(row.get("id", -1)): str(row.get("name", "")) for row in achievement_rows()}
                            await send_json(ws, {"cmd": "client.notice", "data": {
                                "msg": f"解锁新称号：{title_by_id.get(int(achievement_id), achievement_id)}"
                            }})
                        else:
                            _, house, _, _ = get_item_parts(state)
                            item_id = int(reward["item_id"])
                            await send_json(ws, {"cmd": "item.update", "data": {"item": {
                                "item_id": item_id,
                                "count": get_house_count(house, item_id),
                            }}})
                except Exception:
                    logging.exception("处理 partycake_reward_light 时出错")
                    await send_response(ws, session, {"state": PARTY_CAKE_LIGHT_REWARD_STATE})

            elif cmd == "partycake_reward_share":
                try:
                    share_index = request_argument(data, "id", "index", "share_index")
                    if share_index is None:
                        current, _ = party_cake_payload(state, policy=load_offline_policy())
                        share_index = next((index + 1 for index, value in enumerate(current.get("share_get", [])) if not value), 1)
                    ok, item_id, count_after = party_cake_claim_share_reward(state, int(share_index), policy=load_offline_policy())
                    if ok:
                        save_state(state, f"partycake.share:{int(share_index)}")
                    await send_response(ws, session, {"code": 0 if ok else 1})
                    if ok:
                        await send_json(ws, {"cmd": "item.update", "data": {"item": {
                            "item_id": item_id, "count": count_after,
                        }}})
                except Exception:
                    logging.exception("处理 partycake_reward_share 时出错")
                    await send_response(ws, session, {"code": 1})

            elif cmd == "museumday_load":
                try:
                    payload, changed = museum_day_payload(state, policy=load_offline_policy())
                    if changed:
                        save_state(state, "museumday.load.initialize")
                    await send_response(ws, session, payload)
                except Exception:
                    logging.exception("处理 museumday_load 时出错")
                    await send_response(ws, session, _museum_day_empty_payload(0))

            elif cmd == "museumday_load_path":
                try:
                    payload, changed = museum_day_payload(state, policy=load_offline_policy())
                    if changed:
                        save_state(state, "museumday.load_path.initialize")
                    await send_response(ws, session, {"path": payload.get("path", [])})
                except Exception:
                    logging.exception("处理 museumday_load_path 时出错")
                    await send_response(ws, session, {"path": []})

            elif cmd == "museumday_refresh":
                try:
                    ok, left_num, reason = museum_day_refresh(state, policy=load_offline_policy())
                    if ok:
                        save_state(state, "museumday.refresh")
                    await send_response(ws, session, {"left_num": left_num})
                    logging.info("MUSEUMDAY REFRESH ok=%s left=%s reason=%s", ok, left_num, reason)
                except Exception:
                    logging.exception("处理 museumday_refresh 时出错")
                    await send_response(ws, session, {"left_num": -1})

            elif cmd in {"museumday_random_compass", "museumday_inspire"}:
                try:
                    policy = load_offline_policy()
                    ok, payload = museum_day_advance(state, policy=policy, use_inspire=cmd == "museumday_inspire")
                    response = {"next": int(payload.get("next", 0) or 0), "inspire": int(payload.get("inspire_num", 0) or 0)}
                    if ok:
                        save_state(state, f"{cmd}.advance")
                    await send_response(ws, session, response)
                    if ok:
                        await send_json(ws, {"cmd": "museumday_info", "data": {
                            "compass": int(payload.get("compass", 0) or 0),
                            "task_num": int(payload.get("task_num", 0) or 0),
                        }})
                    if ok and int(payload.get("frog", 0) or 0) == len(payload.get("path", [])):
                        await send_json(ws, {"cmd": "museumday_load", "data": payload})
                    logging.info("MUSEUMDAY ADVANCE cmd=%s ok=%s next=%s", cmd, ok, response["next"])
                except Exception:
                    logging.exception("处理 %s 时出错", cmd)
                    await send_response(ws, session, {"next": 0, "inspire": 0})

            elif cmd == "museumday_dir_compass":
                try:
                    direction = data.get("dir") if isinstance(data, dict) else data
                    policy = load_offline_policy()
                    ok, payload = museum_day_advance(state, policy=policy, direction=int(direction))
                    if ok:
                        save_state(state, "museumday.dir_compass")
                    await send_response(ws, session, {"code": 0 if ok else 1})
                    if ok:
                        await send_json(ws, {"cmd": "museumday_info", "data": {
                            "compass": int(payload.get("compass", 0) or 0),
                            "task_num": int(payload.get("task_num", 0) or 0),
                        }})
                        await send_json(ws, {"cmd": "museumday_load", "data": payload})
                except Exception:
                    logging.exception("处理 museumday_dir_compass 时出错")
                    await send_response(ws, session, {"code": 1})

            elif cmd == "museumday_get_items":
                try:
                    ok, items = museum_day_get_items(state, policy=load_offline_policy())
                    if items:
                        save_state(state, "museumday.get_items")
                    await send_response(ws, session, {"code": 0 if ok else 1})
                except Exception:
                    logging.exception("处理 museumday_get_items 时出错")
                    await send_response(ws, session, {"code": 1})

            elif cmd == "museumday_arrive":
                try:
                    ok, reason = museum_day_arrive(state, policy=load_offline_policy())
                    if ok:
                        save_state(state, "museumday.arrive")
                    await send_response(ws, session, {"code": 0 if ok else 1})
                    logging.info("MUSEUMDAY ARRIVE ok=%s reason=%s", ok, reason)
                except Exception:
                    logging.exception("处理 museumday_arrive 时出错")
                    await send_response(ws, session, {"code": 1})

            elif cmd == "museumday_start_advance":
                try:
                    museum_id = data.get("id") if isinstance(data, dict) else data
                    museum_id = int(museum_id)
                    policy = load_offline_policy()
                    payload, _ = museum_day_payload(state, policy=policy)
                    completed = {
                        int(row.get("id", 0) or 0)
                        for row in payload.get("museum_list", []) if isinstance(row, dict)
                    }
                    if len(completed) < len(MUSEUM_DAY_TICKET_BY_ID):
                        ok, reason, cost = False, "collection_not_complete", 0
                    else:
                        prices = [max(0, int(value)) for value in museum_day_policy(policy).get("restart_clover", [100, 200, 200])]
                        price_index = max(0, len(payload.get("museum_list", [])) - len(MUSEUM_DAY_TICKET_BY_ID))
                        cost = prices[min(price_index, len(prices) - 1)] if prices else 0
                        ok, reason = _museum_day_begin(state, museum_id, int(time.time()), policy, cost=cost)
                    if ok:
                        save_state(state, f"museumday.start_advance:{museum_id}")
                    await send_response(ws, session, {"code": 0 if ok else 1})
                    if ok:
                        res, _ = get_clover_parts(state)
                        await send_json(ws, {"cmd": "clover.update", "data": {"clover": int(res.get("clover_point", 0) or 0)}})
                    logging.info("MUSEUMDAY START id=%s ok=%s cost=%s reason=%s", museum_id, ok, cost, reason)
                except Exception:
                    logging.exception("处理 museumday_start_advance 时出错")
                    await send_response(ws, session, {"code": 1})

            elif cmd == "museum.load":
                try:
                    old_museum = copy.deepcopy(
                        state.setdefault("offline_meta", {}).get("museum_list")
                    )
                    museum_list = sync_museum_progress(state)
                    payload = {"museum_list": museum_list}
                    # ProtocolList.museum_load has response=true, same pattern as
                    # mail.load: reply on the request session so the registered
                    # MuseumModel callback receives the data.
                    if session is not None:
                        await send_response(ws, session, payload)
                    else:
                        await send_json(ws, {"cmd": "museum.load", "data": payload})
                    # Avoid creating a backup on every museum open when nothing
                    # changed; preserve only actual progress changes.
                    if old_museum != museum_list:
                        save_state(state, "museum.load.sync")
                except Exception:
                    logging.exception("处理 museum.load 时出错")
                    if session is not None:
                        await send_response(ws, session, {"museum_list": []})
                    else:
                        await send_json(ws, {"cmd": "museum.load", "data": {"museum_list": []}})

            elif cmd == "mail.load":
                await send_response(ws, session, copy.deepcopy(get_mail_list(state)))

            elif cmd == "mail.load_mails":
                try:
                    start = max(1, int(data.get("start", 1) or 1)) if isinstance(data, dict) else 1
                    count = max(1, int(data.get("count", 5) or 5)) if isinstance(data, dict) else 5
                    mails = copy.deepcopy(get_mail_list(state))
                    await send_response(ws, session, {
                        "start": start,
                        "total": len(mails),
                        "mails": mails[start - 1:start - 1 + count],
                    })
                except Exception:
                    logging.exception("处理 mail.load_mails 时出错")
                    await send_response(ws, session, {"start": 1, "total": 0, "mails": []})

            elif cmd == "mail.read":
                try:
                    mail_id = int(request_argument(data, "id") or -1)
                    row = next((mail for mail in get_mail_list(state) if int(mail.get("id", -2)) == mail_id), None)
                    if row is not None and not bool(row.get("read", False)):
                        row["read"] = True
                        save_state(state, f"mail.read:{mail_id}")
                except Exception:
                    logging.exception("处理 mail.read 时出错")

            elif cmd == "mail.open":
                if isinstance(data, dict) and "id" in data:
                    try:
                        ok, updates = apply_mail_open(state, int(data["id"]))
                        if ok:
                            if "clover" in updates:
                                await send_json(ws, {"cmd": "clover.update", "data": {"clover": updates["clover"]}})
                            if "ticket" in updates:
                                await send_json(ws, {"cmd": "item.update_ticket", "data": {"ticket": updates["ticket"]}})
                            if "calendar_task" in updates:
                                await send_json(ws, calendar_task_push(updates["calendar_task"]))
                            for iid, count in updates.get("items", []):
                                await send_json(ws, {"cmd": "item.update", "data": {"item": {"item_id": iid, "count": count}}})
                    except Exception:
                        logging.exception("处理 mail.open 时出错")

            elif cmd == "client.confirm_event":
                if isinstance(data, dict) and "id" in data:
                    try:
                        event_id = int(data["id"])
                        matched = next((copy.deepcopy(e) for e in get_event_list(state) if isinstance(e, dict) and int(e.get("id", -1)) == event_id), None)
                        ok = apply_confirm_event(state, event_id)
                        if ok and isinstance(matched, dict) and int(matched.get("evt_type", -1)) == 16:
                            pics = [copy.deepcopy(x) for x in _to_list(matched.get("evt_pic")) if isinstance(x, dict)]
                            if pics:
                                for pic in pics:
                                    pic["visit"] = True
                                await send_json(ws, {"cmd": "album.load_new", "data": {"pictures": [], "visted_pic": pics, "has_ads": False, "is_share": False}})
                    except Exception:
                        logging.exception("处理 client.confirm_event 时出错")

            elif cmd in {"album.save_new", "album.delete_new"}:
                if isinstance(data, dict) and "id" in data:
                    try:
                        ok = apply_new_album_action(state, int(data["id"]), cmd == "album.save_new")
                        await send_response(ws, session, {"code": 0 if ok else 1})
                    except Exception:
                        logging.exception("处理 %s 时出错", cmd)
                        await send_response(ws, session, {"code": 1})

            elif cmd == "album.load_all":
                await send_response(ws, session, album_full_index_payload(state))

            elif cmd == "album.load":
                start_v = int(data.get("start", 1) or 1) if isinstance(data, dict) else 1
                count_v = int(data.get("count", 20) or 20) if isinstance(data, dict) else 20
                await send_response(ws, session, album_page_payload(state, start_v, count_v))

            elif cmd == "album.load_by_id_list":
                ids = data.get("id_list", []) if isinstance(data, dict) else []
                out = []
                if isinstance(ids, list):
                    for iid in ids:
                        pic = album_picture_by_instance_id(state, int(iid))
                        if pic is not None:
                            out.append(pic)
                await send_response(ws, session, {"pic_list": out})
                logging.info("ALBUM FULL-LAYER LOAD requested=%d returned=%d", len(ids) if isinstance(ids,list) else 0, len(out))

            elif cmd == "animpicture.load":
                await send_response(ws, session, copy.deepcopy(get_animpicture_state(state)))

            elif cmd == "animpicture.guide":
                try:
                    guide = animpicture_guide(state)
                    await send_response(ws, session, {"code": 0})
                    logging.info("ANIMPICTURE GUIDE -> %d", guide)
                except Exception:
                    logging.exception("处理 animpicture.guide 时出错")
                    await send_response(ws, session, {"code": 1})

            elif cmd == "animpicture.get_item":
                try:
                    ok, gained, total = animpicture_get_item(state)
                    if gained > 0:
                        await send_json(
                            ws,
                            {
                                "cmd": "item.update",
                                "data": {
                                    "item": {
                                        "item_id": ANIM_PICTURE_ALBUM_ITEM_ID,
                                        "count": total,
                                    }
                                },
                            },
                        )
                    await send_response(ws, session, {"code": 0 if ok else 1})
                    logging.info("ANIMPICTURE GET ITEM gained=%d total=%d", gained, total)
                except Exception:
                    logging.exception("处理 animpicture.get_item 时出错")
                    await send_response(ws, session, {"code": 1})

            elif cmd == "animpicture.select_pic":
                try:
                    iid = int(data.get("id", -1)) if isinstance(data, dict) else -1
                    ok, reason = animpicture_select_pic(state, iid)
                    await send_response(ws, session, {"code": 0 if ok else 1})
                    if ok:
                        await send_json(
                            ws,
                            {
                                "cmd": "animpicture.load",
                                "data": copy.deepcopy(get_animpicture_state(state)),
                            },
                        )
                    logging.info("ANIMPICTURE SELECT id=%d ok=%s reason=%s", iid, ok, reason)
                except Exception:
                    logging.exception("处理 animpicture.select_pic 时出错")
                    await send_response(ws, session, {"code": 1})

            elif cmd == "animpicture.add_pic":
                try:
                    ai = int(data.get("anim_index", 0)) if isinstance(data, dict) else 0
                    ids = data.get("ids", []) if isinstance(data, dict) else []
                    ok, reason = animpicture_add_pics(
                        state,
                        ai,
                        [int(x) for x in ids] if isinstance(ids, list) else [],
                    )
                    await send_response(ws, session, {"code": 0 if ok else 1})
                    logging.info("ANIMPICTURE ADD anim=%d ids=%r ok=%s reason=%s", ai, ids, ok, reason)
                except Exception:
                    logging.exception("处理 animpicture.add_pic 时出错")
                    await send_response(ws, session, {"code": 1})

            elif cmd == "animpicture.remove_pic":
                try:
                    ai = int(data.get("anim_index", 0)) if isinstance(data, dict) else 0
                    pi = int(data.get("pic_index", 0)) if isinstance(data, dict) else 0
                    delete = bool(data.get("is_delete", False)) if isinstance(data, dict) else False
                    ok, reason = animpicture_remove_exp_pic(state, ai, pi, delete)
                    await send_response(ws, session, {"code": 0 if ok else 1})
                    logging.info("ANIMPICTURE REMOVE anim=%d pic=%d delete=%s ok=%s reason=%s", ai, pi, delete, ok, reason)
                except Exception:
                    logging.exception("处理 animpicture.remove_pic 时出错")
                    await send_response(ws, session, {"code": 1})

            elif cmd == "animpicture.use_item":
                try:
                    ai = int(data.get("anim_index", 0)) if isinstance(data, dict) else 0
                    iid = int(data.get("item_id", 0)) if isinstance(data, dict) else 0
                    ok, reason, out = animpicture_use_item(
                        state, ai, iid, policy=load_offline_policy()
                    )
                    if ok:
                        await send_json(
                            ws,
                            {
                                "cmd": "item.update",
                                "data": {
                                    "item": {
                                        "item_id": iid,
                                        "count": int(out.get("item_count", 0)),
                                    }
                                },
                            },
                        )
                        if bool(out.get("completed", False)):
                            await complete_capsule_task_and_push(8, "dynamic_picture_complete")
                    await send_response(
                        ws,
                        session,
                        {
                            "code": 0 if ok else 1,
                            "phase": int(out.get("phase", -1)),
                            "exp": int(out.get("exp", 0)),
                        },
                    )
                    logging.info(
                        "ANIMPICTURE USE anim=%d item=%d ok=%s reason=%s phase=%s exp=%s complete=%s",
                        ai, iid, ok, reason, out.get("phase"), out.get("exp"), out.get("completed"),
                    )
                except Exception:
                    logging.exception("处理 animpicture.use_item 时出错")
                    await send_response(ws, session, {"code": 1, "phase": -1, "exp": 0})

            elif cmd == "animpicture.open_album":
                try:
                    ai = int(data.get("index", 0)) if isinstance(data, dict) else 0
                    ok, reason, count = animpicture_open_album(state, ai)
                    if ok:
                        await send_json(
                            ws,
                            {
                                "cmd": "item.update",
                                "data": {
                                    "item": {
                                        "item_id": ANIM_PICTURE_ALBUM_ITEM_ID,
                                        "count": count,
                                    }
                                },
                            },
                        )
                    await send_response(ws, session, {"code": 0 if ok else 1})
                    logging.info("ANIMPICTURE OPEN ALBUM anim=%d ok=%s reason=%s", ai, ok, reason)
                except Exception:
                    logging.exception("处理 animpicture.open_album 时出错")
                    await send_response(ws, session, {"code": 1})

            elif cmd == "animpicture.album_add_pic":
                try:
                    ai = int(data.get("anim_index", 0)) if isinstance(data, dict) else 0
                    pi = int(data.get("pic_index", 0)) if isinstance(data, dict) else 0
                    uid = int(data.get("pic_uid", -1)) if isinstance(data, dict) else -1
                    ok, reason = animpicture_album_add_pic(state, ai, pi, uid)
                    await send_response(ws, session, {"code": 0 if ok else 1})
                    logging.info("ANIMPICTURE ALBUM ADD anim=%d pic=%d uid=%d ok=%s reason=%s", ai, pi, uid, ok, reason)
                except Exception:
                    logging.exception("处理 animpicture.album_add_pic 时出错")
                    await send_response(ws, session, {"code": 1})

            elif cmd == "animpicture.album_remove_pic":
                try:
                    ai = int(data.get("anim_index", 0)) if isinstance(data, dict) else 0
                    pi = int(data.get("pic_index", 0)) if isinstance(data, dict) else 0
                    delete = bool(data.get("is_delete", False)) if isinstance(data, dict) else False
                    ok, reason = animpicture_album_remove_pic(state, ai, pi, delete)
                    await send_response(ws, session, {"code": 0 if ok else 1})
                    logging.info("ANIMPICTURE ALBUM REMOVE anim=%d pic=%d delete=%s ok=%s reason=%s", ai, pi, delete, ok, reason)
                except Exception:
                    logging.exception("处理 animpicture.album_remove_pic 时出错")
                    await send_response(ws, session, {"code": 1})

            elif cmd == "misc.moment_unlock":
                if isinstance(data, dict) and "id" in data:
                    try:
                        persist_moment_unlock(state, int(data["id"]))
                        await send_response(ws, session, {"code": 0})
                    except Exception:
                        logging.exception("处理 misc.moment_unlock 时出错")
                        await send_response(ws, session, {"code": 1})
                else:
                    await send_response(ws, session, {"code": 1})

            elif cmd == "task.get_reward":
                try:
                    task_id = int(data.get("id", 0) or 0) if isinstance(data, dict) else 0
                    ok, reason, reward = apply_guide_task_reward(state, task_id)
                    if ok and reward:
                        if reward["kind"] == "clover":
                            await send_json(ws, {"cmd": "clover.update", "data": {"clover": reward["total"]}})
                        elif reward["kind"] == "ticket":
                            await send_json(ws, {"cmd": "item.update_ticket", "data": {"ticket": reward["total"]}})
                        else:
                            await send_json(ws, {"cmd": "item.update", "data": {"item": {"item_id": reward["item_id"], "count": reward["total"]}}})
                    await send_response(ws, session, {"code": 0 if ok else 1, "offline_reason": reason})
                except Exception:
                    logging.exception("处理 task.get_reward 时出错")
                    await send_response(ws, session, {"code": 1})

            elif cmd == "task.get_list_reward":
                try:
                    reward_id = int(data.get("id", 0) or 0) if isinstance(data, dict) else 0
                    ok, reason, reward = apply_guide_task_list_reward(state, reward_id)
                    if ok and reward:
                        if reward["kind"] == "clover":
                            await send_json(ws, {"cmd": "clover.update", "data": {"clover": reward["total"]}})
                        elif reward["kind"] == "ticket":
                            await send_json(ws, {"cmd": "item.update_ticket", "data": {"ticket": reward["total"]}})
                        else:
                            await send_json(ws, {"cmd": "item.update", "data": {"item": {"item_id": reward["item_id"], "count": reward["total"]}}})
                    await send_response(ws, session, {"code": 0 if ok else 1, "offline_reason": reason})
                except Exception:
                    logging.exception("处理 task.get_list_reward 时出错")
                    await send_response(ws, session, {"code": 1})

            elif cmd == "task.client_pro":
                try:
                    value = data.get("param") if isinstance(data, dict) else data
                    if record_guide_task_client_progress(state, value):
                        await send_json(ws, copy.deepcopy(find_push(state, "task.load")))
                except Exception:
                    logging.exception("处理 task.client_pro 时出错")

            elif cmd == "adsmgr.refuse":
                await send_response(ws, session, {"code": 0})

            elif cmd == "client.rename_cost":
                await send_response(ws, session, {"code": 0, "clover": 0})

            elif cmd == "client.set_name":
                if persist_client_setting(state, cmd, data):
                    await send_response(ws, session, {"code": 0})
                else:
                    logging.warning("未能持久化 %s", cmd)
                    await send_response(ws, session, {"code": 1})

            elif cmd in NO_RESPONSE_CMDS:
                continue

            elif cmd in PERSISTENT_NO_RESPONSE_CMDS:
                if not persist_client_setting(state, cmd, data):
                    logging.warning("未能持久化 %s", cmd)

            else:
                # Stage 5 继续沿用安全策略：
                # 未实现的操作绝不再“假装成功”，否则 UI 会变化而 state.json 不变。
                append_unknown(req)
                if session is not None:
                    logging.warning("BLOCK 未实现写操作: %s session=%s", cmd, session)
                    await send_response(
                        ws,
                        session,
                        {
                            "code": 1,
                            "offline_blocked": True,
                            "cmd": cmd,
                        },
                    )
                else:
                    logging.warning("记录但忽略未实现无响应命令: %s", cmd)

            if refresh_guide_task_progress(state):
                save_state(state, "task.gameplay_progress")
                await send_json(ws, copy.deepcopy(find_push(state, "task.load")))

    except websockets.ConnectionClosed:
        logging.info("客户端断开: %s", peer)


async def main_async(
    snapshot: Path,
    host: str,
    port: int,
    reset_state: bool,
) -> None:
    state = load_or_create_state(snapshot, reset=reset_state)
    migrate_stage6c1_state(state)
    migrate_stage6d1_orphaned_remote_activity(state)

    policy = load_offline_policy()
    migrate_stage6e2_social_split(state, policy=policy)
    migrate_stage6f1_lottery_phase171(state, policy=policy)
    migrate_stage6f2_lottery_extra_mail(state)
    migrate_stage6f3_clover_net_cursor(state)
    migrate_stage6g_calendar_sep2026(state)
    migrate_stage6h_album_official_layers(state)
    get_album_recover_list(state)
    get_animpicture_state(state)
    get_wishing_pool_state(state, policy)
    logging.info("Stage 6I.2 offline policy: %s", json.dumps(policy, ensure_ascii=False))
    logging.info("原始快照: %s", snapshot)
    logging.info("可写存档: %s", STATE_FILE)
    logging.info("early pushes: %d", len(state.get("early_pushes", [])))
    logging.info("sync pushes: %d", len(state.get("sync_pushes", [])))
    logging.info("本地 WebSocket: ws://%s:%d", host, port)

    async def handler(ws, path=None):
        await serve_client(ws, state)

    async with websockets.serve(
        handler,
        host,
        port,
        max_size=None,
        ping_interval=None,
    ):
        await asyncio.Future()


def main() -> None:
    global GALLERY_MODE, DATA_DIR, STATE_FILE, BACKUP_DIR, UNKNOWN_LOG, LOG_FILE
    parser = argparse.ArgumentParser(
        description="旅行青蛙个人存档本地服务器 Stage 6I.2（旅行提示/家具替换/年终总结修复 + Stage 6I.1）"
    )
    parser.add_argument(
        "--snapshot",
        type=Path,
        default=DEFAULT_SNAPSHOT,
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int)
    parser.add_argument("--gallery", action="store_true", help="使用独立图鉴版存档与日志")
    parser.add_argument("--data-dir", type=Path, help="存档、备份与日志目录")
    parser.add_argument(
        "--reset-state",
        action="store_true",
        help="备份现有 state.json 后，从原始抓取重新创建。",
    )
    args = parser.parse_args()

    GALLERY_MODE = bool(args.gallery)
    if args.data_dir is not None:
        DATA_DIR = args.data_dir.resolve()
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        STATE_FILE = DATA_DIR / "state.json"
        BACKUP_DIR = DATA_DIR / "backups"
        UNKNOWN_LOG = DATA_DIR / "unknown_commands.jsonl"
        LOG_FILE = DATA_DIR / "server.log"
    if GALLERY_MODE:
        STATE_FILE = DATA_DIR / "gallery_state.json"
        BACKUP_DIR = DATA_DIR / "gallery_backups"
        UNKNOWN_LOG = DATA_DIR / "gallery_unknown_commands.jsonl"
        LOG_FILE = DATA_DIR / "gallery_server.log" if args.data_dir is not None else Path(__file__).resolve().parent / "gallery_server.log"
    port = args.port if args.port is not None else (52353 if GALLERY_MODE else 8080)

    configure_logging()

    try:
        with state_process_lock():
            asyncio.run(
                main_async(args.snapshot, args.host, port, args.reset_state)
            )
    except KeyboardInterrupt:
        logging.info("服务器已停止")


@contextmanager
def state_process_lock(state_file: Path | None = None):
    """Keep a save owned by exactly one running server process."""
    state_file = STATE_FILE if state_file is None else state_file
    path = state_file.with_suffix(".lock")
    with path.open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise RuntimeError(f"已有服务器正在使用这个存档：{state_file}") from exc
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield


if __name__ == "__main__":
    main()
