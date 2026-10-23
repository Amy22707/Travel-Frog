"""Build local-resource APKs for the two Windows-server clients."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def release_client(filename):
    code = (ROOT / "client" / "js" / filename).read_text(encoding="utf-8")
    port = 8081 if filename == "main.gallery.js" else 8080
    old = f'"ws://127.0.0.1:{port}"'
    if code.count(old) != 4:
        raise ValueError(f"Unexpected offline endpoint count in {filename}")
    code = code.replace(old, f'(window.FROG_SERVER_URL||{old})')
    return code.replace('return t.timeout=2e3,t', 'return t.timeout=15e3,t')


def write_bootstrap(game, desktop=False):
    manifest = json.loads((game / "manifest.json").read_text(encoding="utf-8"))
    scripts = manifest["initial"] + ["js/default.thm.js"]
    if not desktop:
        scripts += ["js/main.min.js", "js/offline-shell.js"]
    tags = "\n".join(f'<script src="{path}"></script>' for path in scripts)
    if desktop:
        tags += '''<script>
var query=new URLSearchParams(location.search);
window.FROG_SERVER_URL="ws://127.0.0.1:"+Number(query.get("serverPort"));
var main=document.createElement("script");
main.src=query.get("edition")==="gallery"?"js/main.gallery.js":"js/main.normal.js";
main.onload=function(){var shim=document.createElement("script");shim.src="js/offline-shell.js";document.body.appendChild(shim);};
document.body.appendChild(main);
</script>'''
    html = '''<!doctype html><html><head><meta charset="utf-8"><title>旅行青蛙·中国之旅</title>
<link rel="icon" href="data:,">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<style>html,body{margin:0;padding:0;width:100%;height:100%;overflow:hidden;background:#e9e6c9}</style>
</head><body><div class="egret-player" data-entry-class="Main" data-orientation="portrait"
data-scale-mode="fixedHeight" data-frame-rate="60" data-content-width="640" data-content-height="1136"
data-multi-fingered="1" data-show-fps="false" style="width:100%;height:100%"></div>
<script>window.gameLanguage="China";window.cdn="";window.appVersion="offline";
window.manifestVersion="1088";window.manifestBuild=1;window.resUrl=null;
window.thmUrl="resource/China/default.thm.json";window.frogCode=null;window.frogArgs=null;</script>
''' + tags + "</body></html>"
    (game / "index.html").write_text(html, encoding="utf-8")


def cached_json(directory, required):
    for file in directory.iterdir():
        if not file.is_file():
            continue
        try:
            value = json.loads(file.read_text(encoding="utf-8"))
        except (ValueError, UnicodeError):
            continue
        if required in value:
            return value
    raise FileNotFoundError(f"Missing {required} in {directory}")


def assemble_assets(game, base_apk, mirror, cache):
    game.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(base_apk) as z:
        for name in z.namelist():
            if name.startswith("assets/game/") and not name.endswith("/"):
                rel = Path(name.removeprefix("assets/game/"))
                target = game / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(z.read(name))
    patch = cached_json(cache / "1088" / "patch.json#", "__version__")
    missing = []
    for rel, entry in patch.items():
        if not isinstance(entry, dict) or "0" not in entry:
            continue
        source = mirror / str(entry["0"]) / rel
        if not source.exists():
            source = cache / str(entry["0"]) / (rel + "#") / str(entry["1"])
        if not source.is_file():
            missing.append(rel)
            continue
        target = game / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    if missing:
        raise FileNotFoundError(f"Hotfix resources missing ({len(missing)}): {missing[:8]}")
    for name in ("config.eab", "reward.eab"):
        source = ROOT / "client" / "config" / name
        if source.exists():
            shutil.copyfile(source, game / "resource" / "China" / "eab" / name)
    shutil.copyfile(ROOT / "client" / "config" / "default.res.json", game / "resource" / "China" / "default.res.json")
    shutil.copyfile(ROOT / "client" / "js" / "default.thm.js", game / "js" / "default.thm.js")
    shutil.copyfile(ROOT / "packaging" / "android" / "offline-shell.js", game / "js" / "offline-shell.js")
    write_bootstrap(game)
    config = game / "resource" / "China" / "gameConfig.json"
    if config.exists():
        value = json.loads(config.read_text(encoding="utf-8"))
        value.update(channelType=1, showLog=False, showGM=False)
        config.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    print(f"Local resources assembled: {len(patch)} patch entries", flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--java", type=Path, default=Path(r"C:\Program Files\Java\jre1.8.0_503\bin\java.exe"))
    p.add_argument("--base-apk", type=Path, default=ROOT / "apk" / "base.apk")
    p.add_argument("--mirror", type=Path, default=ROOT / "backup" / "hotfix_mirror_1088")
    p.add_argument("--cache", type=Path, default=ROOT / "hotfix" / "c1_client" / "c1_client" / "release" / "lingxi" / "android")
    p.add_argument("--out", type=Path, default=ROOT / "release" / "TravelFrog" / "apks")
    p.add_argument("--reuse-assets", action="store_true")
    p.add_argument("--edition", choices=["normal", "gallery", "both"], default="both")
    args = p.parse_args()
    work = ROOT / ".build" / "android"
    game = work / "assets" / "game"
    if not args.reuse_assets:
        assemble_assets(game, args.base_apk, args.mirror, args.cache)
    else:
        shutil.copyfile(ROOT / "packaging" / "android" / "offline-shell.js", game / "js" / "offline-shell.js")
        write_bootstrap(game)
    smali = work / "smali" / "org" / "travelfrog" / "offline"
    smali.mkdir(parents=True, exist_ok=True)
    for name in ("MainActivity.smali", "ConsoleClient.smali"):
        shutil.copyfile(ROOT / "packaging" / "android" / name, smali / name)
    drawable = work / "res" / "drawable"
    drawable.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.base_apk) as z:
        icon = next(name for name in z.namelist() if name.startswith("res/drawable") and name.endswith("/icon2.png"))
        (drawable / "icon.png").write_bytes(z.read(icon))
    (work / "apktool.yml").write_text('''version: 2.12.1
apkFileName: Frog.apk
usesFramework:
  ids:
  - 1
sdkInfo:
  minSdkVersion: 24
  targetSdkVersion: 28
packageInfo:
  forcedPackageId: 127
versionInfo:
  versionCode: 1
  versionName: 1.0.0
doNotCompress:
- arsc
- png
- jpg
- mp3
- eab
''', encoding="utf-8")
    args.out.mkdir(parents=True, exist_ok=True)
    for edition, label, source, filename in (
        ("normal", "旅行青蛙·中国之旅离线版", "main.offline.stage6i2.js", "FrogNormal"),
        ("gallery", "旅行青蛙·中国之旅图鉴版", "main.gallery.js", "FrogGallery"),
    ):
        if args.edition != "both" and edition != args.edition:
            continue
        (work / "AndroidManifest.xml").write_text(f'''<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="org.travelfrog.offline.{edition}">
<uses-permission android:name="android.permission.INTERNET"/>
<application android:label="{label}" android:icon="@drawable/icon" android:allowBackup="false"
android:usesCleartextTraffic="true" android:theme="@android:style/Theme.Material.Light.NoActionBar.Fullscreen"
android:hardwareAccelerated="true">
<activity android:name="org.travelfrog.offline.MainActivity" android:exported="true" android:screenOrientation="portrait"
android:launchMode="singleTask" android:configChanges="keyboardHidden|orientation|screenSize" android:windowSoftInputMode="adjustResize">
<intent-filter><action android:name="android.intent.action.MAIN"/><category android:name="android.intent.category.LAUNCHER"/></intent-filter>
</activity></application></manifest>''', encoding="utf-8")
        (game / "js" / "main.min.js").write_text(release_client(source), encoding="utf-8")
        unsigned = work / (filename + "-unsigned.apk")
        subprocess.run([str(args.java), "-jar", str(ROOT / "tmp" / "apktool_2.12.1.jar"), "b", str(work), "-o", str(unsigned)], check=True)
        subprocess.run([str(args.java), "-jar", str(ROOT / "tmp" / "uber-apk-signer-1.3.0.jar"),
                        "--apks", str(unsigned), "--overwrite"], check=True)
        target = args.out / (filename + ".apk")
        shutil.copyfile(unsigned, target)
        print(f"Built {target} ({target.stat().st_size / 1048576:.1f} MiB)", flush=True)


if __name__ == "__main__":
    main()
