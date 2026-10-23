from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
CLIENT = ROOT / r"client\js\main.offline.stage6i2.js"
OUTDIR = ROOT / r"capture\travelmap\static_map"
OUTDIR.mkdir(parents=True, exist_ok=True)

if not CLIENT.exists():
    raise SystemExit(f"ERROR: missing client: {CLIENT}")

s = CLIENT.read_text(encoding="utf-8", errors="replace")

queries = [
    ("TravelMapController", r"TravelMapController"),
    ("room_map_handler", r"on_enterTravelMap_tap"),
    ("album_travelMapBtn", r"travelMapBtn"),
    ("EncyTravelViewControl", r"EncyTravelViewControl"),
    ("PictureDB", r"PictureDB"),
    ("GoalNumberDB", r"GoalNumberDB"),
    ("popup_addChild", r"getPopupLayer\(\)\.addChild"),
    ("eui_Image", r"new eui\.Image"),
    ("eui_Button", r"new eui\.Button"),
]

chunks = [f"FILE={CLIENT}\nSIZE={len(s)}\n"]
for label, pat in queries:
    ms = list(re.finditer(pat, s))
    chunks.append(f"\n######## {label} count={len(ms)} ########\n")
    for idx, m in enumerate(ms[:12]):
        lo = max(0, m.start() - 5000)
        hi = min(len(s), m.start() + 9000)
        chunks.append(
            f"\n===== {label} #{idx+1} @ {m.start()} =====\n"
            + s[lo:hi]
            + "\n"
        )

context_path = OUTDIR / "static_map_context.txt"
context_path.write_text("".join(chunks), encoding="utf-8")

# Search likely reusable image assets. This is intentionally filename-based and read-only.
exts = {".png", ".jpg", ".jpeg", ".webp"}
keywords = (
    "map", "travel", "china", "province", "marker", "location", "city",
    "ditu", "dili", "lvxing", "zuobiao", "dingwei"
)
hits = []
for base in [ROOT / "client", ROOT / "server", ROOT / "backup"]:
    if not base.exists():
        continue
    for p in base.rglob("*"):
        if not p.is_file() or p.suffix.lower() not in exts:
            continue
        low = p.name.lower()
        if any(k in low for k in keywords):
            try:
                size = p.stat().st_size
            except OSError:
                size = -1
            hits.append((str(p), size))

hits.sort(key=lambda x: x[0].lower())
asset_path = OUTDIR / "static_map_assets.txt"
asset_path.write_text(
    "\n".join(f"{size}\t{path}" for path, size in hits),
    encoding="utf-8"
)

print("EXTRACT OK")
print("context =", context_path)
print("assets  =", asset_path)
print("asset_hits =", len(hits))
