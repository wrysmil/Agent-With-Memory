---
artifact: implementation-plan
route: superpowers:writing-plans
skills:
  - writing-plans
skills_evidence:
  - ~/.agents/skills/writing-plans/SKILL.md
dispatch: n/a
source:
  - AGENTS.md
  - core/routing.md
  - .ai-runtime-artifacts/specs/2026-09-14-memory-total-switch-spec.md
created_at: 2026-09-14
status: draft
approved: false
---

# Plan：记忆总开关（WebUI 配置页 + 热生效接线）

> 本计划严格按已批准 spec 落地，无方案变更。范围有界、可在单个 worktree 内顺序完成，**不**做并行 dispatch（FM `dispatch: n/a`）。

---

## Goal

让用户能在 WebUI「设置 → 记忆」页通过一个总开关**热控制**聊天是否自动整理与检索记忆。点击「开」= 后续聊天按新值开启抽取 + 检索；点击「关」= 后续聊天立即停止抽取与检索。**不重启 gateway**。开关默认关。

## Architecture（现状 → 目标）

### 现状

- `AgentLoop.__init__` 接收 `memory_extraction_enabled` / `active_retrieval_enabled` 两个内部布尔（[loop.py:315-318](nanobot/agent/loop.py#L315)），`__init__` 时一次性装配 hook factories / ContextBuilder
- `MemoryExtractionHook` 在 `before_iteration` / `after_run` / `on_finally` 直接干活
- `ContextBuilder.build_messages` 用 `self._active_retrieval_enabled` 判断是否检索（[context.py:133](nanobot/agent/context.py#L133)）
- gateway 构造 `AgentLoop.from_config(...)` 不传以上两个 flag → 默认 False → 抽取/检索**从未工作**
- WebUI 「设置 → 记忆」无任何开关

### 目标

- schema: `AgentDefaults.memory_enabled: bool = False`
- loop 内部参数 `memory_extraction_enabled=True` / `active_retrieval_enabled=True` **始终传**，但加 `memory_enabled_provider` 闸门
- `MemoryExtractionHook` 每个回调开头判 `provider()`，False 直接返回
- `ContextBuilder` 加 `_should_retrieve()`：合并 `active_retrieval_enabled` 与 `provider()` 两层判断
- gateway 始终构造 `MemoryServices` + `RetrievalEngine`（常驻），传 provider 实时读 `defaults.memory_enabled`
- settings mutation 写完库后通过 bus publish `RUNTIME_CONTROL_MEMORY_RELOAD`，listener ack
- 前端 MemorySection 顶部加 toggle，提交后等 ack，按结果展示 toast

## Tech Stack

- 后端：Python 3.11+、Pydantic v2、asyncio
- 前端：React 18 + TS、react-i18next
- 现有依赖无新增

---

## Task 拆分（顺序执行，无并行）

### Task 0：实施前确认（阻塞 Task 1 启动）

**目标**：消除 spec §8 中风险项的歧义。

**步骤**：
1. grep `nanobot/webui/settings_system.py` 中 `defaults.timezone` 与 `defaults.tool_hint_max_length` 的写入方式——确认是**原地修改**字段还是替换 `defaults` 对象
2. grep `nanobot/config/loader.py` 确认 `Config` 是否每次 reload 都是新对象（决定 provider 读取策略）

**产物**：在执行日志中写一段「实施前确认结论」，影响 Task 1 的 provider 实现细节

**DoD**：上述 grep 完成，结论记录

---

### Task 1：schema + loop 内部闸门（后端核心）

**目标**：让 loop 具备「运行时按 provider 决定是否走抽取/检索」的能力，**但不让任何生产入口行为变化**（向后兼容）。

**改动**：
- `nanobot/config/schema.py`（`AgentDefaults`）：加 `memory_enabled: bool = False`
- `nanobot/agent/loop.py`（`AgentLoop.__init__`）：加可选参 `memory_enabled_provider: Callable[[], bool] | None = None`
- `nanobot/agent/loop.py`（`AgentLoop.from_config`）：**不**修改（仍不传这个新参），保持 `**extra` 透传机制以供未来 gateway 接入
- `nanobot/agent/hooks/memory_extraction.py`（`MemoryExtractionHook.__init__`）：加 `memory_enabled_provider=None`
- `nanobot/agent/hooks/memory_extraction.py`（3 个回调 `before_iteration` / `after_run` / `on_finally`）：入口早返回 `if self._memory_enabled_provider is not None and not self._memory_enabled_provider(): return`
- `nanobot/agent/hooks/memory_extraction.py`（`create_memory_extraction_hook_factory`）：签名加 `memory_enabled_provider=None`，构造 hook 时透传
- `nanobot/agent/context.py`（`ContextBuilder.__init__`）：加 `memory_enabled_provider=None`
- `nanobot/agent/context.py`（`build_messages` 第 133 / 212 行）：改为 `if not self._should_retrieve(): return`
- `nanobot/agent/context.py`：新增方法 `_should_retrieve(self) -> bool`

**DoD**：
- `pytest tests/memory -q` 全绿（向后兼容：所有现有测试不传 provider，等价旧行为）
- `pytest tests/agent -q` 全绿
- ruff lint 无新增告警

---

### Task 2：bus reload 事件通路

**目标**：参照 `image_generation_reload` 模式（[image_generation.py:277](nanobot/agent/tools/image_generation.py#L277)）加 `memory_reload`。

**改动**：
- `nanobot/bus/events.py`：加 `RUNTIME_CONTROL_MEMORY_RELOAD = "memory_reload"`
- 新文件 `nanobot/memory/reload.py`：
  - `async def request_memory_reload(bus, *, timeout=5.0) -> dict`：publish_inbound + await ack
  - `async def handle_memory_reload(message, agent_loop) -> dict`：listener 入口，**仅 ack**（实际热生效靠 provider 实时读，不重建对象）
- `nanobot/cli/gateway_runtime.py`：注册 listener（参照 image_generation 的 listener 注册位置）

**DoD**：
- 单测 `tests/memory/test_memory_reload.py`：mock bus，验证 publish 路径与 ack 返回值

---

### Task 3：gateway 接线（让 WebUI 真正启用）

**改动**：
- `nanobot/cli/gateway_runtime.py`：
  - 顶部 import `MemoryServices`、`RetRetrievalEngine`（如 `RetrievalEngine` 直接 import）
  - 在 `AgentLoop.from_config` 之前：
    - 构造 `memory_services = MemoryServices.for_workspace(_MEMORY_WORKSPACE_ID, config.workspace_path)`
    - 构造 `retrieval_engine = RetrievalEngine(store=memory_services.database, brain=None)`
    - 定义 `def _memory_enabled_provider() -> bool: return config.agents.defaults.memory_enabled`
  - `from_config` 调用加新参：
    - `memory_extraction_enabled=True`
    - `memory_services=memory_services`
    - `active_retrieval_enabled=True`
    - `retrieval_engine=retrieval_engine`
    - `memory_enabled_provider=_memory_enabled_provider`

**DoD**：
- `nanobot gateway` 启动不报错（日志应见 `memory services ready` / `retrieval engine ready` 类信息，按需加）
- `grep -n "memory_extraction_enabled\|active_retrieval_enabled" nanobot/cli/gateway_runtime.py` 显示两行传 True

---

### Task 4：设置写入与读取

**改动**：
- `nanobot/webui/settings_system.py`：
  - `update_agent_system_settings` 加 `memory_enabled` 分支（原地写 `defaults.memory_enabled`，**不**标 `restart_required`）
  - 写完调 `await request_memory_reload(self.bus)`；异常兜底设 `restart_required=True`
  - `_coerce_bool` helper 复用或新增（接受 `"true"/"false"/"1"/"0"/"on"/"off"` 等）
  - `system_settings_payload` 在 `runtime` 段加 `memory_enabled`
- `nanobot/webui/settings_routes.py`：确保 mutation 路径能拿到 `bus`（参照现有 image_generation 调用方式）

**DoD**：
- 单测 `tests/webui/test_memory_setting_persistence.py`：模拟 POST /api/settings/agent 改 `memory_enabled`，验证 `config.json` 写入 + reload 请求发出
- `pytest tests/webui -q` 全绿

---

### Task 5：前端 payload 类型 + MemorySection toggle

**改动**：
- `webui/src/lib/types.ts`：`SettingsPayload.runtime.memory_enabled: boolean`
- `webui/src/components/settings/memory/MemorySection.tsx`：
  - 顶部加 toggle 卡片（`<Switch>` 复用 `SettingsControls`）
  - 状态绑 `runtime.memory_enabled`
  - 提交后根据 `restart_required` 与 mutation 结果展示不同 toast
  - 关闭时显示 `disabledNotice`
- `webui/src/i18n/locales/en/common.json` 与 `webui/src/i18n/locales/zh-CN/common.json`：补 5 个键，详见 spec §4.9

---

### Task 6：end-to-end 自测（实跑）

**步骤**：
1. `nanobot gateway` 启动
2. WebUI 进「设置 → 记忆」→ 默认关 → 切「开」→ toast「已生效」→ 发一条「我喜欢喝美式咖啡」→ 等回复 → 检查 state.db 有 `memories` 新增
3. 切回「关」→ 发一条「我爱吃辣」→ 不应新增记忆；之前那条仍在
4. 验证 `config.json` 持久化

---

## 自检

- [x] Goal 单义、可验收
- [x] Architecture 现状与目标清晰对照
- [x] Tech Stack 无新增依赖
- [x] Task 拆分按依赖顺序（Task 0 阻塞 Task 1；Task 1 → 2 → 3 → 4 → 5 → 6 串行）
- [x] 每 Task 有 DoD（可验证命令 / 文件状态）
- [x] 风险已在前置确认 Task 处理
- [x] 不依赖未声明的外部资源

---

## Next

**（写入后须暂停 — 即使用户句末含「然后执行」）**

- 计划确认 → 说「**开始实现**」或「**执行**」
- 需要调整 → 直接说修改意见
- 想拆并行 → 改为写 `*-dispatch.md` 并审后说「并行执行」

---

## 附录：与 spec 的对齐

| spec 节 | 对应 Task |
|---|---|
| §3.3 切换语义 | Task 2（bus 通路）+ Task 4（mutation 写入） |
| §4.1 schema 字段 | Task 1 |
| §4.2 loop 内部闸门 | Task 1 |
| §4.3 gateway 接线 | Task 3 |
| §4.4 bus reload | Task 2 |
| §4.5 设置写入 | Task 4 |
| §4.6-4.9 前端 / i18n | Task 5 |
| §5 验收 | Task 6 |
| §8 风险「原地改 vs 替换」 | Task 0 |