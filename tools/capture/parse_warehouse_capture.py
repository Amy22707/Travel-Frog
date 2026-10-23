#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Travel Frog China - warehouse + travel-note + seasonal-event capture parser v3.

Purpose:
  Parse one official-server raw logcat made with main.capture.js and extract:
    - 图鉴 / inventory handbook
    - 相册
    - 故事
    - 旅行笔记（encytravel）
    - 礼品盒
    - 手工品（pray / handcraft）
    - 博物馆日 / compass state
    - 家具（含嘟嘟商店、工具台、网兜、堆肥、花盆、不倒翁）
    - 友情绘本（guest.load_drawing and related ops）
    - 百科（encyclopedia）
    - 日历
    - 动态照片（animpicture）
  It also preserves a narrow action timeline, including calendar claims and
  furniture_buy_shop / furniture_putin_bench operations.

Outputs:
  protocol_full_LOCAL_ONLY.jsonl        KEEP LOCAL, DO NOT UPLOAD
  warehouse_timeline_SHARE_SAFE.jsonl  all relevant records, sanitized
  warehouse_latest_state_SHARE_SAFE.json
  warehouse_actions_SHARE_SAFE.jsonl
  warehouse_summary_SHARE_SAFE.txt
  album_full_layers_SHARE_SAFE.json
  album_coverage_SHARE_SAFE.txt
  native_album_trace_SHARE_SAFE.txt
  modules/<module>_SHARE_SAFE.jsonl
  warehouse_SHARE_SAFE_bundle.zip

Security:
  - Login/account/device/server-url records are excluded from SHARE_SAFE outputs.
  - Story partner names/IDs are redacted in SHARE_SAFE outputs.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SRC = ROOT / "capture" / "warehouse_20260912" / "raw_logcat.txt"
# The v3 parser's canonical result is the already-used parsed_v3 directory.
# Keeping this default explicit prevents a future run from silently creating a
# second ambiguous "parsed" result tree beside the canonical output.
DEFAULT_OUT = ROOT / "capture" / "warehouse_20260912" / "parsed_v3"

CHUNK_RE = re.compile(r"FROG\|(\d+)\|(\d+)\|(\d+)\|(.*)$")

# Canonicalize wire dot-form and callback underscore-form into underscore-form.
def canon_cmd(cmd: Any) -> str:
    if not isinstance(cmd, str):
        return ""
    return cmd.replace(".", "_")


GENERIC_SENSITIVE_KEYS = {
    "token", "ptoken", "game_token", "access_token", "refresh_token",
    "account", "account_id", "device_id", "deviceid", "imei", "oaid",
    "mac", "ip", "openid", "open_id", "unionid", "union_id",
    "server_secret", "session_key",
}

STORY_PRIVATE_KEYS = {
    "name", "nickname", "nick_name", "user_name", "username",
    "uid", "user_id", "player_id", "pid", "role_id",
    "partner", "partner_id", "from_uid", "to_uid",
    "openid", "open_id", "unionid", "union_id",
}

DRAWING_CMDS = {
    "guest_load_drawing",
    "guest_accept_invit",
    "guest_putin_bag",
    "guest_takeout_bag",
    "guest_lock_bag",
}

# The module classification is intentionally protocol-based rather than UI-name-based.
def module_for_cmd(cmd: str) -> str | None:
    c = canon_cmd(cmd)
    if c in {
        "item_load_handbook", "item_load_items", "item_update",
        "item_update_ticket", "item_gift_open",
        "clover_update", "clover_notice_get",
    }:
        return "handbook_inventory"
    if c.startswith("album_"):
        return "album"
    if c.startswith("story_"):
        return "story"

    # True travel notebook: 137 "见闻" + 27 "旅友" entries are driven by
    # Note.json and travel_load_note / travel_read_note.
    if c in {"travel_load_note", "travel_read_note"}:
        return "travel_note"

    # EncyTravel is a separate travel-item encyclopedia shown under the
    # encyclopedia menu, not the travel notebook.
    if c.startswith("encytravel_"):
        return "travel_encyclopedia"

    if (
        c == "travel_load_gift"
        or c.startswith("travel_gift_")
        or c == "travel_bag_to_gift"
        or c == "travel_album_to_gift"
    ):
        return "giftbox"
    if c.startswith("pray_"):
        return "handcraft"
    if c.startswith("museumday_"):
        return "museum"
    if c.startswith("furniture_"):
        return "furniture"
    if c in DRAWING_CMDS:
        return "friendship_drawing"
    if c.startswith("encyclopedia_"):
        return "encyclopedia"
    if c.startswith("calendar_"):
        return "calendar"
    if c.startswith("animpicture_"):
        return "animpicture"

    # Dormant / seasonal systems. These are useful even when end_time == 0,
    # because login pushes often preserve the last event state.
    if c.startswith("greetcard_"):
        return "event_midautumn_card"
    if c.startswith("springcard_"):
        return "event_spring_card"
    if c.startswith("partycake_"):
        return "event_partycake"
    if c.startswith("capsule_"):
        return "event_capsule"
    if c.startswith("wishingpool_"):
        return "event_wishingpool"
    if c.startswith("cooking_"):
        return "event_monthly_cooking"
    if c.startswith("annual_"):
        return "event_annual_review"
    if c.startswith("misc_moment_"):
        return "moment"

    return None


# Load/push commands whose latest values are especially useful as snapshots.
STATE_LOAD_COMMANDS = {
    "item_load_items",
    "item_load_handbook",
    "album_load_all",
    "album_load_new",
    "story_load",
    "travel_load_note",
    "encytravel_load",
    "travel_load_gift",
    "pray_load_grays",
    "museumday_load",
    "furniture_load_furniture",
    "furniture_load_tumbler",
    "furniture_load_pocket",
    "furniture_load_compost",
    "furniture_load_flowerpot",
    "guest_load_drawing",
    "encyclopedia_load",
    "calendar_load",
    "animpicture_load",
    "greetcard_load",
    "springcard_load",
    "partycake_load",
    "capsule_load",
    "wishingpool_load",
    "cooking_load_cooking",
    "annual_load",
    "misc_moment_load",
}

# "Actions" = state-changing or explicit user requests we want to highlight.
READ_ONLY_REQUESTS = {
    "album_load_by_id_list",
    "calendar_load",
    "calendar_load_note",
    "travel_load_note",
    "travel_load_gift",
    "museumday_load",
    "animpicture_load",
    "greetcard_load",
    "greetcard_load_count",
    "springcard_load",
    "springcard_load_count",
    "partycake_load",
    "partycake_load_qa",
    "capsule_load",
    "wishingpool_load",
    "cooking_load_cooking",
    "annual_load",
}

NATIVE_ALBUM_KEYWORDS = (
    "save_to_album",
    "saveImageToPhotosAlbum",
    "save_texture_to_album",
    "support_save_to_album",
    "temp_picture",
    "保存成功",
    "保存失败",
    "相册权限",
    "WRITE_EXTERNAL_STORAGE",
    "Permission.check_permission",
    "permission",
)


def dump_json(path: Path, obj: Any) -> None:
    path.write_text(
        json.dumps(obj, ensure_ascii=False, indent=2),
        encoding="utf-8",
        newline="\n",
    )


def dump_jsonl(path: Path, rows: Iterable[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def sanitize_obj(obj: Any, *, story: bool = False) -> Any:
    if isinstance(obj, list):
        return [sanitize_obj(x, story=story) for x in obj]
    if not isinstance(obj, dict):
        return obj

    out = {}
    for k, v in obj.items():
        kl = str(k).lower()
        if kl in GENERIC_SENSITIVE_KEYS:
            out[k] = "<REDACTED>"
            continue
        if story and kl in STORY_PRIVATE_KEYS:
            out[k] = "<REDACTED_STORY_PARTNER>"
            continue
        out[k] = sanitize_obj(v, story=story)
    return out


def parse_chunks(src: Path):
    groups: dict[tuple[int, int], dict[int, str]] = defaultdict(dict)
    totals: dict[tuple[int, int], int] = {}
    order: list[tuple[int, int]] = []

    epoch = 1
    prev_msg_id: int | None = None
    reset_points: list[tuple[int, int, int]] = []
    frog_lines = 0

    text = src.read_text(encoding="utf-8", errors="replace")
    raw_lines = text.splitlines()

    for line in raw_lines:
        m = CHUNK_RE.search(line)
        if not m:
            continue

        frog_lines += 1
        msg_id = int(m.group(1))
        idx = int(m.group(2))
        total = int(m.group(3))
        payload = m.group(4)

        if (
            prev_msg_id is not None
            and msg_id < prev_msg_id
            and msg_id <= 20
            and prev_msg_id - msg_id >= 20
        ):
            epoch += 1
            reset_points.append((epoch, prev_msg_id, msg_id))
        prev_msg_id = msg_id

        key = (epoch, msg_id)
        if key not in totals:
            order.append(key)
        totals[key] = total
        groups[key][idx] = payload

    if not groups:
        raise RuntimeError(
            "没有找到 FROG 分块日志。请确认正式服抓包使用 main.capture.js。"
        )

    assembled: list[tuple[int, int, str]] = []
    missing: list[tuple[int, int, list[int]]] = []
    for ep, mid in order:
        key = (ep, mid)
        total = totals[key]
        parts = groups[key]
        absent = [i for i in range(total) if i not in parts]
        if absent:
            missing.append((ep, mid, absent))
            continue
        assembled.append((ep, mid, "".join(parts[i] for i in range(total))))

    records: list[dict] = []
    parse_fail: list[tuple[int, int, str, str]] = []

    for ep, mid, msg in assembled:
        if "发送协议：" in msg:
            marker, kind = "发送协议：", "send"
        elif "接收协议：" in msg:
            marker, kind = "接收协议：", "recv"
        elif "创建连接 url:" in msg:
            records.append({
                "epoch": ep,
                "log_id": mid,
                "kind": "connect",
                "url": msg.split("创建连接 url:", 1)[1].strip(),
            })
            continue
        else:
            continue

        payload = msg.split(marker, 1)[1]
        try:
            obj = json.loads(payload)
        except Exception as exc:
            parse_fail.append((ep, mid, str(exc), payload[:240]))
            continue

        records.append({
            "epoch": ep,
            "log_id": mid,
            "kind": kind,
            "message": obj,
        })

    health = {
        "frog_lines": frog_lines,
        "epochs": epoch,
        "restart_resets": reset_points,
        "assembled_messages": len(assembled),
        "protocol_records": len(records),
        "missing_chunk_groups": missing,
        "json_parse_failures": parse_fail,
    }
    return records, health, raw_lines


def build_request_map(records: list[dict]):
    reqs: dict[tuple[int, Any], dict] = {}
    for r in records:
        if r.get("kind") != "send":
            continue
        m = r.get("message")
        if not isinstance(m, dict):
            continue
        session = m.get("session")
        if session is not None:
            reqs[(r["epoch"], session)] = r
    return reqs


def relevant_safe_records(records: list[dict]):
    reqs = build_request_map(records)
    out = []
    for r in records:
        if r.get("kind") not in {"send", "recv"}:
            continue
        m = r.get("message")
        if not isinstance(m, dict):
            continue

        direct_cmd = canon_cmd(m.get("cmd"))
        direct_module = module_for_cmd(direct_cmd)

        for_cmd = ""
        for_module = None
        if r["kind"] == "recv" and m.get("session") is not None:
            req = reqs.get((r["epoch"], m.get("session")))
            if req:
                rm = req.get("message") or {}
                for_cmd = canon_cmd(rm.get("cmd"))
                for_module = module_for_cmd(for_cmd)

        module = direct_module or for_module
        if module is None:
            continue

        story = module in {"story", "event_midautumn_card", "event_spring_card"}
        safe_msg = sanitize_obj(copy.deepcopy(m), story=story)

        rr = {
            "epoch": r["epoch"],
            "log_id": r["log_id"],
            "kind": r["kind"],
            "module": module,
            "message": safe_msg,
        }
        if for_cmd:
            rr["for_cmd"] = for_cmd
        out.append(rr)

    return out


def latest_states(rows: list[dict]):
    latest = {}
    for r in rows:
        if r.get("kind") != "recv":
            continue
        m = r.get("message") or {}
        c = canon_cmd(m.get("cmd"))
        if c in STATE_LOAD_COMMANDS and "data" in m:
            latest[c] = {
                "epoch": r["epoch"],
                "log_id": r["log_id"],
                "module": r["module"],
                "data": m.get("data"),
            }
    return latest


def action_rows(rows: list[dict]):
    # Keep user/server mutation requests, plus their paired responses.
    action_sessions: set[tuple[int, Any]] = set()
    out = []

    for r in rows:
        if r.get("kind") != "send":
            continue
        m = r.get("message") or {}
        c = canon_cmd(m.get("cmd"))
        if not c:
            continue
        if c in READ_ONLY_REQUESTS:
            continue
        # Opening set-show-sub changes persisted UI state, so keep it.
        if r.get("module") is not None:
            out.append(r)
            session = m.get("session")
            if session is not None:
                action_sessions.add((r["epoch"], session))

    for r in rows:
        if r.get("kind") != "recv":
            continue
        m = r.get("message") or {}
        session = m.get("session")
        if session is not None and (r["epoch"], session) in action_sessions:
            out.append(r)

    out.sort(key=lambda r: (r["epoch"], r["log_id"], 0 if r["kind"] == "send" else 1))
    return out


def iter_picture_dicts(obj: Any):
    if isinstance(obj, dict):
        if "id" in obj and "pic_id" in obj and isinstance(obj.get("layers"), list):
            yield obj
        for v in obj.values():
            yield from iter_picture_dicts(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from iter_picture_dicts(v)


def album_extract(rows: list[dict]):
    compact = []
    compact_source = None
    full_by_id = {}

    for r in rows:
        if r.get("module") != "album" or r.get("kind") != "recv":
            continue
        m = r.get("message") or {}
        c = canon_cmd(m.get("cmd"))
        data = m.get("data")

        if c == "album_load_all" and isinstance(data, dict):
            id_list = data.get("id_list")
            if isinstance(id_list, list):
                tmp = []
                for x in id_list:
                    if not isinstance(x, dict):
                        continue
                    try:
                        tmp.append({"id": int(x["id"]), "pic_id": int(x["pic_id"])})
                    except Exception:
                        pass
                compact = tmp
                compact_source = {"epoch": r["epoch"], "log_id": r["log_id"]}

        for p in iter_picture_dicts(data):
            try:
                pid = int(p["id"])
                pic_id = int(p["pic_id"])
            except Exception:
                continue
            q = {"id": pid, "pic_id": pic_id, "layers": p["layers"]}
            for k in ("for_ads", "visit", "visted"):
                if k in p:
                    q[k] = p[k]
            q["source"] = c or r.get("for_cmd", "unknown")
            full_by_id[pid] = q

        # Responses to album_load_by_id_list often carry no cmd.
        if not c and r.get("for_cmd") == "album_load_by_id_list":
            for p in iter_picture_dicts(data):
                try:
                    pid = int(p["id"])
                    pic_id = int(p["pic_id"])
                except Exception:
                    continue
                full_by_id[pid] = {
                    "id": pid,
                    "pic_id": pic_id,
                    "layers": p["layers"],
                    "source": "album_load_by_id_list",
                }

    full = sorted(full_by_id.values(), key=lambda x: x["id"])
    full_ids = set(full_by_id)
    missing = [p for p in compact if p["id"] not in full_ids]
    return compact, compact_source, full, missing


def native_album_trace(raw_lines: list[str]):
    out = []
    lowered_keywords = tuple(x.lower() for x in NATIVE_ALBUM_KEYWORDS)
    for line in raw_lines:
        low = line.lower()
        if any(k in low for k in lowered_keywords):
            # Do not include explicit URLs/tokens if a noisy SDK line happens to match.
            if "token" in low or "http://" in low or "https://" in low:
                continue
            out.append(line)
    return out



def transaction_rows(records: list[dict]):
    """
    Build exact request transactions for relevant state-changing requests.
    Unlike warehouse_actions_SHARE_SAFE.jsonl, this also retains unsolicited
    side pushes between SEND and the matching session response, e.g.
    furniture.buy_shop -> clover.update -> item.update -> response.
    """
    out = []
    n = len(records)

    for i, r in enumerate(records):
        if r.get("kind") != "send":
            continue
        m = r.get("message")
        if not isinstance(m, dict):
            continue

        cmd = canon_cmd(m.get("cmd"))
        module = module_for_cmd(cmd)
        session = m.get("session")

        if module is None or cmd in READ_ONLY_REQUESTS or session is None:
            continue

        story = module in {"story", "event_midautumn_card", "event_spring_card"}
        txn = {
            "epoch": r["epoch"],
            "request_log_id": r["log_id"],
            "module": module,
            "cmd": cmd,
            "request": sanitize_obj(copy.deepcopy(m), story=story),
            "events": [],
        }

        for j in range(i + 1, n):
            rr = records[j]

            # Never cross an app/capture epoch.
            if rr.get("epoch") != r["epoch"]:
                break

            if rr.get("kind") != "recv":
                continue

            mm = rr.get("message")
            if not isinstance(mm, dict):
                continue

            direct_cmd = canon_cmd(mm.get("cmd"))
            direct_module = module_for_cmd(direct_cmd)
            event_story = story or direct_module in {"story", "event_midautumn_card", "event_spring_card"}

            # Keep state pushes that occur during this request even when they
            # belong to another module (item.update, clover.update, etc.).
            txn["events"].append({
                "log_id": rr["log_id"],
                "cmd": direct_cmd or None,
                "module": direct_module,
                "message": sanitize_obj(copy.deepcopy(mm), story=event_story),
            })

            if mm.get("session") == session:
                txn["response_log_id"] = rr["log_id"]
                txn["response_found"] = True
                break
        else:
            txn["response_found"] = False

        out.append(txn)

    return out


def write_transaction_readable(path: Path, txns: list[dict]) -> None:
    lines = []
    for t in txns:
        lines.append(
            f"EPOCH {t['epoch']} REQUEST log_id={t['request_log_id']} "
            f"module={t['module']} cmd={t['cmd']} "
            f"data={json.dumps(t['request'].get('data'), ensure_ascii=False, separators=(',', ':'))}"
        )
        for ev in t["events"]:
            msg = ev["message"]
            lines.append(
                f"  -> RECV log_id={ev['log_id']} "
                f"module={ev.get('module')} cmd={ev.get('cmd') or '<session-response>'} "
                f"data={json.dumps(msg.get('data'), ensure_ascii=False, separators=(',', ':'))}"
            )
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8", newline="\n")


def main() -> None:
    ap = argparse.ArgumentParser(
        description="解析旅行青蛙小仓库全模块 + 相册 + 动态照片正式服抓包。"
    )
    ap.add_argument("--src", type=Path, default=DEFAULT_SRC)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()

    if not args.src.exists():
        raise FileNotFoundError(f"找不到抓包文件: {args.src}")

    args.out.mkdir(parents=True, exist_ok=True)
    modules_dir = args.out / "modules"
    modules_dir.mkdir(parents=True, exist_ok=True)

    records, health, raw_lines = parse_chunks(args.src)

    # Exact raw protocol - never upload.
    dump_jsonl(args.out / "protocol_full_LOCAL_ONLY.jsonl", records)

    safe = relevant_safe_records(records)
    dump_jsonl(args.out / "warehouse_timeline_SHARE_SAFE.jsonl", safe)

    # Per-module files.
    module_counts = Counter()
    module_rows: dict[str, list[dict]] = defaultdict(list)
    for r in safe:
        module_rows[r["module"]].append(r)
        module_counts[r["module"]] += 1

    for module, rows in sorted(module_rows.items()):
        dump_jsonl(modules_dir / f"{module}_SHARE_SAFE.jsonl", rows)

    latest = latest_states(safe)
    dump_json(args.out / "warehouse_latest_state_SHARE_SAFE.json", latest)

    actions = action_rows(safe)
    dump_jsonl(args.out / "warehouse_actions_SHARE_SAFE.jsonl", actions)

    transactions = transaction_rows(records)
    dump_jsonl(args.out / "warehouse_transactions_SHARE_SAFE.jsonl", transactions)
    write_transaction_readable(
        args.out / "warehouse_transactions_READABLE_SHARE_SAFE.txt",
        transactions,
    )

    compact, compact_source, full, missing = album_extract(safe)
    dump_json(
        args.out / "album_full_layers_SHARE_SAFE.json",
        {"count": len(full), "pictures": full},
    )
    coverage = [
        f"compact_album_count={len(compact)}",
        f"full_layers_count={len(full)}",
        f"full_layers_for_compact={len([p for p in compact if p['id'] in {x['id'] for x in full}])}",
        f"missing_full_layers={len(missing)}",
        f"compact_source={compact_source}",
        "",
        "[missing id,pic_id]",
    ]
    coverage += [f"{p['id']},{p['pic_id']}" for p in missing]
    (args.out / "album_coverage_SHARE_SAFE.txt").write_text(
        "\n".join(coverage) + "\n", encoding="utf-8", newline="\n"
    )

    native = native_album_trace(raw_lines)
    (args.out / "native_album_trace_SHARE_SAFE.txt").write_text(
        "\n".join(native) + ("\n" if native else ""),
        encoding="utf-8",
        newline="\n",
    )

    # Human-readable action summary.
    action_cmds = Counter()
    action_lines = []
    for r in actions:
        m = r.get("message") or {}
        c = canon_cmd(m.get("cmd")) or r.get("for_cmd", "")
        if r["kind"] == "send":
            action_cmds[c] += 1
        action_lines.append(
            f"EPOCH {r['epoch']} log_id={r['log_id']} {r['kind'].upper()} "
            f"module={r['module']} cmd={c or '<session-response>'} "
            f"data={json.dumps(m.get('data'), ensure_ascii=False, separators=(',', ':'))}"
        )
    (args.out / "warehouse_actions_READABLE_SHARE_SAFE.txt").write_text(
        "\n".join(action_lines) + ("\n" if action_lines else ""),
        encoding="utf-8",
        newline="\n",
    )

    summary = [
        f"source={args.src}",
        f"frog_lines={health['frog_lines']}",
        f"epochs={health['epochs']}",
        f"restart_resets={health['restart_resets']}",
        f"assembled_messages={health['assembled_messages']}",
        f"protocol_records={health['protocol_records']}",
        f"missing_chunk_groups={len(health['missing_chunk_groups'])}",
        f"json_parse_failures={len(health['json_parse_failures'])}",
        f"share_safe_records={len(safe)}",
        f"action_records={len(actions)}",
        f"transactions={len(transactions)}",
        f"native_album_trace_lines={len(native)}",
        "",
        "[module record counts]",
    ]
    for module in (
        "handbook_inventory", "album", "story", "travel_note",
        "travel_encyclopedia", "giftbox", "handcraft", "museum",
        "furniture", "friendship_drawing", "encyclopedia", "calendar",
        "animpicture", "event_midautumn_card", "event_spring_card",
        "event_partycake", "event_capsule", "event_wishingpool",
        "event_monthly_cooking", "event_annual_review", "moment",
    ):
        summary.append(f"{module}={module_counts.get(module, 0)}")

    summary += ["", "[latest state commands seen]"]
    for c in sorted(latest):
        summary.append(c)

    summary += ["", "[state-changing request counts]"]
    for c, n in sorted(action_cmds.items()):
        summary.append(f"{c}={n}")

    summary += [
        "",
        f"compact_album_count={len(compact)}",
        f"full_layers_count={len(full)}",
        f"missing_full_layers={len(missing)}",
        "",
        "IMPORTANT:",
        "- protocol_full_LOCAL_ONLY.jsonl 只留本机，不要上传。",
        "- 故事/贺卡中的其他玩家姓名与ID在 SHARE_SAFE 中自动脱敏。",
        "- travel_note 才是 137见闻/27旅友 的旅行笔记；travel_encyclopedia 是另一套系统。",
        "- greetcard/springcard/partycake/capsule/wishingpool/cooking/annual 等季节活动也会抽取。",
        "- 相册“保存到手机相册”属于本机权限/Native路径，不是服务器存档；",
        "  native_album_trace_SHARE_SAFE.txt 只尝试从普通 logcat 中提取相关迹象。",
        "- 可以直接上传 warehouse_SHARE_SAFE_bundle.zip。",
    ]

    if health["missing_chunk_groups"]:
        summary += ["", "[missing chunk groups]"]
        for ep, mid, absent in health["missing_chunk_groups"][:100]:
            summary.append(f"epoch={ep} msg_id={mid} missing={absent}")

    if health["json_parse_failures"]:
        summary += ["", "[json parse failures]"]
        for ep, mid, err, prefix in health["json_parse_failures"][:50]:
            summary.append(
                f"epoch={ep} msg_id={mid} error={err} prefix={prefix!r}"
            )

    (args.out / "warehouse_summary_SHARE_SAFE.txt").write_text(
        "\n".join(summary) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    # Bundle every SHARE_SAFE file, excluding LOCAL_ONLY.
    bundle = args.out / "warehouse_SHARE_SAFE_bundle.zip"
    if bundle.exists():
        bundle.unlink()

    with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(args.out.rglob("*")):
            if not p.is_file() or p == bundle:
                continue
            rel = p.relative_to(args.out)
            if "LOCAL_ONLY" in p.name:
                continue
            if "SHARE_SAFE" not in p.name and "modules" not in rel.parts:
                continue
            zf.write(p, arcname=rel.as_posix())

    print("解析完成")
    print(f"  relevant records   : {len(safe)}")
    print(f"  action records     : {len(actions)}")
    print(f"  transactions       : {len(transactions)}")
    print(f"  album full layers  : {len(full)}")
    print(f"  album missing      : {len(missing)}")
    print(f"  native album trace : {len(native)}")
    print(f"  missing chunks     : {len(health['missing_chunk_groups'])}")
    print(f"  json parse failures: {len(health['json_parse_failures'])}")
    print()
    print("模块记录数:")
    for module in sorted(module_counts):
        print(f"  {module:<22} {module_counts[module]}")
    print()
    print(f"输出目录: {args.out}")
    print(f"可上传:   {bundle}")
    print("不要上传 protocol_full_LOCAL_ONLY.jsonl")


if __name__ == "__main__":
    main()
