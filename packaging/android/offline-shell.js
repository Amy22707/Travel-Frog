/* Native file saving and a local-only web bootstrap for the offline client. */
(function () {
    GameConfig.channel = ChannelType.Test;
    GameConfig.isAPP = false;
    GameConfig.isH5 = true;
    GameConfig.fullScreen = true;
    core.DebugConfig.ShowLog = false;
    var shopItemIsLimited = ItemModel.prototype.isShopItemBuyLimit;
    ItemModel.prototype.isShopItemBuyLimit = function (shopId) {
        if (Number(shopId) === 65) return false;
        return shopItemIsLimited.call(this, shopId);
    };
    var createCalendar = CalendarView.prototype.childrenCreated;
    CalendarView.prototype.childrenCreated = function () {
        createCalendar.call(this);
        this.imagePic.touchEnabled = false;
        this.imageToday.touchEnabled = false;
        this.groupReward.touchEnabled = false;
        this.groupReward.touchChildren = false;
        var view = this, model = this.getModel(CalendarModel);
        var today = new Date(core.Time.getServerTime() * 1000).getDate();
        var age = this.getModel(RoleModel).getCreateDay();
        var beginner = Tabikaeru.DataManager.instance().calendarData.get("beginner");
        this.imageList.forEach(function (cell, index) {
            var day = index + 1, slot = age + day - today;
            if (day >= today || slot < 1 || slot > 7 || model.data.new_flag[slot - 1]) return;
            var point = cell.localToGlobal(0, 0);
            point = view.groupReward.globalToLocal(point.x, point.y);
            var item = Tabikaeru.DataManager.instance().ItemDB.get(beginner[slot].item_id);
            var icon = new eui.Image(Tabikaeru.path.formatPathImage(item.img));
            icon.x = point.x + 6;
            icon.y = point.y + 2;
            icon.width = icon.height = 60;
            view.groupReward.addChild(icon);
            cell.addEventListener(egret.TouchEvent.TOUCH_TAP, function (event) {
                if (model.data.new_flag[slot - 1]) return;
                event.stopImmediatePropagation();
                model.req_code_reward(beginner[slot].code, function () {
                    icon.visible = false;
                    model.checkRedot();
                });
            }, view, false, 100);
        });
    };
    var loadCalendar = CalendarModel.prototype.calendar_load;
    CalendarModel.prototype.calendar_load = function (data) {
        this.data.lucky_days = {};
        this.data.st_days = {};
        loadCalendar.call(this, data);
    };
    CalendarModel.prototype.canGetStReward = function () {
        var today = new Date(core.Time.getServerTime() * 1000).getDate();
        var tasks = this.data.task_list;
        var rewards = this.data.st_days;
        return tasks.length > 0 && tasks.every(function (task) { return task && task.complete; }) &&
            Object.keys(rewards).some(function (day) { return day > 0 && day <= today && rewards[day] > 0; });
    };
    CalendarModel.prototype.canGetLuckyReward = function () {
        var today = new Date(core.Time.getServerTime() * 1000).getDate();
        var rewards = this.data.lucky_days;
        return Object.keys(rewards).some(function (day) { return day > 0 && day <= today && rewards[day] > 0; });
    };
    CalendarModel.prototype.canGetMissedBeginnerReward = function () {
        var today = new Date(core.Time.getServerTime() * 1000).getDate();
        var age = this.getModel(RoleModel).getCreateDay();
        return this.data.new_flag.some(function (flag, index) {
            var day = today - age + index + 1;
            return !flag && day >= 1 && day <= today;
        });
    };
    CalendarModel.prototype.canShowMainOutBtn = function () {
        return this.canGetMissedBeginnerReward() || this.canGetStReward() || this.canGetLuckyReward();
    };
    var checkCalendarRedot = CalendarModel.prototype.checkRedot;
    CalendarModel.prototype.checkRedot = function () {
        checkCalendarRedot.call(this);
        if (this.canGetMissedBeginnerReward()) {
            Tabikaeru.RedotManager.instance().setRedotValue(Tabikaeru.RedotType.REDOT_CALENDAR, 1);
        }
        var scene = core.PageManage.getInstance().getControl(MainOutController, core.ViewLayerType.SceneLayer);
        if (scene && scene.view && scene.view.btnCalendar) {
            scene.view.btnCalendar.visible = scene.view.btnCalendar.includeInLayout = this.canShowMainOutBtn();
        }
    };
    CalendarModel.prototype.req_luck_reward = function (day, onClaim) {
        var model = this;
        core.SocketManage.getInstance().send("calendar_get_luck_reward", new core.Action2(function (result) {
            if (result.code !== 0) return;
            delete model.data.lucky_days[result.day];
            model.checkRedot();
            core.PageManage.getInstance().addViewControl(GiftPackageViewController, core.ViewLayerType.NoticeLayer,
                null, [{item_id: result.item_id, count: 1}]);
            if (onClaim) onClaim();
        }), day);
    };
    CalendarModel.prototype.req_st_reward = function (day, onClaim) {
        var model = this;
        core.SocketManage.getInstance().send("calendar_get_st_reward", new core.Action2(function (result) {
            if (result.code !== 0) return;
            delete model.data.st_days[result.day];
            if (result.task) model.calendar_task_update({task: result.task});
            model.checkRedot();
            core.PageManage.getInstance().addViewControl(GiftPackageViewController, core.ViewLayerType.NoticeLayer,
                null, [{item_id: result.item_id, count: 1}]);
            if (onClaim) onClaim();
        }), day);
    };
    // The courtyard skin already binds REDOT_CALENDAR to its own badge.
    var completeLumberroom = Lumberroom.prototype.onComplete;
    Lumberroom.prototype.onComplete = function () {
        completeLumberroom.call(this);
        var dot = new eui.Image("red_dot_little_png");
        var manager = Tabikaeru.RedotManager.instance();
        var key = Tabikaeru.RedotType.REDOT_CALENDAR;
        var handler = manager.getRedotHandler(key);
        function update() { dot.visible = manager.getRedotValue(key) > 0; }
        dot.x = dot.y = 10;
        dot.width = dot.height = 23;
        dot.touchEnabled = false;
        dot.addEventListener(egret.Event.ADDED_TO_STAGE, function () {
            handler.add(update, dot);
            update();
        }, dot);
        dot.addEventListener(egret.Event.REMOVED_FROM_STAGE, function () { handler.remove(update, dot); }, dot);
        this.calendarBtn.addChild(dot);
    };
    LotteryModel.prototype.getLeftTime = function () {
        if (!this.data.phase || !this.data.select_list.length) return 0;
        var now = core.Time.getServerTime();
        var china = new Date((now + 8 * 3600) * 1000);
        var day = china.getUTCDay();
        if (day !== 6 && day !== 0) return 0;
        china.setUTCHours(0, 0, 0, 0);
        return china.getTime() / 1000 - 8 * 3600 + (day === 6 ? 2 : 1) * 86400 - now;
    };
    var loadBundle = EjoyAssetBundleAnalyzer.prototype.onLoadStart;
    EjoyAssetBundleAnalyzer.prototype.onLoadStart = function (host, resource) {
        return loadBundle.call(this, host, resource).catch(function (error) {
            console.error("OFFLINE_BUNDLE " + resource.url + " " + error);
            throw error;
        });
    };
    TestChannel.prototype.check_permission = function () { return Promise.resolve(true); };
    TestChannel.prototype.support_save_to_album = function () { return Promise.resolve(true); };
    window.__frogSaveComplete = function (success) {
        var resolve = window.__frogSaveResolve;
        window.__frogSaveResolve = null;
        if (resolve) resolve(success);
    };
    TestChannel.prototype.save_texture_to_album = function (texture) {
        var url = texture.toDataURL("image/png");
        if (!window.FrogNative) {
            var link = document.createElement("a");
            link.href = url;
            link.download = "frog-" + Date.now() + ".png";
            document.body.appendChild(link);
            link.click();
            link.remove();
            return Promise.resolve(true);
        }
        return new Promise(function (resolve) {
            window.__frogSaveResolve = resolve;
            window.FrogNative.saveImage(url.substring(url.indexOf(",") + 1), "frog-" + Date.now() + ".png");
        });
    };
    function photoShareButton(text, width, onTap) {
        var button = new eui.Group();
        var background = new eui.Rect(width, 52, 0xfffbe9);
        background.strokeColor = 0x526b42;
        background.strokeWeight = 2;
        background.ellipseWidth = background.ellipseHeight = 16;
        var label = new eui.Label(text);
        label.textColor = 0x414b37;
        label.size = 25;
        label.horizontalCenter = label.verticalCenter = 0;
        button.width = width;
        button.height = 52;
        button.touchChildren = false;
        button.addChild(background);
        button.addChild(label);
        button.addEventListener(egret.TouchEvent.TOUCH_TAP, onTap, button);
        return button;
    }
    PhotoShareView.prototype.capture = function () {
        this.visible = true;
        var view = this;
        var close = photoShareButton("×", 52, function () { view.close(); });
        close.name = "offlinePhotoShareClose";
        close.right = close.top = 16;
        this.addChild(close);
        var status = new eui.Label("分享失败");
        status.textColor = 0x414b37;
        status.strokeColor = 0xfffbe9;
        status.stroke = 4;
        status.size = 24;
        status.horizontalCenter = 0;
        status.top = 30;
        status.touchEnabled = false;
        this.addChild(status);
        var save = photoShareButton("保存图片", 156, async function () {
            var texture;
            save.touchEnabled = false;
            try {
                texture = Utils.Capture.instance().captureTexture(view.c_view);
                var saved = await BaseChannel.getInstance().save_texture_to_album(texture);
                status.text = saved ? "保存成功" : "保存失败";
            } catch (error) {
                status.text = "保存失败";
            } finally {
                if (texture) texture.dispose();
                save.touchEnabled = true;
            }
        });
        save.name = "offlinePhotoShareSave";
        save.left = save.top = 16;
        this.addChild(save);
    };
    function openOfflinePhotoShare(selector) {
        var parent = selector.parent;
        if (!parent || !selector.sharePic) return;
        var picture = selector.sharePic;
        var preview = new PhotoShareView(picture);
        core.SocketManage.getInstance().send("cooking_share");
        if (picture.id) core.ModelManage.getInstance().getModel(ShareModel).req_get_reward(picture.id, 0);
        selector.close();
        if (selector.share_callback_custom) selector.share_callback_custom(false);
        parent.addChild(preview);
    }
    var openSharePhoto = ShareView.prototype.open_pic;
    ShareView.prototype.open_pic = function () {
        openSharePhoto.apply(this, arguments);
        egret.callLater(openOfflinePhotoShare, null, this);
    };
    var executeShare = ShareView.prototype.share;
    ShareView.prototype.share = function (platform) {
        if (this.sharePic) openOfflinePhotoShare(this);
        else executeShare.call(this, platform);
    };
    var selectEncyTravelItem = EncyTravelView.prototype.onSelectItem;
    EncyTravelView.prototype.onSelectItem = function () {
        var row = this.itemList[this.list.selectedIndex];
        if (row && Tabikaeru.DataManager.instance().ItemDB.get(row.item_id)) {
            selectEncyTravelItem.call(this);
        } else {
            this.lbName.text = this.lbSubName.text = this.imagePic.source = "";
            this.listSub.dataProvider = new eui.ArrayCollection();
            this.listDesc.dataProvider = new eui.ArrayCollection();
        }
    };
    var createPartyCake = PartyCakeView.prototype.childrenCreated;
    PartyCakeView.prototype.childrenCreated = function () {
        createPartyCake.call(this);
        var panel = this.groupTask;
        var parent = panel.parent;
        var clip = new eui.Group();
        clip.percentWidth = 100;
        clip.maxWidth = 854; // back_makecake.png width
        clip.horizontalCenter = 0;
        clip.top = clip.bottom = 0;
        clip.touchThrough = true;
        clip.scrollEnabled = true;
        parent.addChildAt(clip, parent.getChildIndex(panel));
        clip.addChild(panel);
    };
    window.addEventListener("error", function (event) {
        console.error("OFFLINE_ERROR " + event.message + " " + event.filename + ":" + event.lineno);
    });
    window.addEventListener("unhandledrejection", function (event) {
        console.error("OFFLINE_PROMISE " + event.reason);
    });
    var wheelAnimations = new WeakMap();
    function cancelWheel(view) {
        var pending = wheelAnimations.get(view);
        if (!pending) return;
        cancelAnimationFrame(pending.id);
        wheelAnimations.delete(view);
        Utils.FpsManager.instance().resume();
    }
    function dragScrollThumb(event) {
        var bar = this, view = bar.viewport, stage = bar.stage;
        if (!view || !bar.thumb || !stage) return;
        event.stopPropagation();
        event.preventDefault();
        var horizontal = bar instanceof eui.HScrollBar;
        var axis = horizontal ? "x" : "y";
        var dimension = horizontal ? "width" : "height";
        var contentDimension = horizontal ? "contentWidth" : "contentHeight";
        var scroll = horizontal ? "scrollH" : "scrollV";
        var origin = bar.globalToLocal(event.stageX, event.stageY)[axis];
        var initial = view[scroll];
        var travel = bar[dimension] - bar.thumb[dimension];
        if (travel <= 0) return;
        cancelWheel(view);
        if (bar.parent instanceof eui.Scroller) bar.parent.stopAnimation();
        Utils.FpsManager.instance().boost();
        function move(next) {
            next.stopPropagation();
            var delta = bar.globalToLocal(next.stageX, next.stageY)[axis] - origin;
            var limit = Math.max(0, view[contentDimension] - view[dimension]);
            view[scroll] = Math.max(0, Math.min(limit, Math.round(initial + delta * limit / travel)));
        }
        function end() {
            stage.removeEventListener(egret.TouchEvent.TOUCH_MOVE, move, bar);
            stage.removeEventListener(egret.TouchEvent.TOUCH_END, end, bar);
            bar.removeEventListener(egret.Event.REMOVED_FROM_STAGE, end, bar);
            Utils.FpsManager.instance().resume();
        }
        stage.addEventListener(egret.TouchEvent.TOUCH_MOVE, move, bar);
        stage.addEventListener(egret.TouchEvent.TOUCH_END, end, bar);
        bar.addEventListener(egret.Event.REMOVED_FROM_STAGE, end, bar);
    }
    var addScrollerPart = eui.Scroller.prototype.partAdded;
    eui.Scroller.prototype.partAdded = function (name, part) {
        addScrollerPart.call(this, name, part);
        if (part === this.verticalScrollBar || part === this.horizontalScrollBar) {
            part.touchEnabled = part.touchChildren = true;
            part.addEventListener(egret.TouchEvent.TOUCH_BEGIN, dragScrollThumb, part);
        }
    };
    document.addEventListener("mousedown", function (event) {
        if (event.button !== 1 || event.target.tagName !== "CANVAS") return;
        event.preventDefault();
        event.stopImmediatePropagation();
        var stage = egret.MainContext.instance.stage, bounds = event.target.getBoundingClientRect();
        var target = stage.$hitTest((event.clientX - bounds.left) * stage.stageWidth / bounds.width,
                                   (event.clientY - bounds.top) * stage.stageHeight / bounds.height);
        while (target && !(target instanceof eui.Scroller)) target = target.parent;
        if (!target || !target.viewport) return;
        var view = target.viewport;
        var vertical = view.contentHeight > view.height;
        var scroll = vertical ? "scrollV" : "scrollH";
        var initial = view[scroll], origin = vertical ? event.clientY : event.clientX;
        var scale = vertical ? stage.stageHeight / bounds.height : stage.stageWidth / bounds.width;
        cancelWheel(view);
        target.stopAnimation();
        Utils.FpsManager.instance().boost();
        function move(next) {
            var limit = Math.max(0, vertical ? view.contentHeight - view.height : view.contentWidth - view.width);
            var delta = (vertical ? next.clientY : next.clientX) - origin;
            view[scroll] = Math.max(0, Math.min(limit, Math.round(initial + delta * scale)));
        }
        function end() {
            window.removeEventListener("mousemove", move);
            window.removeEventListener("mouseup", end);
            window.removeEventListener("blur", end);
            Utils.FpsManager.instance().resume();
        }
        window.addEventListener("mousemove", move);
        window.addEventListener("mouseup", end);
        window.addEventListener("blur", end);
    }, true);
    document.addEventListener("auxclick", function (event) {
        if (event.button === 1 && event.target.tagName === "CANVAS") event.preventDefault();
    });
    document.addEventListener("wheel", function (event) {
        if (event.ctrlKey || !event.deltaY || Math.abs(event.deltaX) > Math.abs(event.deltaY)) return;
        var stage = egret.MainContext.instance.stage;
        var canvas = document.querySelector("canvas");
        if (!stage || !canvas) return;
        var bounds = canvas.getBoundingClientRect();
        var x = (event.clientX - bounds.left) * stage.stageWidth / bounds.width;
        var y = (event.clientY - bounds.top) * stage.stageHeight / bounds.height;
        var target = stage.$hitTest(x, y);
        var delta = event.deltaY * (event.deltaMode === 1 ? 30 : event.deltaMode === 2 ? 400 : 1);
        while (target) {
            if (target instanceof eui.Scroller && target.viewport && target.scrollPolicyV !== eui.ScrollPolicy.OFF) {
                var view = target.viewport;
                var limit = Math.max(0, view.contentHeight - view.height);
                var pending = wheelAnimations.get(view);
                var base = Math.max(0, Math.min(limit, pending ? pending.destination : view.scrollV));
                var step = Math.sign(delta) * Math.min(Math.abs(delta), 120);
                var next = Math.max(0, Math.min(limit, Math.round(base + step * stage.stageHeight / bounds.height)));
                event.preventDefault();
                if (next !== base || pending) {
                    if (pending) cancelAnimationFrame(pending.id);
                    else {
                        if (target.stopAnimation) target.stopAnimation();
                        Utils.FpsManager.instance().boost();
                    }
                    var from = Math.max(0, Math.min(limit, view.scrollV)), start = performance.now();
                    var animation = {destination: next, id: 0};
                    function scrollFrame(now) {
                        var fraction = Math.max(0, Math.min(1, (now - start) / 145));
                        var currentLimit = Math.max(0, view.contentHeight - view.height);
                        var position = Math.round(from + (next - from) * (1 - Math.pow(1 - fraction, 3)));
                        view.scrollV = Math.max(0, Math.min(currentLimit, position));
                        if (fraction < 1 && view.stage) animation.id = requestAnimationFrame(scrollFrame);
                        else {
                            wheelAnimations.delete(view);
                            Utils.FpsManager.instance().resume();
                        }
                    }
                    animation.id = requestAnimationFrame(scrollFrame);
                    wheelAnimations.set(view, animation);
                } else {
                    view.scrollV = base;
                }
                return;
            }
            target = target.parent;
        }
    }, {passive: false});
    var query = new URLSearchParams(location.search);
    if (query.get("desktopApp") === "1") {
        var ping = function () { fetch("/__desktop/ping", {method: "POST"}).catch(function () {}); };
        ping();
        setInterval(ping, 10000);
        window.addEventListener("pagehide", function () { navigator.sendBeacon("/__desktop/closed", ""); });
        var manage = document.createElement("button");
        manage.textContent = "存档";
        manage.title = "导出存档与存档管理";
        manage.style.cssText = "position:fixed;bottom:8px;left:8px;z-index:99;border:1px solid #a7ad90;border-radius:14px;padding:5px 12px;color:#526b42;background:#f6f1decc;font:12px sans-serif;cursor:pointer";
        manage.onclick = function () { fetch("/__desktop/saves", {method: "POST"}); };
        document.body.appendChild(manage);
    }
    egret.runEgret({renderMode: "webgl", audioType: 0});
    var splash = document.getElementById("frog-loading");
    if (splash) {
        var loadingTimer = setInterval(function () {
            if (GameConfig.loadComplete) { clearInterval(loadingTimer); splash.remove(); }
        }, 100);
        // Let the game's own error dialog remain accessible if loading fails.
        setTimeout(function () { clearInterval(loadingTimer); splash.remove(); }, 30000);
    }
}());
