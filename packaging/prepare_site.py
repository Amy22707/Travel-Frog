"""Prepare the public studio page and the device-local game web app."""

import argparse
import shutil
import zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out",type=Path,required=True)
    args=parser.parse_args()
    source=ROOT/"release/TravelFrogMobile"
    forbidden={"state.json","gallery_state.json","launcher-settings.json"}
    for file in source.rglob("*"):
        if file.is_file() and (file.name in forbidden or file.suffix in {".frogsave",".jsonl",".log"}):
            raise RuntimeError(f"Personal data in web output: {file}")
    with zipfile.ZipFile(source/"python-app.zip") as archive:
        if any(Path(name).name in forbidden or name.endswith("album_official_full_20260912.json") for name in archive.namelist()):
            raise RuntimeError("Personal save or historical album in runtime archive")
    args.out.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(ROOT/"mobile/studio.html",args.out/"index.html")
    shutil.copyfile(ROOT/"mobile/studio.en.html",args.out/"en.html")
    shutil.copyfile(ROOT/"mobile/studio.css",args.out/"studio.css")
    shutil.copytree(source,args.out/"play",dirs_exist_ok=True)
    print(args.out,flush=True)


if __name__=="__main__":main()
