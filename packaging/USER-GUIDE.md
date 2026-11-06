# 旅行青蛙离线版

Windows 双击 `TravelFrogSetup.exe`，选择普通版或图鉴版。安装器会下载对应的压缩包，也可从安装器所在文件夹读取已经下载的版本。安装目录是 `%LOCALAPPDATA%\TravelFrog`，其中普通版打开 `FrogNormal.exe`，图鉴版打开 `FrogGallery.exe`。下载压缩包后也可以手动解压整个文件夹再启动。

独立窗口版安装时会创建对应版本的桌面快捷方式。已有进度时，双击快捷方式或 EXE 会直接打开游戏。窗口使用 Windows 内嵌的 WebView2 运行游戏，程序与资源均在本机。首次使用可以创建初始进度或导入存档。窗口左下角的「存档」按钮可打开存档管理并导出进度。关闭游戏窗口后，本地服务自动退出。 [WebView2 Runtime](https://developer.microsoft.com/microsoft-edge/webview2/#download-section)

已有进度但需要导入另一份备份时，先关闭游戏，然后双击 `存档管理.cmd` 打开管理界面，再选择“导入存档”。

## 开始游戏

安卓：安装对应 APK，打开后导入 `.frogsave` 或 JSON 存档，也可以创建初始进度，然后点击“继续旅程”。运行环境、游戏资源和存档都在手机本地，不需要连接电脑、ADB 或 root。

iPhone / iPad：用 Safari 打开网页版本，从分享菜单选择“添加到主屏幕”。从新图标打开后导入存档，点击“下载离线资源”，等待完成后即可断网游玩。Safari 页面与主屏幕应用可能使用不同存储，建议在主屏幕应用内导入。首次下载约 320 MB，建议使用 Wi-Fi。

其他电脑与手机也可以直接打开网页版本。所有版本通过存档文件迁移进度，不会自动同步到另一设备。

普通版按照实际库存与进度游玩。图鉴版使用独立存档，解锁收藏并固定资源数量。新建普通版提供 500 三叶草与三份基础食物。

两个安卓包使用独立包名、独立存档，可以同时安装。不需要官服更新或手机号登录。

## 存档

- Windows 数据保存在程序旁的 `user-data/normal/` 或 `user-data/gallery/`；安卓数据在应用私有目录；网页数据在当前设备的网站存储中。
- “导出存档”生成 `.frogsave` 文件，包含当前版本的完整离线状态，包括日记和已经保存的照片图层。
- 导入支持 `.frogsave`、兼容的 `state.json` 以及完整初始化协议 `.jsonl`。导入前退出当前游戏画面。
- 导入覆盖前自动备份到该版本的 `import-backups/`；普通版和图鉴版存档不会混用。
- 安卓与网页起始页的“导出上次导入前的备份”可取回最近一次被覆盖的进度；导出后可重新导入恢复。Windows 可在“存档文件夹”的 `import-backups/` 中找到备份。
- 官服账号密码不是存档。官服初始化抓包可能只有相册索引，历史照片的准确构图需要额外保存相册详情；导入工具会合并同一抓包中包含的照片图层。
- 存档包包含个人游戏数据，请自行保管。

周末的小插曲按北京时间周六、周日开放。提交五个选项后，下个周末可领取答谢；错过周末不会丢掉已提交的答谢。离线版不提供玩家排行榜，漂流瓶入口改为本地旅行日记，不与其他玩家交换内容。

Windows 升级时保留 `user-data/`，更新程序和资源文件。安卓升级请覆盖安装；卸载应用会删除本地进度。清理浏览器网站数据也会删除网页存档，请先导出备份。

网页和 Windows 版保存照片时会下载 PNG 文件，可在浏览器或游戏窗口的下载提示中查看保存位置。明信片分享页也可以单独保存带信封的图片。安卓版会打开系统的文件保存界面，允许自行选择位置。

官网下载与完整教程：https://amy22707.github.io/studio/travel-frog/ 。其中“从官服保存自己的进度”介绍支持客户端的日志开启、采集、解析、导入与恢复原脚本的过程。

## 本机构建

源码仓库不包含原游戏资源。自行准备原客户端、配置与本机资源镜像后，在 Windows CMD 中执行：

```bat
python -m pip install --target .build-deps -r requirements-build.txt
python packaging\build_apks.py
python packaging\build_windows.py
python packaging\bundle_release.py
python packaging\build_windows_setup.py
python packaging\fetch_mobile_runtime.py
python packaging\build_mobile.py
python packaging\build_phone_apks.py
```

旧的 `build_apks.py` 用于准备原游戏资源和电脑连接型 APK。独立手机版由 `build_phone_apks.py` 构建。需要本机 Java、Apktool 与签名工具；安卓编译依赖和 Pyodide 下载到 `.build/`。

新构建产物位于 `release/TravelFrogNormal/`、`release/TravelFrogGallery/`、`release/Android/` 和 `release/TravelFrogMobile/`。手机网页目录可以部署到 HTTPS 静态站点的子目录，不需要网站服务端运行 Python。

安卓发布包使用独立应用标识，可以与先前的测试包并存。进度通过同版本的 `.frogsave` 迁移。构建时指定长期保存的签名密钥库和别名，通过环境变量 `FROG_ANDROID_KEYSTORE_PASSWORD` 提供密码。APK 输出到 `release/AndroidRelease/`，后续覆盖升级继续使用同一密钥库。

```bat
python packaging\build_phone_apks.py --release-keystore signing\android\release.p12 --key-alias travelfrog
```
