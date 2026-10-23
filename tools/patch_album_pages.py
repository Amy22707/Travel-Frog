"""Keep album pagination tied to real stored photos."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for name in ("main.offline.stage6i2.js", "main.gallery.js"):
    path = ROOT / "client" / "js" / name
    code = path.read_text(encoding="utf-8")
    changes = {
        "var e=Math.ceil(this.pictureList.length/this.itemPerPage);this.maxPage.text":
            "var e=Math.max(1,Math.ceil(this.pictureList.length/this.itemPerPage)+this.getModel(ItemModel).getHouseItemCount(9e3));this.maxPage.text",
        "var n=Math.max(1,Math.ceil(this.pictureList.length/this.itemPerPage)),r=this.currentPageIndex;":
            "var n=Math.max(1,Math.ceil(this.pictureList.length/this.itemPerPage)+this.getModel(ItemModel).getHouseItemCount(9e3)),r=this.currentPageIndex;",
        "var t=Math.ceil(this.pictureList.length/this.itemPerPage),i=[e];":
            "var t=Math.max(1,Math.ceil(this.pictureList.length/this.itemPerPage)+this.getModel(ItemModel).getHouseItemCount(9e3)),i=[e];",
    }
    for before, after in changes.items():
        if after in code:
            code = code.replace(after, before)
        elif before not in code:
            raise SystemExit(f"unexpected album-page implementation in {name}: {before[:50]}")
    path.write_text(code, encoding="utf-8")
    print(path)
