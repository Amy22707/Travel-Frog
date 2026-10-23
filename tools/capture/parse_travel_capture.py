from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SRC = ROOT / "capture" / "actions" / "travel_return_001" / "raw_logcat.txt"
DEFAULT_OUT = ROOT / "capture" / "actions" / "travel_return_001" / "parsed_v2"

CHUNK_RE = re.compile(r"FROG\|(\d+)\|(\d+)\|(\d+)\|(.*)$")

SENSITIVE_KEYS = {
    "token", "access_token", "accesstoken", "refresh_token", "refreshtoken",
    "authorization", "auth",
    "account", "accountid", "chuid",
    "umidtoken", "utdid",
    "imei", "imsi", "androidid", "android_id", "deviceid", "device_id",
    "oaid", "idfa", "idfv",
    "phone", "mobile", "openid", "open_id",
    "mac", "ip",
}

LOGIN_CMDS = {"hall.login", "hall_login"}
ENV_CMDS = {"client.set_client_envinfo", "client_set_client_envinfo"}

# 这些协议对于旅行生命周期最有价值；另外所有非心跳协议也会写入 timeline。
TRAVEL_KEYWORDS = (
    "travel", "album", "mail", "event", "frog", "guest", "item",
    "clover", "client.load_role", "client_load_role"
)


def dump_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def redact(v: Any) -> Any:
    if isinstance(v, dict):
        out = {}
        for k, x in v.items():
            if str(k).lower() in SENSITIVE_KEYS:
                out[k] = "<REDACTED>"
            else:
                out[k] = redact(x)
        return out
    if isinstance(v, list):
        return [redact(x) for x in v]
    return v


def share_safe_record(r: dict) -> dict:
    rr = redact(r)
    m = rr.get("message")
    if isinstance(m, dict):
        cmd = m.get("cmd")
        if cmd in LOGIN_CMDS:
            m["data"] = "<REDACTED_LOGIN_DATA>"
        elif cmd in ENV_CMDS:
            m["data"] = "<REDACTED_DEVICE_ENV>"
    if rr.get("kind") == "connect":
        rr["url"] = "<REDACTED_SERVER_URL>"
    return rr


def is_heartbeat(r: dict) -> bool:
    m = r.get("message")
    if not isinstance(m, dict):
        return False

    if m.get("cmd") in {"client.hello", "client_hello"}:
        return True

    # client.hello 的 session response 通常只有 timestamp。
    if r.get("kind") == "recv" and "session" in m:
        data = m.get("data")
        if isinstance(data, dict) and set(data.keys()) == {"timestamp"}:
            return True

    return False


def compact_line(r: dict) -> str:
    m = r.get("message")
    if not isinstance(m, dict):
        if r.get("kind") == "connect":
            return f'EPOCH {r["epoch"]} CONNECT'
        return f'EPOCH {r["epoch"]} {r.get("kind")}'

    cmd = m.get("cmd")
    session = m.get("session")
    data = m.get("data")
    return (
        f'EPOCH {r["epoch"]} log_id={r["log_id"]} '
        f'{r["kind"].upper()} cmd={cmd} session={session} '
        f'data={json.dumps(data, ensure_ascii=False, separators=(",", ":"))}'
    )


def main() -> None:
    ap = argparse.ArgumentParser(
        description="解析旅行青蛙 FROG 长时间日志，支持游戏重启导致的 log_id 重置。"
    )
    ap.add_argument("--src", type=Path, default=DEFAULT_SRC)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()

    src = args.src
    outdir = args.out
    outdir.mkdir(parents=True, exist_ok=True)

    if not src.exists():
        raise FileNotFoundError(f"找不到抓包文件: {src}")

    # 关键修复：
    # 旧解析器只用 msg_id 分组。游戏重启后 _frogLogId 会从头开始，
    # 会造成不同进程的同号消息互相覆盖。
    # 这里在检测到明显的 msg_id 回绕后增加 epoch。
    groups: dict[tuple[int, int], dict[int, str]] = defaultdict(dict)
    totals: dict[tuple[int, int], int] = {}
    order: list[tuple[int, int]] = []

    epoch = 1
    prev_msg_id: int | None = None
    reset_points: list[tuple[int, int, int]] = []  # (new_epoch, previous_id, new_id)

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

        # 只在“明显回到很小编号”时判断为客户端重新启动，
        # 避免偶发日志交错造成误判。
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
            "没有找到 FROG 分块日志。请确认源文件来自 main.capture.js。"
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
            records.append({
                "epoch": epoch_id,
                "log_id": msg_id,
                "kind": kind,
                "message": obj,
            })
        except Exception as exc:
            parse_fail.append((epoch_id, msg_id, str(exc), payload[:200]))

    # LOCAL_ONLY：保留在自己电脑，不上传。
    dump_jsonl(outdir / "protocol_full_LOCAL_ONLY.jsonl", records)

    safe_all = [share_safe_record(r) for r in records]
    dump_jsonl(outdir / "protocol_SHARE_SAFE.jsonl", safe_all)

    # 去掉 client.hello 和对应 timestamp response，得到便于人工阅读的真实动作时间线。
    safe_timeline = [r for r in safe_all if not is_heartbeat(r)]
    dump_jsonl(outdir / "travel_timeline_SHARE_SAFE.jsonl", safe_timeline)

    # 每次启动分别统计。
    per_epoch: dict[int, list[dict]] = defaultdict(list)
    for r in safe_all:
        per_epoch[r["epoch"]].append(r)

    summary: list[str] = [
        f"source={src}",
        f"frog_lines={frog_lines}",
        f"epochs={len(per_epoch)}",
        f"restart_resets={reset_points}",
        f"assembled_messages={len(assembled)}",
        f"protocol_records={len(records)}",
        f"missing_chunk_groups={len(missing)}",
        f"json_parse_failures={len(parse_fail)}",
        "",
    ]

    for ep in sorted(per_epoch):
        ep_rows = per_epoch[ep]
        sends = []
        pushes = []
        role_statuses = []
        events = []
        album_new = []

        for r in ep_rows:
            m = r.get("message")
            if not isinstance(m, dict):
                continue

            cmd = m.get("cmd")
            if r.get("kind") == "send" and isinstance(cmd, str):
                sends.append(cmd)
            elif r.get("kind") == "recv" and isinstance(cmd, str):
                pushes.append(cmd)

            if cmd in {"client.load_role", "client_load_role"}:
                data = m.get("data")
                if isinstance(data, dict):
                    frog = data.get("frog")
                    if isinstance(frog, dict):
                        role_statuses.append({
                            "status": frog.get("status"),
                            "motion": frog.get("motion"),
                        })

            if cmd in {"client.load_events", "client_load_events"}:
                events.append(m.get("data"))

            if cmd in {"album.load_new", "album_load_new"}:
                album_new.append(m.get("data"))

        summary += [
            f"[epoch {ep}]",
            f"records={len(ep_rows)}",
            f"client_requests={dict(Counter(sends))}",
            f"server_pushes={dict(Counter(pushes))}",
            f"frog_states={json.dumps(role_statuses, ensure_ascii=False)}",
            f"client_load_events={json.dumps(events, ensure_ascii=False)}",
            f"album_load_new={json.dumps(album_new, ensure_ascii=False)}",
            "",
        ]

    summary += ["[non-heartbeat timeline]"]
    summary.extend(compact_line(r) for r in safe_timeline)

    if missing:
        summary += ["", "[missing chunks]"]
        summary.extend(repr(x) for x in missing)

    if parse_fail:
        summary += ["", "[parse failures]"]
        summary.extend(repr(x) for x in parse_fail)

    (outdir / "travel_summary_SHARE_SAFE.txt").write_text(
        "\n".join(summary), encoding="utf-8"
    )

    print("完成。")
    print(f"检测到客户端启动次数: {len(per_epoch)}")
    print(f"log_id 重置点:       {reset_points}")
    print(f"缺失分块组:          {len(missing)}")
    print(f"JSON 解析失败:        {len(parse_fail)}")
    print()
    print("请不要上传:")
    print(f"  {outdir / 'protocol_full_LOCAL_ONLY.jsonl'}")
    print()
    print("可以发给我:")
    print(f"  {outdir / 'travel_summary_SHARE_SAFE.txt'}")
    print(f"  {outdir / 'travel_timeline_SHARE_SAFE.jsonl'}")
    print(f"  {outdir / 'protocol_SHARE_SAFE.jsonl'}")


if __name__ == "__main__":
    main()
