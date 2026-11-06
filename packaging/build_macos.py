"""Build the macOS app bundles from a verified Windows release and the shared server."""

import argparse
import platform
import plistlib
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[1]
EDITIONS = {"normal": ("TravelFrogNormal", "旅行青蛙·中国之旅"),
            "gallery": ("TravelFrogGallery", "旅行青蛙·中国之旅图鉴版")}


def run(*args):
    subprocess.run([str(arg) for arg in args], check=True, cwd=ROOT)


def release_files(archive, game, shared, folder):
    with zipfile.ZipFile(archive) as package:
        for entry in package.infolist():
            parts = PurePosixPath(entry.filename).parts
            if entry.is_dir() or not parts or parts[0] != folder or ".." in parts:
                continue
            relative = parts[1:]
            if relative[:2] == ("web", "game"):
                target = game.joinpath(*relative[2:])
            elif relative[:3] == ("_internal", "server", "config") or relative == ("_internal", "client", "config", "config.eab"):
                target = shared.joinpath(*relative[1:])
            else:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with package.open(entry) as source, target.open("wb") as destination:
                shutil.copyfileobj(source, destination)
    for path in (game / "index.html", game / "icon.png", shared / "client/config/config.eab", shared / "server/config/Item_json.json"):
        if not path.is_file():
            raise ValueError(f"Missing expected release file: {path.name}")


def build_icon(source, resources, work):
    from PIL import Image

    iconset = work / "GameIcon.iconset"
    iconset.mkdir(exist_ok=True)
    with Image.open(source) as original:
        for size in (16, 32, 128, 256, 512):
            for scale in (1, 2):
                suffix = "@2x" if scale == 2 else ""
                original.resize((size * scale, size * scale), Image.Resampling.LANCZOS).save(iconset / f"icon_{size}x{size}{suffix}.png")
    run("iconutil", "-c", "icns", iconset, "-o", resources / "GameIcon.icns")


def configure_game(game):
    shell = game / "js/offline-shell.js"
    js = shell.read_text(encoding="utf-8")
    old = 'manage.onclick = function () { fetch("/__desktop/saves", {method: "POST"}); };'
    if js.count(old) != 1:
        raise ValueError("Unexpected desktop save button in the release assets")
    js = js.replace(old, "manage.onclick = function () { window.FrogDesktop.showSaves(); };")
    shell.write_text(js, encoding="utf-8")


def package(edition, archive_dir, output, work, version):
    folder, title = EDITIONS[edition]
    architecture = platform.machine()
    stage = work / edition / "image"
    app = stage / f"{title}.app"
    contents = app / "Contents"
    resources = contents / "Resources"
    game = resources / "game"
    macos = contents / "MacOS"
    macos.mkdir(parents=True)
    shared = work / "game-config"
    release_files(archive_dir / f"{folder}-Windows.zip", game, shared, folder)
    configure_game(game)
    shutil.copyfile(ROOT / "desktop/mac-scroll.js", resources / "mac-scroll.js")
    build_icon(game / "icon.png", resources, work)
    plist = {"CFBundleExecutable": "TravelFrog", "CFBundleIdentifier": f"org.travelfrog.offline.macos.{edition}",
             "CFBundleName": title, "CFBundleDisplayName": title, "CFBundlePackageType": "APPL",
             "CFBundleShortVersionString": version, "CFBundleVersion": version, "CFBundleIconFile": "GameIcon.icns",
             "LSMinimumSystemVersion": "13.0", "NSHighResolutionCapable": True, "FrogEdition": edition,
             "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True,
                                        "NSExceptionDomains": {"127.0.0.1": {"NSExceptionAllowsInsecureHTTPLoads": True}}}}
    (contents / "Info.plist").write_bytes(plistlib.dumps(plist))
    run("swiftc", "-swift-version", "5", "-parse-as-library", "-O", "-target", f"{architecture}-apple-macos13.0",
        ROOT / "desktop/MacApp.swift", "-o", macos / "TravelFrog")

    runtime = work / "runtime-dist/FrogRuntime"
    if not runtime.is_dir():
        run(sys.executable, "-m", "PyInstaller", "--noconfirm", "--onedir", "--name", "FrogRuntime",
            "--paths", ROOT, "--workpath", work / "runtime-build", "--specpath", work,
            "--distpath", runtime.parent,
            "--add-data", f"{shared / 'server/config'}:server/config",
            "--add-data", f"{shared / 'client/config/config.eab'}:client/config",
            "--add-data", f"{ROOT / 'server/offline_policy.json'}:server",
            ROOT / "desktop/mac_runtime.py")
    shutil.copytree(runtime, resources / "runtime", symlinks=True)
    run("codesign", "--force", "--sign", "-", app)
    run("codesign", "--verify", "--deep", "--strict", app)
    (stage / "Applications").symlink_to("/Applications")

    image = output / f"{folder}-macOS-{architecture}.dmg"
    run("hdiutil", "create", "-ov", "-fs", "HFS+", "-format", "UDZO", "-volname", title, "-srcfolder", stage, image)
    run("hdiutil", "verify", image)
    print(image, flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archives", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--version", default="0.2.4")
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("Build these disk images on macOS")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="frog-macos-") as temp:
        for edition in EDITIONS:
            package(edition, args.archives.resolve(), output, Path(temp), args.version)


if __name__ == "__main__":
    main()
