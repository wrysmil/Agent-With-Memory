# nanobot → 新品牌 品牌重塑 spec

日期：2026-09-30
状态：待用户确认

## 1. 目标

把 nanobot 换成一个新名字和新 logo，用户在产品表面（WebUI、TUI、CLI、文档、浏览器标签、桌面图标）看不到旧品牌。

## 2. 范围决策（用户已选）

**换：品牌层 + 界面文案 + 包名/CLI 命令。**
**不换：Python 包目录、数据目录、环境变量。**

四层品牌分布与处置：

| 层 | 内容 | 处置 |
| --- | --- | --- |
| 视觉资产 | `webui/public/brand/*` 6 个图标、`images/nanobot_logo.svg`、`nanobot_mark.svg` | 换 |
| 界面文案 | WebUI 字面量 + 10 语言 i18n、TUI 字面量、`index.html` title/meta/boot 文案 | 换 |
| 产品标识 | `pyproject.toml` 包名/作者、`[project.scripts]` CLI 命令名、README/docs | 换 |
| 代码命名空间 | Python 包目录 `nanobot/`、16058 条 import | **不换** |

## 3. 关键约束（范围裁剪换来的具体好处）

保留 `~/.nanobot` 与 `NANOBOT_*` 带来三条硬性免改：

1. **Dockerfile 完全不动。** 镜像内 `nanobot` 既是系统用户也是容器用户，数据目录 `~/.nanobot` 建在 `/home/nanobot/` 下。改数据目录必须连带改系统用户，属于大改。保持数据目录 = 保持系统用户 = Dockerfile、`docker-compose.yml`、`docker-compose.bwrap.yml`、`entrypoint.sh` 零改动。
2. **已部署实例升级不丢数据。** 历史会话、配置、凭据都在 `~/.nanobot`，不动即无迁移成本。
3. **环境变量兼容。** 用户已有的 `NANOBOT_*` 配置继续生效。

因此本次重塑是**纯表现层变更，零迁移风险**。

## 4. 命名要求

新名字需满足：

- CLI 命令名可打、无冲突（不与 `npm`/`bun` 等常用命令撞名）
- `pip install <name>` 在 PyPI 可用，或接受未发布状态
- npm scope 可用（`@<name>/tui`、`@<name>/webui`）
- 中英文都易读，适合个人自托管场景
- 与产品定位契合：有记忆、会持续成长的个人 agent

**候选待定** —— 见下方待确认项。

## 5. 改动清单

### 5.1 视觉资产
- `webui/public/brand/`：6 个 PNG 重新生成，文件名去 `nanobot_` 前缀
- `images/nanobot_logo.png` / `nanobot_logo.svg` / `nanobot_mark.svg`：替换或重命名
- 新增尺寸：favicon_32、apple_touch(180)、icon_192、icon_512、icon_maskable、mark.svg
- README 用截图 `images/nanobot_*.png`（5 张）文件名可保留（属内部资产引用），但 alt 文本与说明改新名

### 5.2 WebUI
- `webui/index.html`：title、description、apple-mobile-web-app-title、boot 文案（中英繁 3 套）
- `webui/public/manifest.json`：name / short_name / description / icon src
- `webui/src/i18n/locales/*/common.json`（10 语言）：`brand`、boot、connecting、title、gatewayHint、restart 系列、tab title、description
- `webui/src/components/Sidebar.tsx:158`：`/brand/nanobot_mark.svg` 引用
- 其余组件内散落字面量（`OverviewSettings.tsx:254` 兜底版本号、`SettingsControls.tsx` 确认弹窗等）

**不动的**：i18n key 名本身（如 `settings.nanobotFeatures.*`）保持不变，只改 value。key 改名是纯负担无收益。

**注意**：`localStorage` key（`nanobot-webui.*`）**保持不变**，否则用户升级后主题、语言、侧栏折叠状态全部丢失。

### 5.3 TUI
- `tui/package.json`：`@nanobot/tui` → `@<new>/tui`
- `tui/bun.lock`：对应更新
- TUI 源码与 README 中的 nanobot 字面量
- 保留 `tui/RELINKING.md` 里的法律与 source-offer 表述结构

### 5.4 Python 包元数据（不碰源码）
- `pyproject.toml`：
  - `name = "nanobot-ai"` → 新名
  - `authors` → 新署名
  - `[project.scripts]` `nanobot = ...` → 新命令名
  - `description` 改写
- `hatch_build.py`：docstring 里的 nanobot 路径说明（逻辑不动）

### 5.5 文档
- `README.md`：标题、徽章、安装命令、截图 alt
- `docs/`（60 文件）、`tui/README.md`、`CONTRIBUTING.md`、`SECURITY.md`、`AGENTS.md` 中的用户可见 nanobot 字面量
- `CLAUDE.md` / `AGENTS.md` 中描述产品身份的段落

## 6. 不改动清单（显式）

- Python 包目录 `nanobot/` 及全部 import
- `~/.nanobot` 数据目录逻辑（1291 处引用）
- `NANOBOT_*` 环境变量（1016 处）
- Dockerfile / docker-compose* / entrypoint.sh / render.yaml
- `webui/src` 里的 `localStorage` key
- i18n key 命名
- CHANGELOG 历史条目

## 7. 验证

- `cd webui && bun run build` 通过，产物中无旧品牌字面量
- `cd webui && bun run test` 通过
- `uv run --no-sync basedpyright` 无新增错误
- `ruff check nanobot/` 通过
- 全新环境 `pip install -e .` 后新 CLI 命令可用
- WebUI 启动后：浏览器标签页标题、侧栏 logo、manifest、favicon 均为新品牌
- 升级验证：已有 `~/.nanobot` 的实例升级后，主题/语言/历史会话/配置完好

## 8. 风险

| 风险 | 缓解 |
| --- | --- |
| 漏改残留旧名 | 5.1-5.5 完成后全仓库 grep 排除已知保留项，输出残留清单人工过一遍 |
| 误改 localStorage / 环境变量导致用户状态丢失 | 第 6 节显式不改动清单 + 审查重点 |
| 新名 PyPI/npm 已被占用 | 命名阶段先查可用性 |
| 旧命令 `nanobot` 消失，老用户升级后命令找不到 | 见 9.1 |

## 9. 待确认

1. **新名字**（含大小写、连字符风格）
2. **logo 设计方向**：现有 `nanobot_mark.svg` 是几何线条风格。新 logo 走同风格延续，还是重新设计？
3. **CLI 向后兼容**：是否保留 `nanobot` 作为旧命令名的 alias，指向新命令？
