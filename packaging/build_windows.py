"""Build the Windows launcher and server into one portable directory."""

import shutil
import os
import sys
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".build-deps"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--edition", choices=["normal", "gallery", "both"], default="both")
    args = parser.parse_args()
    editions = ("normal", "gallery") if args.edition == "both" else (args.edition,)
    from PIL import Image
    icon = ROOT / ".build/android/res/drawable/icon.png"
    Image.open(icon).save(ROOT / ".build/icon.ico", sizes=[(16,16),(32,32),(48,48),(64,64),(128,128),(256,256)])
    from PyInstaller.__main__ import run
    from build_apks import release_client, write_bootstrap
    for edition in editions:
        name = "TravelFrogGallery" if edition == "gallery" else "TravelFrogNormal"
        exe = "FrogGallery.exe" if edition == "gallery" else "FrogNormal.exe"
        out = ROOT / "release" / name
        for file in (exe, "FrogServer.exe"):
            existing = out / file
            if existing.exists():
                try:
                    handle = os.open(existing, os.O_RDWR)
                except PermissionError:
                    raise SystemExit(f"请先关闭 {existing} 再构建。")
                os.close(handle)
        os.environ["FROG_BUILD_EDITION"] = edition
        run(["--noconfirm", "--distpath", str(ROOT / ".build/windows-dist"),
             "--workpath", str(ROOT / ".build" / ("windows-" + edition)), str(ROOT / "packaging/windows.spec")])
        shutil.copytree(ROOT / ".build/windows-dist" / name, out, dirs_exist_ok=True)
        shutil.copyfile(ROOT / "packaging/USER-GUIDE.md", out / "使用说明.md")
        (out / "存档管理.cmd").write_text(f'@echo off\r\nstart "" "%~dp0{exe}" --manage\r\n', encoding="ascii")
        game = out / "web/game"
        shutil.copytree(ROOT / ".build/android/assets/game", game, dirs_exist_ok=True)
        shutil.copyfile(ROOT / "packaging/android/offline-shell.js", game / "js/offline-shell.js")
        source = "main.gallery.js" if edition == "gallery" else "main.offline.stage6i2.js"
        target = "main.gallery.js" if edition == "gallery" else "main.normal.js"
        (game / "js" / target).write_text(release_client(source), encoding="utf-8")
        shutil.copyfile(icon, game / "icon.png")
        write_bootstrap(game, desktop=True)
        shutil.copyfile(icon, out / "icon.png")
        print(out / exe, flush=True)


if __name__ == "__main__":
    main()
