# 旅行青蛙离线启动器

将整个目录解压到可写入的位置，双击 `FrogLauncher.exe`。保留 `_internal`、`apks` 等文件夹，不要单独移动 EXE。

`TravelFrog-Windows.zip` 包含电脑版运行环境，不含安卓 APK。需要安卓版本时，将 `FrogNormal.apk` 和 `FrogGallery.apk` 放入启动器旁的 `apks/` 文件夹。

## 开始游戏

Windows 直接游玩：选择版本，创建或导入存档，然后点击“电脑上玩”。启动器使用电脑上的 Edge 或 Chrome 打开独立游戏窗口。游戏资源和服务均在本机，无需安卓模拟器、手机号登录或 Python 环境；游玩时保留启动器运行。

安卓模拟器游玩：

1. 启动安卓模拟器并开启 ADB 调试。启动器会尝试查找 ADB，也可以选择模拟器自带的 `adb.exe`。
2. 在启动器中选择普通版或图鉴版，创建初始存档，或导入自己的离线存档。
3. 刷新设备，选择模拟器，点击“安装本版本 APK”。两版使用不同包名，可同时安装；不覆盖原游戏。
4. 点击“进入游戏”。启动器启动 Windows 服务，配置端口转接并打开游戏。

普通版按照实际库存与进度游玩。图鉴版使用独立存档，解锁收藏并固定资源数量。新建普通版提供 500 三叶草与三份基础食物。

这些 APK 内置游戏资源，不需要官服更新或手机号登录。当前安卓组合需要 Windows 端服务运行；APK 不是手机独立服务端。

## 存档

- 数据保存在启动器旁的 `user-data/normal/` 和 `user-data/gallery/`。
- “导出存档”生成 `.frogsave` 文件，包含当前版本的完整离线状态，包括日记和已经保存的照片图层。
- 导入支持 `.frogsave`、兼容的 `state.json` 以及完整初始化协议 `.jsonl`。导入前停止对应服务器。
- 导入覆盖前自动备份到该版本的 `import-backups/`；普通版和图鉴版存档不会混用。
- 官服账号密码不是存档。官服初始化抓包可能只有相册索引，历史照片的准确构图需要额外保存相册详情；导入工具会合并同一抓包中包含的照片图层。
- 存档包包含个人游戏数据，请自行保管。

升级启动器时保留 `user-data/`，更新程序和资源文件。启动器只停止它自己启动的服务器；如默认端口已被原服务占用，可以先关闭原服务，或在启动器中更改端口。

电脑版保存照片会下载 PNG 文件。安卓版会打开系统的文件保存界面，允许自行选择位置，不需要 root。

## 本机构建

源码仓库不包含原游戏资源。自行准备原客户端、配置与本机资源镜像后，在 Windows CMD 中执行：

```bat
python -m pip install --target .build-deps -r requirements-build.txt
python packaging\build_apks.py
python packaging\build_windows.py
python packaging\bundle_release.py
```

APK 构建使用本机的 Java、Apktool 与签名工具。`build_apks.py --help` 可指定 Java、原 APK、资源镜像和输出位置。构建产物保存在 `release/TravelFrog/`，不进入 Git。
