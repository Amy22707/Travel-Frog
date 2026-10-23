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
    egret.runEgret({renderMode: "webgl", audioType: 0});
}());
