from pathlib import Path
from datetime import datetime
import re
import shutil
import sys

ROOT = Path(__file__).resolve().parents[3]
CLIENT = ROOT / r"client\js\main.offline.stage6i2.js"

if not CLIENT.exists():
    raise SystemExit(f"ERROR: missing client: {CLIENT}")

s = CLIENT.read_text(encoding="utf-8", errors="strict")

# Idempotent: if the exact A6 force-persist implementation is already present, exit cleanly.
if (
    "OFFLINE_TRAVEL_DIARY_6H3A6" in s
    and 'send("client_set_client",null,JSON.stringify(n.clientSettings))' in s
    and "OFFLINE_TRAVEL_DIARY_SAVE_SENT entries=" in s
):
    print("Stage 6H.3A6 force-persist already applied; no changes made.")
    sys.exit(0)

# Structural checks only — do NOT require a specific previous A4/A5 marker.
store_start = s.find("var TravelDiaryStore=function(){")
map_start = s.find("var TravelMapStaticView=function(e){")
if store_start < 0:
    raise SystemExit("ERROR: TravelDiaryStore not found")
if map_start < 0 or map_start <= store_start:
    raise SystemExit("ERROR: TravelMapStaticView boundary not found after TravelDiaryStore")

store_region = s[store_start:map_start]

save_start_rel = store_region.find("e.save=function(t){")
picture_meta_rel = store_region.find("e.pictureMeta=function", save_start_rel)
if save_start_rel < 0 or picture_meta_rel < 0:
    raise SystemExit(
        "ERROR: could not structurally locate TravelDiaryStore.save "
        f"(save={save_start_rel}, pictureMeta={picture_meta_rel})"
    )

save_start = store_start + save_start_rel
save_end = store_start + picture_meta_rel

# Find whichever diary runtime marker is currently installed (A1/A2/A3/A4/A5/A6...).
marker_re = re.compile(r'core\.Log\.print\("(OFFLINE_TRAVEL_DIARY_6H3A\d+)"\)')
markers = marker_re.findall(s)
if len(markers) != 1:
    print("Detected diary markers:", markers)
    raise SystemExit(f"ERROR: expected exactly one Travel Diary runtime marker, found {len(markers)}")

current_marker = markers[0]
print("current_marker =", current_marker)

# Sanity checks for important already-working pieces.
required = {
    "travelmap_v9": "OFFLINE_STATIC_TRAVELMAP_V9",
    "native_input": "egret.TextFieldType.INPUT",
    "photo_pipeline": "Tabikaeru.loadPicture(e)",
    "diary_store": "TravelDiaryStore",
}
for name, needle in required.items():
    if needle not in s:
        raise SystemExit(f"ERROR: baseline sanity check failed: {name} missing")

stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
backup_dir = ROOT / "backup" / f"stage6h3a6_diary_force_persist_robust_{stamp}"
backup_dir.mkdir(parents=True, exist_ok=False)
shutil.copy2(CLIENT, backup_dir / CLIENT.name)

# Replace only TravelDiaryStore.save, independent of whitespace/minification/previous patch version.
new_save = (
    'e.save=function(t){'
    't.version=1,t.next_id=t.next_id||1;'
    'var i=JSON.parse(JSON.stringify(t)),n=core.ModelManage.getInstance().getModel(UserModel);'
    'n.clientSettings.travelDiary=i,'
    'core.SocketManage.getInstance().send("client_set_client",null,JSON.stringify(n.clientSettings)),'
    'core.Log.print("OFFLINE_TRAVEL_DIARY_SAVE_SENT entries="+i.entries.length),'
    'i'
    '},'
)

s = s[:save_start] + new_save + s[save_end:]

# Promote whatever current diary marker exists to A6.
s, replaced = marker_re.subn('core.Log.print("OFFLINE_TRAVEL_DIARY_6H3A6")', s, count=1)
if replaced != 1:
    raise SystemExit(f"ERROR: failed to replace diary runtime marker, replaced={replaced}")

CLIENT.write_text(s, encoding="utf-8")

# Verify from the file we actually wrote.
v = CLIENT.read_text(encoding="utf-8", errors="strict")
checks = {
    "a6_marker": v.count("OFFLINE_TRAVEL_DIARY_6H3A6") == 1,
    "direct_settings_update": "n.clientSettings.travelDiary=i" in v,
    "direct_protocol_send": 'send("client_set_client",null,JSON.stringify(n.clientSettings))' in v,
    "save_log": "OFFLINE_TRAVEL_DIARY_SAVE_SENT entries=" in v,
    "travelmap_v9_preserved": v.count("OFFLINE_STATIC_TRAVELMAP_V9") == 1,
    "native_input_preserved": "egret.TextFieldType.INPUT" in v,
    "photo_pipeline_preserved": "Tabikaeru.loadPicture(e)" in v,
    "drift_suppression_preserved": "dm.type==Mail.EvtId.Drift" in v,
}
for k, ok in checks.items():
    print(f"{k}={ok}")

if not all(checks.values()):
    raise SystemExit("ERROR: Stage 6H.3A6 robust verification failed")

print("PATCH STAGE 6H.3A6 DIARY FORCE-PERSIST ROBUST OK")
print(f"backup={backup_dir}")
