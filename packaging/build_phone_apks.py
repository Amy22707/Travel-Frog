"""Package the phone-local runtime and assets into independent Android apps."""

import argparse
import os
import shutil
import subprocess
import urllib.request
import zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SIGNER = ROOT / "tmp/uber-apk-signer-1.3.0.jar"
PASSWORD_ENV = "FROG_ANDROID_KEYSTORE_PASSWORD"


def sign_release(java, apk, keystore, alias, work):
    with zipfile.ZipFile(SIGNER) as signer:
        for name in ("win-zipalign_33_0_2.exe", "binary-lib/windows-33_0_2/libwinpthread-1.dll"):
            (work / Path(name).name).write_bytes(signer.read(name))
    aligned = work / "aligned.apk"
    signed = work / "signed.apk"
    zipalign = str(work / "win-zipalign_33_0_2.exe")
    subprocess.run([zipalign, "-f", "4", str(apk), str(aligned)], check=True)
    command = [str(java), "-cp", str(SIGNER), "com.android.apksigner.ApkSignerTool"]
    subprocess.run([*command, "sign", "--ks", str(keystore), "--ks-key-alias", alias,
                    "--ks-pass", "env:" + PASSWORD_ENV, "--out", str(signed), str(aligned)], check=True)
    subprocess.run([*command, "verify", "--verbose", "--print-certs", str(signed)], check=True)
    subprocess.run([zipalign, "-c", "4", str(signed)], check=True)
    return signed


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--java",type=Path,default=Path(shutil.which("java") or "java"))
    parser.add_argument("--edition",choices=["normal","gallery","both"],default="both")
    parser.add_argument("--release-keystore", type=Path, help="Build signed candidates into release/AndroidRelease.")
    parser.add_argument("--key-alias", default="travelfrog")
    args=parser.parse_args()
    if args.release_keystore and (not args.release_keystore.is_file() or not os.environ.get(PASSWORD_ENV)):
        parser.error(f"--release-keystore requires an existing keystore and {PASSWORD_ENV}.")
    deps=ROOT/".build/android-java"
    deps.mkdir(parents=True,exist_ok=True)
    dependencies={
        "ecj.jar":"org/eclipse/jdt/ecj/3.26.0/ecj-3.26.0.jar",
        "android.jar":"org/robolectric/android-all/4.4_r1-robolectric-r2/android-all-4.4_r1-robolectric-r2.jar",
        "dx.jar":"com/google/android/tools/dx/1.7/dx-1.7.jar",
    }
    for filename,artifact in dependencies.items():
        target=deps/filename
        if not target.exists():
            with urllib.request.urlopen("https://repo.maven.apache.org/maven2/"+artifact,timeout=120) as response,target.open("wb") as output:
                shutil.copyfileobj(response,output)
    work=ROOT/".build/phone-android"
    classes=work/"classes"
    classes.mkdir(parents=True,exist_ok=True)
    sources=list((ROOT/"mobile/android").glob("*.java"))
    subprocess.run([str(args.java),"-jar",str(deps/"ecj.jar"),"-1.6","-nowarn","-cp",str(deps/"android.jar"),
                    "-d",str(classes),*[str(p) for p in sources]],check=True)
    project=work/"apk"
    project.mkdir(parents=True,exist_ok=True)
    subprocess.run([str(args.java),"-cp",str(deps/"dx.jar"),"com.android.dx.command.Main","--dex",
                    "--output="+str(project/"classes.dex"),str(classes)],check=True)
    shutil.copytree(ROOT/"release/TravelFrogMobile",project/"assets/mobile",dirs_exist_ok=True)
    (project/"res/drawable").mkdir(parents=True,exist_ok=True)
    shutil.copyfile(ROOT/".build/android/res/drawable/icon.png",project/"res/drawable/icon.png")
    (project/"apktool.yml").write_text('''version: 2.12.1
apkFileName: FrogPhone.apk
usesFramework:
  ids:
  - 1
sdkInfo:
  minSdkVersion: 24
  targetSdkVersion: 28
packageInfo:
  forcedPackageId: 127
versionInfo:
  versionCode: 4
  versionName: 2.0.2
doNotCompress:
- arsc
- png
- jpg
- mp3
- eab
- wasm
''',encoding="utf-8")
    out = ROOT / "release" / ("AndroidRelease" if args.release_keystore else "Android")
    out.mkdir(parents=True,exist_ok=True)
    package_prefix = "org.travelfrog.china" if args.release_keystore else "org.travelfrog.offline"
    for edition in ("normal","gallery"):
        if args.edition!="both" and args.edition!=edition:continue
        name="FrogGallery" if edition=="gallery" else "FrogNormal"
        label="旅行青蛙·中国之旅"+("图鉴版" if edition=="gallery" else "离线版")
        (project/"AndroidManifest.xml").write_text(f'''<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="{package_prefix}.{edition}">
<uses-permission android:name="android.permission.INTERNET"/>
<application android:label="{label}" android:icon="@drawable/icon" android:allowBackup="false"
android:usesCleartextTraffic="true" android:hardwareAccelerated="true" android:theme="@android:style/Theme.Material.Light.NoActionBar.Fullscreen">
<activity android:name="org.travelfrog.offline.MainActivity" android:exported="true" android:screenOrientation="portrait"
android:configChanges="keyboardHidden|orientation|screenSize" android:launchMode="singleTask">
<intent-filter><action android:name="android.intent.action.MAIN"/><category android:name="android.intent.category.LAUNCHER"/></intent-filter>
</activity></application></manifest>''',encoding="utf-8")
        apk = work / (name + ".apk")
        subprocess.run([str(args.java),"-jar",str(ROOT/"tmp/apktool_2.12.1.jar"),"b",str(project),"-o",str(apk)],check=True)
        if args.release_keystore:
            apk = sign_release(args.java, apk, args.release_keystore, args.key_alias, work)
        else:
            subprocess.run([str(args.java),"-jar",str(SIGNER),"--apks",str(apk),"--overwrite"],check=True)
        target = out / (name + ".apk")
        apk.replace(target)
        print(target,flush=True)


if __name__=="__main__":main()
