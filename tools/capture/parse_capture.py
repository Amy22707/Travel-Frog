from pathlib import Path
import json
import re
from collections import defaultdict, Counter

ROOT = Path(__file__).resolve().parents[2]
src = ROOT / "capture" / "raw" / "login_chunked_logcat.txt"
outdir = ROOT / "capture" / "initial_sync"
outdir.mkdir(parents=True, exist_ok=True)

chunk_re = re.compile(r'FROG\|(\d+)\|(\d+)\|(\d+)\|(.*)$')

groups = defaultdict(dict)
totals = {}
order = []

for line in src.read_text(encoding="utf-8", errors="replace").splitlines():
    m = chunk_re.search(line)
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
    raise RuntimeError("没有找到 FROG 分块日志。请确认已换成 main.capture.js 后重新抓取。")

assembled = []
missing = []
for msg_id in order:
    total = totals[msg_id]
    parts = groups[msg_id]
    absent = [i for i in range(total) if i not in parts]
    if absent:
        missing.append((msg_id, absent))
        continue
    assembled.append((msg_id, "".join(parts[i] for i in range(total))))

records = []
parse_fail = []

for msg_id, msg in assembled:
    marker = None
    kind = None
    if "发送协议：" in msg:
        marker, kind = "发送协议：", "send"
    elif "接收协议：" in msg:
        marker, kind = "接收协议：", "recv"
    elif "创建连接 url:" in msg:
        url = msg.split("创建连接 url:", 1)[1].strip()
        records.append({"kind": "connect", "url": url})
        continue
    else:
        continue

    payload = msg.split(marker, 1)[1]
    try:
        obj = json.loads(payload)
        records.append({"kind": kind, "message": obj})
    except Exception as exc:
        parse_fail.append((msg_id, str(exc), payload[:160]))

def dump_jsonl(path, rows):
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")

# 这是本地保存用的完整数据，不要上传。
dump_jsonl(outdir / "protocol_full_LOCAL_ONLY.jsonl", records)

requests = [r for r in records if r["kind"] == "send"]
responses = [
    r for r in records
    if r["kind"] == "recv" and "session" in r.get("message", {})
]
pushes = [
    r for r in records
    if r["kind"] == "recv" and "cmd" in r.get("message", {})
]

dump_jsonl(outdir / "requests_LOCAL_ONLY.jsonl", requests)
dump_jsonl(outdir / "responses_LOCAL_ONLY.jsonl", responses)
dump_jsonl(outdir / "server_push_LOCAL_ONLY.jsonl", pushes)

SENSITIVE = {
    "token", "access_token", "accesstoken", "refresh_token", "refreshtoken",
    "authorization", "auth",
    "account", "accountid", "chuid",
    "umidtoken", "utdid",
    "imei", "imsi", "androidid", "deviceid",
    "phone", "mobile", "openid", "open_id",
    "mac",
}

def redact(v):
    if isinstance(v, dict):
        out = {}
        for k, x in v.items():
            if k.lower() in SENSITIVE:
                out[k] = "<REDACTED>"
            else:
                out[k] = redact(x)
        return out
    if isinstance(v, list):
        return [redact(x) for x in v]
    return v

share = []
for r in records:
    rr = redact(r)
    if rr.get("kind") in ("send", "recv"):
        m = rr.get("message", {})
        # 登录凭据和设备环境对协议分析没有价值，整段遮掉。
        if m.get("cmd") == "hall.login":
            m["data"] = "<REDACTED_LOGIN_DATA>"
        elif m.get("cmd") == "client.set_client_envinfo":
            m["data"] = "<REDACTED_DEVICE_ENV>"
    share.append(rr)

dump_jsonl(outdir / "protocol_SHARE_SAFE.jsonl", share)

send_cmds = []
push_cmds = []
for r in records:
    m = r.get("message", {})
    if r["kind"] == "send" and "cmd" in m:
        send_cmds.append(m["cmd"])
    elif r["kind"] == "recv" and "cmd" in m:
        push_cmds.append(m["cmd"])

summary = [
    f"assembled_messages={len(assembled)}",
    f"protocol_records={len(records)}",
    f"requests={len(requests)}",
    f"session_responses={len(responses)}",
    f"server_pushes={len(pushes)}",
    f"missing_chunk_groups={len(missing)}",
    f"json_parse_failures={len(parse_fail)}",
    "",
    "[client requests]",
]
summary += [f"{k}\t{v}" for k, v in Counter(send_cmds).items()]
summary += ["", "[server pushes]"]
summary += [f"{k}\t{v}" for k, v in Counter(push_cmds).items()]

if missing:
    summary += ["", "[missing chunks]"] + [repr(x) for x in missing]
if parse_fail:
    summary += ["", "[parse failures]"] + [repr(x) for x in parse_fail]

(outdir / "command_summary.txt").write_text("\n".join(summary), encoding="utf-8")

print("完成。")
print(f"完整本地数据: {outdir / 'protocol_full_LOCAL_ONLY.jsonl'}")
print(f"可上传脱敏版: {outdir / 'protocol_SHARE_SAFE.jsonl'}")
print(f"命令摘要:     {outdir / 'command_summary.txt'}")
print(f"JSON解析失败: {len(parse_fail)}")
print(f"缺失分块组:   {len(missing)}")
