"""Build the Windows launcher and server into one portable directory."""

import shutil
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".build-deps"))


def main():
    for name in ("FrogLauncher.exe", "FrogServer.exe"):
        existing = ROOT / "release" / "TravelFrog" / name
        if existing.exists():
            try:
                handle = os.open(existing, os.O_RDWR)
            except PermissionError:
                raise SystemExit(f"请先关闭发布目录中的 {name}，再重新构建。")
            os.close(handle)
    from PyInstaller.__main__ import run
    run(["--noconfirm", "--distpath", str(ROOT / ".build" / "windows-dist"),
         "--workpath", str(ROOT / ".build" / "windows"), str(ROOT / "packaging" / "windows.spec")])
    out = ROOT / "release" / "TravelFrog"
    shutil.copytree(ROOT / ".build" / "windows-dist" / "TravelFrog", out, dirs_exist_ok=True)
    shutil.copyfile(ROOT / "packaging" / "USER-GUIDE.md", out / "使用说明.md")
    from build_apks import release_client, write_bootstrap
    game = out / "web" / "game"
    shutil.copytree(ROOT / ".build" / "android" / "assets" / "game", game, dirs_exist_ok=True)
    shutil.copyfile(ROOT / "packaging" / "android" / "offline-shell.js", game / "js" / "offline-shell.js")
    for source, target in (("main.offline.stage6i2.js", "main.normal.js"), ("main.gallery.js", "main.gallery.js")):
        (game / "js" / target).write_text(release_client(source), encoding="utf-8")
    write_bootstrap(game, desktop=True)
    icon = ROOT / ".build" / "android" / "res" / "drawable" / "icon.png"
    if icon.exists():
        shutil.copyfile(icon, out / "icon.png")
    print(out / "FrogLauncher.exe")


if __name__ == "__main__":
    main()
