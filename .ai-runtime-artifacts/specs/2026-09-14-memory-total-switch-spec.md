---
artifact: spec
route: superpowers:brainstorming
skills:
  - brainstorming
skills_evidence:
  - ~/.agents/skills/brainstorming/SKILL.md
source:
  - AGENTS.md
  - core/routing.md
  - 用户三轮确认（位置/粒度/默认值）
created_at: 2026-09-14
status: draft
approved: false
---

# Spec：记忆总开关（WebUI 设置页 + gateway 接线）

> 关联背景：用户测试 WebUI 时发现对话不写入记忆（gateway 未接线 `memory_extraction_enabled` / `active_retrieval_enabled`），需要把开关暴露到配置页让用户自管。
> 关联代码：`nanobot/agent/loop.py`（开关默认值与 `__init__` 入口）、`nanobot/cli/gateway_runtime.py:482`（构造 `AgentLoop.from_config`）、`nanobot/config/schema.py`（`AgentDefaults`）、`nanobot/webui/settings_system.py:96`（`system_settings_payload`）、`webui/src/components/settings/memory/MemorySection.tsx`（前端顶部位置）。
> 相关今日产物：`tests/memory/manual_memory_test_cases.md`（已交付的测试用例）；`.ai-runtime-artifacts/specs/2026-09-14-memory-model-and-text-management-spec.md`（已存在，本 spec 视为其「总开关」子功能）。

---

## 1. 目标（用户视角）

用户在 WebUI「设置 → 记忆」页**最顶部**看到一个总开关：

- **点击「关」** → 关闭记忆功能：聊天不会自动写入语义记忆 / 情节 / 工作记忆；自动检索也不再向回复里注入历史记忆。
- **点击「开」** → 开放记忆功能：聊天自动整理；检索正常启用。

开关**默认关**（保持当前生产行为，避免现有用户配置变化）。**核心约束（用户原话）：「点击关/开启按钮，后续聊天也可以根据这个按钮的值来进行记忆」**——意味着切换后**不需要重启 gateway**，下一次发消息即按当前 `memory_enabled` 决定是否提取 /检索。

---

## 2. 用户决策记录（AskUserQuestion 已确认，不再讨论）

| 维度 | 决策 | 理由 |
|---|---|---|
| 开关位置 | **放记忆设置页顶部**（segmented tabs 上方） | 用户已确认：最少改动、用户好找；避免新增一级导航 |
| 开关粒度 | **一个总开关** | 用户已确认：符合当前诉求；细分开关留待后续 |
| 默认值 | **默认关** | 用户已确认：保守，不改变现有用户环境；老 `config.json` 缺字段时按关处理 |

---

## 3. 行为定义

### 3.1 开关 ON（开放记忆）

| 项 | 行为 |
|---|---|
| `memory_enabled` | True（**每次 callback 都从 config 实时读取**，不是构造时一次性绑定） |
| 抽取触发 | `MemoryExtractionHook` 在 `after_run` / `before_iteration` / `on_finally` 时，先读 `memory_enabled`，False 直接返回（不调 extractor） |
| 检索注入 | `ContextBuilder.build_messages` 每次构建上下文时读 `memory_enabled`，False 直接不走检索路径 |
| `memory_services` | gateway 构造时即创建并传入 loop（不依赖运行时切换：DB 连接常驻，开关本身只控「用不用」） |
| `retrieval_engine` | 同上：构造时即创建并传入 loop；运行时由 `ContextBuilder` 的 lazy getter 决定是否调用 |

### 3.2 开关 OFF（关闭记忆）

| 项 | 行为 |
|---|---|
| `memory_enabled` | False（运行时实时读，无需重启） |
| 抽取 hook | `MemoryExtractionHook` 入口早返回，**不调 extractor**，不写 scratchpad focus / 不触发 LLM |
| 检索注入 | `ContextBuilder.build_messages` 跳过检索分支 |
| `memory_services` / `retrieval_engine` | 对象仍在 loop 上常驻（避免热切换重建），但不被调用 |

### 3.3 开关切换（热生效，不重启 gateway）

用户切换 toggle → 前端提交 settings mutation → `update_agent_system_settings`：

1. 写 `config.agents.defaults.memory_enabled`（持久化到 `~/.nanobot/config.json`）
2. 通过 `bus` publish 一条 `RUNTIME_CONTROL_MEMORY_RELOAD` 事件（参照现有 `request_image_generation_reload` 模式，[image_generation.py:277](nanobot/agent/tools/image_generation.py#L277)）
3. loop 端有 listener：收到事件 → 仅 `ack`（**不**真正重建 extractor / retrieval engine，因为 hook / context builder 通过实时读 config 自行判断）→ 设置 mutation 等到 ack 后给前端返回 `restart_required=False`

**关键：实现层每条回调实时读最新 config，而不是依赖 loop 重启。** 这保证：
- 切换后**下一次**发消息即按新值决定是否写入 / 检索
- 没有中间态「旧会话还在抽取」的不一致

### 3.4 兼容

- 老 `config.json` 缺 `agents.defaults.memory_enabled` → Pydantic 默认 `False`（与现状一致），不报错。
- 旧 sessions 重启后行为不变（提取/检索 hook 不注册）。

---

## 4. 数据 / 接线改动

### 4.1 Schema（[nanobot/config/schema.py:116](nanobot/config/schema.py#L116) `AgentDefaults`）

新增：

```python
memory_enabled: bool = Field(
    default=False,
    validation_alias=AliasChoices("memoryEnabled", "memory_enabled"),
    serialization_alias="memoryEnabled",
)
```

> 字段名讨论：原本 `memory_extraction_enabled` 在 loop 里是控制 hook 注册的内部参数，本 spec 把它翻成「用户语义」——`memory_enabled`。loop 内的两个内部参数 `memory_extraction_enabled` / `active_retrieval_enabled` **仍按 True 传入以确保对象构建**；运行时由 hook / context builder 通过 `_memory_enabled_provider` 回调读取最新开关。

### 4.2 Loop 改造（[nanobot/agent/loop.py](nanobot/agent/loop.py)）

`AgentLoop.__init__` 新增参数（向后兼容，默认 None 表示永远视为 True，保留旧测试不走这条路径）：

```python
memory_enabled_provider: Callable[[], bool] | None = None
```

- 不传（None）：现有行为不变，所有 hook / retrieval 正常跑（保留旧测试场景）
- 传 callable：hook 和 context builder 通过它每次实时读取最新 `defaults.memory_enabled`

`AgentLoop.from_config` 与现有 CLI/gateway/SDK 调用方**不需要改动**——只是在 gateway 接线时（第 4.3 节）传这个新 provider。

**hook 侧改造**（[nanobot/agent/hooks/memory_extraction.py](nanobot/agent/hooks/memory_extraction.py)）：

```python
# MemoryExtractionHook.__init__ 增参
def __init__(self, ..., memory_enabled_provider=None):
    self._memory_enabled_provider = memory_enabled_provider

# 在每个回调入口前加闸门
async def before_iteration(self, ctx):
    if self._memory_enabled_provider and not self._memory_enabled_provider():
        return
    try: await self._detect_topic_change(ctx)
    except Exception: ...

async def after_run(self, ctx):
    if self._memory_enabled_provider and not self._memory_enabled_provider():
        return
    ...

async def on_finally(self, ctx):
    if self._memory_enabled_provider and not self._memory_enabled_provider():
        return
    ...
```

`create_memory_extraction_hook_factory` 同步加这个 provider 并在构造 `MemoryExtractionHook` 时透传。

**ContextBuilder 改造**（[nanobot/agent/context.py:110](nanobot/agent/context.py#L110)）：

```python
def __init__(self, ..., memory_enabled_provider=None):
    self._memory_enabled_provider = memory_enabled_provider

# build_messages 内已有 if not self._active_retrieval_enabled / self._retrieval_engine is None: 改为
    if not self._should_retrieve():
        return

def _should_retrieve(self) -> bool:
    if not self._active_retrieval_enabled or self._retrieval_engine is None:
        return False
    if self._memory_enabled_provider is not None and not self._memory_enabled_provider():
        return False
    return True
```

### 4.3 Gateway 接线（[nanobot/cli/gateway_runtime.py:482](nanobot/cli/gateway_runtime.py#L482)）

`AgentLoop.from_config(...)` 调用前构造 `MemoryServices` 与 `RetrievalEngine`，**always-on**（常驻对象，仅 hook 内部决定是否调用）：

```python
memory_services = MemoryServices.for_workspace(
    _MEMORY_WORKSPACE_ID, config.workspace_path
)
retrieval_engine = RetrievalEngine(
    store=memory_services.database, brain=None
)

def _memory_enabled_provider() -> bool:
    # 每次都重读 config，确保热生效
    return config.agents.defaults.memory_enabled

agent = AgentLoop.from_config(
    config, bus,
    ...,
    memory_extraction_enabled=True,   # 始终注册 hook，hook 内部判断
    memory_services=memory_services,
    active_retrieval_enabled=True,    # 始终注册 retrieval，context 内部判断
    retrieval_engine=retrieval_engine,
    memory_enabled_provider=_memory_enabled_provider,  # 新参数
    ...,
)
```

`_MEMORY_WORKSPACE_ID` 与 `RetrievalEngine` / `MemoryServices` 顶层 import 在文件顶部一次性补齐。`config` 已经是同一个引用对象，provider 每次从 config 读的就是最新值（用户 settings mutation 修改 `defaults.memory_enabled` 后，内存里 `config.agents.defaults` 已是新值——但要注意 settings mutation **是否替换 config 对象**还是原地改字段，需要确认；如不能保证，改用持久化重读）

**配置重读策略确认**（实施时必查）：
- 如果 `update_agent_system_settings` 是原地改 `defaults.memory_enabled`（大概率是），上面 provider 就够了
- 如果是替换 `defaults` 对象，provider 需要读 `~/.nanobot/config.json` 后重 parse——但性能差。**首选原地修改方案**，需要在 settings_system.py 实施时确认

### 4.4 热生效事件通路（参照 image_generation_reload）

新增事件常量（[nanobot/bus/events.py:20](nanobot/bus/events.py#L20) 旁加）：

```python
RUNTIME_CONTROL_MEMORY_RELOAD = "memory_reload"
```

新增 helper（`nanobot/memory/reload.py`，或挂在 `nanobot/memory/hooks/__init__.py`）：

```python
async def request_memory_reload(bus: MessageBus, *, timeout: float = 5.0) -> dict:
    """前端提交 settings 后调用，loop ack 即代表 hook 已收到新值。"""
    loop = asyncio.get_running_loop()
    ack: asyncio.Future[dict] = loop.create_future()
    await bus.publish_inbound(
        InboundMessage(
            channel="system", sender_id="webui-settings", chat_id="runtime",
            content=RUNTIME_CONTROL_MEMORY_RELOAD,
            metadata={
                INBOUND_META_RUNTIME_CONTROL: RUNTIME_CONTROL_MEMORY_RELOAD,
                RUNTIME_CONTROL_ACK: ack,
            },
        )
    )
    return await asyncio.wait_for(ack, timeout=timeout)
```

loop 端订阅（与 image_generation 的 listener 同结构）：收到 `RUNTIME_CONTROL_MEMORY_RELOAD` → 仅 `ack.set_result({"reloaded": True})`（实际不重建对象，靠 provider 实时读）

### 4.5 设置写入（[nanobot/webui/settings_system.py:144](nanobot/webui/settings_system.py#L144) `update_agent_system_settings`）

```python
memory_enabled = query_first_alias(query, "memory_enabled", "memoryEnabled")
if memory_enabled is not None:
    parsed = _coerce_bool(memory_enabled)
    if defaults.memory_enabled != parsed:
        defaults.memory_enabled = parsed
        changed = True
        # 注意：restart_required = False，热生效
```

注意：**不再**标 `restart_required=True`。改完 settings 后调：

```python
await request_memory_reload(self.bus)  # 等 ack 即返回
```

若 ack 超时或失败：仍写库（用户开关值已落），但前端收到 `restart_required=True` 兜底提示「需重启 gateway 才生效」。

### 4.6 设置读取（`system_settings_payload`）

在 `runtime` 段补字段：

```python
"runtime": {
    ...,
    "memory_enabled": defaults.memory_enabled,
},
```

### 4.7 前端 SettingsPayload 类型（[webui/src/lib/types.ts:583](webui/src/lib/types.ts#L583)）

`SettingsPayload.runtime.memory_enabled: boolean`。`agent` 块**不动**。

### 4.8 前端 UI（[webui/src/components/settings/memory/MemorySection.tsx](webui/src/components/settings/memory/MemorySection.tsx)）

在 `SegmentedControl` 上方加**顶部 toggle 卡片**：
- 复用 `SettingsControls.tsx` 的 `Switch`
- 提交 mutation 后：
  - 后端 ack 成功 → toast「已生效，下次聊天即按新值决定」/记忆」
  - 后端 ack 失败 → fallback 提示「已保存，但需重启 gateway 生效」
- 关闭时仍展示下方 3 个 tab，可手动查看 / 编辑记忆；顶部加 `disabledNotice`

### 4.9 i18n（10 个 `common.json`）

最小改动：仅 `en` 与 `zh-CN` 必填，其他 locale fallback 到 `en`。

```jsonc
// settings.memory.enable
{
  "title": "Auto memory | 自动记忆",
  "description": "Automatically organize chat into memory and retrieve it on replies. | 自动整理聊天为记忆并在回复时检索。",
  "disabledNotice": "Auto memory is off. You can still view and edit memories here, but new conversations won't be summarized. | 自动记忆已关闭。你仍可在此查看与编辑记忆，但新对话不会被自动整理。",
  "appliedToast": "Memory setting applied to new chats. | 已生效，将应用于后续聊天。",
  "restartRequiredFallback": "Saved, but restart the gateway for this change to take effect. | 已保存，需重启 gateway 后生效。"
}
```

---

## 5. 验收口径

| 用例 | 通过标准 |
|---|---|
| 配置初次写入 | 老 `config.json` 缺 `memory_enabled` → 不报错、默认 False |
| WebUI 默认 | 新装用户进「设置 → 记忆」页看到顶部 toggle 为**关** |
| **打开开关热生效**（核心） | 提交后 ack 立即返回（≤1s）；**不需要重启 gateway**；用户下次发消息即触发 T1 抽取 + 检索注入；state.db 新增语义记忆/情节；切之前在聊天中发的话**不会**被追溯记录 |
| **关闭开关热生效**（核心） | 提交后 ack 立即返回；下次发消息不调 extractor、不注入检索；之前已记的 memory **保留**在 state.db |
| ack 超时兜底 | ack 超时（≥5s）→ 写库成功但提示「已保存，需重启 gateway 后生效」 |
| 切换不丢数据 | 关闭前 state.db 里的记忆保留；切换不影响 state.db |
| i18n | en + zh-CN 必填；其他 locale fallback 英文不报错 |
| 已交付测试用例 | 开关打开后，`tests/memory/manual_memory_test_cases.md` 用例 1~9 在 WebUI 可复现；开关关闭后对应预期「应记的没记」属于**正常** |

---

## 6. 开放决策点（请用户确认）

| # | 议题 | 选项 |
|---|---|---|
| A | 前端 payload 暴露开关字段的位置 | (1) `runtime.memory_enabled`（与 dream / unified_session 同段） / (2) `agent.memory_enabled`（与 `tool_hint_max_length` 同段，**推荐**：和用户改动的直觉一致） |
| B | toggle 关闭时，是否禁用下方 3 个 tab 的内容展示 | (1) 仅顶部提示，tab 仍可看（推荐：用户仍可查 / 改旧记忆） / (2) 灰化 tab |
| C | 是否同时暴露「重启 gateway」按钮 | (1) 仅提示文字 / (2) 加按钮触发 gateway 重启（需先确认 WebUI 是否已有此能力，超出本 spec 范围则**不做**） |

---

## 7. 范围边界 / Out of Scope

- 不改 schema 里 `ActiveRetrievalConfig`（不存在），不开新嵌套配置块；一个 `memory_enabled` 布尔足以。
- 不实现细分开关（自动检索 vs 实时抽取 vs 会话结束）——用户已确认本 spec 只要总开关。
- 不动 `state.db` schema、不做数据迁移。
- 不改 `tests/memory/manual_memory_test_cases.md`（已交付，开关打开后自然生效）。
- 不接 WebUI 重启 gateway 按钮（除非现状已有，仅当 B 选项决定需要时再讨论）。
- 不动 `extract_session` / `extract_incremental` / RetrievalEngine 实现本身。

---

## 8. 风险与缓解

| 风险 | 等级 | 缓解 |
|---|---|---|
| `update_agent_system_settings` 原地改字段 vs 替换 `config` 对象——若替换则 provider 读到旧值 | 中 | **实施时必须先 grep 确认原地改**；若替换，则 provider 改读 `config.json` 重新 parse（性能可接受：每条消息多 parse 一次） |
| hook 在切换瞬间正在进行一次 LLM 抽取（异步），开关一关已发起的仍会写库 | 低 | 用户可接受：切换不影响进行中的任务；下一次新消息按新值决定 |
| ack 通路本身有 bug（参照 image_generation_reload 在测试里的多次 mock） | 中 | 复用既有模式，listener 结构和 image_generation 完全一致 |
| 旧 sessions `metadata["memory_enabled"]` 无值，重启后行为与现状一致 | 低 | schema 默认 False |
| 关闭后用户仍期待自动记忆工作 | 低 | UI 顶部说明 + `disabledNotice` 提示 |

---

## 9. 后续动作

按 `harness-kit/core/routing.md` § 阶段门禁：本 spec 经用户确认 → 进入 `writing-plans` 阶段拆实施计划，或在范围有界的情况下用户说「直接实现」。

---

## Next

**（写入后须暂停，等用户明确继续）**

- 确认方案无误 → 说「写计划」或「制定实施计划」
- 范围小、无需计划 → 说「直接实现」或「直接做」
- 需要调整方案 → 直接说修改意见
- §6 三个决策点请一并确认（推荐 A2 + B1 + C1）