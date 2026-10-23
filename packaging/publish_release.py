"""Upload local builds to a draft release; publishing requires --publish."""

import argparse
import json
import subprocess
import urllib.request
import urllib.parse
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
REPO="Amy22707/Travel-Frog"


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag",default="v0.2.0")
    parser.add_argument("--publish",action="store_true",help="Publish after upload; otherwise keep the release as a draft.")
    args=parser.parse_args()
    credential=subprocess.run(["git","credential","fill"],input="protocol=https\nhost=github.com\n\n",
                              text=True,capture_output=True,check=True)
    values=dict(line.split("=",1) for line in credential.stdout.splitlines() if "=" in line)
    token=values["password"]
    def request(url,method="GET",data=None,extra=None):
        headers={"Authorization":"Bearer "+token,"Accept":"application/vnd.github+json","User-Agent":"TravelFrog-release"}
        if extra:headers.update(extra)
        if isinstance(data,dict):data=json.dumps(data).encode();headers["Content-Type"]="application/json"
        with urllib.request.urlopen(urllib.request.Request(url,data=data,headers=headers,method=method),timeout=600) as response:
            return json.load(response)
    files=[ROOT/"release/TravelFrogNormal-Windows.zip",ROOT/"release/TravelFrogGallery-Windows.zip",
           ROOT/"release/Android/FrogNormal.apk",ROOT/"release/Android/FrogGallery.apk",
           ROOT/"release/TravelFrogWeb.zip"]
    for file in files:
        if not file.is_file():raise FileNotFoundError(file)
    api=f"https://api.github.com/repos/{REPO}"
    releases=request(api+"/releases")
    release=next((r for r in releases if r["tag_name"]==args.tag),None)
    if release is None:
        release=request(api+"/releases","POST",{"tag_name":args.tag,"target_commitish":"main","name":"旅行青蛙离线版 v0.2.0",
            "body":"Windows 普通版与图鉴版独立程序、手机独立安卓包及网页离线安装。存档请自行导入；各设备通过 .frogsave 手动迁移进度。", "draft":True})
    assets={a["name"]:a for a in release["assets"]}
    upload=release["upload_url"].split("{")[0]
    for file in files:
        if file.name in assets:
            if assets[file.name]["size"]==file.stat().st_size:
                print(f"Already uploaded: {file.name}",flush=True);continue
            raise RuntimeError(f"Release already contains a different {file.name}; choose a new tag.")
        with file.open("rb") as stream:
            request(upload+"?name="+urllib.parse.quote(file.name),"POST",stream,
                    {"Content-Type":"application/octet-stream","Content-Length":str(file.stat().st_size)})
        print(f"Uploaded {file.name}",flush=True)
    result=request(api+"/releases/"+str(release["id"]),"PATCH",{"draft":not args.publish})
    print("Published" if args.publish else "Draft retained",flush=True)
    print(result["html_url"],flush=True)


if __name__=="__main__":main()
