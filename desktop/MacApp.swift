import AppKit
import WebKit
import UniformTypeIdentifiers

@main
struct Main {
    static func main() {
        let app = NSApplication.shared
        let delegate = FrogApp()
        app.delegate = delegate
        app.setActivationPolicy(.regular)
        withExtendedLifetime(delegate) { app.run() }
    }
}

final class FrogApp: NSObject, NSApplicationDelegate, NSWindowDelegate, WKScriptMessageHandler, WKNavigationDelegate {
    let edition = Bundle.main.object(forInfoDictionaryKey: "FrogEdition") as? String ?? "normal"
    let resources = Bundle.main.resourceURL!
    let smoke = CommandLine.arguments.dropFirst().first == "--smoke-test"
    lazy var dataRoot: URL = {
        if smoke && CommandLine.arguments.count == 3 {
            return URL(fileURLWithPath: CommandLine.arguments[2], isDirectory: true)
        }
        return FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("TravelFrog", isDirectory: true)
    }()
    lazy var save = dataRoot.appendingPathComponent(edition).appendingPathComponent(edition == "gallery" ? "gallery_state.json" : "state.json")
    lazy var title = "旅行青蛙·中国之旅" + (edition == "gallery" ? "图鉴版" : "")
    var window: NSWindow!
    var view: WKWebView?
    var runtime: Process?
    var gameURL: URL?
    var runtimeLog: FileHandle?
    var busy = false
    var smokeStarted = Date()
    var smokeChecking = false

    var hasSave: Bool { FileManager.default.fileExists(atPath: save.path) }

    func applicationDidFinishLaunching(_ notification: Notification) {
        let height = min(850, (NSScreen.main?.visibleFrame.height ?? 850) - 36)
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 520, height: max(400, height)),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        window.title = title
        window.contentMinSize = NSSize(width: 320, height: 400)
        window.delegate = self
        window.center()
        let menu = NSMenu()
        let appMenu = NSMenuItem()
        let actions = NSMenu()
        actions.addItem(item("存档管理", #selector(showSaveActions), "s"))
        actions.addItem(item("打开存档文件夹", #selector(showFolder), ""))
        actions.addItem(.separator())
        actions.addItem(item("退出\(title)", #selector(NSApplication.terminate(_:)), "q", target: NSApp))
        appMenu.submenu = actions
        menu.addItem(appMenu)
        NSApp.mainMenu = menu
        showHome()
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        if smoke || (hasSave && !CommandLine.arguments.contains("--manage")) { play() }
    }

    func item(_ text: String, _ selector: Selector, _ key: String, target: AnyObject? = nil) -> NSMenuItem {
        let result = NSMenuItem(title: text, action: selector, keyEquivalent: key)
        result.target = target ?? self
        return result
    }

    func showHome(loading: Bool = false) {
        view?.configuration.userContentController.removeScriptMessageHandler(forName: "frogDesktop")
        view = nil
        let pane = NSView()
        pane.wantsLayer = true
        pane.layer?.backgroundColor = NSColor.white.cgColor
        let icon = NSImageView(image: NSApp.applicationIconImage ?? NSImage())
        icon.widthAnchor.constraint(equalToConstant: 92).isActive = true
        icon.heightAnchor.constraint(equalToConstant: 92).isActive = true
        let heading = NSTextField(labelWithString: title)
        heading.font = .boldSystemFont(ofSize: 24)
        heading.textColor = NSColor(red: 0.25, green: 0.30, blue: 0.22, alpha: 1)
        let stack = NSStackView(views: [icon, heading])
        stack.orientation = .vertical
        stack.alignment = .centerX
        stack.spacing = 18
        if loading {
            let progress = NSProgressIndicator()
            progress.style = .spinning
            progress.startAnimation(nil)
            stack.addArrangedSubview(progress)
            stack.addArrangedSubview(NSTextField(labelWithString: "正在读取中"))
        } else {
            let start = NSButton(title: hasSave ? "继续旅程" : "开始新的旅程", target: self, action: #selector(play))
            start.bezelStyle = .rounded
            start.keyEquivalent = "\r"
            let load = NSButton(title: "导入存档", target: self, action: #selector(importFile))
            let saveButton = NSButton(title: "导出存档", target: self, action: #selector(exportFile))
            saveButton.isEnabled = hasSave
            stack.addArrangedSubview(start)
            stack.addArrangedSubview(NSStackView(views: [load, saveButton]))
        }
        stack.translatesAutoresizingMaskIntoConstraints = false
        pane.addSubview(stack)
        NSLayoutConstraint.activate([stack.centerXAnchor.constraint(equalTo: pane.centerXAnchor),
                                     stack.centerYAnchor.constraint(equalTo: pane.centerYAnchor)])
        window.contentView = pane
    }

    func command(_ action: String, _ path: URL) -> Process {
        let child = Process()
        child.executableURL = resources.appendingPathComponent("runtime/FrogRuntime")
        child.arguments = [edition, dataRoot.path, action, path.path]
        return child
    }

    @objc func play() {
        guard runtime == nil, !busy else { return }
        showHome(loading: true)
        let child = command("play", resources.appendingPathComponent("game"))
        let output = Pipe()
        child.standardOutput = output
        do {
            try FileManager.default.createDirectory(at: save.deletingLastPathComponent(), withIntermediateDirectories: true)
            let log = save.deletingLastPathComponent().appendingPathComponent("desktop.log")
            if !FileManager.default.fileExists(atPath: log.path) { FileManager.default.createFile(atPath: log.path, contents: nil) }
            let stream = try FileHandle(forWritingTo: log)
            try stream.seekToEnd()
            runtimeLog = stream
            child.standardError = stream
            child.terminationHandler = { [weak self] child in
                DispatchQueue.main.async {
                    guard let self = self, self.runtime === child else { return }
                    self.runtime = nil
                    self.runtimeLog = nil
                    self.showHome()
                    self.error("本地游戏服务已退出。请查看存档文件夹中的 desktop.log。")
                }
            }
            try child.run()
            runtime = child
        } catch {
            runtimeLog = nil
            showHome()
            self.error(error.localizedDescription)
            return
        }
        DispatchQueue.global(qos: .userInitiated).async {
            var line = Data()
            let stream = output.fileHandleForReading
            while line.count < 512, let byte = try? stream.read(upToCount: 1), !byte.isEmpty, byte.first != 10 {
                line.append(byte)
            }
            guard let address = String(data: line, encoding: .utf8), let url = URL(string: address), url.host == "127.0.0.1" else { return }
            DispatchQueue.main.async { [weak self] in
                guard let self = self, self.runtime === child else { return }
                self.loadGame(url)
            }
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 50) { [weak self] in
            guard let self = self, self.runtime === child, self.view == nil else { return }
            self.stopGame { self.showHome(); self.error("游戏启动超时。请查看存档文件夹中的 desktop.log。") }
        }
    }

    func loadGame(_ url: URL) {
        let config = WKWebViewConfiguration()
        config.websiteDataStore = .nonPersistent()
        config.preferences.javaScriptCanOpenWindowsAutomatically = false
        let bridge = """
        window.FrogDesktop = {showSaves: () => webkit.messageHandlers.frogDesktop.postMessage({action: 'saves'})};
        window.FrogNative = {saveImage: (data, name) => webkit.messageHandlers.frogDesktop.postMessage({action: 'image', data, name})};
        """
        let wheel = (try? String(contentsOf: resources.appendingPathComponent("mac-scroll.js"), encoding: .utf8)) ?? ""
        config.userContentController.addUserScript(WKUserScript(source: bridge + "\n" + wheel, injectionTime: .atDocumentStart, forMainFrameOnly: true))
        config.userContentController.add(self, name: "frogDesktop")
        let browser = WKWebView(frame: .zero, configuration: config)
        browser.navigationDelegate = self
        browser.allowsBackForwardNavigationGestures = false
        view = browser
        gameURL = url
        window.contentView = browser
        browser.load(URLRequest(url: url))
        if smoke { smokeStarted = Date(); checkSmoke() }
    }

    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        let address = navigationAction.request.url
        decisionHandler(address?.host == "127.0.0.1" && address?.port == gameURL?.port ? .allow : .cancel)
    }

    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        stopGame { self.showHome(); self.error("游戏窗口已关闭，请重新进入旅程。") }
    }

    func stopGame(_ completion: @escaping () -> Void) {
        guard let child = runtime else { completion(); return }
        runtime = nil
        gameURL = nil
        busy = true
        if child.isRunning { child.terminate() }
        DispatchQueue.global().async {
            child.waitUntilExit()
            DispatchQueue.main.async {
                self.runtimeLog = nil
                self.busy = false
                completion()
            }
        }
    }

    func windowWillClose(_ notification: Notification) { NSApp.terminate(nil) }

    func applicationWillTerminate(_ notification: Notification) {
        if let child = runtime, child.isRunning { child.terminate(); child.waitUntilExit() }
    }

    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
        guard message.frameInfo.isMainFrame, message.frameInfo.securityOrigin.host == "127.0.0.1",
              message.frameInfo.securityOrigin.port == gameURL?.port,
              let payload = message.body as? [String: String] else { return }
        if payload["action"] == "saves" { showSaveActions() }
        if payload["action"] == "image", let encoded = payload["data"], let data = Data(base64Encoded: encoded) {
            let panel = NSSavePanel()
            panel.allowedContentTypes = [.png]
            panel.nameFieldStringValue = payload["name"] ?? "frog.png"
            panel.beginSheetModal(for: window) { response in
                var saved = false
                if response == .OK, let url = panel.url {
                    do { try data.write(to: url, options: .atomic); saved = true }
                    catch { self.error(error.localizedDescription) }
                }
                self.view?.evaluateJavaScript("window.__frogSaveComplete(\(saved));")
            }
        }
    }

    @objc func showSaveActions() {
        guard !busy, view != nil, window.attachedSheet == nil else { return }
        let dialog = NSAlert()
        dialog.messageText = "存档管理"
        dialog.addButton(withTitle: "导出存档")
        dialog.addButton(withTitle: "返回起始页")
        dialog.addButton(withTitle: "继续游戏")
        dialog.beginSheetModal(for: window) { response in
            if response == .alertFirstButtonReturn { self.exportFile() }
            if response == .alertSecondButtonReturn { self.stopGame { self.showHome() } }
        }
    }

    @objc func showFolder() {
        let folder = save.deletingLastPathComponent()
        do {
            try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
            NSWorkspace.shared.open(folder)
        } catch { self.error(error.localizedDescription) }
    }

    @objc func importFile() {
        guard runtime == nil, !busy else { return }
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [UTType(filenameExtension: "frogsave") ?? .archive, .json, UTType(filenameExtension: "jsonl") ?? .plainText]
        panel.beginSheetModal(for: window) { response in
            guard response == .OK, let url = panel.url else { return }
            if self.hasSave {
                let confirm = NSAlert()
                confirm.messageText = "导入这份存档？"
                confirm.informativeText = "当前进度会先备份。"
                confirm.addButton(withTitle: "导入")
                confirm.addButton(withTitle: "取消")
                confirm.beginSheetModal(for: self.window) { response in
                    if response == .alertFirstButtonReturn { self.saveCommand("import", url) }
                }
            } else { self.saveCommand("import", url) }
        }
    }

    @objc func exportFile() {
        guard hasSave, !busy else { return }
        let panel = NSSavePanel()
        panel.allowedContentTypes = [UTType(filenameExtension: "frogsave") ?? .archive]
        panel.nameFieldStringValue = "frog-\(edition).frogsave"
        panel.beginSheetModal(for: window) { response in
            if response == .OK, let url = panel.url { self.saveCommand("export", url) }
        }
    }

    func saveCommand(_ action: String, _ url: URL) {
        busy = true
        let child = command(action, url)
        let errors = Pipe()
        child.standardError = errors
        DispatchQueue.global(qos: .userInitiated).async {
            var failure: String?
            do {
                try child.run()
                let data = errors.fileHandleForReading.readDataToEndOfFile()
                child.waitUntilExit()
                if child.terminationStatus != 0 { failure = String(data: data, encoding: .utf8) ?? "存档操作失败。" }
            } catch { failure = error.localizedDescription }
            DispatchQueue.main.async {
                self.busy = false
                if let failure = failure { self.error(failure) }
                else if self.view == nil { self.showHome() }
            }
        }
    }

    func error(_ message: String) {
        if smoke { fputs("\(edition): \(message)\n", stderr); exit(1) }
        let dialog = NSAlert()
        dialog.messageText = title
        dialog.informativeText = message
        dialog.alertStyle = .warning
        dialog.beginSheetModal(for: window)
    }

    func checkSmoke() {
        guard smoke, !smokeChecking else { return }
        if Date().timeIntervalSince(smokeStarted) > 100 {
            stopGame { self.error("WebKit 游戏未完成登录。") }
            return
        }
        let ready = "Boolean(window.GameConfig?.loadComplete && window.UserModel && core.ModelManage.getInstance().getModel(UserModel).uid && document.querySelector('canvas'))"
        view?.evaluateJavaScript(ready) { value, _ in
            if value as? Bool == true {
                self.smokeChecking = true
                self.runScrollSmoke()
            } else {
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { self.checkSmoke() }
            }
        }
    }

    func runScrollSmoke() {
        let script = """
        (() => {
          const stage = egret.MainContext.instance.stage, canvas = document.querySelector('.egret-player canvas');
          const bounds = canvas.getBoundingClientRect();
          const panel = new eui.Scroller();
          const view = new eui.Group();
          panel.viewport = view;
          panel.width = panel.height = 100;
          view.addChild(new eui.Rect(500, 800, 0xeeeeee));
          stage.addChild(panel);
          panel.validateNow();
          const original = stage.$hitTest;
          stage.$hitTest = () => panel;
          function wheel(x, y, mode = 0, shiftKey = false, ctrlKey = false) {
            canvas.dispatchEvent(new WheelEvent('wheel', {bubbles:true, cancelable:true,
              clientX:bounds.left+10, clientY:bounds.top+10, deltaX:x, deltaY:y, deltaMode:mode, shiftKey, ctrlKey}));
          }
          try {
            for(let i=0;i<8;i++) wheel(0,0.25);
            if (!(view.scrollV > 0 && view.scrollV < 10)) throw Error('trackpad fractional motion');
            const start = view.scrollV;
            wheel(0,1,1);
            if (view.scrollV <= start + 15) throw Error('wheel line conversion');
            wheel(0,6,0,true);
            if (view.scrollH <= 0) throw Error('horizontal scroll');
            const beforePinch = view.scrollV;
            wheel(0,12,0,false,true);
            if (view.scrollV !== beforePinch) throw Error('pinch treated as scroll');
            for(let i=0;i<30;i++) wheel(0,400);
            if (view.scrollV !== view.contentHeight-view.height) throw Error('bottom boundary');
            for(let i=0;i<30;i++) wheel(0,-400);
            if (view.scrollV !== 0) throw Error('top boundary');
            if (!Array.from(document.querySelectorAll('button')).some(b => b.textContent === '存档')) throw Error('save button');
            return true;
          } finally { stage.$hitTest = original; stage.removeChild(panel); }
        })()
        """
        view?.evaluateJavaScript(script) { value, error in
            self.stopGame {
                guard error == nil, value as? Bool == true else { self.error(error?.localizedDescription ?? "滚动测试未通过"); return }
                print("\(self.edition): WebKit login, save button, trackpad, wheel and scroll bounds verified")
                NSApp.terminate(nil)
            }
        }
    }
}
