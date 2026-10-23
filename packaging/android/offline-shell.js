/* Native file saving and a local-only web bootstrap for the offline client. */
(function () {
    GameConfig.channel = ChannelType.Test;
    GameConfig.isAPP = false;
    GameConfig.isH5 = true;
    GameConfig.fullScreen = true;
    core.DebugConfig.ShowLog = false;
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
                var next = Math.max(0, Math.min(limit, view.scrollV + delta * stage.stageHeight / bounds.height));
                if (next !== view.scrollV) {
                    if (target.stopAnimation) target.stopAnimation();
                    view.scrollV = next;
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
}());
