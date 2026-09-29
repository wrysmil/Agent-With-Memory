# DISPATCH-TRACK: Agent 档案存储与 CRUD（一期）

> 日期：2026-09-29
> Plan: `.ai-runtime-artifacts/plans/2026-09-29-agents-storage-and-crud-plan.md`
> Dispatch: `.ai-runtime-artifacts/plans/2026-09-29-agents-storage-and-crud-dispatch.md`
> Contract: `.ai-runtime-artifacts/contracts/2026-09-29-agents-contract.md`

---

## 元信息

- Workspace: **主 checkout**（用户 2026-09-29 指示不用 worktree）
- Branch: `feature/memory-system`（不切分支，逐 WU 顺序合入）
- GROUP 数量: 4（按 dispatch 执行图）
- 尾部: 集体测试 → 集体审查

> **无 worktree 下的冲突防线**：并行只发生在**文件集完全不相交**的 WU 之间。
> 后端 WU 只碰 `nanobot/` + `tests/webui/test_agents_*.py`；
> 前端 WU 只碰 `webui/src/`。同一波次内的 WU 若出现文件重叠，立即降级为串行。
>
> 契约：任何 coder 若发现需要改**不属于自己 WU** 的文件才能跑通，
> **停下来报告**，不得自行修改 —— 否则会污染并行中的其他 WU。

---

## 依赖波次

| 波次 | 可并行 WU | 前置 |
| --- | --- | --- |
| WAVE-1 | WU-01, WU-05 | 无 |
| WAVE-2 | WU-02 | WU-01 |
| WAVE-3 | WU-03 | WU-01, WU-02 |
| WAVE-4 | WU-04, WU-06 | WU-03 / WU-05 |
| WAVE-5 | WU-07 | WU-06 |

后端链 WU-01→02→03→04 与前端链 WU-05→06→07 除尾盘联调外互不相交。

---

## GROUP-1

| WU | 状态 | 描述 | Closeout |
|----|------|------|----------|
| WU-01 | completed | 数据模型与静态目录 | 28 passed, ruff OK, basedpyright 0 error |
| WU-05 | completed | 前端 API 客户端 | 6 passed, tsc OK |

## GROUP-2

| WU | 状态 | 描述 | Closeout |
|----|------|------|----------|
| WU-02 | completed | 存储层 AgentStore | 20 passed, ruff OK |

## GROUP-3

| WU | 状态 | 描述 | Closeout |
|----|------|------|----------|
| WU-03 | completed | agents_api + agents_routes | 31 passed, basedpyright 0 error |

## GROUP-4

| WU | 状态 | 描述 | Closeout |
|----|------|------|----------|
| WU-04 | completed | 端点注册与 gateway 绑定 | 573 passed, 6 个 action 已注册 |
| WU-06 | completed | 主从布局骨架 + 端点接线 | build OK, 13/13 passed |
| WU-07 | pending | 详情区 Tab 化与概览 | - |

---

## Closeout

| 项目 | 状态 | 产物 |
|------|------|------|
| collective-test | pending | `verifications/2026-09-29-agents-storage-and-crud-collective-test.md` |
| code-review | pending | `reviews/2026-09-29-agents-storage-and-crud-code-review.md` |

---

## 进度日志

[2026-09-29] DISPATCH-WAVE-1 | Leader | Status: started
Detail: 先按 dispatch 建了 2 个 worktree，用户指示「不用创建 worktree 直接改吧」，
已 `git worktree remove` + 删分支，回到主 checkout 直改模式。
派发 WU-01（models + catalog）与 WU-05（前端 api.ts）并行。
一个只碰 `nanobot/agents/`，一个只碰 `webui/src/lib/agents/api.ts`，文件集不相交。
（WU-02 追加同波次：store.py 只 import 前两者，代码形状已由 plan Task 1/2 钉死，
且禁止它回改 models.py / catalog.py —— 冲突面为零。）

[2026-09-29] WAVE-1-complete | Leader | Status: verified
Detail: 合并验证 `pytest tests/webui/test_agents_{models,catalog,store}.py -q` → 48 passed；
`ruff check nanobot/agents/` → All checks passed；`vitest run src/tests/api-agents.test.ts` → 6 passed；
`tsc -p tsconfig.build.json` → OK。

[2026-09-29] LEADER-INTERVENTION | Leader | Status: done
Detail: WU-05 报告的偏离 1 —— 因 `lib/api.ts` 的 `request`/`mutation` 是模块私有而复刻了 30 行。
Leader 判定为不该留下的技术债，已处置：
  1. `lib/api.ts:89,137` 两处加 `export`（原本零外部引用者，改动无行为影响）
  2. `agents/api.ts` 删掉复刻，改为 import，并**显式**传 `AGENTS_READ_TIMEOUT_MS` /
     `AGENTS_MUTATION_TIMEOUT_MS`
  3. 关键细节：`lib/api.ts` 的 `request` 第四参默认值是 `0`（不限时），
     而复刻版默认 `20_000`。若直接改用导入版而不显式传值，
     智能体的 7 个读请求会从 20s 超时退化成**永不超时**。已按 `lib/api.ts`
     既有约定（`:206` 显式传 `API_READ_TIMEOUT_MS`）补上参数。
  结果：`agents/api.ts` 159 → 129 行，6 个测试仍全绿。

[2026-09-29] DISPATCH-WAVE-2 | Leader | Status: started
Detail: 派发 WU-03（agents_api + agents_routes）与 WU-06（主从布局骨架）并行。
WU-03 只碰 `nanobot/webui/agents_*.py` + `tests/webui/`；
WU-06 只碰 `webui/src/`。文件集不相交。

[2026-09-29] WAVE-2-complete | Leader | Status: verified
Detail: WU-03 完成后端 payload 函数与域 handler；WU-06 完成主从双栏骨架。
WU-04 随后注册端点（6 个 action）并修掉 `TOOL_RISK_OVERRIDES` 的 `execute_command` → `exec`。
WU-04 额外发现并修正了计划的两处缺口：写路径也必须登记进 `_SYSTEM_ROUTES`（否则所有写操作 404）、
`_null_agents_operations` 应抛 503 而非返回空列表（谎报「没有档案」）。
后端 `pytest tests/webui/` → 579 passed。

[2026-09-29] CATALOG-CLEANUP | Leader | Status: verified
Detail: 清掉 `TOOL_CATEGORIES` 与 ops-runner 预设里的 `execute_command` 死键（真实工具名经
`nanobot/agent/tools/shell.py:246` 确认为 `exec`）。Coder 另发现 4 个同类死名
（notebook_edit / long_task / image_generation / session_messages / search），
因需逐条判断原意指哪个真名，未擅自重命名，改以 `_KNOWN_STALE_TOOL_REFS` 白名单钉住，
清单不许扩大也不许悄悄删。`pytest tests/webui/` → 579 passed。

[2026-09-29] I18N-GAP | Leader | Status: 已定性为既有失败，待决
Detail: `i18n.test.tsx > keeps every locale aligned` 失败。经比对 HEAD(a397aca) 的 locale 文件确认：
**该测试在本次开发之前就已是红的** —— 8 个非 en/zh-CN 的 locale 当时各缺 152 个 key，
来自上一个 commit「agents 管理界面」只补了 en + zh-CN。本次新增 14 个 key 使缺口变为 166。
即：计划里「其余 8 个语言靠 fallbackLng 兜底、不必对齐」的判断与项目既有测试相抵触，
该判断有误。非本次引入，但被本次放大。

[2026-09-29] WU-07-pending | Leader | Status: 未开工
Detail: 详情区 Tab 化（概览/能力/设置）与概览页尚未实现，
`parts/AgentPreviewRail.tsx` 仍在，`AgentDetailPane.tsx` 无 tab 结构。
