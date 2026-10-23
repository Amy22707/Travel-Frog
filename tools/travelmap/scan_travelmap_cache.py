#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Scan the preserved Android WebView/browser cache for the old Travel Map H5 URL
and bridge strings. Read-only: it never changes the backup.

Usage:
  python tools/travelmap/scan_travelmap_cache.py
"""

from __future__ import annotations
import os
import re
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[2]
PKG = ROOT / "backup" / "snapshot_20260911" / "full_data_extracted" / "com.aligames.lxqw.hhb"
OUT = ROOT / "capture" / "travelmap_cache_scan.txt"

SEARCH_ROOTS = [
    PKG / "app_webview",
    PKG / "cache" / "WebView",
    PKG / "no_backup" / ".webview",
    PKG / "files" / "GameDataCache",
    PKG / "shared_prefs",
]

KEYWORDS = [
    b"travelmap",
    b"getPictureList",
    b"getPictureData",
    b"bbsDetail",
    b"moment_token",
    b"moment-token",
    "打卡全国美景".encode("utf-8"),
    "旅行地图".encode("utf-8"),
]

# UTF-16LE variants are common in browser stores.
KEYWORDS_UTF16 = [
    x.decode("utf-8", "ignore").encode("utf-16le")
    for x in KEYWORDS
    if x
]

URL_RE = re.compile(rb'https?://[^\x00-\x20"\'<>\\]{5,500}', re.I)

# Interesting hints to rank URLs. This does not decide truth; it only reduces noise.
URL_HINTS = (
    b"travel", b"map", b"frog", b"tabikaeru", b"lxqw",
    b"ali", b"ejoy", b"moment", b"bbs", b"city",
)

CHUNK = 4 * 1024 * 1024
OVERLAP = 2048
CTX = 220


def clean_ascii(b: bytes) -> str:
    s = b.replace(b"\x00", b" ")
    return "".join(chr(c) if 32 <= c < 127 else "." for c in s)


def clean_utf8(b: bytes) -> str:
    try:
        return b.decode("utf-8", "replace").replace("\x00", " ")
    except Exception:
        return clean_ascii(b)


def interesting_url(u: bytes) -> bool:
    lo = u.lower()
    return any(h in lo for h in URL_HINTS)


def sanitize_url(raw: str) -> str:
    # Keep scheme/host/path for identification, but strip query/fragment because
    # cached URLs can contain account/session parameters.
    try:
        s = urlsplit(raw)
        return urlunsplit((s.scheme, s.netloc, s.path, "", ""))
    except Exception:
        return raw.split("?", 1)[0].split("#", 1)[0]


def scan_file(path: Path):
    hits = []
    urls = set()
    try:
        size = path.stat().st_size
        if size == 0:
            return hits, urls
        with path.open("rb") as f:
            pos = 0
            tail = b""
            while True:
                data = f.read(CHUNK)
                if not data:
                    break
                buf = tail + data
                base = pos - len(tail)

                for kw in KEYWORDS:
                    start = 0
                    low = buf.lower()
                    needle = kw.lower()
                    while True:
                        i = low.find(needle, start)
                        if i < 0:
                            break
                        a = max(0, i - CTX)
                        z = min(len(buf), i + len(kw) + CTX)
                        hits.append((base + i, kw.decode("utf-8", "replace"), clean_utf8(buf[a:z])))
                        start = i + 1

                for kw in KEYWORDS_UTF16:
                    start = 0
                    while True:
                        i = buf.find(kw, start)
                        if i < 0:
                            break
                        a = max(0, i - CTX * 2)
                        z = min(len(buf), i + len(kw) + CTX * 2)
                        try:
                            ctx = buf[a:z].decode("utf-16le", "replace").replace("\x00", " ")
                        except Exception:
                            ctx = clean_ascii(buf[a:z])
                        hits.append((base + i, "UTF16:" + kw.decode("utf-16le", "replace"), ctx))
                        start = i + 2

                for m in URL_RE.finditer(buf):
                    u = m.group(0)
                    # Strip common binary/cache delimiters that regex may trail into.
                    u = u.rstrip(b".,;)]}")
                    if interesting_url(u):
                        urls.add(sanitize_url(u.decode("utf-8", "replace")))

                tail = buf[-OVERLAP:]
                pos += len(data)
    except (OSError, PermissionError) as e:
        hits.append((-1, "ERROR", str(e)))
    return hits, urls


def iter_files(root: Path):
    if root.is_file():
        yield root
        return
    if not root.exists():
        return
    for base, dirs, files in os.walk(root):
        # Skip obvious ad cache subtree if accidentally nested here.
        dirs[:] = [d for d in dirs if "pangle" not in d.lower()]
        for name in files:
            p = Path(base) / name
            try:
                # Avoid pathological huge media files; browser DB/cache files are normally much smaller.
                if p.stat().st_size <= 256 * 1024 * 1024:
                    yield p
            except OSError:
                continue


def main():
    if not PKG.exists():
        raise SystemExit(f"找不到备份目录: {PKG}")

    OUT.parent.mkdir(parents=True, exist_ok=True)

    total_files = 0
    hit_files = 0
    all_urls = set()
    sections = []

    for root in SEARCH_ROOTS:
        if not root.exists():
            sections.append(f"[MISSING ROOT] {root}")
            continue
        sections.append(f"[SCAN ROOT] {root}")
        for p in iter_files(root):
            total_files += 1
            hits, urls = scan_file(p)
            if hits or urls:
                hit_files += 1
                sections.append("")
                sections.append(f"FILE: {p}")
                try:
                    sections.append(f"SIZE: {p.stat().st_size}")
                except OSError:
                    pass

                if urls:
                    sections.append("URLS:")
                    for u in sorted(urls):
                        sections.append("  " + u)
                        all_urls.add(u)

                if hits:
                    sections.append("KEYWORD HITS:")
                    for off, kw, ctx in hits[:100]:
                        sections.append(f"  offset={off} keyword={kw}")
                        sections.append("    " + ctx.replace("\r", " ").replace("\n", " ")[:1000])
                    if len(hits) > 100:
                        sections.append(f"  ... {len(hits)-100} more hits omitted")

    header = [
        "TRAVELMAP CACHE SCAN",
        f"PACKAGE_ROOT={PKG}",
        f"FILES_SCANNED={total_files}",
        f"FILES_WITH_HITS={hit_files}",
        f"CANDIDATE_URL_COUNT={len(all_urls)}",
        "",
        "CANDIDATE URLS (deduplicated):",
    ]
    header.extend("  " + u for u in sorted(all_urls))
    header.append("")
    report = "\n".join(header + sections) + "\n"
    OUT.write_text(report, encoding="utf-8")

    print(f"扫描文件数: {total_files}")
    print(f"命中文件数: {hit_files}")
    print(f"候选 URL 数: {len(all_urls)}")
    print(f"报告: {OUT}")
    if all_urls:
        print("\n候选 URL:")
        for u in sorted(all_urls):
            print(" ", u)
    else:
        print("\n没有从明文/二进制缓存中直接抽出候选 URL。")
        print("仍请把报告中 FILE/KEYWORD HITS 部分发回来；下一步可针对 Chromium Cache_Data 做格式解析。")


if __name__ == "__main__":
    main()
