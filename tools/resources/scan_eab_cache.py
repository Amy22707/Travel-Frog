#!/usr/bin/env python3
import argparse
import hashlib
import json
import struct
from pathlib import Path

MAGIC = b"\x89EAB\r\n\x1a\n"
ENCRYPTED_MAGIC = b"\x89EAB\r\n\x1b\n"
XXTEA_DELTA = 0x9E3779B9
EAB_KEY = b"ejoyassetbundle"

def md5_file(path: Path) -> str:
    h = hashlib.md5()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def safe_name(s: str) -> str:
    return "".join(c if c.isalnum() or c in "._-()" else "_" for c in s)

def _uint32_list(data: bytes, include_length: bool) -> list[int]:
    words = (len(data) + 3) // 4
    values = [0] * (words + int(include_length))
    for offset, byte in enumerate(data):
        values[offset // 4] |= byte << ((offset % 4) * 8)
    if include_length:
        values[-1] = len(data)
    return values


def _bytes_from_uint32(values: list[int], include_length: bool) -> bytes | None:
    length = len(values) * 4
    if include_length:
        stored_length = values[-1]
        if stored_length < length - 7 or stored_length > length - 4:
            return None
        length = stored_length
    return bytes(
        (values[offset // 4] >> ((offset % 4) * 8)) & 0xFF
        for offset in range(length)
    )


def _xxtea_mix(total: int, y: int, z: int, index: int, key_index: int, key: list[int]) -> int:
    return (
        ((z >> 5 ^ y << 2) + (y >> 3 ^ z << 4))
        ^ ((total ^ y) + (key[(index & 3) ^ key_index] ^ z))
    ) & 0xFFFFFFFF


def decrypt_xxtea(payload: bytes, key: bytes = EAB_KEY) -> bytes | None:
    """Decode the encrypted EAB variant used by the current China client."""
    values = _uint32_list(payload, False)
    if len(values) < 2:
        return None
    key_values = _uint32_list(key, False)
    while len(key_values) < 4:
        key_values.append(0)

    count = len(values)
    total = (6 + 52 // count) * XXTEA_DELTA & 0xFFFFFFFF
    y = values[0]
    while total:
        key_index = (total >> 2) & 3
        for index in range(count - 1, 0, -1):
            z = values[index - 1]
            values[index] = (values[index] - _xxtea_mix(total, y, z, index, key_index, key_values)) & 0xFFFFFFFF
            y = values[index]
        z = values[-1]
        values[0] = (values[0] - _xxtea_mix(total, y, z, 0, key_index, key_values)) & 0xFFFFFFFF
        y = values[0]
        total = (total - XXTEA_DELTA) & 0xFFFFFFFF
    return _bytes_from_uint32(values, True)


def parse_eab(path: Path):
    data = path.read_bytes()
    if len(data) < len(MAGIC) + 4:
        return None
    header = data[:len(MAGIC)]
    if header == ENCRYPTED_MAGIC:
        data = decrypt_xxtea(data[len(MAGIC):])
        if data is None:
            return None
        raw_start = 0
    elif header == MAGIC:
        data = data[len(MAGIC):]
        raw_start = 0
    else:
        return None

    config_len = struct.unpack("<I", data[:4])[0]
    config_raw = data[4:4 + config_len]
    config = json.loads(config_raw.decode("utf-8"))
    raw_start += 4 + config_len
    return config, raw_start, data

def main():
    ap = argparse.ArgumentParser(
        description="Scan Travel Frog client cache for EAB bundles and extract JSON/text assets."
    )
    ap.add_argument("root", type=Path, help="Extracted app/cache root to scan recursively")
    ap.add_argument("--out", type=Path, default=Path("eab_scan"), help="Output directory")
    args = ap.parse_args()

    root = args.root.resolve()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)

    bundles = []
    checked = 0

    for path in root.rglob("*"):
        if not path.is_file():
            continue
        checked += 1
        try:
            parsed = parse_eab(path)
        except Exception:
            continue
        if parsed is None:
            continue

        config, raw_start, bundle_data = parsed
        rel = str(path.relative_to(root))
        digest = md5_file(path)

        bundle = {
            "path": rel,
            "size": path.stat().st_size,
            "md5": digest,
            "asset_count": len(config),
            "assets": config,
        }
        bundles.append(bundle)

        bundle_dir = out / f"{len(bundles):02d}_{safe_name(Path(rel).parent.name)}_{digest[:8]}"
        bundle_dir.mkdir(parents=True, exist_ok=True)

        (bundle_dir / "index.json").write_text(
            json.dumps(config, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # Assets are concatenated in index order immediately after the JSON index.
        offset = raw_start
        for asset in config:
            size = int(asset.get("s", 0))
            typ = asset.get("t", "")
            name = asset.get("n", "unnamed")
            blob = bundle_data[offset:offset + size]
            offset += size

            if typ not in {"json", "text", "sheet"}:
                continue

            ext = ".json" if typ in {"json", "sheet"} else ".txt"
            dest = bundle_dir / (safe_name(name) + ext)

            try:
                if typ in {"json", "sheet"}:
                    obj = json.loads(blob.decode("utf-8"))
                    dest.write_text(
                        json.dumps(obj, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                else:
                    dest.write_text(blob.decode("utf-8"), encoding="utf-8")
            except Exception:
                # Keep the raw bytes if decoding fails.
                dest = bundle_dir / (safe_name(name) + ".bin")
                dest.write_bytes(blob)

    summary = {
        "root": str(root),
        "files_checked": checked,
        "eab_bundles_found": len(bundles),
        "bundles": [
            {
                "path": b["path"],
                "size": b["size"],
                "md5": b["md5"],
                "asset_count": b["asset_count"],
                "types": sorted(set(a.get("t", "") for a in b["assets"])),
                "config_assets": [
                    a.get("n")
                    for a in b["assets"]
                    if a.get("t") in {"json", "text", "sheet"}
                ],
            }
            for b in bundles
        ],
    }

    (out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"files_checked={checked}")
    print(f"eab_bundles_found={len(bundles)}")
    for b in summary["bundles"]:
        print(
            f"- {b['path']}  md5={b['md5']}  "
            f"assets={b['asset_count']}  types={','.join(b['types'])}"
        )
        if b["config_assets"]:
            print("  config:", ", ".join(b["config_assets"]))
    print(f"summary={out / 'summary.json'}")

if __name__ == "__main__":
    main()
