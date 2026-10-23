"""Build the self-contained mobile web app and its Python resource archive."""

import json
import shutil
import zipfile
from pathlib import Path
from PIL import Image
from build_apks import release_client, write_bootstrap

ROOT=Path(__file__).resolve().parents[1]


def main():
    out=ROOT/"release"/"TravelFrogMobile"
    out.mkdir(parents=True,exist_ok=True)
    for name in ("index.html","phone.js","worker.js","socket.js","sw.js"):
        shutil.copyfile(ROOT/"mobile"/name,out/name)
    game=out/"game"
    shutil.copytree(ROOT/".build/android/assets/game",game,dirs_exist_ok=True)
    shutil.copyfile(ROOT/"packaging/android/offline-shell.js",game/"js/offline-shell.js")
    for src,dst in (("main.offline.stage6i2.js","main.normal.js"),("main.gallery.js","main.gallery.js")):
        (game/"js"/dst).write_text(release_client(src),encoding="utf-8")
    write_bootstrap(game,desktop=True)
    icon=ROOT/".build/android/res/drawable/icon.png"
    shutil.copyfile(icon,out/"icon.png")
    shutil.copyfile(icon,game/"icon.png")
    for size in (192,512):
        Image.open(icon).resize((size,size)).save(out/f"icon-{size}.png")
    for edition in ("normal","gallery"):
        (out/f"manifest-{edition}.webmanifest").write_text(json.dumps({
            "id":f"frog-{edition}","name":"旅行青蛙·中国之旅"+("图鉴版" if edition=="gallery" else ""),
            "short_name":"旅行青蛙"+("图鉴" if edition=="gallery" else ""),
            "start_url":f"./index.html?edition={edition}","scope":"./","display":"standalone",
            "background_color":"#f6f1de","theme_color":"#526b42",
            "icons":[{"src":f"icon-{size}.png","sizes":f"{size}x{size}","type":"image/png"} for size in (192,512)]
        },ensure_ascii=False),encoding="utf-8")
    shutil.copytree(ROOT/".build/pyodide",out/"pyodide",dirs_exist_ok=True)
    with zipfile.ZipFile(out/"python-app.zip","w",zipfile.ZIP_DEFLATED) as z:
        files=[ROOT/"server/server.py",ROOT/"server/offline_policy.json",ROOT/"desktop/saves.py",
               ROOT/"tools/build_gallery_state.py",ROOT/"tools/resources/scan_eab_cache.py",ROOT/"mobile/runtime.py",
               ROOT/"client/config/config.eab"]
        files += [p for p in (ROOT/"server/config").glob("*.json") if p.name!="album_official_full_20260912.json"]
        for file in files:z.write(file,file.relative_to(ROOT))
        for file in (ROOT/".build-deps/websockets").rglob("*.py"):
            z.write(file,file.relative_to(ROOT/".build-deps"))
        for package in ("server","desktop","tools","tools/resources","mobile"):
            z.writestr(package+"/__init__.py","")
    names=sorted("./"+p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file() and p.name!="offline-files.json")
    (out/"offline-files.json").write_text(json.dumps(names),encoding="utf-8")
    print(f"Mobile app: {out}; offline files: {len(names)}",flush=True)


if __name__=="__main__":main()
