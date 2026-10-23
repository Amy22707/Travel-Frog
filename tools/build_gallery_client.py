"""Build the gallery client's port-isolated hotfix bundle."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
source = ROOT / "client" / "js" / "main.offline.stage6i2.js"
destination = ROOT / "client" / "js" / "main.gallery.js"
code = source.read_text(encoding="utf-8")

old_endpoint = "ws://127.0.0.1:8080"
if code.count(old_endpoint) != 4:
    raise SystemExit("unexpected offline endpoint count")
code = code.replace(old_endpoint, "ws://127.0.0.1:8081")

old_limit = "t.prototype.isShopItemBuyLimit=function(e){var i=Tabikaeru.DataManager.instance().ShopDataDB.get(e);if(i.limit>0)"
new_limit = "t.prototype.isShopItemBuyLimit=function(e){var i=Tabikaeru.DataManager.instance().ShopDataDB.get(e);if(i&&Tabikaeru.DataManager.instance().ItemDB.get(i.itemId).spend==1)return!1;if(i.limit>0)"
if code.count(old_limit) != 1:
    raise SystemExit("unexpected shop limit implementation")
code = code.replace(old_limit, new_limit)

date_label = 'this.lblLimitDate.text=_("活动截止至 {0}",core.DateFormat.format(1e3*Number(i[1]),core.DateFormater["YYYY/M/D hh:mm"]))'
if code.count(date_label) != 3:
    raise SystemExit("unexpected seasonal end-date labels")
code = code.replace(date_label, 'this.lblLimitDate.visible=this.lblLimitDate.includeInLayout=!1')

release_schedule = 'var releaseOffsets = {1: 0, 2: 604800, 3: 604800, 4: 1209600, 5: 1814400};'
if code.count(release_schedule) != 1:
    raise SystemExit("unexpected anniversary cake release schedule")
code = code.replace(release_schedule, 'var releaseOffsets = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0};')

cake_material_debit = 'i.data.cream-=r.cream,i.data.sugar-=r.sugar'
if code.count(cake_material_debit) != 1:
    raise SystemExit("unexpected anniversary cake material handling")
code = code.replace(cake_material_debit, 'i.data.cream=99,i.data.sugar=99')

destination.write_text(code, encoding="utf-8")
import patch_album_pages
print(destination)
