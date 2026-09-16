# openakita 身份配置 + MEMORY.md 生成机制调研（为 nanobot 移植）

> 调研日期：2026-09-16
> 调研对象：`D:\studyspace\源码学习\openakita\openakita`（openakita）
> 改造对象：`D:\studyspace\源码学习\Agent-With-Memory`（nanobot，feature/memory-system 分支）
> 用途：为 nanobot 增加**身份配置界面**、把 **MEMORY.md 改为从 SQLite 生成**、调整 **system prompt 关系**提供事实依据。
>
> ⚠️ **2026-09-16 已做一轮对抗性核验**（独立 agent 逐条比对源码 + Leader 复核关键项）。行号与结论已按核验结果修正，修正处标 `核验修正`。
> 本轮修正的核心是 §6.6 —— 原稿该处的 `truncate_memory_md` 代码块**与源码不符**（引用了不存在的 `extract_title()`、正则拼错、丢了一个截断分支），现已换成源码逐字摘录。

---

## 〇、先明确 nanobot 现状（改造基线）

| 项 | nanobot 现状 | 位置 |
| --- | --- | --- |
| 身份文件 | `SOUL.md` / `USER.md` / `memory/MEMORY.md`，另有 `AGENTS.md`（项目级） | `nanobot/agent/memory.py:74-93` |
| 管理类 | `MemoryStore`（纯文件 I/O） | `memory.py:60-744` |
| MEMORY.md 来源 | **Dream 机制 LLM 改写**（读 `history.jsonl` → LLM → 改文件） | `nanobot/templates/agent/dream.md` |
| 注入方式 | `SOUL.md` / `USER.md` 整段拼进 system prompt；`MEMORY.md` 拼成 `# Memory\n\n## Long-term Memory` | `nanobot/agent/context.py:307-334`, `230-233` |
| 默认模板跳过 | `_SKIPPABLE_DEFAULTS = {"AGENTS.md", "USER.md"}`（内容等于出厂模板则跳过注入） | `context.py:107`, `328-331` |
| **MEMORY.md 也有模板跳过** | `context.py:232` 单独判断 `_is_template_content(memory, "memory/MEMORY.md")` — 不只 bootstrap 文件有这层保护 | `context.py:230-233` |
| **已 track 四文件的 GitStore** | `GitStore(workspace, tracked_files=["SOUL.md","USER.md","memory/MEMORY.md","memory/.dream_cursor"])` — 是 openakita `.file_hashes.json` 的对等物，但**用途不同**（给 Dream commit 做 diff 落地，不是"升级覆盖检测"） | `memory.py:90-92` |
| 前端 | Settings → Memory：总开关 + semantic/episode tab + 列表/编辑/删除。**无身份文件编辑器** | `webui/src/components/settings/memory/` |
| 记忆 API | `/api/settings/memory/memories`(list/search/get/create/update/delete)、`/episodes`、`/scratchpad`、**`/stats`** | `nanobot/webui/settings_routes.py:148-173`（stats 在 160 行） |
| 身份文件 API | **无** | — |

**结论**：nanobot 缺三样——(1) 身份文件的读写 API，(2) 身份配置前端，(3) MEMORY.md 的确定性生成路径（现在只有 LLM 的 Dream 路径）。

---

## 一、前端：身份配置界面

### 1.1 技术栈与位置

| 项 | 值 |
| --- | --- |
| 目录 | `apps/setup-center/` |
| 栈 | React 18.3 + TypeScript 5.7 + Vite 6.1 + **Tauri v2**（也支持纯 Web 构建）|
| UI | TailwindCSS 4 + Radix UI + lucide-react |
| i18n | i18next + react-i18next（`src/i18n/zh.json` / `en.json`） |
| 页面组件 | `apps/setup-center/src/views/IdentityView.tsx`（499 行） |
| 懒加载注册 | `src/App.tsx:23` |
| 渲染入口 | `src/App.tsx:4767-4770`（`view === "identity"`） |
| 侧边栏入口 | `src/components/Sidebar.tsx:426-437`（挂在 `view === "agent"` 下的 `configModeItem`） |
| 标题 | `zh.json:2702-2703`：`"title": "身份配置"` / `"desc": "管理 Agent 的核心身份文件"` |

`IdentityView` 是**纯 props 组件**（只吃 `serviceRunning` + `apiBaseUrl`），零全局状态库，全部 `useState` 局部状态。

⚠️ **否定结论**：`package.json` 里**没有 monaco-editor**（也无 CodeMirror）。截图里像 monaco 的编辑器实际是原生 `<textarea>` + `fontFamily: monospace`（`IdentityView.tsx:366-381`）。
（核验修正：`editorRef` **确实在被使用** —— `:83` 声明、`:367` `ref={editorRef}` 绑定。只是没有承载任何命令式调用，正确的说法是"未做任何命令式操作"，不是"未使用"。）

### 1.2 文件树：来源是后端 API

```tsx
// IdentityView.tsx:91-102
const loadFiles = useCallback(async () => {
  if (!serviceRunning) return;
  try {
    const res = await safeFetch(`${API}/api/identity/files`);
    const data = await res.json();
    setFiles(data.files || []);
  } catch (e) { setError(String(e)); }
}, [API, serviceRunning]);
```

渲染：固定宽度 220px 左侧栏，标题 `identity.sourceFiles`（"源文件"），逐项 `<FileItem>`（`465-499`）。`FileItem` 细节：
- `personas/` 前缀被剥掉只显示文件名（466-468）
- `file.restricted === true` → 右侧黄色 `IconAlertCircle`（494-496）—— **仅图标提示，不拦截编辑**
- `file.exists === false` → 置灰（485-486）

### 1.3 顶部四个按钮 → 接口映射

| 按钮 i18n key | zh 文案 | 处理函数 | 接口 |
| --- | --- | --- | --- |
| `identity.reload` | **重载** | `handleReload` (202-209) | `POST /api/identity/reload` |
| `identity.compile` | **LM 优化** | `handleCompile("llm")` (183-199) | `POST /api/identity/compile?mode=llm` |
| `identity.compileRules` | **规则编译** | `handleCompile("rules")` | `POST /api/identity/compile?mode=rules` |
| `identity.save` | **保存** | `handleSave(false)` (123-180) | `POST /api/identity/validate` → 确认 → `PUT /api/identity/file` |

（官方文案是 "LM 优化"，不是 "LLM 优化"。i18n 见 `zh.json:2705-2708`。）

**两个徽标**（只对特定文件显示，`282-291`）：
- `SOUL.md` → 绿色 `identity.fullTextInject` = "全文注入，修改即生效"
- `AGENT.md` → 黄色 `identity.needsCompile` = "修改后需编译生效"

⚠️ **绿色徽标与代码不符**：SOUL.md 实际注入的是**编译产物**（见 §五 5.3），不是全文。该徽标文案过时。

`handleCompile` 带 60 秒超时：`signal: AbortSignal.timeout(60_000)`（189 行）。

### 1.4 字符限制 1500 的来源：**前端硬编码**

```tsx
// IdentityView.tsx:27
const MEMORY_MAX_CHARS = 1500;
```

**前端写死的常量**，不是后端下发。后端同值在 `src/openakita/memory/types.py:82` `MEMORY_MD_MAX_CHARS = 1500` —— 两边靠**人工同步**，没有下发机制。

红字警告（仅 `selectedFile === "MEMORY.md"`，394-401）：

```tsx
{selectedFile === "MEMORY.md" && (
  <span style={{ color: content.length > MEMORY_MAX_CHARS ? "#dc2626" : undefined,
                 fontWeight: content.length > MEMORY_MAX_CHARS ? 600 : undefined }}>
    {t("identity.charCount", { count: content.length, max: MEMORY_MAX_CHARS })}
    {content.length > MEMORY_MAX_CHARS && ` — ${t("identity.charCountExceeded")}`}
  </span>
)}
```

文案（`zh.json:2734-2735`）：
- `"charCount": "{{count}} / {{max}} 字符"`
- `"charCountExceeded": "超出字符限制，保存后将被自动截断"`

同一条状态栏右侧还有 **token 预算**（402-408），budget 值来自后端 `files[].budget_tokens`。前端自己实现了 `estimateTokens`（`29-37`，中文/1.5 + 英文/4）。
（核验修正：这与后端 `prompt/budget.py:207` 的 `estimate_tokens` 是**同一个公式的重复实现**，不是两种不同算法 —— 作为"重复维护"的论据成立，但不要读成"两套算法"。）

### 1.5 MEMORY.md「会被覆盖」提示的实现

**双层提示**：

**(a) 文件级黄色横幅** —— 前端硬编码白名单映射（`IdentityView.tsx:50-57`）：

```tsx
const WARNING_KEYS: Record<string, string> = {
  "SOUL.md": "soulWarning", "AGENT.md": "agentWarning",
  "USER.md": "userWarning", "MEMORY.md": "memoryWarning",
  "POLICIES.yaml": "policiesYamlWarning", "prompts/policies.md": "policiesMdWarning",
};
```

MEMORY.md 对应文案（`zh.json:2719`）：

> 此文件由系统每日自动刷新。手动修改的内容可能在下次自动刷新时被完全覆盖。如需持久化规则，建议使用记忆系统的 add_memory 功能（type=rule）。

⚠️ **判断条件只比对文件名 `=== "MEMORY.md"`**。后端另有一份 `_FILE_WARNINGS`（`api/routes/identity.py:56-63`）返回 `warning_key` 字段，但**前端没有使用它** —— 冗余且可能不一致。

**(b) 页面级横幅**：`identity.pageBanner`（`230-251`），可关闭，状态存 `localStorage["identity_banner_dismissed"]`。

### 1.6 权限/保护逻辑：前端几乎不设防

- 后端返回 `restricted` 字段（AGENT.md / MEMORY.md / POLICIES.yaml / prompts/policies.md 为 true），前端**只渲染小图标**（494-496），**不禁用编辑、不禁用保存**。
- 唯一的"高危"判断：`isHighRisk = selectedFile === "SOUL.md" || "AGENT.md"`，只是往确认框多加一句风险提示（144、150 行）。
- 保存按钮 `disabled={saving || !hasChanges}`（306 行）—— 只防未修改时的空保存。
- **前端没有任何「core 文件不可编辑」的硬拦截。**

---

## 二、后端 API：`src/openakita/api/routes/identity.py`（522 行）

Router：`APIRouter(prefix="/api/identity", tags=["identity"])`（27 行）。

### 2.1 端点清单

| 路径 | 方法 | 用途 | 请求体 / 关键响应 |
| --- | --- | --- | --- |
| `/api/identity/files` | GET | 列出可编辑身份文件 + 元数据 | `{"files":[{name, exists, restricted, warning_key, budget_tokens, size, modified, tokens}]}`（189-222） |
| `/api/identity/file` | GET | 读单个文件 | Query `name`（或别名 `path`）→ `{name, content, tokens, budget_tokens}`（225-249） |
| `/api/identity/file` | PUT | 写文件（带校验） | Body `{name, content, force=false}`；400 错误 / 200 `{saved:false, needs_confirm:true, warnings}` / `{saved:true, ...}`（252-305） |
| `/api/identity/validate` | POST | 只校验不落盘 | `{name, content}` → `{errors:[], warnings:[]}`（308-312） |
| `/api/identity/reload` | POST | 热重载身份文件进运行中的 agent | 无 body；失败 500（315-327） |
| `/api/identity/compile` | POST | 触发编译 | Query `mode=llm\|rules` → `{status, mode_used, compiled_files, runtime}`（330-389） |
| `/api/identity/compile-status` | GET | 编译状态 | `{outdated, last_compiled, files:{key:{tokens,budget_tokens,has_content}}}`（392-429） |
| `/api/identity/persona/template` | GET | 下载人格 MD 模板 | `PlainTextResponse`（470-479） |
| `/api/identity/persona/import` | POST | 上传人格 MD | Form `file` → `{saved, name, persona_id, tokens}`（482-522） |

### 2.2 文件白名单与预算表

```python
# identity.py:40-54
_EDITABLE_SOURCE_FILES = [
    "SOUL.md", "AGENT.md", "USER.md", "MEMORY.md",
    "POLICIES.yaml", "prompts/policies.md",
]
_RESTRICTED_FILES = {"AGENT.md", "MEMORY.md", "POLICIES.yaml", "prompts/policies.md"}

# identity.py:32-38
_BUDGET_MAP = {
    "SOUL.md": 3600,
    "runtime/identity.core.md": 600,
    "runtime/agent.behavior.md": 450,
    "runtime/user.profile.core.md": 300,
    "prompts/policies.md": 1200,
}
```

⚠️ `_RESTRICTED_FILES` **只是元数据标记**，`PUT /file` 里**没有任何地方读它来做拦截**。

### 2.3 路径穿越防护

```python
# identity.py:73-79
def _resolve_file(name: str) -> Path:
    identity = _identity_dir()
    target = (identity / name).resolve()
    if not str(target).startswith(str(identity.resolve())):
        raise HTTPException(400, "Path traversal not allowed")
    return target
```

先 `.resolve()` 归一化（吃掉 `..`），再做字符串前缀比对。前缀比对偏弱（`identity` vs `identity-evil`），但结合 resolve 已足够拦 `../../etc/passwd`。

persona 导入另有一套（495-510）：文件名 `re.sub(r"[^\w\-.]", "_", fname)` 清洗，再拒绝以 `.` 开头或含 `/` `\`。

### 2.4 写入保护：只有 compiled 路径被硬拦

```python
# identity.py:260-264
name = req.name
# Block writing to .compiled_at or other non-editable paths
if name.startswith("runtime/") or name.startswith("compiled/"):
    raise HTTPException(403, "Cannot write to compiled identity files")
```

⚠️ **结论：`AGENT.md`、`MEMORY.md` 等"restricted"文件其实可以通过 API 直接写入，服务端不拦。** 所谓"core 文件不可写"在 openakita 里**并不存在**，只是 UI 上打了个小图标。

### 2.5 校验在哪一层：**全在服务端**

`validate_identity_file(name, content)`（92-169）：

- **errors（阻断保存）**：只有 `POLICIES.yaml` 的 YAML 结构校验（顶层键白名单 `tool_policies/scope_policy/auto_confirm` + 类型检查，100-133）
- **warnings（需 force 确认）**：
  - `MEMORY.md`：`len(content) > MEMORY_MD_MAX_CHARS` → `"内容超出 1500 字符限制（当前 N），保存后将被自动截断"`（135-142，**从 `openakita.memory.types` 导入常量**）
  - `USER.md`：无 `**字段名**:` 格式 → 提示自动学习可能失效（144-147）
  - `personas/*.md`：非标准段落（149-156）
  - `prompts/policies.md`：覆盖系统内置段落（158-167）

⚠️ **「截断」动作不在 API 层** —— API 只在超过时发 warning；真正的截断在 `memory/types.py:truncate_memory_md`，由写入方调用。
⚠️ 前端 `handleSave` 是 `force=true` 直写（`IdentityView.tsx:166`），所以**用户手改超长 MEMORY.md 后文件里就是超长内容，不会被 API 截断**。

### 2.6「规则编译」与「LM 优化」的后端逻辑

```python
# identity.py:330-389
@router.post("/compile")
async def compile_identity(request: Request, mode: str = "rules"):
    if mode == "llm":
        agent = _get_agent(request)
        brain = getattr(agent, "brain", None)
        if brain is None:
            local = getattr(agent, "_local_agent", None)
            if local: brain = getattr(local, "brain", None)
        if brain:
            from openakita.prompt.compiler import PromptCompiler
            compiler = PromptCompiler(brain=brain)
            await compiler.compile_all(identity_dir)      # 异步 LLM 编译
            mode_used = "llm"
        else:
            from openakita.prompt.compiler import compile_all
            compile_all(identity_dir)                     # 降级为规则编译
            mode_used = "rules (LLM not available)"
    else:
        from openakita.prompt.compiler import compile_all
        compile_all(identity_dir)
        mode_used = "rules"
    ...
    runtime = get_runtime_config_coordinator(request).rebuild_agent_prompt()
```

两者最终都写 `identity/runtime/*.md`，区别只是 `PromptCompiler.compile_all`（异步、走 brain）vs `compile_all`（同步、纯规则）。

---

## 三、IdentityLoader：`src/openakita/agent/identity.py`（687 行）

### 3.1 加载与热重载

```python
# identity.py:105-123
self.soul_path = soul_path or settings.soul_path
self.agent_path = agent_path or settings.agent_path
self.user_path = user_path or settings.user_path
self.memory_path = memory_path or settings.memory_path
self._soul = self._agent = self._user = self._memory = None
self._pending_upgrades: list[dict] = []
```

`load()`（125-137）—— **前三个文件走 `_sync_identity_file`（带升级检测），MEMORY.md 走 `_load_file`（无追踪）**：

```python
def load(self) -> None:
    self._pending_upgrades = []
    if self.sync_templates:
        self._soul  = self._sync_identity_file(self.soul_path, "SOUL.md")
        self._agent = self._sync_identity_file(self.agent_path, "AGENT.md")
        self._user  = self._sync_identity_file(self.user_path, "USER.md")
    else:
        self._soul = self._load_file(self.soul_path, "SOUL.md")
        ...
    self._memory = self._load_file(self.memory_path, "MEMORY.md")   # ← 不进 sync
```

`reload()`（139-146）—— **这就是热重载的全部机制**：

```python
def reload(self) -> None:
    self._soul = None; self._agent = None; self._user = None; self._memory = None
    self.load()
```

属性访问器带惰性加载（276-302），如 `soul` 属性：`if self._soul is None: self.load()`。

### 3.2 `_sync_identity_file` 四场景决策矩阵（173-254）

用 **SHA-256 前 16 位 hash**，账本 `identity/runtime/.file_hashes.json`（`_HASH_FILE`，31 行），追踪列表 `_TRACKED_FILES = ["SOUL.md","AGENT.md","USER.md"]`（32 行）—— **MEMORY.md 刻意不在其中**。

```
场景 1（188-198）：文件不存在 + .example 存在 → 复制模板并记录 hash
        （找不到 .example 时回退到包内模板 _resolve_bundled_identity_template，212-215，
          向上最多走 10 层找 identity/，覆盖 pip wheel / Tauri 安装场景）
场景 2（225-233）：current_hash == recorded_hash 且 example_hash != recorded_hash
        → 静默覆盖（用户没改过，系统升级）
场景 3（234-235）：有 hash 且不匹配 → return current_content，不覆盖（用户改过）
场景 4（236-254）：无 hash 记录 → 若 current_hash == example_hash 则记 hash；
        否则加入 _pending_upgrades 并记录当前 hash
```

### 3.3 `_pending_upgrades` 机制

- 每项 `{name, path, example_path, hash_key}`（244-251）
- `load()` 开头清空（127）
- `get_pending_upgrades()`（148-150）读取
- `apply_upgrade(name, accept)`（152-171）：accept → 写入 `.example` 内容并更新 hash；decline → 只记录当前 hash（等价"认账不再提示"），最后从列表移除
- 设计意图（文件头 docstring 5-10）：用户改过的文件不静默覆盖，挂起来让 CLI/API 询问用户

⚠️ **未找到** `_pending_upgrades` 在 HTTP API 层的任何暴露端点 —— 只有 CLI 路径可能用到。

### 3.4 缓存机制

4 个 `str | None` 字段 `_soul/_agent/_user/_memory`（119-122），读写全经属性访问器。
**没有 mtime 失效** —— `reload()` 是唯一的失效入口（由 `runtime_config_coordinator.refresh_identity` 调用，见 §六 6.6）。

### 3.5 摘要方法（旧全量注入路径）

`get_soul_summary` / `get_agent_summary` / `get_user_summary` / `get_memory_summary`（304-361）返回**全文**包裹标题；`get_system_prompt`（371-498）把它们拼成一大段。
**这条路径已被 v2 编译管线取代**（`get_compiled_prompt` 508-548，docstring 明说"相比 get_system_prompt()（全文注入）… Token 消耗降低约 55%"）。

---

## 四、PromptCompiler：`src/openakita/prompt/compiler.py`（531 行）

### 4.1 三个编译目标

```python
# compiler.py:66-76
_SOURCE_MAP = {"identity_core": "SOUL.md", "agent_behavior": "AGENT.md",
               "user_profile_core": "USER.md"}
_OUTPUT_MAP = {"identity_core": "identity.core.md",
               "agent_behavior": "agent.behavior.md",
               "user_profile_core": "user.profile.core.md"}
```

输出目录 `identity/runtime/`（101 行）。

### 4.2 token 上限与排他式约束

`_COMPILE_PROMPTS`（35-64）：`identity_core` 600 / `agent_behavior` 450 / `user_profile_core` 300。
每个目标都做了"只保留 X，不要包含 Y"的**排他式约束**，例如 identity_core：

> 将以下 SOUL.md 编译为不超过 600 tokens 的身份核心。只保留自称、使命、独特价值取向、交流气质和人格特征。**不要包含**安全边界、诚实/来源标签、人类监督、权限、工具、任务执行、验证、记忆、自修复或多 Agent 规则；这些由平台提示统一提供。

user_profile_core（61 行）：

> 从以下 USER.md 提取 pinned 用户偏好和稳定事实，**跳过占位、过期、空内容**，不超过 300 tokens。

**"跳过占位"这条正是 nanobot 最缺的** —— nanobot 的 `[ ]` checkbox 用户没填时会原样注入。

### 4.3 LLM 编译 vs 规则降级

```python
# compiler.py:129-144
async def _compile_with_llm(self, content, config) -> str:
    if self.brain:
        try:
            prompt = config["user"].format(content=content, max_tokens=config["max_tokens"])
            if hasattr(self.brain, "think_lightweight"):
                response = await self.brain.think_lightweight(prompt, system=config["system"])
            else:
                response = await self.brain.think(prompt, system=config["system"])
            result = (getattr(response, "content", None) or str(response)).strip()
            if result: return result
        except Exception as e:
            logger.warning(f"[Compiler] LLM compilation failed, using rules: {e}")
    return _compile_with_rules(content, config)
```

`compile_all(identity_dir, use_llm=False)`（152-185）是纯规则同步版；路由 `/compile?mode=rules` 直接走它。

**规则编译细节**：
- `_extract_owned_sections`（319-360）按 `_OWNED_SECTION_MARKERS`（239-252）抓"本目标拥有的章节"：
  - `identity_core` 认 `身份认知 / 核心性格 / personality / soul / 使命 / overview`
  - `agent_behavior` 只认 `成长循环 / self-healing / 自我修复`
- 抓不到章节时降级为该文件**全部非标题行**（369-379）
- `_TARGET_EXCLUDED_LINE_MARKERS`（254-316）逐行剔除属于"平台职责"的关键词（安全/诚实/权限/工具/记忆/tool/permission/memory…）
- 行去重 + 单行 240 字符截断（381-396）
- 结果为空时用 `_STATIC_FALLBACKS`（451-463）硬编码兜底

**`user_profile_core` 的排除清单（307-316）—— 直接可抄**：

```python
"user_profile_core": (
    "[待学习", "[待统计", "[待补充",
    "[agent 会", "[其他需要记住",
    "此文件由 openakita 自动维护",
    "最后更新:",
),
```

### 4.4 严格 token 封顶（二分查找）

`_enforce_token_limit`（399-427）：超限就逐行累加，最后一行用二分切到刚好不超预算：

```python
low, high = 0, len(line)
while low < high:
    mid = (low + high + 1) // 2
    partial = "\n".join([*kept, line[:mid].rstrip()])
    if estimate_tokens(partial) <= max_tokens: low = mid
    else: high = mid - 1
```

### 4.5 缓存失效：mtime + schema version

- `COMPILED_SCHEMA_VERSION = "9"`（27），版本文件 `.compiler_version`（28）
- `_is_up_to_date`（471-477）：`output.stat().st_mtime_ns >= source.stat().st_mtime_ns`
- `_compiler_schema_is_current`（480-485）：读 `.compiler_version` 是否等于 `"9"`
- `_write_compiled_timestamp`（196-211）：写 `.compiled_at`，并**手动 `os.utime` 把 mtime 抬到"所有源文件 mtime 最大值 + 1ns"** —— 避免同秒写入误判（**踩过坑的做法，值得抄**）
- `check_compiled_outdated(identity_dir, max_age_hours=24)`（488-519）：三层判断（schema 版本 / 超 24h / 任一源文件 mtime 晚于 `.compiled_at`）
- `_cleanup_orphan_files`（214-223）：清理 6 个旧管线遗留文件（78-85）

---

## 五、提示词注入：`src/openakita/prompt/builder.py`（2464 行）

### 5.1 注入前自动编译

```python
# builder.py:427-438
_compiled_cache = _static_prompt_cache.get(f"compiled:{_id_dir_key}")
_now_ts = time.time()
if _compiled_cache and (_now_ts - _compiled_cache[0]) < _STATIC_CACHE_TTL:
    compiled = _compiled_cache[1]
else:
    if check_compiled_outdated(identity_dir):
        logger.info("Compiled files outdated, recompiling...")
        compile_all(identity_dir)
    compiled = get_compiled_content(identity_dir)
    _static_prompt_cache[f"compiled:{_id_dir_key}"] = (_now_ts, compiled)
```

**每次构建 system prompt 都检查新鲜度，过期则自动规则编译**，结果进 TTL 缓存。

### 5.2 各层预算（`prompt/budget.py:89-98`）

```python
identity_budget: int = 6000   # SOUL.md(60%) + agent.core(25%) + user_policies(15%)
catalogs_budget: int = 8000   # tools 33% + skills 55% + mcp 10%
user_budget: int = 1200       # user.summary + runtime_facts
memory_budget: int = 3500     # retriever 输出（含 MEMORY.md + pinned rules + vector memory）
```

⚠️ **核验修正**：实际有两套 tier 映射，且都不是「LARGE 6000 / MEDIUM 5000 / SMALL 2500 / TINY 600」：
- `budget.py:116-162` `for_context_window` 按上下文窗口四档：**5000 / 3500 / 2500 / 600**
- `budget.py:164-193` `for_tier` 按 tier：SMALL **600**、MEDIUM **3000**，其余走 dataclass 默认 **6000**

文档原写的「LARGE 6000」「MEDIUM 5000」在源码中不作为档位存在（6000 只是 `BudgetConfig` 的字段默认值）。

### 5.3 Identity 层：注入**编译产物**，不是全文

```python
# builder.py:881-939
identity_core = compiled.get("identity_core") or _BUILT_IN_DEFAULTS.get("soul", "")
if identity_core:
    result = apply_budget(identity_core.strip(), budget_tokens * 30 // 100, "identity_core")
    parts.append(result.content)
if include_behavior:
    agent_behavior = compiled.get("agent_behavior", "")
    if agent_behavior:
        result = apply_budget(agent_behavior.strip(), budget_tokens * 40 // 100, "agent_behavior")
```

- `SOUL.md` → 注入 `compiled["identity_core"]`（编译产物，≤600 tokens），预算 `identity_budget*30%`
- `AGENT.md` → `compiled["agent_behavior"]`，预算 `identity_budget*40%`，仅 `prompt_mode==FULL` 时注入
- `prompts/policies.md` → 直读原文，预算 `identity_budget*15%`（926-936）
- 兜底 `_BUILT_IN_DEFAULTS["soul"]`（823-829）是三行硬编码字符串

⚠️ **两处不一致**：
1. `main.py:1868` 注释与 `zh.json:2714` 徽标都声称 SOUL.md 是"全文注入"，但代码实际注入编译产物 `identity_core`。**以代码为准**（注释疑似来自 commit `7ff280bb`，未落地或已过时）。
2. `builder.py:912` 用 `budget_tokens*30//100`，而 `budget.py:404` 定义 `"identity_core": identity_budget * 60 // 100`。两套数在不同调用点生效。

### 5.4 User 层：注入编译产物，**明确不全文注入**

```python
# builder.py:2347-2365
"""构建 User 层 — 使用编译后的用户档案摘要，不全文注入 USER.md。

P1-4：所有 user profile 输出都经 _clean_user_content 过滤，
防止 USER.md 编译产物里残留的占位字段（如"称呼: [待学习]"）
被 LLM 当作真实用户信息使用，进而覆盖动态学习到的姓名。
"""
content = compiled.get("user_profile_core", "")
if not content: return ""
cleaned = _clean_user_content(content)
```

包装成 `"## User Profile Core\n\n" + content`，进 `user_parts`（649-655，**所有 prompt_mode 都注入**）。

### 5.5 MEMORY.md 注入：渐进式披露 Layer 2

```python
# builder.py:1841-1847
# Layer 2: Core Memory (MEMORY.md — 用户基本信息 + 永久规则)
from openakita.memory.types import MEMORY_MD_MAX_CHARS as _MD_MAX
core_budget = min(budget_tokens // 2, 500)
core_memory = _get_core_memory(memory_manager, max_chars=min(core_budget * 3, _MD_MAX))
if core_memory:
    parts.append(f"## 核心记忆\n\n{core_memory}")
```

即 `max_chars = min(500*3, 1500) = 1500`。

`_get_core_memory`（2146-2193）：
- 路径从 `memory_manager.memory_md_path` 取
- **损坏自动 fallback**：先试 `MEMORY.md`，再试 `MEMORY.md.bak`（2157）
- **进程级 mtime 缓存**：`_CORE_MEMORY_CACHE: dict[(path, mtime_ns, max_chars), str]`，上限 32，按插入顺序淘汰
- 截断委托 `truncate_memory_md`（段落拆分、规则段落优先保留，`types.py:94-148`）

### 5.6 Memory 层完整分层（`builder.py:1812-1880`）

这是 openakita **最值得直接抄的设计** —— 记忆注入拆成 6 层，每层职责互不重叠：

| 层 | 内容 | 注入条件 |
| --- | --- | --- |
| **Layer 0** | `_MEMORY_SYSTEM_GUIDE`（**默认注入 compact 版，实测 ~295 token**；full 版实测 ~1865 token，仅 LOCAL_AGENT+LARGE 或显式 env 时用） | 满足 `_memory_scope` + `prompt_mode` 前置条件时注入 |
| Layer 1 | Scratchpad（当前任务 + 近期完成） | 总是注入 |
| Layer 1.5 | Pinned Rules（直接查 SQLite `type=rule`，**不受裁剪**） | 总是注入 |
| Layer 2 | Core Memory（MEMORY.md，`min(1500, ...)`） | 总是注入 |
| Layer 3 | Experience Hints（高权重经验，max_items=5） | 高输入压力时跳过 |
| Layer 4 | Active Retrieval（多路召回 `## 相关记忆（自动检索）`） | 短消息/闲聊时跳过 |
| Layer 5 | Relational graph（图检索） | 中高输入压力时跳过 |

**Layer 0 的实际文本**（`src/openakita/prompts/memory/guide.md`）见 §八，这是 nanobot 最该补的东西。

### 5.7 热重载链路

`api/routes/identity.py:290-296`（PUT 保存后）：

```python
runtime = get_runtime_config_coordinator(request).refresh_identity(
    _identity_dir(), reason=f"identity:{name}", refresh_policy=name == "POLICIES.yaml")
```

→ `runtime_config_coordinator.py:195-240` `_refresh_identity_in_engine`：

```python
result = self._clear_prompt_cache_in_engine()
identity = getattr(local_agent, "identity", None)
if identity is not None:
    identity.reload()                       # 清 Identity 4 字段缓存
    result.refreshed.append("global_identity")
compile_all(identity_dir)                   # 重新编译 runtime/*.md
result.refreshed.append("compiled_prompt")
result.merge(self._rebuild_agent_prompt_in_engine())
```

`_rebuild_agent_prompt_in_engine`（248-288）优先调 `agent._build_system_prompt_compiled_sync()`，写进 `agent._context.system`（282）。
`POST /reload` 走同一个 `refresh_identity(reason="identity:manual_reload", refresh_policy=True)`（315-327）。

---

## 六、MEMORY.md 从 SQLite 生成 —— **这是本次改造的核心参照**

### 6.1 主路径：`LifecycleManager.refresh_memory_md`（`memory/lifecycle.py:1384-1458`）

```python
def refresh_memory_md(self, identity_dir: Path) -> None:
    """刷新 MEMORY.md — LLM 审查后直接选取 top-K（无需关键词过滤）

    PR-B2：
    - 排除 source="profile_fallback" 的记忆（那是会话内的非结构化档案补充，
      不应该写进全局 MEMORY.md，否则跨会话注入会造成身份污染）。
    - 同一 (type, content_hash) 仅保留一条，避免 manual / session_extraction /
      daily_consolidation 三份重复同时写入。
    """
    # v4：限定 scope='user'，防止 pending_consolidation / legacy_quarantine
    # 里的未审查内容直接写进 MEMORY.md（之前未过滤 scope 会跨用户污染）。
    memories = self.store.query_semantic(scope="user", min_importance=0.5, limit=200)

    try:
        from ..core.feature_flags import is_enabled as _ff_enabled
        ff_filter = _ff_enabled("memory_session_scope_v1")
    except Exception:
        ff_filter = True

    if ff_filter:
        memories = [m for m in memories
                    if str(getattr(m, "source", "") or "") != "profile_fallback"]

    by_type: dict[str, list[SemanticMemory]] = defaultdict(list)
    seen_hashes: set[tuple[str, str]] = set()
    for mem in memories:
        type_value = mem.type.value
        content_norm = (mem.content or "").strip().lower()
        if ff_filter and content_norm:
            ch = hashlib.sha1(content_norm.encode("utf-8")).hexdigest()[:16]
            key = (type_value, ch)
            if key in seen_hashes: continue
            seen_hashes.add(key)
        by_type[type_value].append(mem)

    lines: list[str] = ["# 核心记忆\n"]
    type_labels = {"preference": "偏好", "rule": "规则", "fact": "事实",
                   "error": "教训", "skill": "技能", "experience": "经验"}
    total_chars = 0
    max_chars = MEMORY_MD_MAX_CHARS                # = 1500

    for type_key, label in type_labels.items():
        group = by_type.get(type_key, [])
        if not group: continue
        group.sort(key=lambda m: m.importance_score, reverse=True)
        lines.append(f"\n## {label}")
        for mem in group[:4]:                       # 每类最多 4 条
            line = f"- {mem.content}"
            if total_chars + len(line) > max_chars: break
            lines.append(line)
            total_chars += len(line)

    memory_md = identity_dir / "MEMORY.md"
    new_content = "\n".join(lines)
    if len(new_content.strip()) < 10:
        logger.warning("[Lifecycle] Generated MEMORY.md content too short, skipping refresh")
        return
    _safe_write_with_backup(memory_md, new_content)
```

**过滤条件汇总**：
`scope='user'` + `min_importance >= 0.5` + `limit=200` + 排除 `source=='profile_fallback'` + 同 `(type, sha1(content)[:16])` 去重 + 按 `importance_score` 降序 + **每类 top-4** + 全局 **1500 字符**封顶。

⚠️ 这条路径**没有优先级过滤**（不像备用路径会筛 PERMANENT/LONG_TERM），只有 importance ≥ 0.5。

**产出结构**（截图里那三段就是这个）：

```markdown
# 核心记忆

## 偏好
- ...
## 规则
- ...
## 事实
- ...
```

### 6.2 备用路径：`DailyConsolidator.refresh_memory_md`（`daily_consolidator.py:125-184`）

仅在 `LifecycleManager` 抛异常时兜底（`manager.py:2564-2574`）：

```python
except Exception as e:
    if isinstance(e, LLMError): self._reload_from_sqlite(); raise
    logger.error(f"[Manager] Daily consolidation failed, using legacy: {e}")
    from .daily_consolidator import DailyConsolidator
    dc = DailyConsolidator(...)
    result = await dc.consolidate_daily()
```

其逻辑（141-161）：`iter_cached()`（排除隔离桶）→ 只留 `priority in (PERMANENT, LONG_TERM)` → 按 type 分组 → `fact` 取 5 条、其余取 3 条 → `_generate_memory_md` **用中文标题**（`## 用户偏好 / ## 重要规则 / ## 关键事实 / ## 成功模式`，186-230）→ 超限时 `_compress_memory_md` 走 LLM 压缩（232-263，brain 不可用则 `truncate_memory_md`）。

⚠️ 两条路径产出的标题格式**不一致**：主路径 `## 偏好/规则/事实/教训/技能/经验`，备用路径 `## 用户偏好/重要规则/关键事实/成功模式`。而 `truncate_memory_md` 的高优先级关键词是 `{"重要规则","规则","rules","行为规则","用户规则"}`（`types.py:91`）—— 主路径的 `## 规则` 和备用路径的 `## 重要规则` 都能命中，但主路径的 `## 经验`/`## 教训` 不在其中。

### 6.3 `refresh_user_md`（`lifecycle.py:1464-1548`）—— **反向同步**

```python
async def refresh_user_md(self, identity_dir: Path) -> None:
    """从语义记忆自动填充 USER.md"""
    user_facts = self.store.query_semantic(subject="用户", limit=50)
    if not user_facts: return
    categories = {"basic": [], "tech": [], "preferences": [], "projects": []}

    _action_words = {"打开","关闭","运行","执行","安装","部署","启动","停止","创建",
                     "删除","修改","搜索","下载","上传","编译","测试","去","进入",
                     "访问","登录","检查","查看","发送"}
    user_facts = [m for m in user_facts
                  if not any(w in (m.predicate or "") for w in _action_words)
                  and not any(w in (m.content or "")[:20] for w in _action_words)]

    for mem in user_facts:
        pred = mem.predicate.lower() if mem.predicate else ""
        content = mem.content
        if any(k in pred for k in ("称呼","名字","身份","时区")):       categories["basic"].append(content)
        elif any(k in pred for k in ("技术","语言","框架","工具","版本")): categories["tech"].append(content)
        elif any(k in pred for k in ("偏好","风格","习惯")):            categories["preferences"].append(content)
        elif any(k in pred for k in ("项目","工作")):                  categories["projects"].append(content)
        elif mem.type == MemoryType.PREFERENCE:                      categories["preferences"].append(content)
        elif mem.type == MemoryType.FACT:                            categories["basic"].append(content)

    lines = ["# 用户档案\n", "> 由记忆系统自动生成\n"]
    section_map = {"basic": "基本信息", "tech": "技术栈", "preferences": "偏好", "projects": "项目"}
    ...
        for item in items[:8]:                                       # 每类最多 8 条
            lines.append(f"- {item}")
    if has_content:
        user_md = identity_dir / "USER.md"
        user_md.write_text("\n".join(lines), encoding="utf-8")       # ⚠️ 裸写，无备份
```

**动作词过滤是双向的**：既过滤 `predicate` 含动作词，也过滤 `content` **前 20 字符**内含动作词 —— 防止"用户希望删除 py 文件"这类一次性任务写进 USER.md。

分类优先级：`predicate` 关键词 → `memory type` 兜底。

⚠️ **两处真实缺陷**：
1. `refresh_user_md` 用**裸 `write_text`，没有 `_safe_write_with_backup`**（对比 `refresh_memory_md:1457`）。
2. 它写出的格式（`# 用户档案` + `## 基本信息`）与 `Identity.update_user_preference`（`agent/identity.py:657-687`）期望的 `**字段名**: [待学习]` 格式**完全不同 —— 两者会互相破坏**。
   → **nanobot 移植时不要照抄这个设计**，要么统一格式，要么 USER.md 只由一条路径写。

### 6.4 触发时机（3 处）

1. **每日凌晨定时任务（主）**：`core/_agent_runtime.py:2486-2610`
   - 记忆整理：**凌晨 3:00**（2506、2553）
   - 系统自检：凌晨 4:00（2507、2610）
   - 适应期内（`memory_consolidation_onboarding_days` 天）改为每 N 小时一次（2538-2558）
   - 执行器：`scheduler/executor.py:1093-1143`（任务名 `system:daily_memory`）→ `mm.consolidate_daily(...)`
2. **`consolidate_memories` 工具**（LLM 主动调）：`tools/handlers/memory.py:527-570` → `agent.memory_manager.consolidate_daily()`（536）。工具定义 `tools/definitions/memory.py:13-32`，面向"整理记忆 / 清理垃圾记忆"场景。
3. **手动 HTTP**：`POST /api/memory/refresh-md` → `lifecycle.refresh_memory_md(...)`（`memory.py:1238-1250`）

调用链：`consolidate_daily()`（`manager.py:2524-2578`）→ `LifecycleManager.consolidate_daily`（`lifecycle.py:145`）→ 依次 `process_unextracted_turns` → `review_memories_with_llm` → `synthesize_experiences` → **`refresh_memory_md` + `refresh_user_md`**（`lifecycle.py:275-277`）。

另有一处：LLM 审查后台任务完成后也会刷新（`memory.py:996`）。

### 6.5 `MEMORY_MD_MAX_CHARS` 定义

```python
# src/openakita/memory/types.py:82-89
MEMORY_MD_MAX_CHARS = 1500
"""MEMORY.md 统一大小上限（字符），写入端和读取端共用。"""

# 借鉴 claude-code 的 memdir.MAX_ENTRYPOINT_LINES / MAX_ENTRYPOINT_BYTES
MEMORY_MD_MAX_LINES = 200
MEMORY_MD_MAX_BYTES = 25_000
```

三档上限（字符/行/字节）由 `truncate_memory_md_with_status` 统一施加，返回 `{original_chars, original_lines, original_bytes, truncated, triggers, warning}`。

### 6.6 `truncate_memory_md` 的段落优先级（`types.py:94-143`）

以下为源码**逐字摘录**（2026-09-16 核验）：

```python
def truncate_memory_md(content: str, max_chars: int = MEMORY_MD_MAX_CHARS) -> str:
    """按段落优先级截断 MEMORY.md 内容。

    策略：
    1. 按 ``## `` 拆分段落
    2. 将段落分为高优先级（规则类）和普通优先级
    3. 先填充高优先级段落（规则），再填充普通段落
    4. 超出预算时截断普通段落，规则段落尽量保留
    """
    content = content.strip()
    if not content or len(content) <= max_chars:
        return content

    sections = re.split(r"(?=^## )", content, flags=re.MULTILINE)

    high_priority: list[str] = []
    normal_priority: list[str] = []
    header = ""

    for section in sections:
        stripped = section.strip()
        if not stripped:
            continue
        if stripped.startswith("# ") and not stripped.startswith("## "):
            header = stripped
            continue
        title_match = re.match(r"^## (.+)", stripped)
        if title_match:
            title = title_match.group(1).strip().lower()
            if any(kw in title for kw in _RULE_SECTION_KEYWORDS):
                high_priority.append(stripped)
                continue
        normal_priority.append(stripped)

    result_parts: list[str] = []
    current_len = len(header) + 2 if header else 0
    if header:
        result_parts.append(header)

    for section in high_priority:
        if current_len + len(section) + 2 <= max_chars:
            result_parts.append(section)
            current_len += len(section) + 2
        else:
            remaining = max_chars - current_len - 20
            if remaining > 50:
                result_parts.append(section[:remaining] + "\n...(规则被截断)")
            break

    for section in normal_priority:
        if current_len + len(section) + 2 <= max_chars:
            result_parts.append(section)
            current_len += len(section) + 2

    return "\n\n".join(result_parts)
```

**四个容易被误读的实现细节**（移植时最容易抄错的地方）：

1. **标题匹配是子串包含，不是等值比较**：`any(kw in title for kw in _RULE_SECTION_KEYWORDS)`，其中 `_RULE_SECTION_KEYWORDS = {"重要规则", "规则", "rules", "行为规则", "用户规则"}`（5 个，见 §6.2）。**没有 `extract_title()` 这个函数**——标题用内联 `re.match(r"^## (.+)", stripped)` 提取。
2. **分隔符计入预算**：每段累加 `len(section) + 2`（对应 `"\n\n".join` 的两个换行），不是 `len(section)`。
3. **高优先级段有"部分截断 + 标注"分支**：预算不够时截到 `remaining` 字符并追加 `"\n...(规则被截断)"`，**然后 break**——不是直接丢弃整段。
4. **普通段不 break 而是 continue**：`for` 循环里没有 `break`，装不下就跳过继续试下一段（可能塞进更短的段）。`# ` 一级标题被单独提取为 header 并优先保留。

---

## 七、手动编辑保护：**直接整体覆盖，只有 .bak 备份**

### 7.1 明确结论：无 diff 检测、无合并

`refresh_memory_md` 每次从 SQLite 重新生成完整内容，然后**整体覆盖**（`lifecycle.py:1450-1457`）：

```python
memory_md = identity_dir / "MEMORY.md"
new_content = "\n".join(lines)
if len(new_content.strip()) < 10: return          # 唯一保护：内容太短则跳过
_safe_write_with_backup(memory_md, new_content)
```

**没有任何 diff 比对、没有用户段落保留、没有冲突提示。**
唯一"保护"是内容 <10 字符时跳过写入（防止把文件清空）。
`refresh_user_md` 更彻底 —— **连备份都没有**。

### 7.2 `_safe_write_with_backup`（`lifecycle.py:102-125`）

```python
def _safe_write_with_backup(path: Path, content: str) -> None:
    """安全写入文件：先备份再写入，写失败则恢复"""
    backup = path.with_suffix(path.suffix + ".bak")
    try:
        if path.exists():
            import shutil
            shutil.copy2(path, backup)
    except Exception as e:
        logger.warning(f"Failed to create backup of {path}: {e}")

    try:
        path.write_text(content, encoding="utf-8")
    except Exception as e:
        logger.error(f"Failed to write {path}: {e}")
        if backup.exists():
            try:
                shutil.copy2(backup, path)
                logger.info(f"Restored {path} from backup")
            except Exception as e2:
                logger.error(f"Failed to restore {path} from backup: {e2}")
        raise
```

`MEMORY.md.bak` **只保存"上一次"内容**（每次覆盖写，不是带时间戳的多版本）。
读取侧 `_get_core_memory` 会在主文件读失败/为空时回退 `.bak`（`builder.py:2157`）。

### 7.3 前端提示语的准确性

- `zh.json:2719` memoryWarning：**"手动修改的内容可能在下次自动刷新时被完全覆盖"—— 描述准确**。并给了正确出路："如需持久化规则，建议使用记忆系统的 add_memory 功能（type=rule）"（写进 SQLite 成为 RULE 记忆后，会经 `refresh_memory_md` 重新生成到 MEMORY.md，也能被 `_build_pinned_rules_section` 独立注入）。
- `zh.json:2720` userWarning："lifecycle 刷新时会整体覆盖此文件" —— 同样准确。

---

## 八、Layer 0：记忆系统自描述（nanobot 最该补的东西）

### 8.1 注入机制（**不是"恒定注入 system prompt 顶部"**）

⚠️ 核验修正：openakita 把这段文本作为 **Memory 层的第一段**，追加进 **`developer_parts`**（不是 system prompt 顶部），且**默认注入的是 compact 版**。

`builder.py:1813`（`_build_memory_section` 内）：

```python
parts.append(_MEMORY_SYSTEM_GUIDE_COMPACT if use_compact_guide else _MEMORY_SYSTEM_GUIDE)
```

这个 `memory_section` 最终由 `builder.py:646` `developer_parts.append(memory_section)` 落位。
**前置条件有两层**：
- `builder.py:613`：`if _memory_scope in {"pinned_only","relevant","full"} and prompt_mode in (FULL, MINIMAL)`
- `builder.py:1807`：`if not memory_manager: return ""`

**full 版 vs compact 版的切换条件**（`builder.py:625-634`，源码注释原文）：

```python
# Phase 5：compact Memory Guide 设为默认（节省 ~600 token/轮）。
# 完整版只在以下场景才用：
#  1) LOCAL_AGENT profile 且 LARGE tier（大上下文窗口，模型有空间消化教学性 prompt）；
#  2) 用户显式设置 OPENAKITA_PROMPT_VERBOSE_MEMORY_GUIDE=1。
# 短窗口 / CONSUMER_CHAT 一律 compact —— 这些场景模型通常按 token 数计费，
# 也是用户最容易感知"慢"的地方。
_verbose_env = os.environ.get("OPENAKITA_PROMPT_VERBOSE_MEMORY_GUIDE", "").strip()
_verbose_override = _verbose_env in {"1", "true", "yes", "on"}
_eligible_for_full = _profile == PromptProfile.LOCAL_AGENT and _tier == PromptTier.LARGE
_use_compact = not (_verbose_override or _eligible_for_full)
```

⚠️ **token 数字三处不一致**（核验发现）：
- 源码注释（`builder.py:1812`）自述「compact 版 ~200 token，完整版 ~600 token」
- 但实测文件体积：`guide.md` = **3177 字符**、`guide_compact.md` = **505 字符**；按项目自带 `estimate_tokens` 公式估算约 **~1865** / **~295** token
- 结论：**以实测为准**，注释低估了约 3 倍。移植做预算时不要引用注释里的 200/600。

### 8.2 compact 版全文（`prompts/memory/guide_compact.md`，14 行）

```markdown
## 你的记忆系统

### 信息优先级
1. **对话历史** — 最高优先级，直接引用即可
2. **系统注入记忆** — 跨会话持久化知识
3. **记忆搜索工具** — 查找更早的历史信息

- 用户提到"之前/上次" → 用 `search_memory` 搜索
- 用户透露偏好时 → 用 `add_memory` 保存
- 记忆可能过时 → 行动前用工具验证当前状态
- 禁止虚假声称已保存记忆

### 当前注入的信息
下方是用户核心档案和高权重经验。
```

### 8.3 full 版全文（`prompts/memory/guide.md`，57 行）

```markdown
## 你的记忆系统

你有一个三层分层记忆网络，各层双向关联。

### 信息优先级（必须遵守）

1. **对话历史**（messages 中的内容）— 最高优先级。本次对话中已讨论的内容、已完成的操作、
   已得出的结论，直接引用即可，**不需要搜索记忆来验证**
2. **系统注入记忆**（下方已注入的核心记忆和经验）— 跨会话的持久化知识，当对话历史中没有
   相关信息时参考
3. **记忆搜索工具**（search_memory / search_conversation_traces 等）— 用于查找**更早的、
   不在当前对话中的**历史信息

常见错误：对话中刚讨论过的内容去 search_memory 搜索 → 浪费时间且可能搜不到（异步索引有延迟）。
正确做法是直接引用对话历史。

### 记忆层级说明
**第一层：核心档案**（下方已注入）— 用户偏好、规则、事实的精炼摘要
**第二层：语义记忆 + 任务情节** — 经验教训、技能方法、每次任务的目标/结果/工具摘要
**第三层：原始对话存档** — 完整的逐轮对话，含工具调用参数和返回值

### 搜索记忆的两种模式

**Mode 1 — 碎片化搜索**（关键词匹配，适用于大多数查询）：
- `search_memory` — 按关键词搜索知识记忆（fact/preference/skill/error/rule）
- `list_recent_tasks` — 列出最近完成的任务情节
- `search_conversation_traces` — 搜索原始对话（含工具调用和结果）
- `trace_memory` — 跨层导航（记忆 ↔ 情节 ↔ 对话）

**Mode 2 — 关系型图谱搜索**（多维度图遍历，适用于复杂关联查询）：
- `search_relational_memory` — 沿因果链、时间线、实体关系多跳搜索

**何时使用 search_relational_memory**（而非 search_memory）：
- 用户问**为什么/什么原因** → 因果链遍历
- 用户问**之前做过什么/经过/时间线** → 时间线遍历
- 用户问**关于某个事物的所有记录** → 实体追踪
- 默认或简单查询 → 用 search_memory 即可（更快）

### 何时保存记忆（使用 add_memory — 仅 Mode 1）

后台会自动从对话中提取记忆，你只需在以下场景**主动**保存：
**preference（偏好）** — 用户透露工作习惯、沟通偏好、风格喜好时
**fact（事实）** — 不能从当前状态推导出的关键信息（角色、截止日期、决策背景等）
**rule（规则）** — 用户设定的行为约束
**error（教训）** — 出了什么错、根因是什么、正确做法是什么
**skill（技能）** — 可复用的方法流程

用户明确要求你记住某件事时，立即按最合适的类型保存。

### 记忆可靠性（行动前必读）

- **记忆可能过时**：行动前先用工具验证当前状态
- **记忆与观察冲突时以观察为准**
- **引用记忆做推荐前先验证**
- **用户说"忽略记忆"时**：当作记忆为空

**禁止虚假声称**：永远不要说"我已将此信息保存到记忆中"，除非你确实调用了 `add_memory` 工具。

### 当前注入的信息
下方是用户核心档案、当前任务状态和高权重历史经验。
```

**为什么这段关键**：它把"优先级 / 各文件职责 / 工具何时用 / 什么时候不该用"一次性讲清，LLM 才不会对 `memory_search` 感到陌生。nanobot 的 `identity.md` 只在第 9 行提了一句 `memory_search`，没有这段全局说明。

**移植建议**：openakita 默认只注入 compact 版（14 行），full 版是少数 profile 的特权。nanobot 若要加，**建议直接上 compact 版的规模**，不必照搬 full 版的 57 行。

---

## 九、关键代码片段索引

| 主题 | 位置 |
| --- | --- |
| 前端 `MEMORY_MAX_CHARS = 1500` | `apps/setup-center/src/views/IdentityView.tsx:27` |
| 前端 token 估算 | `IdentityView.tsx:29-37` |
| 前端 diffLines | `IdentityView.tsx:39-48` |
| 前端 WARNING_KEYS 映射 | `IdentityView.tsx:50-57` |
| 保存流程（validate → 确认 → force 直写） | `IdentityView.tsx:123-180` |
| 编辑器 textarea | `IdentityView.tsx:366-381` |
| 字符数红字警告 | `IdentityView.tsx:394-401` |
| 后端路径穿越防护 | `src/openakita/api/routes/identity.py:73-79` |
| 后端 compiled 写保护 | `identity.py:260-264` |
| 后端 MEMORY.md 长度 warning | `identity.py:135-142` |
| 后端 compile 端点 | `identity.py:330-389` |
| 四场景决策矩阵 | `src/openakita/agent/identity.py:173-254` |
| hash 账本 | `agent/identity.py:31-52`（`.file_hashes.json`） |
| reload 清缓存 | `agent/identity.py:139-146` |
| 编译目标与 token 上限 | `src/openakita/prompt/compiler.py:35-76` |
| user_profile_core 排除清单 | `compiler.py:307-316` |
| 严格 token 封顶（二分） | `compiler.py:399-427` |
| mtime + schema 缓存失效 | `compiler.py:471-519` |
| 编译模型注入点（`_compile_with_llm`） | `compiler.py:160-175` |
| 编译降级路径（LLM → rules） | `compiler.py:178` |
| Identity 层注入 | `src/openakita/prompt/builder.py:881-939` |
| IdentityView 编译按钮 + 60s 超时 | `IdentityView.tsx:183-199` |
| **nanobot 编译模型可配置设计** | §十 10.2.3 |
| User 层注入（不全文） | `builder.py:2347-2381` |
| Memory 层 6 分层 | `builder.py:1812-1880` |
| MEMORY.md 注入（Layer 2） | `builder.py:1841-1847` |
| `_get_core_memory` + mtime 缓存 | `builder.py:2142-2193` |
| 预算默认值 | `src/openakita/prompt/budget.py:89-98` |
| MEMORY 层 Layer 0 文本 | `src/openakita/prompts/memory/guide.md` |
| `MEMORY_MD_MAX_CHARS = 1500` | `src/openakita/memory/types.py:82` |
| `truncate_memory_md`（段落优先级） | `types.py:94-143` |
| 主 refresh_memory_md | `src/openakita/memory/lifecycle.py:1384-1458` |
| `_safe_write_with_backup` | `lifecycle.py:102-125` |
| refresh_user_md | `lifecycle.py:1464-1548` |
| 备用 DailyConsolidator | `src/openakita/memory/daily_consolidator.py:125-230` |
| 每日整理调度（凌晨 3:00） | `src/openakita/core/_agent_runtime.py:2486-2610` |
| `POST /api/memory/refresh-md` | `src/openakita/api/routes/memory.py:1238-1250` |
| 热重载链路 | `src/openakita/runtime_config_coordinator.py:195-288` |
| 过时注释「SOUL.md 已改为全文注入」 | `src/openakita/main.py:1868` |

---

## 十、nanobot 移植方案

### 10.1 nanobot 需要建立的三个不变量（**先做这个**）

1. **SQLite `memories` 表是唯一真相源**。
   openakita 明确 `SQLite 是唯一真相源，MEMORY.md 是派生产物`（`manager.py:424-426` `_LEGACY_JSON_BACKFILL_SENTINEL`、`_reload_from_sqlite`）。
   nanobot 现在是 **Dream LLM 直接改写文件**，没有"派生"这个语义。必须先确立这个不变量，否则"手动编辑 vs 自动刷新"永远模糊。

2. **MEMORY.md 不进"随版本升级"的追踪矩阵**。
   openakita 的 `_TRACKED_FILES = ["SOUL.md","AGENT.md","USER.md"]` **刻意排除 MEMORY.md**（`identity.py:32`）—— 因为它是运行时派生产物，不该被 `.example` 覆盖检测管。nanobot 移植时保持这一点。

3. **文件写入必须走"先备份再写"**。
   照抄 `_safe_write_with_backup`，**并且给 USER.md 也加上**（openakita 自己漏了）。

### 10.2 nanobot 四块改造，难度排序

**（1）后端 API —— 最容易，直接照搬**
`api/routes/identity.py` 522 行自包含。移植时替换两处依赖：
- `prompt.budget.estimate_tokens` → nanobot 自己的 tokenizer
- `runtime_config_coordinator.refresh_identity` → nanobot 的 **MemoryStore 重载 + Dream 重跑**

nanobot 已有 `nanobot/webui/settings_routes.py` 的路由注册模式（`148-173` 行有现成的 memory 路由表），身份文件端点可以放在同一个路由体系里。

**（2）前端页面 —— 中等，但反而要升级**
`IdentityView.tsx` 499 行、零全局状态、只吃两个 props，**几乎可整文件复制**。需改：
- `safeFetch` / `apiBaseUrl` → nanobot 的 gateway 调用方式
- `t()` → nanobot 的 i18n（`webui/src/i18n/locales/`，已有 zh-CN / en / ja 等 11 个语种）
- `IconXxx` → nanobot 的图标库（`lucide-react` 已有）
- **建议换掉 textarea 上 monaco**：openakita 自己没上，但 `@monaco-editor/react` 能带来语法高亮 + `Ctrl+F`，成本低。注意体积，`IdentityView` 已是 `React.lazy`，正好复用 Suspense 边界。

**（3）编译管线 —— 中等偏难**
`PromptCompiler`（531 行，无外部依赖）可整体移植，但规则降级依赖的 `_extract_owned_sections` / `_TARGET_EXCLUDED_LINE_MARKERS` 是**针对 openakita 中文段落标题硬编码**的（`身份认知/核心性格/使命/成长循环/自我修复`），nanobot 要重写关键词表。
`COMPILED_SCHEMA_VERSION` + `.compiler_version` + `.compiled_at` **mtime 抬到源文件最大值+1ns** 的做法值得直接抄（踩过同秒写入误判的坑）。
**`user_profile_core` 的排除清单（`compiler.py:307-316`）可以原样抄** —— 这正是 nanobot 缺的"跳过未填占位"。

#### 10.2.3 LLM 编译 + 编译模型可配置（新增决策：2026-09-16）

**背景**：openakita 的 `PromptCompiler._compile_with_llm`（[compiler.py:160-175](#)）只接受一个 `brain: Brain` 注入，**编译模型 = agent 主对话模型**，无法独立配置。三个后果：

| 痛点 | 说明 |
| --- | --- |
| 成本高 | 编译是大文本摘要，主对话模型（如 Sonnet 5）浪费；轻量模型（Haiku 4.5）足够 |
| 速度慢 | 主模型延迟决定编译延迟，按钮点下去 60s 超时（[IdentityView.tsx:189](#)） |
| 不可换 | 用户想"编译用便宜模型、对话用贵模型"做不到 |

**nanobot 目标**：保留 openakita 的"LLM 编译 + 规则降级"双路径，但**让编译模型独立可配**；前端只暴露一个下拉框。

##### (a) 三层抽象

```
前端「编译模型」下拉
   ↓  POST /api/identity/compile
   ↓  body: { mode: "llm", model?: "haiku-4-5" | "sonnet-5" | "<follow-main>" }
路由层 compile_identity
   ↓  读 settings.identity_compilation.model
   ↓  若 "<follow-main>" → 用 brain 当前模型
   ↓  否则 → 复用现有 provider，只换 model 字段（同一 key、同一个 client）
PromptCompiler.compile_all
   ↓  把"编译专用 brain / 客户端"注入进去
```

**关键约束：复用现有 provider，不引入新 provider 配置**。用户已经在 provider 配了 key，编译只是切 model 字段。这样不会引入新的配置爆炸。

##### (b) 配置 schema（落到 `nanobot/config/schema.py`，紧贴现有 `memory_*` 字段）

```jsonc
{
  "memory_enabled": true,
  "memory_idle_seconds": 60,

  // 新增：身份编译专用模型（与 openakita 默认走 brain 的行为不同）
  "identity_compilation": {
    "mode": "llm",                              // "llm" | "rules" | "auto"
    "model": "haiku-4-5",                      // "<follow-main>" 跟主对话走
    "max_tokens_per_target": {                 // 覆盖默认值，可选
      "identity_core": 600,
      "agent_behavior": 450,
      "user_profile_core": 300
    },
    "timeout_seconds": 30,                      // openakita 60s 偏长
    "fallback_to_rules_on_error": true         // LLM 失败自动降级
  }
}
```

##### (c) 默认值与决策

| 决策 | 默认 | 理由 |
| --- | --- | --- |
| `mode` | `"llm"` | 用户买的是"智能"，规则是兜底 |
| `model` | `"<follow-main>"`（v1）→ `"haiku-4-5"`（v2 优化后） | v1 兼容 openakita 行为，零迁移成本；v2 测出主模型延迟后再切 |
| `timeout_seconds` | `30` | 编译失败快速降级比慢速重试体验好 |
| `fallback_to_rules_on_error` | `true` | 与 openakita 一致（[compiler.py:178](#)） |

##### (d) 前端下拉框（参考 openakita 但升级）

openakita 现在只有「LM 优化 / 规则编译」两个按钮，无模型选择（[IdentityView.tsx:183-199](#)）。nanobot 在 IdentityView 顶栏紧挨着重载按钮加一个 **「编译模型」下拉**：

```
编译模型:  [Haiku 4.5 ▾]   ← 默认（便宜快）
            ├─ Haiku 4.5         ← 推荐：摘要任务足够
            ├─ Sonnet 5          ← 质量更高
            ├─ 跟随主对话模型    ← 与 agent 同款（兼容旧行为）
            └─ 仅规则编译        ← 不调 LLM（mode=rules）
```

**选项动态从 provider registry 拉**（[nanobot/providers/registry.py](nanobot/providers/registry.py) 已有模型发现），不需新做模型发现逻辑；不暴露 provider key 切换（用户已在 provider 设置页配过）。

##### (e) 与 openakita 的差异（nanobot 做得更好的地方）

| 维度 | openakita | nanobot |
| --- | --- | --- |
| 编译模型 | 跟随主对话，不可配 | 下拉独立选 |
| 超时 | 固定 60s | 默认 30s，可配 |
| 三档预算（600/450/300） | 硬编码 | config 可覆盖 |
| mode 切换 | 两个按钮 | 一个下拉 + 全局 mode 配置 |
| 降级可见性 | log warning，无 UI 反馈 | 返回 `{mode_used: "rules (LLM failed)"}`，前端 banner 提示 |

##### (f) WU 拆分建议

| WU | 范围 | agent_role |
| --- | --- | --- |
| WU-C1 | 配置 schema + 加载逻辑（`identity_compilation` 字段） | coder |
| WU-C2 | `PromptCompiler` 接受 `compilation_brain` 参数 + 复用 provider | coder |
| WU-C3 | `POST /api/identity/compile` 读取 model + 30s 超时 + 降级路径 | coder |
| WU-C4 | 前端「编译模型」下拉 + 降级结果 banner | coder（UI 子任务） |
| WU-C5 | 单测：model 注入、降级触发、超时熔断 | test-engineer |

依赖链 C1 → C2 → C3 → C4；C5 与 C2/C3 并行。

##### (g) 风险与回退

- **风险 1：复用 provider 但切 model 字段，跨 provider 边界**。如 `provider=anthropic` 切到 `model=openai/gpt-5` 应当**直接报错**（不在同 provider）。实施时校验 `provider_id` 匹配，不匹配返回 400。
- **风险 2：`<follow-main>` 语义在并发场景下歧义**。若用户在前端点编译的同时 agent 正在切换 provider，需锁住 `provider_id` 快照。简单做法：进入编译时拍下 `(provider_id, model)` 副本，编译期间不感知后续变更。
- **回退**：保留 openakita 的纯规则编译为兜底；`mode=rules` 永远可用；最坏情况用户改回 `model=<follow-main>` + `fallback=true` 即回到当前行为。

**（4）MEMORY.md 生成 —— 中等，且是概念变更核心**
把 openakita 的 `refresh_memory_md` 逻辑搬到 nanobot，接在现有 `SessionEndOrchestrator` / `MemoryExtractor` 之后，与 Dream 路径**并存**：

| 路径 | 产出特征 | 何时用 |
| --- | --- | --- |
| **Dream（现有）** | LLM 生成式总结，可复现性差，价值密度高 | 保留，处理需要"提炼"的场景 |
| **refresh_memory_md（新增）** | 确定性程序化镜像，可预测 | 新增，作为"记忆事实的证据视图" |

### 10.3 nanobot 特有的风险点

| 风险 | 说明 |
| --- | --- |
| **Dream 会 LLM 改写文件** | openakita 的 `refresh_memory_md` 是**确定性**的。nanobot 的 Dream 输出**不可复现**，前端"1543/1500"红字警告会出现得更频繁。建议给 Dream 也加确定性截断（照抄 `truncate_memory_md_with_status` 三档：字符/行/字节）。 |
| **USER.md 双向覆盖冲突** | openakita 这里有 bug（`refresh_user_md` 与 `update_user_preference` 格式互斥）。nanobot 移植时**不要照抄** —— 要么统一格式，要么 USER.md 只由一条路径写。 |
| **`restricted` 是假保护** | openakita 前后端都没真正阻止写 MEMORY.md。nanobot 若真要做"core 不可编辑"，需在 PUT 端点里**显式读白名单并 403**，别只学 UI 图标。 |
| **字符上限双份维护** | openakita 前端 `MEMORY_MAX_CHARS=1500` 与后端 `MEMORY_MD_MAX_CHARS=1500` 人工同步。nanobot 建议顺手修掉：`GET /files` 响应里带上 `max_chars`。 |
| **`_BUDGET_MAP` 与 `BudgetConfig` 不一致** | `identity.py:34` 说 identity.core 预算 600，`builder.py:912` 实际按 `identity_budget*30%` 算。两套数脱节。nanobot 统一为单一来源。 |
| **凌晨 3:00 调度** | openakita 用自建 scheduler + 适应期模式 + checkpoint 断点续跑。nanobot 若已有 cron（`nanobot/cron/`）可简化，但 **checkpoint 机制值得保留**（LLM 审查耗时长，中断后续跑避免重复烧 token）。 |

### 10.4 建议的 MVP 范围

1. **先做后端 + 数据层不变量**：`GET /files`、`GET /file`、`PUT /file`、`POST /reload`（`POST /compile` 可后置），并把 MEMORY.md 确立为派生产物。
2. **补 Layer 0**：把 §八 的记忆系统自描述注入 system prompt 顶部（这是**独立于身份配置的、收益最快的一项**）。
3. **改 Dream 输出**：Dream 改写后套确定性截断，把 `warning` 字段透出到 API。
4. **改 USER.md 注入**：用编译产物替代全文（过滤 `[ ]` 占位），或至少做到"空字段不渲染"。
5. **最后做前端**：搬 `IdentityView.tsx`，换 fetch 层 + i18n，textarea 换 monaco。

---

## 十一、未找到 / 需注意

- **未找到** monaco 或任何富文本编辑器依赖（`package.json` 无 `monaco-editor` / CodeMirror）。截图的编辑器是 `<textarea>` + `fontFamily: monospace`。
- **未找到** `_pending_upgrades` 的 HTTP 暴露端点 —— 只有 `Identity.get_pending_upgrades()` / `apply_upgrade()` 供 CLI 调用。
- **未找到** 前端对 `GET /api/identity/compile-status` 的任何调用（端点存在但 `IdentityView` 未使用，前端只用 `/files` 里的 `budget_tokens`）。
- **未找到** "core 文件不可编辑"的服务端硬拦截 —— `_RESTRICTED_FILES` 只用于返回 `restricted` 标记。
- **文档/代码不一致**：`main.py:1868` 注释与 `zh.json:2714` 徽标均称 SOUL.md 是"全文注入"，但 `builder.py:910-912` 实际注入编译产物。**以代码为准**。
- **前后端常量不一致**：`builder.py:912` 用 `budget_tokens*30//100`，`budget.py:404` 定义 `identity_core: identity_budget*60//100`。

---

## 十二、与向量化文档的接口

本文档与《openakita 记忆向量化实现调研》在数据流上是**同一条链的下游**：

```
SQLite memories 表  ←── 唯一真相源
   ├─→ 向量索引（向量化文档）      ：镜像 memories，供语义召回
   └─→ MEMORY.md（本文档）         ：从 memories 程序化生成，供人类审阅 + system prompt 注入
```

**两条下游的共同约束**：

1. 向量索引的写入必须**挂在 SQLite 写入路径上**（openakita 的 `save_semantic` 双写），否则会漂移。
2. MEMORY.md 的生成必须**读同一张表**（openakita 的 `refresh_memory_md` 查 `query_semantic`）。

**关键推论 —— 这是两个改动应当同批设计的原因**：

一旦两条下游都从 SQLite 派生，就会共享同一套 `type`（fact/preference/rule/error/skill/experience）与同一套 `importance_score` 语义。
- MEMORY.md 的六段结构（`## 偏好 / ## 规则 / ## 事实 / ## 教训 / ## 技能 / ## 经验`）直接来自 `MemoryType` 枚举 —— nanobot 的 `MemoryType` 已经有这 6 个值（`models.py:11-18`），**无需新增枚举**。
- `min_importance=0.5` 的过滤阈值同时影响"哪些记忆进 MEMORY.md"和"哪些记忆进向量召回候选"，**必须统一**，否则会出现"MEMORY.md 里有但搜不到"或反之。
