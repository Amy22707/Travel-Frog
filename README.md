# Travel Frog 离线保存项目

《旅行青蛙·中国之旅》的非官方离线保存与协议兼容项目。保留原客户端的交互方式，通过本地 WebSocket 服务处理旅行、照片、花圃、家具、手工品与活动状态。

本仓库公开独立编写的服务端、解析器和客户端补丁工具，不提供原游戏 APK、完整客户端 JS、美术音频、提取配置表或个人存档。运行时需要自行准备合法取得的原游戏文件和兼容数据。本项目与原开发商、发行商无隶属或授权关系。

## 两个版本

| 版本 | 存档 | Windows 服务端口 | 安卓客户端端口 |
| --- | --- | --- | --- |
| 普通版 | `server/data/state.json` | 52352 | 8080 |
| 图鉴版 | `server/data/gallery_state.json` | 52353 | 8081 |

普通版保留实际库存、制作和旅行进度。图鉴版使用独立存档，解锁收藏内容，并按图鉴版规则保持资源数量；部分季节活动常驻。

Windows 启动器、服务端 EXE、两个本地资源 APK 与存档导入导出正在开发。当前仓库是服务端和工具源码，不是下载后即可独立运行的完整游戏包。

## 源码目录

```text
Travel-Frog/
├─ server/
│  ├─ server.py                   本地 WebSocket 服务与玩法状态
│  ├─ offline_policy.json         离线概率、时长和路线策略
│  └─ config/lunar_festivals.json 农历节日日期
├─ client/js/calendar.offline.js  离线日历扩展
├─ tools/
│  ├─ capture/                   抓包解析与脱敏代码
│  ├─ resources/                 资源容器解析和本机资源整理工具
│  ├─ travelmap/                 本机地图资源分析工具
│  ├─ patches/current/           当前维护的窄补丁
│  ├─ build_gallery_*.py         图鉴版客户端与存档构建
│  └─ test_*.py                  状态与活动回归测试
└─ docs/                         架构与本机目录说明
```

完整本机工作目录还包含 `apk/`、`backup/`、`cache/`、`capture/`、`hotfix/`、`picture/`、`tmp/`，以及私有的客户端与存档。这些目录不进入 Git，原有内容保留在本机。具体规则见[仓库与本机文件](docs/repository-layout.md)。

## 本地开发

需要 Python 3.10 或更高版本；当前环境使用 Python 3.14。以下为 Windows CMD 命令，在项目根目录执行：

```bat
python -m pip install -r requirements.txt
python server\server.py --port 52352
```

图鉴版：

```bat
python server\server.py --gallery --port 52353
```

启动前，需要在本机准备 `server/config/` 中所需的原游戏配置、`client/config/config.eab`，以及对应版本的离线存档。普通版首次创建存档还可读取本机完整登录抓包；原始抓包不属于公开仓库。

现有安卓客户端通过 ADB reverse 连接 Windows 服务：

```bat
adb reverse tcp:8080 tcp:52352
adb reverse tcp:8081 tcp:52353
```

本机准备好配置后，可执行回归测试：

```bat
python -m unittest tools.test_logic_audit tools.test_seasonal_activities
```

## 数据与实现依据

客户端和正式配置可以确定的结构优先沿用；无法从保留资料确定的服务端概率、时长与路线权重集中在 `offline_policy.json`，这些参数属于本地规则。两份存档互相独立，服务默认只监听本机。

正式服抓包中的账号、令牌、设备标识和个人存档留在本机。工具输出名为 `SHARE_SAFE` 也不代表自动进入 Git；提交内容由 `.gitignore` 的源码白名单控制。

原游戏代码、图像、音频与商标的权利归各自权利人所有。本仓库不对原游戏内容授予许可。公开源码尚未指定统一开源许可证。
