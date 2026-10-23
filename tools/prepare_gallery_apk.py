"""Prepare a side-by-side gallery APK from the user's local decoded client."""

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
decoded = ROOT / "tmp" / "gallery_apk_decode"
manifest_path = decoded / "AndroidManifest.xml"
original_package = "com.aligames.lxqw.hhb"
gallery_package = "com.aligames.lxqw.hhb.gallery"

manifest = manifest_path.read_text(encoding="utf-8")
if manifest.count(f'package="{original_package}"') != 1:
    raise SystemExit("unexpected package declaration")
manifest = manifest.replace(f'package="{original_package}"', f'package="{gallery_package}"', 1)
manifest = manifest.replace('android:label="@string/app_name"', 'android:label="旅行青蛙·中国之旅图鉴版"', 1)
main_activity = (
    '<activity android:configChanges="keyboardHidden|orientation|screenLayout|screenSize|smallestScreenSize" '
    'android:name="com.Aligames.lxqw.MainActivity" android:screenOrientation="portrait">'
)
manifest = manifest.replace(
    main_activity,
    main_activity.replace(' android:name=', ' android:launchMode="singleTask" android:name='),
    1,
)
for attribute in ("authorities", "permission", "readPermission", "writePermission", "host"):
    manifest = manifest.replace(f'android:{attribute}="{original_package}', f'android:{attribute}="{gallery_package}')
for tag in ("permission", "uses-permission", "category"):
    manifest = re.sub(
        rf'(<{tag}\b[^>]*android:name="){re.escape(original_package)}',
        rf'\1{gallery_package}',
        manifest,
    )
manifest_path.write_text(manifest, encoding="utf-8")

client = ROOT / "client" / "js" / "main.gallery.js"
target = decoded / "assets" / "game" / "js" / "main.min.js"
target.write_bytes(client.read_bytes())
print(f"package: {gallery_package}")
print(f"label: 旅行青蛙·中国之旅图鉴版")
print(f"client: {target}")
