/* WebKit reports wheel motion in CSS pixels, lines, or pages. */
(function () {
    "use strict";
    let scrollIdle;
    window.addEventListener("wheel", function (event) {
        if (event.ctrlKey || event.metaKey) return;
        const stage = window.egret?.MainContext.instance.stage;
        const canvas = document.querySelector(".egret-player canvas");
        if (!stage || !canvas || !window.eui) return;
        const bounds = canvas.getBoundingClientRect();
        const x = event.clientX - bounds.left;
        const y = event.clientY - bounds.top;
        if (x < 0 || y < 0 || x >= bounds.width || y >= bounds.height) return;

        const scaleX = stage.stageWidth / bounds.width;
        const scaleY = stage.stageHeight / bounds.height;
        const mode = event.deltaMode;
        const deltaX = event.deltaX || (event.shiftKey ? event.deltaY : 0);
        const deltaY = event.shiftKey && !event.deltaX ? 0 : event.deltaY;
        const horizontal = Math.abs(deltaX) > Math.abs(deltaY);
        const delta = horizontal ? deltaX : deltaY;
        if (!delta) return;

        let target = stage.$hitTest(x * scaleX, y * scaleY);
        while (target) {
            if (target instanceof eui.Scroller && target.viewport) {
                const view = target.viewport;
                const policy = horizontal ? target.scrollPolicyH : target.scrollPolicyV;
                const limit = horizontal ? view.contentWidth - view.width : view.contentHeight - view.height;
                if (policy !== eui.ScrollPolicy.OFF && limit > 0) {
                    event.preventDefault();
                    event.stopImmediatePropagation();
                    const key = horizontal ? "scrollH" : "scrollV";
                    const unit = mode === 1 ? 30 : mode === 2 ? (horizontal ? bounds.width : bounds.height) : 1;
                    const pixels = Math.max(-120, Math.min(120, delta * unit));
                    target.stopAnimation();
                    view[key] = Math.max(0, Math.min(limit, view[key] + pixels * (horizontal ? scaleX : scaleY)));
                    if (!scrollIdle) Utils.FpsManager.instance().boost();
                    clearTimeout(scrollIdle);
                    scrollIdle = setTimeout(function () {
                        scrollIdle = null;
                        Utils.FpsManager.instance().resume();
                    }, 160);
                    return;
                }
            }
            target = target.parent;
        }
    }, {capture: true, passive: false});
}());
