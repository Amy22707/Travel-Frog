"""Make the three explicitly permanent offline activities stay open in the client."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
CLIENT = ROOT / "client" / "js" / "main.offline.stage6i2.js"


def replace_once(source: str, old: str, new: str, label: str) -> str:
    count = source.count(old)
    if count == 0 and new in source:
        return source
    if count != 1:
        raise RuntimeError(f"{label}: expected one original anchor, found {count}")
    return source.replace(old, new, 1)


def replace_in_model(source: str, model: str, old: str, new: str, label: str) -> str:
    start = source.index(f"var {model}=function")
    finish = source.index(f'__reflect({model}.prototype', start)
    part = source[start:finish]
    count = part.count(old)
    if count == 0 and new in part:
        return source
    if count != 1:
        raise RuntimeError(f"{label}: expected one model anchor, found {count}")
    return source[:start] + part.replace(old, new, 1) + source[finish:]


def replace_through_in_model(source: str, model: str, start_anchor: str, finish_anchor: str, replacement: str) -> str:
    start = source.index(f"var {model}=function")
    finish = source.index(f'__reflect({model}.prototype', start)
    part = source[start:finish]
    if start_anchor not in part:
        if replacement in part:
            return source
        raise RuntimeError(f"{model}: missing label anchor")
    left = part.index(start_anchor)
    right = part.index(finish_anchor, left)
    return source[:start] + part[:left] + replacement + part[right:] + source[finish:]


def replace_between(source: str, start: str, finish: str, replacement: str, label: str) -> str:
    count = source.count(start)
    if count != 1:
        raise RuntimeError(f"{label}: expected one start anchor, found {count}")
    left = source.index(start)
    right = source.index(finish, left)
    return source[:left] + replacement + source[right:]


def replace_until_in_model(source: str, model: str, start_anchor: str, finish_anchor: str, replacement: str) -> str:
    start = source.index(f"var {model}=function")
    finish = source.index(f'__reflect({model}.prototype', start)
    part = source[start:finish]
    if start_anchor not in part:
        if replacement in part:
            return source
        raise RuntimeError(f"{model}: missing history label anchor")
    left = part.index(start_anchor)
    right = part.index(finish_anchor, left) + len(finish_anchor)
    return source[:start] + part[:left] + replacement + part[right:] + source[finish:]


source = CLIENT.read_text(encoding="utf-8")

# These activities deliberately have no closing date in the offline build.
source = replace_in_model(
    source,
    "CapsuleModel",
    "t.prototype.isOpen=function(){var e=this.getActivityTime(),t=e[0],i=e[1];return core.Time.getServerTime()>=t&&core.Time.getServerTime()<=i},t.prototype.closeActivity=function(){var e=!1;core.PageManage.getInstance().getControl(CapsuleViewControl",
    "t.prototype.isOpen=function(){return this.data.end_time>0},t.prototype.closeActivity=function(){if(this.data.end_time>0)return;var e=!1;core.PageManage.getInstance().getControl(CapsuleViewControl",
    "capsule permanent state",
)
source = replace_in_model(
    source,
    "MuseumDayModel",
    "t.prototype.isOpen=function(){var e=this.getActivityTime(),t=e[0],i=e[1];return core.Time.getServerTime()>=t&&core.Time.getServerTime()<=i},t.prototype.canInspire=function(){return this.data.inspire_num<=0?!1:core.Time.getServerTime()<this.data.inspire_time?!1:!0}",
    "t.prototype.isOpen=function(){return this.data.end_time>0},t.prototype.canInspire=function(){return this.data.inspire_num<=0?!1:core.Time.getServerTime()<this.data.inspire_time?!1:!0}",
    "museum permanent state",
)
source = replace_in_model(
    source,
    "MuseumDayModel",
    "t.prototype.closeActivity=function(){var e=!1;core.PageManage.getInstance().getControl(MuseumDayExploreViewControl",
    "t.prototype.closeActivity=function(){if(this.data.end_time>0)return;var e=!1;core.PageManage.getInstance().getControl(MuseumDayExploreViewControl",
    "museum close guard",
)
source = replace_in_model(
    source,
    "WishingPoolModel",
    "t.prototype.isOpen=function(){return core.Time.getServerTime()<this.data.end_time},t.prototype.getLeftTime=function(){return this.data.end_time-core.Time.getServerTime()}",
    "t.prototype.isOpen=function(){return this.data.end_time>0},t.prototype.getLeftTime=function(){return this.data.end_time>0?0:0}",
    "wishing pool permanent state",
)
source = replace_through_in_model(
    source, "CapsuleView", "this.lblLimitDate.text=", ";var n=Tabikaeru.DataManager.instance().capsuleData",
    "this.lblLimitDate.visible=this.lblLimitDate.includeInLayout=!1",
)
source = replace_through_in_model(
    source, "MuseumDayExploreView", "this.lblLimitDate.text=", "},t.prototype.updateCompass=function",
    "this.lblLimitDate.visible=this.lblLimitDate.includeInLayout=!1",
)
source = replace_between(
    source,
    "t.prototype.checkWishingPool=function(){",
    "},t.prototype.update_season",
    "t.prototype.checkWishingPool=function(){this.userModel.getClientSettings().guideStep==GuideStep.Complete&&(this.wishGroup.visible=this.wishGroup.includeInLayout=!0,this.wishLb.visible=this.wishLb.includeInLayout=!1)",
    "wishing pool permanent label",
)
source = replace_through_in_model(
    source, "WishingPoolView", "this.lbTime.text=", ",this.cover.addEventListener",
    "this.lbTime.visible=this.lbTime.includeInLayout=!1",
)
source = replace_until_in_model(
    source,
    "MuseumDayHistoryView",
    "else{var i=this.getModel(MuseumDayModel).getActivityTime();this.lblDesc.text=",
    "}},t.prototype.updatCompass=function",
    'else this.lblDesc.text=""},t.prototype.updatCompass=function',
)

CLIENT.write_text(source, encoding="utf-8")
print("OFFLINE_PERMANENT_EVENTS_V1")
