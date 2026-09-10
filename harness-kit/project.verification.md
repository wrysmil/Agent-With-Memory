# Project Verification

本文件描述当前项目的验证命令。迁移到新项目后，应通过 `harness-kit/init/project-profiler.prompt.md` 重新生成。

## Harness 验证

```bash
bash harness-kit/scripts/harness-check.sh
```

> 脚本位于 `harness-kit/scripts/` 时以 **source** 布局检查 kit 自身；投影与 `project.*` 更新后应在项目根执行并人工确认根目录 `AGENTS.md`、`.claude/`、`.ai-runtime-artifacts/` 子目录齐全。

## 应用验证

| 命令 | 用途 |
| --- | --- |
| `uv sync --all-extras --dev` | 安装 Python 开发依赖（uv） |
| `uv run --no-sync python -m scripts.install_channel_dependencies --all-channels` | 安装 channels 依赖（匹配 CI） |
| `uv run --no-sync python -m pytest <test_path>::<test_name> -v` | 跑单个 Python 测试 |
| `uv run --no-sync python -m pytest tests/ -q` | 跑全部 Python 测试（CI 用 `-n auto --dist loadfile`） |
| `cd webui && bun run test` | WebUI 单测 |
| `cd webui && bun run build` | WebUI 生产构建 |
| `cd tui && bun run check`、`bun run test`、`bun run build` | TUI 检查/测试/构建 |

## 静态检查

| 命令 | 用途 |
| --- | --- |
| `uv run --no-sync ruff check nanobot/` | Python lint（规则 E、F、I、N、W，E501 忽略） |
| `uv run --no-sync basedpyright` | Python 严格类型检查（匹配 CI） |
| `cd webui && bun run lint` | WebUI ESLint |
| `uv pip check` | 依赖一致性（匹配 CI） |
| `bash harness-kit/scripts/harness-check.sh` | Harness 文件与产物 front matter |

## 最小验证策略（Leader）

1. 改 `nanobot/`：对应 `pytest` 单测 + `ruff check nanobot/`；涉及类型则 `basedpyright`。
2. 改 `webui/`：`bun run test` + `bun run build`（+ `bun run lint` 若 touched TS）。
3. 改 `tui/`：`bun run check` + `bun run test`。
4. 涉及 gateway/WS 协议：确认 `nanobot gateway` 可启动并跑冒烟。
5. 声称完成前：运行与本 diff 相关的上表命令并贴输出摘要（`verification-before-completion`）。

## 待确认项

- `uv.lock` 被 `.gitignore` 排除但 CI 引用 —— 本地 `uv sync` 生成后是否提交（`uv.lock` 策略见 project.profile 待确认项）。
- WebUI 测试用 Bun 跑（`bun run test`）还是含 `package-lock.json` 的 npm 一致性检查（CI 两处都有），本地以 `bun run test` 为准。
