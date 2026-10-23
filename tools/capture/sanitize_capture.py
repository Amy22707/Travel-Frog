import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

src = ROOT / "capture" / "initial_sync" / "protocol_utf8.txt"
dst = ROOT / "capture" / "initial_sync" / "protocol_sanitized.txt"

SENSITIVE_KEYS = {
    "token",
    "access_token",
    "accessToken",
    "game_token",
    "gameToken",
    "refresh_token",
    "refreshToken",
    "authorization",
    "auth",
    "session_key",
    "sessionKey",
    "openid",
    "openId",
}

def redact(obj):
    if isinstance(obj, dict):
        result = {}
        for k, v in obj.items():
            if k.lower() in {x.lower() for x in SENSITIVE_KEYS}:
                result[k] = "<REDACTED>"
            else:
                result[k] = redact(v)
        return result

    if isinstance(obj, list):
        return [redact(x) for x in obj]

    return obj


out = []

with src.open("r", encoding="utf-8-sig", errors="replace") as f:
    for line in f:
        line = line.rstrip("\r\n")

        marker = None

        if "发送协议：" in line:
            marker = "发送协议："
        elif "接收协议：" in line:
            marker = "接收协议："

        if marker is None:
            # 保留服务器连接地址，但不处理为 JSON
            if "创建连接 url:" in line:
                out.append(line)
            continue

        prefix, payload = line.split(marker, 1)

        try:
            data = json.loads(payload)
            data = redact(data)

            # hall.login 的 data 直接整体遮掉，避免 token
            if data.get("cmd") == "hall.login":
                data["data"] = "<REDACTED_LOGIN_DATA>"

            payload = json.dumps(
                data,
                ensure_ascii=False,
                separators=(",", ":")
            )

        except Exception:
            # JSON 解析失败时，至少对明显 token 字段做文本脱敏
            payload = re.sub(
                r'("(?:token|access_token|game_token|authorization)"\s*:\s*")[^"]*(")',
                r'\1<REDACTED>\2',
                payload,
                flags=re.I,
            )

        out.append(prefix + marker + payload)

with dst.open("w", encoding="utf-8") as f:
    f.write("\n".join(out))

print(f"输出: {dst}")
print(f"共提取 {len(out)} 行")
