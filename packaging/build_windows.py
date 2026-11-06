"""Build the Windows launcher and server into one portable directory."""

import shutil
import os
import sys
import argparse
import subprocess
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".build-deps"))
WEBVIEW2_VERSION = "1.0.4314.35"


def build_native_window(icon):
    work = ROOT / ".build/windows-webview"
    work.mkdir(exist_ok=True)
    package = work / f"Microsoft.Web.WebView2.{WEBVIEW2_VERSION}.nupkg"
    if not package.is_file():
        base = "https://api.nuget.org/v3-flatcontainer/microsoft.web.webview2"
        with urllib.request.urlopen(f"{base}/{WEBVIEW2_VERSION}/{package.name.lower()}", timeout=60) as response:
            package.write_bytes(response.read())
    core = work / "Microsoft.Web.WebView2.Core.dll"
    forms = work / "Microsoft.Web.WebView2.WinForms.dll"
    with zipfile.ZipFile(package) as sdk:
        for name, target in (
            (f"lib/net462/{core.name}", core),
            (f"lib/net462/{forms.name}", forms),
            ("runtimes/win-x64/native/WebView2Loader.dll", work / "WebView2Loader.dll"),
        ):
            target.write_bytes(sdk.read(name))
    csc = Path(os.environ["WINDIR"]) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    exe = work / "FrogWindow.exe"
    subprocess.run([str(csc), "/nologo", "/target:winexe", "/platform:x64", "/codepage:65001",
                    f"/out:{exe}", f"/win32icon:{icon}", "/reference:System.Drawing.dll",
                    "/reference:System.Windows.Forms.dll", f"/reference:{core}", f"/reference:{forms}",
                    str(ROOT / "desktop/FrogWindow.cs")], check=True)
    return (exe, core, forms, work / "WebView2Loader.dll")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--edition", choices=["normal", "gallery", "both"], default="both")
    args = parser.parse_args()
    editions = ("normal", "gallery") if args.edition == "both" else (args.edition,)
    from PIL import Image
    icon = ROOT / ".build/android/res/drawable/icon.png"
    icon_ico = ROOT / ".build/icon.ico"
    Image.open(icon).save(icon_ico, sizes=[(16,16),(32,32),(48,48),(64,64),(128,128),(256,256)])
    native_window = build_native_window(icon_ico)
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
        native_dir = out / "_internal/FrogWindow"
        native_dir.mkdir(exist_ok=True)
        for path in native_window:
            shutil.copyfile(path, native_dir / path.name)
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
