#!/usr/bin/env python3
import argparse
import hashlib
import json
import os
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

def md5_file(path: Path) -> str:
    h = hashlib.md5()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def download_one(base_url, relpath, meta, out_root, retries=4):
    version = str(meta["0"])
    expected_md5 = str(meta["1"]).lower()
    expected_size = int(meta["2"])

    dest = out_root / version / Path(relpath)
    dest.parent.mkdir(parents=True, exist_ok=True)

    if dest.exists() and dest.stat().st_size == expected_size:
        if md5_file(dest).lower() == expected_md5:
            return ("skip", relpath, expected_size, "")

    # Preserve slashes while URL-encoding non-ASCII/special characters.
    quoted_path = urllib.parse.quote(relpath.replace("\\", "/"), safe="/-_.~")
    url = base_url.rstrip("/") + "/" + urllib.parse.quote(version, safe="") + "/" + quoted_path
    tmp = dest.with_suffix(dest.suffix + ".part")

    last_err = ""
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as r, tmp.open("wb") as f:
                while True:
                    chunk = r.read(1024 * 256)
                    if not chunk:
                        break
                    f.write(chunk)

            actual_size = tmp.stat().st_size
            if actual_size != expected_size:
                raise RuntimeError(f"size mismatch expected={expected_size} actual={actual_size}")

            actual_md5 = md5_file(tmp).lower()
            if actual_md5 != expected_md5:
                raise RuntimeError(f"md5 mismatch expected={expected_md5} actual={actual_md5}")

            os.replace(tmp, dest)
            return ("ok", relpath, expected_size, url)
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass
            if attempt < retries:
                time.sleep(min(2 ** attempt, 8))

    return ("fail", relpath, expected_size, f"{url} | {last_err}")

def main():
    ap = argparse.ArgumentParser(
        description="Mirror the complete Travel Frog China hotfix resource set listed by patch.json."
    )
    ap.add_argument("patch_json", type=Path, help="Path to 1088/patch.json")
    ap.add_argument("--out", type=Path, required=True, help="Mirror output directory")
    ap.add_argument("--workers", type=int, default=8, help="Concurrent downloads (default 8)")
    args = ap.parse_args()

    patch = json.loads(args.patch_json.read_text(encoding="utf-8"))
    base_url = patch["__url__"]
    entries = [(k, v) for k, v in patch.items() if not k.startswith("__")]

    total_size = sum(int(v["2"]) for _, v in entries)
    args.out.mkdir(parents=True, exist_ok=True)

    print(f"version={patch.get('__version__')}")
    print(f"files={len(entries)}")
    print(f"total_bytes={total_size}")
    print(f"total_mib={total_size / 1024 / 1024:.2f}")
    print(f"base_url={base_url}")

    ok = skip = fail = bytes_done = 0
    failures = []

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
        futs = {
            ex.submit(download_one, base_url, relpath, meta, args.out): (relpath, meta)
            for relpath, meta in entries
        }
        for i, fut in enumerate(as_completed(futs), 1):
            status, relpath, size, detail = fut.result()
            if status == "ok":
                ok += 1
                bytes_done += size
            elif status == "skip":
                skip += 1
                bytes_done += size
            else:
                fail += 1
                failures.append({"path": relpath, "error": detail})

            if i % 100 == 0 or status == "fail" or i == len(entries):
                print(
                    f"[{i}/{len(entries)}] ok={ok} skip={skip} fail={fail} "
                    f"verified={bytes_done / 1024 / 1024:.1f} MiB"
                )

    manifest = {
        "patch_version": patch.get("__version__"),
        "base_version": patch.get("__base_version__"),
        "base_url": base_url,
        "file_count": len(entries),
        "expected_total_bytes": total_size,
        "verified_ok_downloaded": ok,
        "verified_existing": skip,
        "failed": fail,
        "failures": failures,
    }
    (args.out / "_mirror_result.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"result={args.out / '_mirror_result.json'}")
    if fail:
        print("Some files failed. Run the same command again; verified files will be skipped.")
        raise SystemExit(2)
    print("All files verified successfully.")

if __name__ == "__main__":
    main()
