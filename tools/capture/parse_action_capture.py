from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]

DEFAULT_SRC = ROOT / "capture" / "actions" / "clover_001" / "raw_logcat.txt"
DEFAULT_OUT = ROOT / "capture" / "actions" / "clover_001" / "parsed"

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
TARGET_CMDS = {"clover.harvest", "clover_harvest"}


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
        # 连接地址本身不含登录凭据，但单动作分析不需要。
        rr["url"] = "<REDACTED_SERVER_URL>"
    return rr


def main() -> None:
    ap = argparse.ArgumentParser(
        description="解析旅行青蛙 FROG 分块日志，并提取单次收草协议窗口。"
    )
    ap.add_argument("--src", type=Path, default=DEFAULT_SRC)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--before", type=int, default=5,
                    help="目标请求前保留多少条协议记录")
    ap.add_argument("--after", type=int, default=15,
                    help="目标请求后保留多少条协议记录")
    args = ap.parse_args()

    src = args.src
    outdir = args.out
    outdir.mkdir(parents=True, exist_ok=True)

    if not src.exists():
        raise FileNotFoundError(f"找不到抓包文件: {src}")

    groups: dict[int, dict[int, str]] = defaultdict(dict)
    totals: dict[int, int] = {}
    order: list[int] = []

    for line in src.read_text(encoding="utf-8", errors="replace").splitlines():
        m = CHUNK_RE.search(line)
        if not m:
            continue
        msg_id = int(m.group(1))
        idx = int(m.group(2))
        total = int(m.group(3))
        payload = m.group(4)

        if msg_id not in totals:
            order.append(msg_id)
        totals[msg_id] = total
        groups[msg_id][idx] = payload

    if not groups:
        raise RuntimeError(
            "没有找到 FROG 分块日志。请确认抓包时使用的是 main.capture.js。"
        )

    assembled: list[tuple[int, str]] = []
    missing: list[tuple[int, list[int]]] = []

    for msg_id in order:
        total = totals[msg_id]
        parts = groups[msg_id]
        absent = [i for i in range(total) if i not in parts]
        if absent:
            missing.append((msg_id, absent))
            continue
        assembled.append(
            (msg_id, "".join(parts[i] for i in range(total)))
        )

    records: list[dict] = []
    parse_fail: list[tuple[int, str, str]] = []

    for msg_id, msg in assembled:
        marker = None
        kind = None

        if "发送协议：" in msg:
            marker, kind = "发送协议：", "send"
        elif "接收协议：" in msg:
            marker, kind = "接收协议：", "recv"
        elif "创建连接 url:" in msg:
            records.append({
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
                "log_id": msg_id,
                "kind": kind,
                "message": obj,
            })
        except Exception as exc:
            parse_fail.append((msg_id, str(exc), payload[:200]))

    # 本地完整结果：不要上传。
    dump_jsonl(outdir / "protocol_full_LOCAL_ONLY.jsonl", records)

    requests = [
        r for r in records
        if r.get("kind") == "send"
    ]
    responses = [
        r for r in records
        if r.get("kind") == "recv"
        and isinstance(r.get("message"), dict)
        and "session" in r["message"]
    ]
    pushes = [
        r for r in records
        if r.get("kind") == "recv"
        and isinstance(r.get("message"), dict)
        and "cmd" in r["message"]
    ]

    dump_jsonl(outdir / "requests_LOCAL_ONLY.jsonl", requests)
    dump_jsonl(outdir / "responses_LOCAL_ONLY.jsonl", responses)
    dump_jsonl(outdir / "server_push_LOCAL_ONLY.jsonl", pushes)

    # 找收草请求。
    target_indexes = []
    target_sessions = []

    for i, r in enumerate(records):
        if r.get("kind") != "send":
            continue
        m = r.get("message")
        if not isinstance(m, dict):
            continue
        if m.get("cmd") in TARGET_CMDS:
            target_indexes.append(i)
            if m.get("session") is not None:
                target_sessions.append(m.get("session"))

    # 收草附近窗口。
    selected_indexes = set()

    for idx in target_indexes:
        lo = max(0, idx - args.before)
        hi = min(len(records), idx + args.after + 1)
        selected_indexes.update(range(lo, hi))

    # 无论距离多远，都把与收草 request 对应的 session response 纳入窗口。
    for i, r in enumerate(records):
        if r.get("kind") != "recv":
            continue
        m = r.get("message")
        if isinstance(m, dict) and m.get("session") in target_sessions:
            selected_indexes.add(i)

    # 同时纳入所有 clover 相关 push，避免漏掉延迟更新。
    for i, r in enumerate(records):
        m = r.get("message")
        if not isinstance(m, dict):
            continue
        cmd = m.get("cmd")
        if isinstance(cmd, str) and "clover" in cmd.lower():
            selected_indexes.add(i)

    action_rows = [
        records[i]
        for i in sorted(selected_indexes)
    ]

    safe_all = [share_safe_record(r) for r in records]
    safe_action = [share_safe_record(r) for r in action_rows]

    dump_jsonl(outdir / "protocol_SHARE_SAFE.jsonl", safe_all)
    dump_jsonl(outdir / "clover_action_SHARE_SAFE.jsonl", safe_action)

    send_cmds = []
    push_cmds = []
    for r in records:
        m = r.get("message")
        if not isinstance(m, dict):
            continue
        cmd = m.get("cmd")
        if not isinstance(cmd, str):
            continue
        if r.get("kind") == "send":
            send_cmds.append(cmd)
        elif r.get("kind") == "recv":
            push_cmds.append(cmd)

    # 给人看的收草核心摘要；内容也做脱敏。
    core = []
    for r in safe_action:
        m = r.get("message")
        if not isinstance(m, dict):
            continue
        if r.get("kind") == "send":
            core.append(
                f'SEND log_id={r["log_id"]} '
                f'cmd={m.get("cmd")} session={m.get("session")} '
                f'data={json.dumps(m.get("data"), ensure_ascii=False)}'
            )
        elif r.get("kind") == "recv" and "cmd" in m:
            core.append(
                f'PUSH log_id={r["log_id"]} '
                f'cmd={m.get("cmd")} '
                f'data={json.dumps(m.get("data"), ensure_ascii=False)}'
            )
        elif r.get("kind") == "recv" and "session" in m:
            core.append(
                f'RESPONSE log_id={r["log_id"]} '
                f'session={m.get("session")} '
                f'data={json.dumps(m.get("data"), ensure_ascii=False)}'
            )

    summary = [
        f"source={src}",
        f"assembled_messages={len(assembled)}",
        f"protocol_records={len(records)}",
        f"requests={len(requests)}",
        f"session_responses={len(responses)}",
        f"server_pushes={len(pushes)}",
        f"missing_chunk_groups={len(missing)}",
        f"json_parse_failures={len(parse_fail)}",
        f"clover_harvest_requests={len(target_indexes)}",
        f"clover_harvest_sessions={target_sessions}",
        f"action_window_records={len(action_rows)}",
        "",
        "[client requests]",
    ]
    summary.extend(
        f"{cmd}\t{count}" for cmd, count in Counter(send_cmds).items()
    )
    summary += ["", "[server pushes]"]
    summary.extend(
        f"{cmd}\t{count}" for cmd, count in Counter(push_cmds).items()
    )
    summary += ["", "[clover action window]"]
    summary.extend(core)

    if missing:
        summary += ["", "[missing chunks]"]
        summary.extend(repr(x) for x in missing)

    if parse_fail:
        summary += ["", "[parse failures]"]
        summary.extend(repr(x) for x in parse_fail)

    (outdir / "clover_summary_SHARE_SAFE.txt").write_text(
        "\n".join(summary),
        encoding="utf-8",
    )

    print("完成。")
    print(f"源文件:             {src}")
    print(f"完整本地协议:       {outdir / 'protocol_full_LOCAL_ONLY.jsonl'}")
    print(f"脱敏完整协议:       {outdir / 'protocol_SHARE_SAFE.jsonl'}")
    print(f"收草脱敏窗口:       {outdir / 'clover_action_SHARE_SAFE.jsonl'}")
    print(f"收草脱敏摘要:       {outdir / 'clover_summary_SHARE_SAFE.txt'}")
    print(f"收草请求数:         {len(target_indexes)}")
    print(f"收草 session:       {target_sessions}")
    print(f"JSON 解析失败:      {len(parse_fail)}")
    print(f"缺失分块组:         {len(missing)}")

    if not target_indexes:
        print()
        print("警告：没有找到 clover.harvest / clover_harvest。")
        print("先不要重新操作，请把 clover_summary_SHARE_SAFE.txt 的前半部分给我看。")


if __name__ == "__main__":
    main()
