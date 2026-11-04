/* Native file saving and a local-only web bootstrap for the offline client. */
(function () {
    GameConfig.channel = ChannelType.Test;
    GameConfig.isAPP = false;
    GameConfig.isH5 = true;
    GameConfig.fullScreen = true;
    core.DebugConfig.ShowLog = false;
    var createCalendar = CalendarView.prototype.childrenCreated;
    CalendarView.prototype.childrenCreated = function () {
        createCalendar.call(this);
        this.imagePic.touchEnabled = false;
        this.imageToday.touchEnabled = false;
        this.groupReward.touchEnabled = false;
        this.groupReward.touchChildren = false;
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
    function calendarBadge(viewType, buttonName) {
        var complete = viewType.prototype.onComplete;
        viewType.prototype.onComplete = function () {
            complete.call(this);
            var button = this[buttonName];
            var dot = new eui.Image("red_dot_png");
            var manager = Tabikaeru.RedotManager.instance();
            var key = Tabikaeru.RedotType.REDOT_CALENDAR;
            var handler = manager.getRedotHandler(key);
            function update() { dot.visible = manager.getRedotValue(key) > 0; }
            function position() { dot.x = button.width - dot.width; }
            dot.width = dot.height = 23;
            dot.touchEnabled = false;
            dot.addEventListener(egret.Event.ADDED_TO_STAGE, function () {
                handler.add(update, dot);
                position();
                update();
            }, dot);
            dot.addEventListener(egret.Event.REMOVED_FROM_STAGE, function () { handler.remove(update, dot); }, dot);
            button.addEventListener(egret.Event.RESIZE, position, dot);
            button.addChild(dot);
        };
    }
    calendarBadge(MainOutView, "btnCalendar");
    calendarBadge(Lumberroom, "calendarBtn");
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
    window.addEventListener("error", function (event) {
        console.error("OFFLINE_ERROR " + event.message + " " + event.filename + ":" + event.lineno);
    });
    window.addEventListener("unhandledrejection", function (event) {
        console.error("OFFLINE_PROMISE " + event.reason);
    });
    var wheelAnimations = new WeakMap();
    document.addEventListener("wheel", function (event) {
        var stage = egret.MainContext.instance.stage;
        var canvas = document.querySelector("canvas");
        if (!stage || !canvas) return;
        var bounds = canvas.getBoundingClientRect();
        var x = (event.clientX - bounds.left) * stage.stageWidth / bounds.width;
        var y = (event.clientY - bounds.top) * stage.stageHeight / bounds.height;
        var target = stage.$hitTest(x, y);
        var delta = event.deltaY * (event.deltaMode === 1 ? 30 : event.deltaMode === 2 ? 400 : 1);
        while (target) {
            if (target instanceof eui.Scroller && target.viewport) {
                var view = target.viewport;
                var limit = Math.max(0, view.contentHeight - view.height);
                var pending = wheelAnimations.get(view);
                var base = pending ? pending.destination : view.scrollV;
                var step = Math.sign(delta) * Math.min(Math.abs(delta), 120);
                var next = Math.max(0, Math.min(limit, base + step * stage.stageHeight / bounds.height));
                if (next !== base || pending) {
                    if (pending) cancelAnimationFrame(pending.id);
                    if (target.stopAnimation) target.stopAnimation();
                    var from = view.scrollV, start = performance.now();
                    var animation = {destination: next, id: 0};
                    function scrollFrame(now) {
                        var fraction = Math.min(1, (now - start) / 145);
                        view.scrollV = from + (next - from) * (1 - Math.pow(1 - fraction, 3));
                        if (fraction < 1 && view.stage) animation.id = requestAnimationFrame(scrollFrame);
                        else wheelAnimations.delete(view);
                    }
                    animation.id = requestAnimationFrame(scrollFrame);
                    wheelAnimations.set(view, animation);
                    event.preventDefault();
                    return;
                }
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
