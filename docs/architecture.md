# 架构

两个平台使用原游戏 UI 与资源，连接本机 WebSocket。Windows 启动器管理本地服务，以 Edge 或 Chrome 独立窗口显示游戏；安卓客户端使用轻量 WebView 外壳。服务持有单份内存状态并将变化原子写入对应版本的 JSON 存档。

```text
普通版客户端 127.0.0.1:8080 → ADB reverse → Windows 127.0.0.1:52352 → state.json
图鉴版客户端 127.0.0.1:8081 → ADB reverse → Windows 127.0.0.1:52353 → gallery_state.json
Windows 游戏窗口 → 本机 HTTP 资源 + 本机 WebSocket → 对应版本存档
```

`server/server.py` 负责协议请求、登录同步、旅行和聚会结算、库存、相册与动态照片、访客、家具、手工品、花圃及活动。`server/offline_policy.json` 保存无法从客户端资料中确定、由离线版选择的参数。

存档中的 `sync_pushes` 保存各模块的权威状态。青蛙数据与客户端设置以 `client.load_role` 为准；顶层同名旧字段不能作为新状态覆盖依据。`offline_meta` 保存离线计时、任务和照片完整图层等扩展数据。

官服登录抓包可用来建立初始状态，但相册索引不等于完整照片图层。保留历史构图需要同时保存相册详情。`desktop/saves.py` 支持存档包、JSON 与完整 JSONL，导入时合并相册详情并标记为独立存档，跳过针对原开发账号的历史照片和固定期数修复。

图鉴版可以从空白进度和本机配置生成，不读取原开发者存档。`packaging/build_apks.py` 整合本机资源并构建两个新包名，`packaging/build_windows.py` 打包服务、启动器与双版本 Web 客户端。`prepare_gallery_apk.py` 保留为旧安装方式的本机辅助工具。

源代码运行时仍保留原开发路径。启动器通过 `--data-dir` 指定版本独立目录；打包后的配置和运行库位于 `_internal/`，用户数据保存在外部 `user-data/`。默认数据路径相对于程序目录保存，可以整体移动。服务持有存档进程锁，阻止另一服务同时写入同一存档。关闭启动器会关闭由它启动的服务。
