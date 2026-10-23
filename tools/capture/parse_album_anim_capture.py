#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Dedicated Travel Frog China capture parser for:
  1) rescuing exact historical album layers via album.load_by_id_list
  2) reverse-engineering / preserving animpicture.* (dynamic-photo system)

Input: a FULL raw adb logcat captured while main.capture.js is active.
Output:
  protocol_full_LOCAL_ONLY.jsonl          KEEP LOCAL, DO NOT UPLOAD
  album_anim_timeline_SHARE_SAFE.jsonl    narrow safe timeline
  album_compact_INDEX_SHARE_SAFE.json     compact album id/pic_id index
  album_full_layers_SHARE_SAFE.json       exact recovered full layers
  album_coverage_SHARE_SAFE.txt           coverage / missing list
  animpicture_SHARE_SAFE.jsonl            only dynamic-photo records/state
  parse_summary_SHARE_SAFE.txt            parser health summary

This parser intentionally excludes login/account/device/server-url records from
all SHARE_SAFE outputs. It only keeps album/animpicture records and filtered
dynamic-photo item counts (8002/8003/8004/9001/9002).
"""

from __future__ import annotations

import argparse
import json
import re
import hashlib
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SRC = ROOT / "capture" / "album_anim_20260912" / "raw_logcat.txt"
DEFAULT_OUT = ROOT / "capture" / "album_anim_20260912" / "parsed"

CHUNK_RE = re.compile(r"FROG\|(\d+)\|(\d+)\|(\d+)\|(.*)$")

DYNAMIC_ITEM_IDS = {8002, 8003, 8004, 9001, 9002}
DYNAMIC_SOURCE_PIC_IDS = {100: 1, 104: 2, 2000: 3, 3102: 4, 3063: 5, 3064: 6}

ALBUM_CMDS = {
    "album.load_all",
    "album.load",
    "album.load_by_id_list",
    "album.load_new",
    "album.load_recover",
}

# Wire commands are normally dot-form. Internal callback names in source are
# underscore-form; normalize both for robust classification.
def norm_cmd(cmd: Any) -> str:
    if not isinstance(cmd, str):
        return ""
    # Wire form is normally e.g. album.load_by_id_list. Internal callback
    # names use album_load_by_id_list, so only convert the FIRST underscore.
    if "." in cmd:
        return cmd
    if "_" in cmd:
        a, b = cmd.split("_", 1)
        return a + "." + b
    return cmd


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


def parse_chunks(src: Path):
    groups: dict[tuple[int, int], dict[int, str]] = defaultdict(dict)
    totals: dict[tuple[int, int], int] = {}
    order: list[tuple[int, int]] = []

    epoch = 1
    prev_msg_id: int | None = None
    reset_points: list[tuple[int, int, int]] = []
    frog_lines = 0

    for line in src.read_text(encoding="utf-8", errors="replace").splitlines():
        m = CHUNK_RE.search(line)
        if not m:
            continue
        frog_lines += 1
        msg_id = int(m.group(1))
        idx = int(m.group(2))
        total = int(m.group(3))
        payload = m.group(4)

        # Same conservative restart detection as the long-travel parser.
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
            "没有找到 FROG 分块日志。请确认抓包时使用 main.capture.js，"
            "并且 --src 指向完整 raw_logcat.txt。"
        )

    assembled: list[tuple[int, int, str]] = []
    missing: list[tuple[int, int, list[int]]] = []

    for epoch_id, msg_id in order:
        key = (epoch_id, msg_id)
        total = totals[key]
        parts = groups[key]
        absent = [i for i in range(total) if i not in parts]
        if absent:
            missing.append((epoch_id, msg_id, absent))
            continue
        assembled.append(
            (epoch_id, msg_id, "".join(parts[i] for i in range(total)))
        )

    records: list[dict] = []
    parse_fail: list[tuple[int, int, str, str]] = []

    for epoch_id, msg_id, msg in assembled:
        marker = None
        kind = None
        if "发送协议：" in msg:
            marker, kind = "发送协议：", "send"
        elif "接收协议：" in msg:
            marker, kind = "接收协议：", "recv"
        elif "创建连接 url:" in msg:
            # Keep only in LOCAL_ONLY.
            records.append({
                "epoch": epoch_id,
                "log_id": msg_id,
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
            parse_fail.append((epoch_id, msg_id, str(exc), payload[:240]))
            continue

        records.append({
            "epoch": epoch_id,
            "log_id": msg_id,
            "kind": kind,
            "message": obj,
        })

    return records, {
        "frog_lines": frog_lines,
        "epochs": epoch,
        "restart_resets": reset_points,
        "assembled_messages": len(assembled),
        "protocol_records": len(records),
        "missing_chunk_groups": missing,
        "json_parse_failures": parse_fail,
    }


def request_maps(records: list[dict]):
    # key = (epoch, session)
    reqs: dict[tuple[int, Any], dict] = {}
    for r in records:
        if r.get("kind") != "send":
            continue
        m = r.get("message")
        if not isinstance(m, dict):
            continue
        session = m.get("session")
        if session is None:
            continue
        reqs[(r["epoch"], session)] = r
    return reqs


def filter_dynamic_item_obj(obj: Any) -> Any:
    """Keep only dynamic-photo item information; discard unrelated inventory."""
    if isinstance(obj, list):
        out = []
        for x in obj:
            y = filter_dynamic_item_obj(x)
            if y is not None:
                if isinstance(y, list):
                    out.extend(y)
                else:
                    out.append(y)
        return out

    if not isinstance(obj, dict):
        return None

    # Direct item record.
    iid = obj.get("item_id", obj.get("id"))
    try:
        iid_int = int(iid)
    except Exception:
        iid_int = None
    if iid_int in DYNAMIC_ITEM_IDS:
        return dict(obj)

    # item.load_items: preserve only filtered house entries.
    if "house" in obj:
        house = filter_dynamic_item_obj(obj.get("house"))
        return {"house": house or []}

    # Other possible wrappers.
    result = {}
    for k, v in obj.items():
        y = filter_dynamic_item_obj(v)
        if y not in (None, [], {}):
            result[k] = y
    return result or None


def safe_relevant_records(records: list[dict]) -> list[dict]:
    reqs = request_maps(records)
    out: list[dict] = []

    for r in records:
        kind = r.get("kind")
        if kind not in {"send", "recv"}:
            continue
        m = r.get("message")
        if not isinstance(m, dict):
            continue

        cmd = norm_cmd(m.get("cmd"))
        session = m.get("session")
        for_cmd = ""

        if kind == "recv" and session is not None:
            req = reqs.get((r["epoch"], session))
            if req:
                rm = req.get("message") or {}
                for_cmd = norm_cmd(rm.get("cmd"))

        keep = False

        # Direct album / dynamic protocol record.
        if cmd in ALBUM_CMDS or cmd.startswith("animpicture."):
            keep = True

        # Session response to album / dynamic request usually has no cmd.
        if for_cmd in ALBUM_CMDS or for_cmd.startswith("animpicture."):
            keep = True

        # Narrow inventory evidence only.
        if cmd in {"item.load_items", "item.update"}:
            filtered = filter_dynamic_item_obj(m.get("data"))
            if filtered:
                mm = dict(m)
                mm["data"] = filtered
                rr = {
                    "epoch": r["epoch"],
                    "log_id": r["log_id"],
                    "kind": kind,
                    "message": mm,
                }
                if for_cmd:
                    rr["for_cmd"] = for_cmd
                out.append(rr)
            continue

        if not keep:
            continue

        rr = {
            "epoch": r["epoch"],
            "log_id": r["log_id"],
            "kind": kind,
            "message": m,
        }
        if for_cmd:
            rr["for_cmd"] = for_cmd
        out.append(rr)

    return out


def iter_picture_dicts(obj: Any):
    """Recursively yield picture-like dicts with id/pic_id/layers."""
    if isinstance(obj, dict):
        if (
            "id" in obj
            and "pic_id" in obj
            and isinstance(obj.get("layers"), list)
        ):
            yield obj
        for v in obj.values():
            yield from iter_picture_dicts(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from iter_picture_dicts(v)


def extract_full_pictures(safe_records: list[dict]):
    # Keep latest occurrence for an instance id. Existing server response is
    # authoritative and exact; no reconstruction is done here.
    by_id: dict[int, dict] = {}
    source_by_id: dict[int, str] = {}

    for r in safe_records:
        m = r.get("message") or {}
        data = m.get("data")
        cmd = norm_cmd(m.get("cmd"))
        src = cmd or r.get("for_cmd") or "unknown"

        for p in iter_picture_dicts(data):
            try:
                pid = int(p["id"])
                pic_id = int(p["pic_id"])
            except Exception:
                continue
            layers = p.get("layers")
            if not isinstance(layers, list):
                continue
            # Only fields needed to exactly reconstruct/render the postcard.
            q = {
                "id": pid,
                "pic_id": pic_id,
                "layers": layers,
            }
            # Preserve harmless presentation flags if present.
            for k in ("for_ads", "visit", "visted"):
                if k in p:
                    q[k] = p[k]
            by_id[pid] = q
            source_by_id[pid] = src

    rows = []
    for pid, p in by_id.items():
        q = dict(p)
        q["source"] = source_by_id.get(pid, "")
        rows.append(q)
    rows.sort(key=lambda x: x["id"])
    return rows


def extract_latest_album_index(safe_records: list[dict]):
    latest: list[dict] = []
    latest_epoch = latest_log = -1

    for r in safe_records:
        m = r.get("message") or {}
        if r.get("kind") != "recv" or norm_cmd(m.get("cmd")) != "album.load_all":
            continue
        data = m.get("data")
        if not isinstance(data, dict):
            continue
        id_list = data.get("id_list")
        if not isinstance(id_list, list):
            continue
        rows = []
        for x in id_list:
            if not isinstance(x, dict):
                continue
            try:
                rows.append({"id": int(x["id"]), "pic_id": int(x["pic_id"])})
            except Exception:
                pass
        if (r["epoch"], r["log_id"]) >= (latest_epoch, latest_log):
            latest = rows
            latest_epoch, latest_log = r["epoch"], r["log_id"]

    return latest, latest_epoch


def extract_animpicture_records(safe_records: list[dict]) -> list[dict]:
    out = []
    for r in safe_records:
        m = r.get("message") or {}
        cmd = norm_cmd(m.get("cmd"))
        for_cmd = r.get("for_cmd", "")
        if (
            cmd.startswith("animpicture.")
            or for_cmd.startswith("animpicture.")
            or cmd in {"item.load_items", "item.update"}
        ):
            out.append(r)
    return out


def latest_dynamic_state(anim_rows: list[dict]) -> dict | None:
    ans = None
    for r in anim_rows:
        m = r.get("message") or {}
        if r.get("kind") == "recv" and norm_cmd(m.get("cmd")) == "animpicture.load":
            if isinstance(m.get("data"), dict):
                ans = m["data"]
    return ans


def main() -> None:
    ap = argparse.ArgumentParser(
        description="解析相册 full layers + 动态照片 animpicture 的 FROG 抓包。"
    )
    ap.add_argument("--src", type=Path, default=DEFAULT_SRC)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()

    if not args.src.exists():
        raise FileNotFoundError(f"找不到抓包文件: {args.src}")

    args.out.mkdir(parents=True, exist_ok=True)

    records, health = parse_chunks(args.src)

    # Full protocol remains LOCAL_ONLY.
    dump_jsonl(args.out / "protocol_full_LOCAL_ONLY.jsonl", records)

    safe = safe_relevant_records(records)
    dump_jsonl(args.out / "album_anim_timeline_SHARE_SAFE.jsonl", safe)

    compact, compact_epoch = extract_latest_album_index(safe)
    dump_json(
        args.out / "album_compact_INDEX_SHARE_SAFE.json",
        {
            "count": len(compact),
            "source_epoch": compact_epoch,
            "pictures": compact,
        },
    )

    full = extract_full_pictures(safe)
    dump_json(
        args.out / "album_full_layers_SHARE_SAFE.json",
        {
            "count": len(full),
            "pictures": full,
        },
    )

    full_ids = {p["id"] for p in full}
    missing = [p for p in compact if p["id"] not in full_ids]
    dyn_compact = [
        {**p, "anim_template": DYNAMIC_SOURCE_PIC_IDS[p["pic_id"]]}
        for p in compact
        if p["pic_id"] in DYNAMIC_SOURCE_PIC_IDS
    ]
    dyn_full = [
        {**p, "anim_template": DYNAMIC_SOURCE_PIC_IDS[p["pic_id"]]}
        for p in full
        if p["pic_id"] in DYNAMIC_SOURCE_PIC_IDS
    ]

    coverage_lines = [
        f"compact_album_count={len(compact)}",
        f"full_layers_recovered={len(full_ids & {p['id'] for p in compact})}",
        f"missing_full_layers={len(missing)}",
        f"dynamic_source_in_album={len(dyn_compact)}",
        f"dynamic_source_with_full_layers={len(dyn_full)}",
        "",
        "[dynamic_source_in_album]",
    ]
    for p in dyn_compact:
        coverage_lines.append(
            f"id={p['id']} pic_id={p['pic_id']} anim_template={p['anim_template']} "
            f"full_layers={'yes' if p['id'] in full_ids else 'no'}"
        )

    coverage_lines += ["", "[missing_full_layers id,pic_id]"]
    for p in missing:
        coverage_lines.append(f"{p['id']},{p['pic_id']}")

    (args.out / "album_coverage_SHARE_SAFE.txt").write_text(
        "\n".join(coverage_lines) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    anim_rows = extract_animpicture_records(safe)
    dump_jsonl(args.out / "animpicture_SHARE_SAFE.jsonl", anim_rows)

    state = latest_dynamic_state(anim_rows)
    if state is not None:
        dump_json(args.out / "animpicture_latest_state_SHARE_SAFE.json", state)

    summary_lines = [
        f"source={args.src}",
        f"frog_lines={health['frog_lines']}",
        f"epochs={health['epochs']}",
        f"restart_resets={health['restart_resets']}",
        f"assembled_messages={health['assembled_messages']}",
        f"protocol_records={health['protocol_records']}",
        f"missing_chunk_groups={len(health['missing_chunk_groups'])}",
        f"json_parse_failures={len(health['json_parse_failures'])}",
        f"share_safe_records={len(safe)}",
        f"compact_album_count={len(compact)}",
        f"full_layers_count={len(full)}",
        f"missing_full_layers={len(missing)}",
        f"dynamic_source_in_album={len(dyn_compact)}",
        f"animpicture_records={len(anim_rows)}",
        "",
        "IMPORTANT:",
        "- protocol_full_LOCAL_ONLY.jsonl 只留本机，不要上传。",
        "- 可上传所有 *_SHARE_SAFE.* 文件。",
        "- 若 missing_full_layers 仍很多，请再次进官服相册翻到缺失照片所在页面。",
    ]

    if health["missing_chunk_groups"]:
        summary_lines += ["", "[missing chunk groups]"]
        for ep, mid, absent in health["missing_chunk_groups"][:100]:
            summary_lines.append(f"epoch={ep} msg_id={mid} missing={absent}")

    if health["json_parse_failures"]:
        summary_lines += ["", "[json parse failures]"]
        for ep, mid, err, prefix in health["json_parse_failures"][:50]:
            summary_lines.append(
                f"epoch={ep} msg_id={mid} error={err} prefix={prefix!r}"
            )

    (args.out / "parse_summary_SHARE_SAFE.txt").write_text(
        "\n".join(summary_lines) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    # One upload-safe bundle. LOCAL_ONLY is deliberately excluded.
    bundle = args.out / "album_anim_SHARE_SAFE_bundle.zip"
    safe_files = sorted(args.out.glob("*_SHARE_SAFE.*"))
    safe_files = [x for x in safe_files if x.name != bundle.name]
    with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in safe_files:
            zf.write(f, arcname=f.name)
    h = hashlib.sha256(bundle.read_bytes()).hexdigest()

    print("解析完成")
    print(f"  compact album      : {len(compact)}")
    print(f"  full layers        : {len(full)}")
    print(f"  missing layers     : {len(missing)}")
    print(f"  dynamic candidates : {len(dyn_compact)}")
    print(f"  anim records       : {len(anim_rows)}")
    print(f"  missing chunks     : {len(health['missing_chunk_groups'])}")
    print(f"  json parse failures: {len(health['json_parse_failures'])}")
    print()
    print(f"输出目录: {args.out}")
    print(f"可直接上传: {bundle}")
    print(f"SHARE_SAFE bundle SHA256: {h}")
    print("请不要上传 protocol_full_LOCAL_ONLY.jsonl。")


if __name__ == "__main__":
    main()
