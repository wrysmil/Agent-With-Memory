---
artifact: contract
route: api-and-interface-design
skills:
  - api-and-interface-design
source:
  - AGENTS.md
  - harness-kit/core/routing.md
  - webui/src/lib/agents/types.ts
  - webui/src/components/settings/agents/AgentsView.tsx
  - webui/src/lib/agents/mock.ts
  - nanobot/identity/catalog.py
  - nanobot/identity/store.py
  - nanobot/webui/identity_routes.py
  - nanobot/webui/identity_api.py
  - nanobot/webui/settings_routes.py
  - nanobot/webui/ws_http.py
  - nanobot/webui/gateway_services.py
  - nanobot/agent/tools/loader.py
  - nanobot/agent/tools/registry.py
  - nanobot/webui/skills_api.py
  - nanobot/webui/settings_models.py
  - /Users/mima0000/Documents/学习-001/源码学习/openakita/src/openakita/agents/profile.py
created_at: 2026-09-29
status: frozen
topic: nanobot-agent-profiles
---

# 接口契约：Agent 档案（Agent Profiles）

> **冻结日期**：2026-09-29 ｜ 覆盖 plan：`.ai-runtime-artifacts/plans/2026-09-29-agents-storage-and-crud-plan.md`（一期）
> 与 `.ai-runtime-artifacts/plans/2026-09-29-agents-runtime-integration-plan.md`（二期）
>
> 本文件冻结**跨 WU 的接口**。并行开发时各 WU 以本文件为准，不以他人实现为准。
> 任何一方需要改签名 → 先改本文件并通知 Leader，不得单方面变更。

## 1. 为什么需要这份契约

一期拆 6 个 WU，其中 4 个可并行（后端 API 层与前端 API 客户端互不依赖）。
并行成立的前提是：**线上字段名、`AgentSelection` 语义、目录描述符形状、错误格式**
先冻结。否则前端按 `mock.ts` 的 snake_case 写完、后端按 camelCase 交付，
合流时才发现全部对不上。

二期（运行时接入）消费一期冻结的 `AgentProfile` 与 `AgentStore`，
因此本契约同时是两期之间唯一的耦合面。

## 2. 命名与线格式 —— 冻结

**线上格式一律 camelCase**，与 `webui/src/lib/agents/types.ts` 的 TypeScript
接口逐字段同名。依据：`nanobot/webui/identity_api.py` 已用 `charLimit` /
`fromTemplate` / `labelKey`，`settings_contracts.py:69` 提供
`query_first_alias(snake, camel)` 兼容两种查询参数名。
**Python 侧用 snake_case，序列化时转 camelCase。**

## 3. `AgentSelection` —— 冻结

| Python | 线上 | 含义 |
| --- | --- | --- |
| `mode: str` | `mode` | `"all"` \| `"include"` \| `"exclude"` |
| `entries: list[str]` | `entries` | `"all"` 模式下忽略 |

**语义与前端 `resolveSelection`（`types.ts:148`）严格一致：**

- `all` → 全部能力 id + 全部 locked id
- `include` → 仅 `entries` 中的 id + locked
- `exclude` → `allIds` 去掉 `entries` + locked

**校验规则**（后端 `save` 时执行，违反 → 400）：
`mode` 必须是三个字面量之一；`entries` 内不得有重复项。
`entries` 中的 id **不做存在性校验**（技能/工具可能尚未安装，允许预配置），
但 `entries` 内不得包含空字符串。

## 4. `AgentProfile` —— 冻结

文件：`nanobot/agents/models.py`

```python
@dataclass
class AgentSelection:
    mode: str = "all"
    entries: list[str] = field(default_factory=list)


@dataclass
class AgentProfile:
    id: str
    name: str
    description: str = ""
    type: str = "custom"            # "system" | "custom"
    customized: bool = False        # 系统预设被实质编辑后置 True
    category_id: str | None = None
    icon: str = "🤖"
    color: str = "#4A90D9"
    prompt: str = ""
    model_id: str | None = None     # None = 跟随全局模型
    tools: AgentSelection = field(default_factory=AgentSelection)
    skills: AgentSelection = field(default_factory=AgentSelection)
    sub_agents: AgentSelection = field(default_factory=AgentSelection)
    hidden: bool = False
    updated_at: str = ""            # ISO-8601，由服务端生成
```

**线格式**（`AgentProfile.to_dict()` 的返回值）：

```json
{
  "id": "code-reviewer",
  "name": "代码评审",
  "description": "专注代码评审的子智能体",
  "type": "system",
  "customized": false,
  "categoryId": "coding",
  "icon": "🔍",
  "color": "#8E44AD",
  "prompt": "你是{{name}}…",
  "modelId": null,
  "tools": {"mode": "all", "entries": []},
  "skills": {"mode": "include", "entries": ["memory"]},
  "subAgents": {"mode": "all", "entries": []},
  "hidden": false,
  "updatedAt": "2026-09-29T10:12:03+00:00"
}
```

### 4.1 字段所有权 —— 冻结

| 字段 | 写入方 | 说明 |
| --- | --- | --- |
| `updatedAt` | **服务端** | `save` / `reset` / `visibility` 时由服务端覆盖为当前 UTC ISO-8601；客户端传入值一律忽略 |
| `customized` | **服务端** | 见 §4.2；客户端传入值一律忽略 |
| `type` | **服务端** | `save` 时按是否命中出厂预设推导：`id` 在出厂表内 → `system`，否则 `custom`；客户端传入值一律忽略 |
| 其余字段 | 客户端 | |

这条边界消除了「前端本地 `new Date().toISOString()` 与服务端时间不一致」
和「前端把系统预设改成 custom」两类已知偏差（见
`AgentsView.tsx:135` 的 `handleDuplicate` 与 `mock.ts` 的硬编码 `updatedAt`）。

### 4.2 `customized` 推导规则 —— 冻结

出厂预设表 `FACTORY_PROFILES` 里的每条记录，携带一份出厂默认值。
`save` 时用 `_CUSTOMIZATION_FIELDS` 判定「本次提交是否偏离出厂默认值」：

```python
_CUSTOMIZATION_FIELDS = frozenset({
    "name", "description", "category_id", "icon", "color", "prompt", "model_id",
    "tools", "skills", "sub_agents",
})
```

- 命中出厂 id，且任一 `_CUSTOMIZATION_FIELDS` 字段 ≠ 出厂值 → `customized = True`
- 命中出厂 id，且全部相等 → `customized = False`（`save` 等同于 `reset`）
- 未命中出厂 id → `customized = False`（自定义档案从出厂语义上无「还原」目标）

`hidden` **不在**该集合内：隐藏是视图偏好，不算实质编辑。
沿用 openakita `src/openakita/agents/profile.py:597` 的同名集合设计。

### 4.3 路径遍历防御 —— 冻结

`AgentStore` 接受外部传入的 `id`（URL query / mutation payload），
必须满足 `ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")`。
不匹配 → `AgentStoreError(..., status=400)`。

落点再过一道前缀比对：`resolved = (profiles_dir / f"{id}.json").resolve()`，
必须严格位于 `profiles_dir.resolve()` 之内（`nanobot/identity/store.py:62`
`_resolve` 的同款双闸：白名单 + `is_relative_to`）。

## 5. 存储布局 —— 冻结

沿用 openakita `ProfileStore`（`{base_dir}/profiles/{id}.json` + `categories.json`），
base_dir 落在 **workspace 之下**，与 `identity/`（`nanobot/identity/catalog.py:9`
`IDENTITY_DIR_NAME = "identity"`）并列：

```
{workspace}/agents/
├── profiles/
│   ├── code-reviewer.json
│   └── research.json
└── categories.json
```

**为什么 workspace 级而不是全局**：gateway 绑定时已持有 `workspace_path`
（`nanobot/webui/gateway_services.py:203` 的 `build_identity_operations`），
沿用同一绑定点即可，无需新增配置项；同时 `require_path_within` 的边界
与 identity 一致。代价是不同 workspace 各自一套 Agent 档案 —— 一期接受。

`AGENTS_DIR_NAME = "agents"` 定义在 `nanobot/agents/catalog.py`。

## 6. `AgentStore` —— 冻结（WU-02 实现，WU-03/WU-04/WU-05 消费）

文件：`nanobot/agents/store.py`

```python
class AgentStoreError(ValueError):
    def __init__(self, message: str, *, status: int = 400) -> None: ...


class AgentStore:
    def __init__(self, workspace: Path) -> None: ...

    # -- 读 -------------------------------------------------------------------
    def list_profiles(self) -> list[AgentProfile]: ...
    def get_profile(self, agent_id: str) -> AgentProfile: ...      # 缺失 → status=404
    def list_categories(self) -> list[AgentCategory]: ...

    # -- 写 -------------------------------------------------------------------
    def save_profile(self, profile: AgentProfile) -> AgentProfile: ...
    def delete_profile(self, agent_id: str) -> None: ...
    def reset_profile(self, agent_id: str) -> AgentProfile: ...   # 非 system → 409
    def set_visibility(self, agent_id: str, hidden: bool) -> AgentProfile: ...
    def save_categories(self, categories: list[AgentCategory]) -> list[AgentCategory]: ...
```

**约定**

| 项 | 约定 |
| --- | --- |
| 线程安全 | 进程内 `threading.RLock` 包住每个公开方法；`_load_all` 一次性读入 `dict[str, AgentProfile]` 内存缓存，写后同步更新缓存 |
| 原子写 | 复用 `nanobot/identity/compiler.py:224` `_atomic_write` 同款：`tmp = path.with_name(path.name + ".tmp")` → `write_text(encoding="utf-8")` → `os.replace(tmp, path)` |
| 出厂预置 | `_load_all` 对 `FACTORY_PROFILES` 中**磁盘上缺失**的 id 补一份 `type="system"` 的出厂档案**进内存缓存**。磁盘上已存在的档案一律不覆盖，无论 `customized` 取值——那已经是用户的数据 |
| 读路径不落盘 | 出厂预置**只进内存**，不写文件。文件只在 `save_profile` / `reset_profile` / `set_visibility` 时产生。因此 `GET /api/settings/agents` 是纯读，不会因为「刷新页面」而创建目录 |
| 加载自愈 | 单个 JSON 解析或校验失败 → 记 warning 并跳过该文件，不影响其余档案；缺字段走 `AgentProfile.from_dict` 的默认值 |
| 排序 | `list_profiles` 按 `(type != "system", category_id or "", name, id)` 升序 —— 系统预设先于自定义，同组内按分类、名称稳定排序 |
| `save_categories` | 一期前端不上报分类（分类写死在 `types.ts:99` `AGENT_CATEGORIES`），端点保留供二期使用；一期不接线 |

## 7. `AgentCategory` —— 冻结

文件：`nanobot/agents/catalog.py`

```python
@dataclass(frozen=True)
class AgentCategory:
    id: str
    name: str
    color: str
    order: int
```

出厂 6 条（与 `types.ts:99` `AGENT_CATEGORIES` 逐条一致）：
`general` / `coding` / `writing` / `research` / `ops` / `efficiency`。
`AgentStore.list_categories()` 读 `categories.json`；文件缺失时返回出厂 6 条。
**一期不提供分类增删改端点**（见 §11 范围外）。

## 8. 目录（catalog）描述符 —— 冻结

`GET /api/settings/agents/catalog` 返回四组描述符。
**形状与 `types.ts` 的 `AgentCatalog`（`AgentEditorDialog.tsx:45`）逐字段同名**，
前端不需要改任何类型定义。

### 8.1 `tools: ToolDescriptor[]`

```json
{"name": "read_file", "label": "read_file", "description": "…",
 "category": "filesystem", "risk": "low", "scope": "core", "locked": true}
```

| 字段 | 来源 —— 冻结 |
| --- | --- |
| `name` | `ToolLoader().load(ctx, registry, scope=...)` 注册后的实例 `.name` |
| `label` | **等于 `name`**。后端不产出中文标签；前端若要 i18n 走 `TOOL_CATEGORY_LABEL_KEY` 那套分类 key，不为单个工具名造 key |
| `description` | 实例 `.description`（首行截断至 200 字符） |
| `category` | `TOOL_CATEGORIES_BY_NAME` 静态表命中则取其值，否则 `"execution"`（兜底桶，保证 8 个分类之一） |
| `risk` | `TOOL_RISK_OVERRIDES` 静态表命中则取其值；否则 `read_only=True → "low"`，`read_only=False → "medium"` |
| `scope` | `"subagent" in cls._scopes and "core" not in cls._scopes → "subagent"`，否则 `"core"` |
| `locked` | `cls._scopes & {"core", "memory"}` 非空 → `true`（框架依赖，不可取消） |

**一期已知简化（显式记录，不隐藏）**：`scope` 恒不返回 `"plugin"`。
`ToolLoader.load` 会把 entry-point 插件一并注册，但当前不保留来源标记，
插件工具按 `core` 归类。二期为 `ToolLoader` 增加来源标记后再放开。
`health` 类字段同理不在一期。

**枚举方式**（`agents_api.py` 内）：构造
`ToolContext(config=tools_config, workspace=str(workspace))`，
对 `"core"` 与 `"subagent"` 两个 scope 各调一次
`ToolLoader().load(ctx, registry, ...)` 打进**同一个** `ToolRegistry`
（同名工具后者覆盖前者，取并集），再按 `ToolRegistry.names()` 排序输出。
为此给 `ToolRegistry` 新增一个只读方法：

```python
def names(self) -> list[str]:
    return sorted(self._tools)
```

### 8.2 `skills: SkillDescriptor[]`

```json
{"name": "memory", "description": "…", "source": "workspace", "tags": []}
```

| 字段 | 来源 —— 冻结 |
| --- | --- |
| `name` / `description` | `nanobot.webui.skills_api.webui_skills_payload(workspace)["skills"]` 的同名字段（已过滤本地路径） |
| `source` | 同上；`unknown` 一律归一为 `"builtin"`（前端 `SkillSource` 只认三个值） |
| `tags` | 一期恒为 `[]`：`SkillsLoader` 不产出标签，前端 `types.ts:65` 已把它声明为必填数组，给空数组比造假标签诚实 |

### 8.3 `models: ModelDescriptor[]`

```json
{"id": "default", "label": "Default", "provider": "anthropic",
 "contextWindow": 200000, "health": "healthy", "vision": true, "toolUse": true}
```

| 字段 | 来源 —— 冻结 |
| --- | --- |
| `id` / `label` | `settings_models.model_settings_payload(config)["model_presets"]` 的 `name` |
| `provider` | 同上的 `resolved_provider`（空串回退 `"auto"`） |
| `contextWindow` | 同上的 `context_window_tokens`（0 或缺省回退 `200000`） |
| `health` | 一期恒为 `"healthy"`：**出现在 `config.model_presets` 里的就是用户已配置可用的**；`statusNote` 一期不下发 |
| `vision` / `toolUse` | 一期恒为 `true`。nanobot 的 agent loop 本身就以工具调用驱动（`toolUse` 为假则档案不可用）；图片走同一 provider 通道。**这是记录在案的简化**：配置层没有逐模型能力表，二期为 provider registry 补能力表后再按实际下发 |

前端 `AgentsView.tsx:30` 硬编码的 `GLOBAL_MODEL_LABEL = "claude-opus-5"`
在一期改为读 `useClient().modelName`（即 `settings.agent.model`），不属本契约。

### 8.4 `mcpServers: McpServerDescriptor[]` —— 2026-09-29 追加

```json
[{"name": "github", "type": "stdio", "command": "npx", "url": "",
  "toolCount": 0, "allTools": true}]
```

| 字段 | 来源 —— 冻结 |
| --- | --- |
| `name` | `config.tools.mcp_servers` 的键 |
| `type` | `MCPServerConfig.type`，缺省回退 `"auto"`（`schema.py:452` 注明省略时自动探测） |
| `command` | stdio 型取 `command`，其余取空串。**不下发 `args` / `env` / `cwd`** |
| `url` | http/sse 型取 `url`，其余取空串。**不下发 `headers`**（可能含凭据） |
| `toolCount` | `len(enabled_tools)`，`["*"]` 时取 `0` |
| `allTools` | `enabled_tools == ["*"]` |

**为什么只读**：`config.tools.mcp_servers` 是**全局配置**，MCP 工具运行时动态注册为
`mcp_<server>_<tool>`，**不在静态工具目录**里（实测 25 个内置工具无 `mcp_` 前缀）。
「按 Agent 勾选 MCP」在当前运行时没有对应语义 —— 真要生效需让 `SubagentManager`
按档案过滤 `mcp_servers` 注入，属二期。因此 `AgentProfile` **不加** `mcpServers` 字段，
存储层不动，一期只做展示 + 跳全局设置。
用户已于 2026-09-29 确认此范围。

**安全**：`headers` 与 `env` 可能含凭据，**一律不下发**。`command` / `url` 已足够让用户
认出这是哪个 server。

## 9. 传输契约 —— 冻结

沿用项目既有的**双通道**约定（见 `nanobot/webui/identity_routes.py` 模块头注释）。

### 9.1 读 —— HTTP GET（走 `_SYSTEM_ROUTES`）

| 方法 | 路径 | `_SYSTEM_ROUTES` 映射值 | 响应 |
| --- | --- | --- | --- |
| GET | `/api/settings/agents` | `agents-list` | `{"agents": AgentProfileWire[]}` |
| GET | `/api/settings/agents/catalog` | `agents-catalog` | `{"tools": [...], "skills": [...], "models": [...], "categories": [...]}` |

### 9.2 写 —— WebSocket mutation

`ws_http.py` 的 `_WEBUI_MUTATION_ACTIONS` 新增四行（模块内 `AGENTS_MUTATION_PATHS` 展开）：

| 前端 action | HTTP 路径 | payload | 响应 |
| --- | --- | --- | --- |
| `agents.save` | `/api/settings/agents/save` | `{"agent": AgentProfileWire}` | `{"agent": AgentProfileWire}` |
| `agents.delete` | `/api/settings/agents/delete` | `{"id": "code-reviewer"}` | `{"id": "code-reviewer"}` |
| `agents.reset` | `/api/settings/agents/reset` | `{"id": "code-reviewer"}` | `{"agent": AgentProfileWire}` |
| `agents.visibility` | `/api/settings/agents/visibility` | `{"id": "code-reviewer", "hidden": true}` | `{"agent": AgentProfileWire}` |

**不做 `GET /api/settings/agents/{id}`**：前端从不单独读某个档案，
`AgentsView` 一次拉全量后在本地编辑。单条读端点一期不建。

### 9.3 settings 路由 action 名 —— 冻结

`AgentSettingsHandler` 认识的动作集合（`AGENTS_ACTION_NAMES`）：

```python
AGENTS_ACTION_NAMES = frozenset({
    "agents-list", "agents-catalog",
    "agents-save", "agents-delete", "agents-reset", "agents-visibility",
})
```

### 9.4 错误格式 —— 冻结

沿用 `settings_contracts.WebUISettingsError` → `SettingsRouteResult.failure(status, message)`
→ `nanobot/webui/http_utils.http_response` 的既有链路，
**响应体形如 `{"error": "<message>"}`**（前端 `webui/src/lib/api.ts` 的
`ApiError` 正是读 body 的 `.error` 字段）。不新增错误形状。

| 状态码 | 触发条件 |
| --- | --- |
| 400 | `id` 不匹配 `ID_PATTERN`；`mode` 非法；`entries` 含重复或空串；`AgentSelection` 结构错误；`color` 非 `#RRGGBB` |
| 403 | `id` 解析后越出 `profiles/` |
| 404 | 档案不存在（`save` 新建不适用） |
| 409 | `reset` 作用于非 `system` 档案；`delete` 作用于 `system` 档案 |
| 422 | `name` 为空或超过 120 字符；`prompt` 超过 `PROMPT_MAX_LENGTH`（5000，与 `types.ts:129` 同值） |
| 503 | `catalog` 构造所需的 `ToolsConfig` 未注入（与 `_persona_activation_unavailable` 同款降级） |

### 9.5 `delete` 拒绝系统预设 —— 冻结

`delete` 作用在 `type == "system"` 的档案上返回 **409**「系统预设不可删除」。
理由：§6 的出厂自愈会在下一次 `_load_all` 时把它补回来，用户点删除后
刷新页面档案又出现 —— 那是比报错更糟的体验。系统预设的退出路径是
`reset`（还原出厂值）或 `set_visibility(hidden=True)`（从列表隐藏）。

前端 `AgentsView` 无需改动：删除失败时把服务端返回的 409 文案显示到
现有的 `saveError` 区域即可（见计划 Task 9）。

## 10. 前端客户端契约 —— 冻结

文件：`webui/src/lib/agents/api.ts`（新建）

```ts
const AGENTS_BASE = "/api/settings/agents";

export interface AgentListResponse { agents: AgentProfile[]; }
export interface AgentCatalogResponse {
  tools: ToolDescriptor[]; skills: SkillDescriptor[];
  models: ModelDescriptor[]; categories: AgentCategory[];
}
export interface AgentMutationResponse { agent: AgentProfile; }

export async function listAgents(token: string, base = ""): Promise<AgentListResponse>;
export async function loadAgentCatalog(token: string, base = ""): Promise<AgentCatalogResponse>;
export async function saveAgent(transport: WebUIMutationTransport, agent: AgentProfile): Promise<AgentMutationResponse>;
export async function deleteAgent(transport: WebUIMutationTransport, id: string): Promise<{ id: string }>;
export async function resetAgent(transport: WebUIMutationTransport, id: string): Promise<AgentMutationResponse>;
export async function setAgentVisibility(transport: WebUIMutationTransport, id: string, hidden: boolean): Promise<AgentMutationResponse>;
```

读走 `request<T>(url, token, init, timeout)`，写走
`mutation<T>(transport, action, payload)`，与 `listIdentityFiles` /
`setActivePersona`（`webui/src/lib/api.ts:1305, 1371`）同款。

`webui/src/lib/agents/mock.ts` 在 WU-05 **整体删除**（唯一引用方是
`AgentsView.tsx:26`）。`webui/src/lib/agents/types.ts` 与 `i18n.ts` 不动。

## 11. 一期范围外 —— 明确不做

| 不做 | 理由 |
| --- | --- |
| 分类的增删改端点 | 前端分类写死在 `types.ts:99`；`save_categories` 保留在 store 层不接线 |
| `GET /api/settings/agents/{id}` | 前端无单条读需求（§9.2） |
| `scope: "plugin"` 与模型能力表 | 依赖 `ToolLoader` 来源标记与 provider 能力表，二期做（§8.1、§8.3） |
| `SpawnTool` / `SubagentManager` 改造 | 属二期 runtime 接入 |
| 档案导入导出 | 无需求 |
| 单档案并发编辑乐观锁 | 一期 WebUI 单写者；`updatedAt` 已下发但不做冲突检测 |

## 12. 变更记录

| 轮次 | 日期 | 变更摘要 |
| --- | --- | --- |
| 1 | 2026-09-29 | 初稿，冻结存储布局、线格式、端点、错误码、目录描述符形状 |
