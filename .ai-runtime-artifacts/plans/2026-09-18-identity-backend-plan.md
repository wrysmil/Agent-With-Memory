---
artifact: implementation-plan
route: superpowers:writing-plans
skills:
  - writing-plans
skills_evidence:
  - .claude/skills/writing-plans/SKILL.md
dispatch: .ai-runtime-artifacts/plans/2026-09-18-identity-backend-dispatch.md
source:
  - AGENTS.md
  - harness-kit/core/routing.md
  - .ai-runtime-artifacts/research/2026-09-16-openakita-identity-config-and-memory-md-research.md
  - .ai-runtime-artifacts/research/2026-09-18-memory-injection-nanobot-baseline-vs-openakita.md
created_at: 2026-09-18
status: draft
approved: false
---

# 身份文件后端接入 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 WebUI 的「身份」设置页脱离 mock 数据，读写真实身份文件；端点形状严格对齐 openakita，为后续接入编译能力留出无痛升级位。

**Architecture:** 新建 `nanobot/identity/` 包作为身份文件的唯一真相源（白名单 / 目录解析 / 读写 / 路径穿越防护），经 `nanobot/webui/identity_api.py` + `identity_routes.py` 暴露为 `/api/settings/identity/*`，注册进既有的 `settings_routes` 系统路由表。`MEMORY.md` 的写入复用既有 `MemoryLifecycle._safe_write_with_backup`，**不新写写入逻辑**。

**Tech Stack:** Python 3.11 / asyncio、dataclass 契约（非 Pydantic）、pytest（`asyncio_mode = "auto"`）；前端 React 19 + TypeScript + Vite、`webui/src/lib/api.ts` 客户端、vitest。

---

## 用户已确认的决策（不得自行更改）

| 决策 | 取值 | 来源 |
|---|---|---|
| 后端范围 | **分阶段**：只做文件列表 / 读取 / 写入 / 重载 + personas 动态发现；三个编译类按钮先返回明确「未启用」 | 用户 2026-09-18 选项 A |
| 文件落盘 | **新建统一 `<workspace>/identity/` 目录**，对齐 openakita 布局 | 用户 2026-09-18 选项 A |

## 范围外（明确不做）

- PromptCompiler（LM 优化 / 规则编译）——独立子系统，本轮只留端点占位
- L0/L1/L3/L5 记忆分层与自适应 token 预算
- `POLICIES.yaml` 的策略**执行**（本轮只做文件读写与 YAML 语法校验，不接入工具审批）
- personas 的**注入**（本轮只做目录发现与编辑，不改变 system prompt 组装）

---

## ⚠️ 两个必须先看清的坑

### 坑 1：`AGENTS.md` ≠ `AGENT.md`，**不要合并**

| 文件 | nanobot 现有语义 | openakita 语义 | 结论 |
|---|---|---|---|
| `AGENTS.md`（复数，**project_root**） | **项目级指令**，等价于 CLAUDE.md（`context.py:314`） | 无此概念 | **保持不动**，不进 identity/ |
| `AGENT.md`（单数，identity dir） | **不存在** | Agent 行为约束 | **新建**，是本次要加的文件 |

两者仅差一个字母，极易被误判为同一文件。**若把 `AGENTS.md` 迁进 `identity/AGENT.md`，会破坏项目级指令语义。**

### 坑 2：`SOUL.md` / `USER.md` 迁移会动到 bootstrapping

这两个文件当前位于 **workspace 根**（`context.py:315-316`），迁入 `identity/` 必须同步改 `ContextBuilder._load_bootstrap_files` 的路径来源，并处理**老工作区已存在的同名文件**（用户可能已经改过）。Task 2 专门处理，不允许跳过。

---

## File Structure

### 新建

| 文件 | 职责 |
|---|---|
| `nanobot/identity/__init__.py` | 包导出 |
| `nanobot/identity/catalog.py` | **唯一真相源**：身份目录解析、核心文件白名单与元数据、personas 发现、字符上限常量 |
| `nanobot/identity/store.py` | `IdentityStore`：`list_files` / `read_file` / `write_file`，含路径穿越防护与写入校验 |
| `nanobot/identity/bootstrap.py` | 首次运行从 `nanobot/templates/` 播种 `identity/` 目录；老工作区迁移 `SOUL.md`/`USER.md` |
| `nanobot/webui/identity_api.py` | 面向 WebUI 的操作实现 + payload 转换（仿 `memory_api.py`） |
| `nanobot/webui/identity_routes.py` | 领域 handler（仿 `memory_routes.py`），action 分发 |
| `tests/identity/test_catalog.py` | 白名单 / 分组 / 常量单测 |
| `tests/identity/test_store.py` | 读写 / 路径穿越 / 校验单测 |
| `tests/identity/test_bootstrap.py` | 播种与迁移单测 |
| `tests/webui/test_identity_routes.py` | 端点契约单测 |

### 修改

| 文件 | 改动 |
|---|---|
| `nanobot/webui/settings_routes.py` | `_SYSTEM_ROUTES` 加 identity 路径；`_SETTINGS_MUTATION_PATHS` 加写路径 |
| `nanobot/webui/ws_http.py` | `GatewayHTTPHandler.__init__` 注入 identity operations；dispatch 加 identity 分支 |
| `nanobot/webui/gateway_services.py` | 组装 identity operations（仿 memory 的 partial 注入） |
| `nanobot/agent/context.py` | `_load_bootstrap_files` 的 `SOUL.md`/`USER.md` 来源改为 identity 目录（保留 workspace 根回退，见 Task 2） |
| `webui/src/lib/api.ts` | 新增 `IDENTITY_BASE` 与 `identityListFiles` / `identityReadFile` / `identityWriteFile` / `identityReload` |
| `webui/src/components/settings/identity/IdentityView.tsx` | 删除 `IDENTITY_FILES` mock，改为真实拉取 |
| `webui/src/tests/api-identity.test.ts` | 新增 API 客户端单测 |

---

## Task 1: 身份目录与文件目录（唯一真相源）

**Files:**
- Create: `nanobot/identity/__init__.py`
- Create: `nanobot/identity/catalog.py`
- Test: `tests/identity/test_catalog.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/identity/test_catalog.py
from pathlib import Path

from nanobot.identity.catalog import (
    CHAR_LIMIT,
    CORE_FILES,
    IdentityFileSpec,
    IDENTITY_DIR_NAME,
    PERSONAS_SUBDIR,
    discover_personas,
    resolve_identity_dir,
)


def test_char_limit_matches_memory_lifecycle_constant():
    """前端 charMax 与后端截断常量必须是同一个数，禁止各自硬编码。"""
    from nanobot.memory.lifecycle import MEMORY_MD_MAX_CHARS

    assert CHAR_LIMIT == MEMORY_MD_MAX_CHARS


def test_core_files_match_frontend_contract():
    names = [spec.name for spec in CORE_FILES]
    assert names == [
        "SOUL.md",
        "AGENT.md",
        "USER.md",
        "MEMORY.md",
        "POLICIES.yaml",
        "prompts/policies.md",
    ]


def test_restricted_set_matches_frontend_contract():
    restricted = {spec.name for spec in CORE_FILES if spec.restricted}
    assert restricted == {"AGENT.md", "MEMORY.md", "POLICIES.yaml", "prompts/policies.md"}


def test_resolve_identity_dir_is_under_workspace(tmp_path: Path):
    assert resolve_identity_dir(tmp_path) == tmp_path / IDENTITY_DIR_NAME


def test_discover_personas_returns_sorted_markdown_only(tmp_path: Path):
    persona_dir = tmp_path / IDENTITY_DIR_NAME / PERSONAS_SUBDIR
    persona_dir.mkdir(parents=True)
    (persona_dir / "b.md").write_text("B", encoding="utf-8")
    (persona_dir / "a.md").write_text("A", encoding="utf-8")
    (persona_dir / "notes.txt").write_text("ignored", encoding="utf-8")

    assert [p.name for p in discover_personas(tmp_path)] == ["a.md", "b.md"]


def test_discover_personas_missing_dir_is_empty(tmp_path: Path):
    assert discover_personas(tmp_path) == []
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run --no-sync pytest tests/identity/test_catalog.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'nanobot.identity'`

- [ ] **Step 3: 实现**

```python
# nanobot/identity/__init__.py
"""身份文件目录：唯一真相源 + 读写。"""

from nanobot.identity.catalog import (
    CHAR_LIMIT,
    CORE_FILES,
    IDENTITY_DIR_NAME,
    PERSONAS_SUBDIR,
    IdentityFileSpec,
    discover_personas,
    resolve_identity_dir,
)
from nanobot.identity.store import IdentityStore, IdentityStoreError

__all__ = [
    "CHAR_LIMIT",
    "CORE_FILES",
    "IDENTITY_DIR_NAME",
    "PERSONAS_SUBDIR",
    "IdentityFileSpec",
    "IdentityStore",
    "IdentityStoreError",
    "discover_personas",
    "resolve_identity_dir",
]
```

```python
# nanobot/identity/catalog.py
"""身份文件的唯一真相源。

白名单、分组、受限标记、字符上限都只在这里定义一次；WebUI 后端与
前端常量都从这里派生，避免两处硬编码漂移。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from nanobot.memory.lifecycle import MEMORY_MD_MAX_CHARS

IDENTITY_DIR_NAME = "identity"
PERSONAS_SUBDIR = "personas"
PROMPTS_SUBDIR = "prompts"

# 前端 IdentityView 的 charMax 由此派生（经 /api/settings/identity/files 下发），
# 与 MEMORY.md 的落盘截断共用同一常量。
CHAR_LIMIT = MEMORY_MD_MAX_CHARS

# badge 的 labelKey 直接复用前端 i18n key，后端只负责透传。
BADGE_NEEDS_COMPILE = "settings.identity.badgeNeedsCompile"
BADGE_AUTO_REGEN = "settings.identity.badgeAutoRegen"
BADGE_SYSTEM_SECTION = "settings.identity.badgeSystemSection"
BADGE_FULL_TEXT_INJECT = "settings.identity.badgeFullTextInject"


@dataclass(frozen=True)
class IdentityFileSpec:
    """单个身份文件的静态描述。"""

    name: str
    group: str  # "core" | "personas"
    restricted: bool = False
    badge_tone: str | None = None  # "amber" | "sage" | "clay"
    badge_label_key: str | None = None
    # 相对 identity/ 的落盘路径；默认与 name 相同。
    # personas 的 name 是裸文件名，落盘在 personas/ 下。
    logical_path: str = ""

    @property
    def path(self) -> str:
        return self.logical_path or self.name


CORE_FILES: tuple[IdentityFileSpec, ...] = (
    IdentityFileSpec(name="SOUL.md", group="core"),
    IdentityFileSpec(
        name="AGENT.md",
        group="core",
        restricted=True,
        badge_tone="clay",
        badge_label_key=BADGE_NEEDS_COMPILE,
    ),
    IdentityFileSpec(name="USER.md", group="core"),
    IdentityFileSpec(
        name="MEMORY.md",
        group="core",
        restricted=True,
        badge_tone="amber",
        badge_label_key=BADGE_AUTO_REGEN,
    ),
    IdentityFileSpec(name="POLICIES.yaml", group="core", restricted=True),
    IdentityFileSpec(
        name="prompts/policies.md",
        group="core",
        restricted=True,
        badge_tone="amber",
        badge_label_key=BADGE_SYSTEM_SECTION,
    ),
)

# 全文注入人格：与 openakita 一致，这两个人格按全文注入而非摘要。
FULL_TEXT_PERSONAS: frozenset[str] = frozenset({"default.md", "tech_expert.md"})


def resolve_identity_dir(workspace: Path) -> Path:
    """返回 *workspace* 下的身份目录（不保证存在）。"""
    return workspace / IDENTITY_DIR_NAME


def discover_personas(workspace: Path) -> list[Path]:
    """返回身份目录下 personas/ 里全部 *.md，按文件名排序。

    目录不存在时返回空列表——不抛异常，因为「还没建 personas」是合法状态。
    """
    persona_dir = resolve_identity_dir(workspace) / PERSONAS_SUBDIR
    if not persona_dir.is_dir():
        return []
    return sorted(
        (p for p in persona_dir.iterdir() if p.is_file() and p.suffix == ".md"),
        key=lambda p: p.name,
    )


def build_persona_specs(workspace: Path) -> list[IdentityFileSpec]:
    """把 personas/ 下的真实文件转成 spec 列表。"""
    specs: list[IdentityFileSpec] = []
    for path in discover_personas(workspace):
        is_full_text = path.name in FULL_TEXT_PERSONAS
        specs.append(
            IdentityFileSpec(
                name=path.name,
                group="personas",
                logical_path=f"{PERSONAS_SUBDIR}/{path.name}",
                badge_tone="sage" if is_full_text else None,
                badge_label_key=BADGE_FULL_TEXT_INJECT if is_full_text else None,
            )
        )
    return specs
```

> **注意**：`field` 已在 import 中但未使用——实现时若用不到请从 import 行移除，保持 `ruff check` 干净。

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run --no-sync pytest tests/identity/test_catalog.py -v`
Expected: PASS（6 passed）

- [ ] **Step 5: 提交**

```bash
git add nanobot/identity/ tests/identity/test_catalog.py
git commit -m "feat(identity): 身份文件目录与白名单唯一真相源"
```

---

## Task 2: 老工作区迁移（坑 2 的解法）

**Files:**
- Create: `nanobot/identity/bootstrap.py`
- Test: `tests/identity/test_bootstrap.py`
- Modify: `nanobot/agent/context.py:309-333`

- [ ] **Step 1: 写失败测试**

```python
# tests/identity/test_bootstrap.py
from pathlib import Path

from nanobot.identity.bootstrap import migrate_legacy_identity_files
from nanobot.identity.catalog import IDENTITY_DIR_NAME


def test_migrates_existing_soul_and_user(tmp_path: Path):
    (tmp_path / "SOUL.md").write_text("# my soul", encoding="utf-8")
    (tmp_path / "USER.md").write_text("# my profile", encoding="utf-8")

    moved = migrate_legacy_identity_files(tmp_path)

    assert sorted(moved) == ["SOUL.md", "USER.md"]
    identity_dir = tmp_path / IDENTITY_DIR_NAME
    assert (identity_dir / "SOUL.md").read_text(encoding="utf-8") == "# my soul"
    assert (identity_dir / "USER.md").read_text(encoding="utf-8") == "# my profile"
    # 老文件必须被移走，不能留下两份互相打架的副本
    assert not (tmp_path / "SOUL.md").exists()
    assert not (tmp_path / "USER.md").exists()


def test_does_not_clobber_existing_identity_copy(tmp_path: Path):
    identity_dir = tmp_path / IDENTITY_DIR_NAME
    identity_dir.mkdir()
    (identity_dir / "SOUL.md").write_text("# new", encoding="utf-8")
    (tmp_path / "SOUL.md").write_text("# old", encoding="utf-8")

    moved = migrate_legacy_identity_files(tmp_path)

    assert moved == []
    assert (identity_dir / "SOUL.md").read_text(encoding="utf-8") == "# new"
    # 老文件保留原处，交给用户手动处理——绝不静默覆盖
    assert (tmp_path / "SOUL.md").read_text(encoding="utf-8") == "# old"


def test_missing_legacy_files_is_noop(tmp_path: Path):
    assert migrate_legacy_identity_files(tmp_path) == []
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run --no-sync pytest tests/identity/test_bootstrap.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'nanobot.identity.bootstrap'`

- [ ] **Step 3: 实现**

```python
# nanobot/identity/bootstrap.py
"""身份目录的初始化与老工作区迁移。"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from nanobot.identity.catalog import IDENTITY_DIR_NAME, resolve_identity_dir

logger = logging.getLogger(__name__)

# 历史上散落在 workspace 根、现在应收归 identity/ 的文件。
LEGACY_ROOT_FILES: tuple[str, ...] = ("SOUL.md", "USER.md")


def migrate_legacy_identity_files(workspace: Path) -> list[str]:
    """把 workspace 根的 SOUL.md / USER.md 移入 identity/。

    返回实际迁移的文件名列表。

    冲突策略：identity/ 下**已存在**同名文件时**不迁移也不覆盖**——
    老文件原地保留并记 WARNING。静默覆盖会丢用户的编辑，宁可让用户自己看一眼。
    """
    identity_dir = resolve_identity_dir(workspace)
    moved: list[str] = []

    for filename in LEGACY_ROOT_FILES:
        legacy = workspace / filename
        if not legacy.is_file():
            continue
        target = identity_dir / filename
        if target.exists():
            logger.warning(
                "identity/%s 已存在，跳过迁移 workspace/%s；请手动确认是否合并",
                filename,
                filename,
            )
            continue
        identity_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(legacy), str(target))
        moved.append(filename)
        logger.info("已迁移 %s → %s/%s", filename, IDENTITY_DIR_NAME, filename)

    return moved
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run --no-sync pytest tests/identity/test_bootstrap.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: 改 `context.py` 的加载来源（保留回退）**

```python
# nanobot/agent/context.py:309-333  _load_bootstrap_files 内
        parts: list[str] = []
        project_root = workspace or self.workspace
        identity_dir = resolve_identity_dir(self.workspace)
        sources = [
            ("AGENTS.md", project_root),          # 项目级指令：保持不动
            # 身份文件优先读 identity/，回退 workspace 根以兼容未迁移的旧工作区
            ("SOUL.md", identity_dir if (identity_dir / "SOUL.md").exists() else self.workspace),
            ("USER.md", identity_dir if (identity_dir / "USER.md").exists() else self.workspace),
        ]
```

在文件顶部 import：

```python
from nanobot.identity.catalog import resolve_identity_dir
```

- [ ] **Step 6: 回归既有 context 测试**

Run: `uv run --no-sync pytest tests/ -k "context" -v`
Expected: 全 PASS（若有既有用例直接构造 workspace 根 SOUL.md，回退分支会兜住）

- [ ] **Step 7: 提交**

```bash
git add nanobot/identity/bootstrap.py tests/identity/test_bootstrap.py nanobot/agent/context.py
git commit -m "feat(identity): 老工作区 SOUL/USER 迁移与加载来源回退"
```

---

## Task 3: IdentityStore 读写与路径穿越防护

**Files:**
- Create: `nanobot/identity/store.py`
- Test: `tests/identity/test_store.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/identity/test_store.py
from pathlib import Path

import pytest

from nanobot.identity.catalog import IDENTITY_DIR_NAME
from nanobot.identity.store import IdentityStore, IdentityStoreError


@pytest.fixture()
def store(tmp_path: Path) -> IdentityStore:
    identity_dir = tmp_path / IDENTITY_DIR_NAME
    (identity_dir / "prompts").mkdir(parents=True)
    (identity_dir / "personas").mkdir()
    (identity_dir / "SOUL.md").write_text("# soul", encoding="utf-8")
    (identity_dir / "prompts" / "policies.md").write_text("# policies", encoding="utf-8")
    (identity_dir / "personas" / "default.md").write_text("# default", encoding="utf-8")
    return IdentityStore(tmp_path)


@pytest.mark.parametrize(
    "evil",
    ["../../etc/passwd", "..\\..\\windows\\system32", "/etc/passwd", "prompts/../../x.md"],
)
def test_rejects_path_traversal(store: IdentityStore, evil: str):
    with pytest.raises(IdentityStoreError):
        store.read_file(evil)


def test_rejects_non_whitelisted_name(store: IdentityStore):
    with pytest.raises(IdentityStoreError):
        store.write_file("secret.env", "x")


def test_list_files_groups_core_and_personas(store: IdentityStore):
    files = store.list_files()
    core = [f for f in files if f["group"] == "core"]
    personas = [f for f in files if f["group"] == "personas"]
    assert [f["name"] for f in core][0] == "SOUL.md"
    assert [f["name"] for f in personas] == ["default.md"]
    assert all(f["exists"] for f in files)


def test_list_files_marks_missing(store: IdentityStore):
    by_name = {f["name"]: f for f in store.list_files()}
    assert by_name["AGENT.md"]["exists"] is False
    assert by_name["AGENT.md"]["restricted"] is True


def test_read_write_roundtrip(store: IdentityStore):
    store.write_file("SOUL.md", "# updated")
    assert store.read_file("SOUL.md") == "# updated"


def test_write_rejects_over_char_limit(store: IdentityStore):
    with pytest.raises(IdentityStoreError) as exc:
        store.write_file("SOUL.md", "x" * 100_000)
    assert "1500" in str(exc.value) or "limit" in str(exc.value).lower()


def test_write_rejects_invalid_yaml(store: IdentityStore):
    with pytest.raises(IdentityStoreError):
        store.write_file("POLICIES.yaml", "tool_policies: [this is: not valid")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run --no-sync pytest tests/identity/test_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'nanobot.identity.store'`

- [ ] **Step 3: 实现**

```python
# nanobot/identity/store.py
"""身份文件读写。所有路径都必须落在 identity/ 目录内。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from nanobot.identity.catalog import (
    CHAR_LIMIT,
    CORE_FILES,
    IdentityFileSpec,
    build_persona_specs,
    resolve_identity_dir,
)


class IdentityStoreError(ValueError):
    """身份文件操作失败。message 直接面向调用方（会被转成 4xx）。"""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


class IdentityStore:
    """workspace 级身份文件读写。

    实例无状态缓存：每次读盘，保证编辑后立刻可见（与 openakita 的
    「注入侧读磁盘文件」语义一致）。
    """

    def __init__(self, workspace: Path) -> None:
        self.workspace = Path(workspace)
        self.identity_dir = resolve_identity_dir(self.workspace)

    # -- 白名单 ------------------------------------------------------------

    def _specs(self) -> list[IdentityFileSpec]:
        return [*CORE_FILES, *build_persona_specs(self.workspace)]

    def _spec_for(self, name: str) -> IdentityFileSpec | None:
        for spec in self._specs():
            if spec.name == name:
                return spec
        return None

    def _resolve(self, name: str) -> Path:
        """把逻辑名解析为绝对路径，并强制其位于 identity/ 内。

        这是唯一的安全边界：先查白名单，再用 resolve() 后做前缀比对。
        任何越界（路径穿越、绝对路径、非白名单文件）都在这里拦下。
        """
        spec = self._spec_for(name)
        if spec is None:
            raise IdentityStoreError(f"不是可编辑的身份文件：{name}", status=403)

        candidate = (self.identity_dir / spec.path).resolve()
        root = self.identity_dir.resolve()
        # Path.is_relative_to 自 Python 3.9 起可用；项目要求 >=3.11。
        if candidate != root and not candidate.is_relative_to(root):
            raise IdentityStoreError("Path traversal not allowed", status=400)
        return candidate

    # -- 读 ------------------------------------------------------------------

    def list_files(self) -> list[dict[str, Any]]:
        """返回前端所需的文件清单（含 exists / restricted / badge）。"""
        items: list[dict[str, Any]] = []
        for spec in self._specs():
            path = (self.identity_dir / spec.path)
            item: dict[str, Any] = {
                "name": spec.name,
                "group": spec.group,
                "exists": path.is_file(),
                "restricted": spec.restricted,
                "charLimit": CHAR_LIMIT,
            }
            if spec.group == "personas":
                item["logicalPath"] = spec.path
            if spec.badge_tone and spec.badge_label_key:
                item["badge"] = {"tone": spec.badge_tone, "labelKey": spec.badge_label_key}
            items.append(item)
        return items

    def read_file(self, name: str) -> str:
        path = self._resolve(name)
        if not path.is_file():
            raise IdentityStoreError(f"文件不存在：{name}", status=404)
        return path.read_text(encoding="utf-8")

    # -- 写 ------------------------------------------------------------------

    def write_file(self, name: str, content: str) -> None:
        """校验后写入。空内容与超限内容都拒绝——避免误删。"""
        path = self._resolve(name)

        if len(content) > CHAR_LIMIT:
            raise IdentityStoreError(
                f"内容超过 {CHAR_LIMIT} 字符上限（当前 {len(content)}）",
                status=400,
            )

        if name.endswith(".yaml"):
            self._validate_yaml(content, name)

        path.parent.mkdir(parents=True, exist_ok=True)
        # MEMORY.md 必须走 MemoryLifecycle 的备份语义，见 Task 4。
        if name == "MEMORY.md":
            raise IdentityStoreError(
                "MEMORY.md 必须经 /api/settings/identity/file 的专用分支写入",
                status=500,
            )
        path.write_text(content, encoding="utf-8")

    @staticmethod
    def _validate_yaml(content: str, name: str) -> None:
        import yaml

        try:
            parsed = yaml.safe_load(content)
        except yaml.YAMLError as exc:
            raise IdentityStoreError(f"{name} 不是合法 YAML：{exc}", status=400) from exc
        if parsed is None:
            raise IdentityStoreError(f"{name} 解析结果为空", status=400)
        if not isinstance(parsed, dict):
            raise IdentityStoreError(f"{name} 顶层必须是映射", status=400)
```

> **`MEMORY.md` 的写入路径**故意在此处 `raise`，由 Task 4 的 API 层分流到 `MemoryLifecycle`。这样"谁能写 MEMORY.md"只有一个入口，不会被绕过。

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run --no-sync pytest tests/identity/test_store.py -v`
Expected: PASS（12 passed）

- [ ] **Step 5: 提交**

```bash
git add nanobot/identity/store.py tests/identity/test_store.py
git commit -m "feat(identity): IdentityStore 读写与路径穿越防护"
```

---

## Task 4: WebUI API 层（含 MEMORY.md 专用写入分支）

**Files:**
- Create: `nanobot/webui/identity_api.py`
- Create: `nanobot/webui/identity_routes.py`
- Test: `tests/webui/test_identity_routes.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/webui/test_identity_routes.py
from pathlib import Path

from nanobot.identity.catalog import IDENTITY_DIR_NAME
from nanobot.webui.identity_api import identity_list_files, identity_read_file, identity_write_file
from nanobot.webui.settings_contracts import SettingsRequest, QueryParams


def _request(**payload):
    return SettingsRequest(query=QueryParams.from_mapping({}), payload=payload, local_browser=True)


def test_list_files_payload_shape(tmp_path: Path):
    (tmp_path / IDENTITY_DIR_NAME).mkdir()
    (tmp_path / IDENTITY_DIR_NAME / "SOUL.md").write_text("# s", encoding="utf-8")

    result = identity_list_files(tmp_path)

    assert set(result.keys()) == {"files", "charLimit"}
    assert result["charLimit"] == 1500
    assert result["files"][0]["name"] == "SOUL.md"


def test_read_returns_content(tmp_path: Path):
    (tmp_path / IDENTITY_DIR_NAME).mkdir()
    (tmp_path / IDENTITY_DIR_NAME / "SOUL.md").write_text("# s", encoding="utf-8")

    result = identity_read_file(tmp_path, "SOUL.md")

    assert result == {"name": "SOUL.md", "content": "# s"}


def test_write_rejects_traversal(tmp_path: Path):
    from nanobot.webui.settings_contracts import WebUISettingsError

    (tmp_path / IDENTITY_DIR_NAME).mkdir()
    try:
        identity_write_file(tmp_path, "../../evil.md", "x")
    except WebUISettingsError as exc:
        assert exc.status in (400, 403)
    else:
        raise AssertionError("应当拒绝路径穿越")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run --no-sync pytest tests/webui/test_identity_routes.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'nanobot.webui.identity_api'`

- [ ] **Step 3: 实现 API 层**

```python
# nanobot/webui/identity_api.py
"""身份文件的 WebUI 操作实现。

薄壳：把 IdentityStore / MemoryLifecycle 的异常翻译成 WebUISettingsError，
编排层不感知领域细节（与 memory_api.py 同构）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from nanobot.identity.store import IdentityStore, IdentityStoreError
from nanobot.webui.settings_contracts import WebUISettingsError


def liveness(*_args: Any, **_kwargs: Any) -> None:
    """用于把 files 清单里的 charLimit 作为单一来源下发。"""


def _store(workspace: Path) -> IdentityStore:
    return IdentityStore(workspace)


def _wrap(exc: IdentityStoreError) -> WebUISettingsError:
    return WebUISettingsError(exc.message, status=exc.status)


def identity_list_files(workspace: Path) -> dict[str, Any]:
    store = _store(workspace)
    files = store.list_files()
    char_limit = files[0]["charLimit"] if files else _char_limit()
    return {"files": files, "charLimit": char_limit}


def _char_limit() -> int:
    from nanobot.identity.catalog import CHAR_LIMIT

    return CHAR_LIMIT


def identity_read_file(workspace: Path, name: str) -> dict[str, Any]:
    try:
        content = _store(workspace).read_file(name)
    except IdentityStoreError as exc:
        raise _wrap(exc) from exc
    return {"name": name, "content": content}


def identity_write_file(workspace: Path, name: str, content: str) -> dict[str, Any]:
    """写身份文件。

    MEMORY.md 是特例：它是 Dream 流程的派生产物，必须复用
    MemoryLifecycle 的备份语义，不能走裸写。
    """
    if name == "MEMORY.md":
        return _write_memory_md(workspace, content)

    try:
        _store(workspace).write_file(name, content)
    except IdentityStoreError as exc:
        raise _wrap(exc) from exc
    return {"name": name, "saved": True}


def _write_memory_md(workspace: Path, content: str) -> dict[str, Any]:
    """复用 MemoryLifecycle 的 _safe_write_with_backup，绝不裸写。"""
    from nanobot.identity.catalog import CHAR_LIMIT

    if len(content) > CHAR_LIMIT:
        raise WebUISettingsError(
            f"内容超过 {CHAR_LIMIT} 字符上限（当前 {len(content)}）",
            status=400,
        )

    # MemoryLifecycle 是 per-workspace 单例，需要 MemoryServices；
    # 调用方（gateway_services）通过 partial 预先绑好 services。
    raise WebUISettingsError("未注入 MemoryServices，见 Task 4 Step 4", status=500)
```

> ⚠️ **Step 3 的 `_write_memory_md` 故意留了未完成的分支**——`MemoryLifecycle` 需要 `MemoryServices`，而 `identity_api` 是纯函数模块。**Step 4 会把 services 通过 `partial` 注入**（与 `gateway_services.py:83` 注入 `refresh_memory_md` 同一手法）。这是本计划唯一允许"先留桩后接"的地方，且必须在 Step 4 一次性接完。

- [ ] **Step 4: 用 partial 注入 services，接完 MEMORY.md 分支**

```python
# nanobot/webui/identity_api.py  替换 _write_memory_md 与 identity_write_file
def identity_write_file(
    workspace: Path, name: str, content: str, *, lifecycle: Any | None = None
) -> dict[str, Any]:
    if name == "MEMORY.md":
        if lifecycle is None:
            raise WebUISettingsError("MemoryLifecycle 未注入", status=500)
        from nanobot.identity.catalog import CHAR_LIMIT

        if len(content) > CHAR_LIMIT:
            raise WebUISettingsError(
                f"内容超过 {CHAR_LIMIT} 字符上限（当前 {len(content)}）",
                status=400,
            )
        lifecycle._safe_write_with_backup(lifecycle.memory_file, content)
        return {"name": name, "saved": True, "autoRegenerated": True}

    try:
        _store(workspace).write_file(name, content)
    except IdentityStoreError as exc:
        raise _wrap(exc) from exc
    return {"name": name, "saved": True}
```

在 `gateway_services.py` 中按既有 memory 手法绑定：

```python
# nanobot/webui/gateway_services.py（在 refresh_memory_md 注入附近）
        write_identity_file=partial(
            identity_api.identity_write_file,
            lifecycle=MemoryLifecycle.for_workspace(workspace_id, services),
        ),
```

- [ ] **Step 5: 写 routes 层**

```python
# nanobot/webui/identity_routes.py
"""身份设置的领域 handler（仿 memory_routes.py）。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

from nanobot.webui.settings_contracts import (
    SettingsRequest,
    SettingsRouteResult,
    WebUISettingsError,
)

logger = logging.getLogger(__name__)

IDENTITY_ACTION_NAMES: frozenset[str] = frozenset(
    {
        "identity-list-files",
        "identity-read-file",
        "identity-write-file",
        "identity-reload",
        "identity-compile",
    }
)


@dataclass(frozen=True)
class IdentitySettingsOperations:
    list_files: Callable[..., dict[str, Any]]
    read_file: Callable[..., dict[str, Any]]
    write_file: Callable[..., dict[str, Any]]
    reload: Callable[..., dict[str, Any]]
    compile: Callable[..., dict[str, Any]]


def _first(payload: dict[str, Any] | None, key: str) -> str:
    value = (payload or {}).get(key)
    return value if isinstance(value, str) else ""


class IdentitySettingsHandler:
    """把 identity-* action 分派到注入的 operations。"""

    def __init__(self, operations: IdentitySettingsOperations) -> None:
        self._ops = operations

    def handle(self, action: str, request: SettingsRequest) -> SettingsRouteResult:
        if action not in IDENTITY_ACTION_NAMES:
            return SettingsRouteResult.failure(404, f"unknown identity action: {action}")
        try:
            if action == "identity-list-files":
                payload = self._ops.list_files()
            elif action == "identity-read-file":
                payload = self._ops.read_file(_first(request.payload, "name"))
            elif action == "identity-write-file":
                payload = self._ops.write_file(
                    _first(request.payload, "name"),
                    _first(request.payload, "content"),
                )
            elif action == "identity-reload":
                payload = self._ops.reload()
            else:
                payload = self._ops.compile(_first(request.payload, "mode"))
        except WebUISettingsError as exc:
            return SettingsRouteResult.failure(exc.status, exc.message)
        return SettingsRouteResult.success(payload)
```

- [ ] **Step 6: 运行测试确认通过**

Run: `uv run --no-sync pytest tests/webui/test_identity_routes.py -v`
Expected: PASS

- [ ] **Step 7: 提交**

```bash
git add nanobot/webui/identity_api.py nanobot/webui/identity_routes.py tests/webui/test_identity_routes.py
git commit -m "feat(identity): WebUI API 与领域 handler"
```

---

## Task 5: 注册路由

**Files:**
- Modify: `nanobot/webui/settings_routes.py:148-217`
- Modify: `nanobot/webui/ws_http.py`（`__init__` 注入 + dispatch 分支）

- [ ] **Step 1: 加路由表与 mutation 白名单**

```python
# nanobot/webui/settings_routes.py  _SYSTEM_ROUTES 内，memory 段之后
    "/api/settings/identity/files": "identity-list-files",
    "/api/settings/identity/file": "identity-read-file",
    "/api/settings/identity/file/save": "identity-write-file",
    "/api/settings/identity/reload": "identity-reload",
    "/api/settings/identity/compile": "identity-compile",
```

```python
# nanobot/webui/settings_routes.py  _SETTINGS_MUTATION_PATHS 内
    "/api/settings/identity/file/save",
    "/api/settings/identity/reload",
    "/api/settings/identity/compile",
```

```python
# nanobot/webui/ws_http.py  _WEBUI_MUTATION_PATHS 内（该表在 140-198 行，memory 段之后）
    "identity.file.save": "/api/settings/identity/file/save",
    "identity.reload": "/api/settings/identity/reload",
```

> ⚠️ **实施时发现的关键事实（2026-09-18 核验）**：前端写操作**不走 HTTP POST**，而走 WebSocket 复用协议的 `requestMutation(action, payload, timeoutMs)`（`webui/src/lib/nanobot-client.ts:880`，发出 `{type:"webui_request", action, payload}` 帧）。服务端 `GatewayHTTPHandler._webui_mutation_path`（`ws_http.py:486-508`）做 **WS action 名 → HTTP 路径** 的翻译：
>
> ```python
> path = _WEBUI_MUTATION_PATHS.get(action)
> if path is not None: return path
> ... session.delete / channel connect 特例 ...
> return _http_error(404, "unknown WebUI mutation action")   # ← 漏登记 = 静默 404
> ```
>
> **漏登记这个表 = 前端保存/重载拿不到任何反馈**。前端已按此约定产出（WU-05 commit `e7d92e7`：`saveIdentityFile` 用 `"identity.file.save"`、`reloadIdentity` 用 `"identity.reload"`），后端必须逐字对齐，**命名不得改动**。
>
> **同类现成反例（既有 bug，勿照抄）**：`MemoryMdCard.tsx:80` 调 `client.requestMutation("memory-refresh-md", {}, 30_000)`、`MemoryMdCard.tsx:61` 调 `"memory-stats"`——这两个 action **都不在** `_WEBUI_MUTATION_PATHS` 中（该表只有点号命名如 `memory.create`），因此必然 404。之所以无人发现，是因为两处调用都包在空的 `catch {}` 里（"non-critical"）。**WU-06 接线时禁止照抄这种空 catch——保存失败必须让用户看见。**
>
> ⚠️ 另一处偏离：openakita 用 `GET /file?name=` + `PUT /file` 同一路径；nanobot 的路由表是**路径→action 的扁平映射**，同路径不同方法无法区分。故拆成 `/file`（读）与 `/file/save`（写）。**这是与 openakita 有意为之的偏离**，需在注释中写明，避免后人误改回同路径。

- [ ] **Step 2: 写失败测试**

```python
# tests/webui/test_identity_routes.py 追加
def test_identity_paths_registered():
    from nanobot.webui.settings_routes import _SETTINGS_MUTATION_PATHS, _SYSTEM_ROUTES

    assert _SYSTEM_ROUTES["/api/settings/identity/files"] == "identity-list-files"
    assert "/api/settings/identity/file/save" in _SETTINGS_MUTATION_PATHS
```

- [ ] **Step 3: 运行确认失败**

Run: `uv run --no-sync pytest tests/webui/test_identity_routes.py::test_identity_paths_registered -v`
Expected: FAIL — `KeyError: '/api/settings/identity/files'`

- [ ] **Step 4: dispatch 分支**

```python
# nanobot/webui/settings_routes.py  dispatch 内，memory 分支之后
        elif action in identity_routes.IDENTITY_ACTION_NAMES:
            result = await asyncio.to_thread(
                lambda: self._identity.handle(action, domain_request),
            )
```

- [ ] **Step 5: 运行确认通过**

Run: `uv run --no-sync pytest tests/webui/test_identity_routes.py -v`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add nanobot/webui/settings_routes.py tests/webui/test_identity_routes.py
git commit -m "feat(identity): 注册身份设置路由"
```

---

## Task 6: 重载与编译占位

**Files:**
- Modify: `nanobot/webui/identity_api.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/webui/test_identity_routes.py 追加
def test_reload_reports_result():
    from nanobot.webui.identity_api import identity_reload

    # 不依赖真实 workspace：只断言结构
    result = identity_reload()
    assert result["status"] in {"ok", "skipped"}


def test_compile_reports_not_enabled():
    from nanobot.webui.identity_api import identity_compile

    result = identity_compile("llm")
    assert result["status"] == "not_enabled"
    assert result["mode"] == "llm"
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run --no-sync pytest tests/webui/test_identity_routes.py -k "reload or compile" -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'identity_reload'`

- [ ] **Step 3: 实现**

```python
# nanobot/webui/identity_api.py 追加
def identity_reload(*, refresh_memory_md: Any | None = None) -> dict[str, Any]:
    """重载：强制重新派生 MEMORY.md，使编辑后立刻对注入侧可见。

    复用既有 MemoryLifecycle 链路（gateway_services 已注入 refresh_memory_md），
    不新建失效逻辑。
    """
    if refresh_memory_md is None:
        return {"status": "skipped", "reason": "no_services"}
    try:
        return dict(refresh_memory_md() or {})
    except Exception:  # noqa: BLE001 - 重载失败不应让设置页 500
        logger.exception("identity reload failed")
        return {"status": "error"}


def identity_compile(mode: str) -> dict[str, Any]:
    """编译能力占位。

    nanobot 尚无 PromptCompiler；按用户决策（分阶段）返回明确状态，
    让前端能显示「未启用」而不是静默失败。端点形状与 openakita 保持一致，
    后续接入编译器时前端无需改动。
    """
    return {"status": "not_enabled", "mode": mode or "rules"}
```

在顶部补 `import logging` 与 `logger = logging.getLogger(__name__)`。

- [ ] **Step 4: 运行确认通过**

Run: `uv run --no-sync pytest tests/webui/test_identity_routes.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add nanobot/webui/identity_api.py tests/webui/test_identity_routes.py
git commit -m "feat(identity): 重载复用既有链路，编译返回明确未启用状态"
```

---

## Task 7: 前端 API 客户端

**Files:**
- Modify: `webui/src/lib/api.ts:1089` 附近
- Test: `webui/src/tests/api-identity.test.ts`

- [ ] **Step 1: 写失败测试**

```ts
// webui/src/tests/api-identity.test.ts
import { describe, expect, it, vi, beforeEach } from "vitest";

import { identityListFiles, identityReadFile, identityReload, identityWriteFile } from "@/lib/api";

describe("identity api", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("lists files", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ files: [], charLimit: 1500 }), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await identityListFiles();

    expect(fetchMock.mock.calls[0][0]).toBe("/api/settings/identity/files");
  });

  it("reads a file with an encoded name", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ name: "prompts/policies.md", content: "" }), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await identityReadFile("prompts/policies.md");

    expect(fetchMock.mock.calls[0][0]).toBe(
      "/api/settings/identity/file?name=prompts%2Fpolicies.md",
    );
  });

  it("writes a file", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ saved: true }), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await identityWriteFile("SOUL.md", "# s");

    expect(fetchMock.mock.calls[0][0]).toBe("/api/settings/identity/file/save");
  });

  it("reloads", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ status: "ok" }), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await identityReload();

    expect(fetchMock.mock.calls[0][0]).toBe("/api/settings/identity/reload");
  });
});
```

- [ ] **Step 2: 运行确认失败**

Run: `cd webui && npx vitest run src/tests/api-identity.test.ts`
Expected: FAIL — `identityListFiles is not a function`

- [ ] **Step 3: 实现（照抄 `api.ts` 中 memory 客户端的既有写法）**

```ts
// webui/src/lib/api.ts  MEMORY_BASE 附近
const IDENTITY_BASE = "/api/settings/identity";

export type IdentityFileBadge = { tone: "amber" | "sage" | "clay"; labelKey: string };

export type IdentityFileEntry = {
  name: string;
  group: "core" | "personas";
  logicalPath?: string;
  exists: boolean;
  restricted: boolean;
  badge?: IdentityFileBadge;
};

export type IdentityFilesPayload = { files: IdentityFileEntry[]; charLimit: number };

export function identityListFiles(): Promise<IdentityFilesPayload> {
  return requestJson<IdentityFilesPayload>(`${IDENTITY_BASE}/files`);
}

export function identityReadFile(name: string): Promise<{ name: string; content: string }> {
  return requestJson(`${IDENTITY_BASE}/file?name=${encodeURIComponent(name)}`);
}

export function identityWriteFile(name: string, content: string): Promise<{ saved: boolean }> {
  return postJson(`${IDENTITY_BASE}/file/save`, { name, content });
}

export function identityReload(): Promise<Record<string, unknown>> {
  return postJson(`${IDENTITY_BASE}/reload`, {});
}
```

> `requestJson` / `postJson` 用文件里**已有**的私有 helper——实现时先搜 `api.ts` 里 memory 客户端用的是哪两个函数名，按同名调用，不要新造。

- [ ] **Step 4: 运行确认通过**

Run: `cd webui && npx vitest run src/tests/api-identity.test.ts`
Expected: PASS（4 passed）

- [ ] **Step 5: 提交**

```bash
git add webui/src/lib/api.ts webui/src/tests/api-identity.test.ts
git commit -m "feat(identity): 前端 API 客户端"
```

---

## Task 8: IdentityView 去除 mock 并接线

**Files:**
- Modify: `webui/src/components/settings/identity/IdentityView.tsx:22-240`（删除 mock）、`:365-403`（状态与 handler）

- [ ] **Step 1: 删除 mock**

删掉 `IDENTITY_FILES`（`IdentityView.tsx:40-240`）与 `IdentityFile` 本地类型（`:30-38`），改为 import：

```ts
import {
  listIdentityFiles,
  fetchIdentityFile,
  saveIdentityFile,
  reloadIdentity,
  type WebUIMutationTransport,
} from "@/lib/api";
import type { IdentityFileEntry } from "@/lib/types";
```

> ⚠️ **函数名与签名以 WU-05 已提交的产物为准**（commit `e7d92e7`），不是本计划初稿的 `identityListFiles` 等命名。实际签名：
> - `listIdentityFiles(token: string, base?: string): Promise<{files, charLimit}>` — HTTP GET
> - `fetchIdentityFile(token: string, name: string, base?: string): Promise<{name, content}>` — HTTP GET
> - `saveIdentityFile(transport: WebUIMutationTransport, input: {name, content}): Promise<{saved}>` — **WS mutation**
> - `reloadIdentity(transport: WebUIMutationTransport): Promise<{status}>` — **WS mutation**
>
> 因此 `IdentityView` 需要拿到 `token` 与 `transport` 两个参数（当前组件无 props，Task 8 Step 0 先补）。

- [ ] **Step 2: 替换状态与数据源**

```ts
  const [files, setFiles] = useState<IdentityFileEntry[]>([]);
  const [charLimit, setCharLimit] = useState(1500);
  const [loading, setLoading] = useState(true);
  const [selectedName, setSelectedName] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [savingState, setSavingState] = useState<"idle" | "saving" | "saved">("idle");
  const [compactDetailOpen, setCompactDetailOpen] = useState(false);

  useEffect(() => {
    let cancelled = false;
    void identityListFiles()
      .then((payload) => {
        if (cancelled) return;
        setFiles(payload.files);
        setCharLimit(payload.charLimit);
        setSelectedName((prev) => prev ?? payload.files[0]?.name ?? null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!selectedName) return;
    let cancelled = false;
    void identityReadFile(selectedName).then((payload) => {
      if (!cancelled) setDraft(payload.content);
    });
    return () => {
      cancelled = true;
    };
  }, [selectedName]);
```

- [ ] **Step 3: 替换 `charMax` 与 handler**

```ts
  const charMax = charLimit;          // 不再硬编码 1500
  const overLimit = charCount > charMax;

  function handleSelect(file: IdentityFileEntry) {
    setSelectedName(file.name);
    setSavingState("idle");
    if (!splitLayout) setCompactDetailOpen(true);
  }

  async function handleSave() {
    if (!selectedName || overLimit) return;
    setSavingState("saving");
    try {
      await identityWriteFile(selectedName, draft);
      setSavingState("saved");
    } catch {
      setSavingState("idle");
    }
  }

  async function handleReload() {
    await identityReload();
    if (selectedName) setDraft((await identityReadFile(selectedName)).content);
  }
```

- [ ] **Step 4: 补状态渲染**

- `loading` 为真时渲染 `Loader2` 占位（沿用 `ChannelsSettings.tsx:204-208` 的写法）。
- 工具栏「重载」按钮 `onClick={handleReload}`（`IdentityView.tsx:504`）。
- 「LM 优化」「规则编译」按钮接 `identityCompile`，返回 `not_enabled` 时用 `toast`/内联提示显示「编译能力未启用」——**不要静默**。
- 保存按钮 `disabled={savingState === "saving" || overLimit || selected?.restricted === undefined}`。

- [ ] **Step 5: 加 i18n key**

在 `webui/src/i18n/locales/zh-CN/common.json` 与 `en/common.json` 的 `settings.identity.*` 下补：

```
"compileNotEnabled": "编译能力尚未启用" / "Compile is not available yet"
"loadFailed": "加载身份文件失败" / "Failed to load identity files"
```

- [ ] **Step 6: 构建验证**

Run: `cd webui && npx vite build`
Expected: `✓ built in` 且无 TS 报错

Run: `cd webui && npx vitest run`
Expected: 全 PASS（既有 `settings-memory-section` 等用例不受影响）

- [ ] **Step 7: 提交**

```bash
git add webui/src/components/settings/identity/IdentityView.tsx webui/src/i18n/locales/zh-CN/common.json webui/src/i18n/locales/en/common.json
git commit -m "feat(identity): IdentityView 接真实后端，charMax 由后端下发"
```

---

## Task 9: 整体回归

- [ ] **Step 1: 后端全量**

Run: `uv run --no-sync pytest tests/ -x -q`
Expected: 全 PASS

- [ ] **Step 2: 类型检查（CI 口径）**

Run: `uv run --no-sync basedpyright`
Expected: 无新增错误

- [ ] **Step 3: Lint**

Run: `ruff check nanobot/`
Expected: `All checks passed!`

- [ ] **Step 4: 前端**

Run: `cd webui && npx vite build && npx vitest run`
Expected: 构建成功 + 全 PASS

- [ ] **Step 5: 手工冒烟**

启动 gateway + dev server，进入设置 → 身份：
1. 左侧列出 core 6 项 + personas（首次运行为空）
2. 点 `SOUL.md` 能读到真实内容
3. 编辑并保存 → 刷新后内容保留
4. 输入超过 1500 字符 → 保存按钮禁用、字数标红
5. 「重载」不报错
6. 「LM 优化」显示「编译能力尚未启用」

---

## 自检记录

**1. Spec 覆盖**：本计划无独立 spec（用户直接要求写计划）。设计依据为两份 research 产物，已写入 FM `source`。范围外项已在开头显式列出。

**2. 占位符扫描**：Task 4 Step 3 的 `_write_memory_md` 是**唯一的临时桩**，且已在紧邻的 Step 4 给出完整替换代码并说明理由，不属于"TBD"式占位。其余各步均含可直接粘贴的实现。

**3. 类型一致性**：
- `CHAR_LIMIT`（后端）↔ `charLimit`（API 字段）↔ `charMax`（前端变量），命名有意区分层次，Task 1/4/8 一致。
- `IdentityFileSpec.name` 是**逻辑名**（personas 用裸文件名），`spec.path` 是**落盘相对路径**；`store._resolve` 统一用 `spec.path`，`list_files` 对 personas 额外下发 `logicalPath`。Task 1/3 一致。
- `IdentityFileEntry`（前端）字段与 `list_files` 返回键一一对应（`name`/`group`/`logicalPath`/`exists`/`restricted`/`badge`）。Task 3/7/8 一致。

**4. 已知风险**：
- Task 2 的迁移是**破坏性动作**（`shutil.move`）。已在测试中锁定"冲突不覆盖"行为。若用户工作区已有 identity/SOUL.md，迁移会跳过并记 WARNING——需要人看一眼。
- Task 5 的路由拆分为 `/file` + `/file/save`，与 openakita 同路径不同方法的设计**有意偏离**，原因是 nanobot 路由表不支持按方法区分。已在计划中标注。

---

## Next

**（写入后须暂停 — 即使用户句末含「然后执行」）**

- 计划确认 → 说「开始实现」或「执行」
- 需要调整 → 直接说修改意见
- 想拆分并行 → 审 `2026-09-18-identity-backend-dispatch.md` 后说「开始实现」或「并行执行」
