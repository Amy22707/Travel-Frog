# 工具目录说明

这里放的是离线保存项目的可复用工具。当前客户端、服务器、存档、抓包和资源备份不属于本目录的清理范围。

GitHub 只包含根目录 `.gitignore` 允许的工具源文件。下文提到的资源分析产物、图片下载目录和 `archive/` 均保留在完整本机目录中，不随仓库发布。仓库结构与运行前提见[项目说明](../README.md)。

### 双版本构建与验证

- `build_gallery_state.py`、`complete_gallery_state.py`：从本机基础存档构建、补齐独立图鉴版。
- `build_gallery_client.py`、`prepare_gallery_apk.py`：客户端差异补丁和旧版 APK 本机构建辅助。
- `patch_album_pages.py`：相册翻页补丁。
- `test_logic_audit.py`、`test_seasonal_activities.py`：堆肥、资源数量、返家进度及年度活动回归测试。
- `test_portable_saves.py`：新建、导入导出、照片保留、版本隔离和存档单进程写入测试。
- `test_desktop_browser.cjs`：使用独立的 Playwright 测试浏览器验收 Windows 客户端。需要在本机准备构建产物与测试存档。

## 当前工具

### `capture/`

- `parse_capture.py`：最初登录快照解析。
- `parse_action_capture.py`：单次动作抓包解析。
- `parse_travel_capture.py`：旅行/返家抓包解析。
- `parse_album_anim_capture.py`：历史相册图层与 `animpicture` 抓包解析。
- `parse_warehouse_capture.py`：仓库类抓包解析，当前 v3。
- `sanitize_capture.py`：初始抓包脱敏。

### `resources/`

- `make_frog_static_analysis_bundle.py`：从备份生成静态分析 bundle。
- `mirror_frog_hotfix_resources.py`：按 patch manifest 镜像资源并校验 MD5。
- `pack_picture_cache.py`：打包保存的图片缓存。
- `download_picture_reference_104.py`：图片构图调查用的公开静态资源下载器。
- `scan_eab_cache.py`：扫描/解析 EAB 缓存。

### `travelmap/`

- `extract_static_travelmap_context.py`：从当前离线 JS 提取地图上下文。
- `scan_travelmap_cache.py`：只读扫描旧 TravelMap 缓存。

### `patches/current/`

- `patch_stage6h3a6_diary_force_persist_robust.py`：当前推荐的日记强制持久化 patch。

## `archive/`

归档内容仍保留，目的是保存演进证据和必要的回退材料，不应直接当作当前 patch 使用：

- `legacy_builders/`：早期 `make_capture_js.py`、`make_offline_js.py`。
- `parsers/warehouse_legacy/`：仓库解析器 v1/v2，已由当前 v3 取代。
- `patches/travelmap/`：TravelMap H5/静态地图的历史 patch 链。
- `patches/diary/`：日记功能及非最终持久化 patch 链。
- `one_off/album/`：相册一次性诊断/修复。
- `one_off/diary/`：北京历史日记一次性存档工具。

## 维护规则

1. 当前 source-of-truth 仍是 `server/server.py`、`server/data/state.json`、`server/offline_policy.json` 和 `client/js/main.offline.stage6i2.js`。
2. 新 patch 必须按结构边界定位、可重复运行，并在写入前备份、写入后重新验证；完成后只保留当前推荐版在 `patches/current/`。
3. 旧 patch 不删除，移入 `archive/patches/<主题>/`，并保留原始文件名与阶段号。
4. 一次性状态迁移工具不得放在当前工具目录；如需再次使用，先核对当前 `state.json` 并确认幂等性。
5. `LOCAL_ONLY` 抓包只能留在本机；新工具输出优先生成 `SHARE_SAFE`。

## 旧路径映射

整理前交接文档中的路径对应为：

```text
tools/parse_album_anim_capture.py
  -> tools/capture/parse_album_anim_capture.py
tools/scan_travelmap_cache.py
  -> tools/travelmap/scan_travelmap_cache.py
tools/patch_stage6h3a6_diary_force_persist_robust.py
  -> tools/patches/current/patch_stage6h3a6_diary_force_persist_robust.py
tools/parse_warehouse_capture_v3.py
  -> tools/capture/parse_warehouse_capture.py
```
