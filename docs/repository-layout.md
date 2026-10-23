# 仓库与本机文件

Git 使用源码白名单。新增文件先留在本机，审核后再将具体路径加入 `.gitignore` 的允许规则。

| 位置 | 用途 | 是否提交 |
| --- | --- | --- |
| `server/server.py` | 独立编写的协议和状态逻辑 | 是 |
| `server/offline_policy.json` | 本地模拟参数 | 是 |
| `server/config/lunar_festivals.json` | 节日日期 | 是 |
| `server/config/` 其余文件 | 提取配置、历史照片与本机分析数据 | 否 |
| `server/data/` | 两版存档、历史备份及未知命令日志 | 否 |
| `client/js/calendar.offline.js` | 独立编写的日历扩展 | 是 |
| `client/js/main*.js` | 完整原版或修改后客户端 | 否 |
| `client/config/`、`client/js/vendor/` | 本机资源与依赖 | 否 |
| `tools/` 中允许的源文件 | 解析器、构建和窄补丁 | 是 |
| `tools/archive/`、分析产物和截图 | 本机历史资料 | 否 |
| `apk/`、`hotfix/`、`picture/` | 安装包与游戏资源 | 否 |
| `backup/`、`cache/`、`capture/` | 原始备份、缓存与抓包 | 否 |
| `tmp/`、生成的 EXE/APK/压缩包 | 构建中间文件和本机交付物 | 否 |
| `AGENTS.md`、交接文档与任务提示词 | 本机开发会话资料 | 否 |

目录整理只调整源码组织与默认路径，不移动或删除原有游戏资源和存档。GitHub 仓库中的文件比完整本机目录少，这是预期的发布结构。
