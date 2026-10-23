#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pack only cached Travel Frog Picture resources from a full_data_extracted backup.
No account databases, shared_prefs, tokens, or other private app data are included.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ROOT = ROOT / "backup" / "snapshot_20260911" / "full_data_extracted" / "com.aligames.lxqw.hhb" / "files" / "games"
DEFAULT_OUT = ROOT / "picture_cache_bundle_20260912.zip"

TARGET_NAMES = (
    "beach3_qw",
    "gy_0_qw",
    "g_qinghai4_qw",
)

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def is_picture_resource(rel: str) -> bool:
    s = rel.replace("\\", "/").lower()
    return "/images/picture/" in "/" + s

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()

    root = args.root
    out = args.out

    if not root.is_dir():
        raise SystemExit(f"找不到 games 目录: {root}")

    selected: list[Path] = []
    skipped_headers = 0

    for p in root.rglob("*"):
        if not p.is_file():
            continue
        rel = str(p.relative_to(root))
        if not is_picture_resource(rel):
            continue
        # HTTP cache metadata is unnecessary for image-dimension analysis.
        if p.name.lower().endswith("#header"):
            skipped_headers += 1
            continue
        selected.append(p)

    selected.sort(key=lambda p: str(p).lower())

    manifest = {
        "source_root": str(root),
        "file_count": len(selected),
        "skipped_header_count": skipped_headers,
        "files": [],
        "target_name_hits": {},
    }

    hits = {name: [] for name in TARGET_NAMES}

    print(f"找到 Picture 资源正文: {len(selected)}")
    print(f"跳过 #header: {skipped_headers}")

    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()

    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for idx, p in enumerate(selected, 1):
            rel = p.relative_to(root).as_posix()
            lower = rel.lower()

            for name in TARGET_NAMES:
                if name.lower() in lower:
                    hits[name].append(rel)

            info = {
                "path": rel,
                "size": p.stat().st_size,
                "sha256": sha256_file(p),
            }
            manifest["files"].append(info)
            zf.write(p, arcname=f"files/{rel}")

            if idx % 50 == 0 or idx == len(selected):
                print(f"已处理 {idx}/{len(selected)}")

        manifest["target_name_hits"] = hits
        zf.writestr(
            "manifest.json",
            json.dumps(manifest, ensure_ascii=False, indent=2),
        )

    print()
    print("三个校准目标命中情况:")
    for name, paths in hits.items():
        print(f"  {name}: {len(paths)}")
        for p in paths:
            print(f"    {p}")

    print()
    print(f"输出: {out}")
    print(f"ZIP SHA256: {sha256_file(out)}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
