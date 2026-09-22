"""规则编译器的单测：取舍规则、字符预算、新鲜度判据。

编译是注入路径的上游，所以这里既测纯函数（取舍规则可单独推理），也测落盘与
过期（它们决定注入侧到底读产物还是读全文）。
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from nanobot.identity.catalog import COMPILED_SCHEMA_VERSION, IDENTITY_DIR_NAME
from nanobot.identity.compiler import (
    COMPILE_TARGETS,
    COMPILED_AT_FILENAME,
    COMPILER_VERSION_FILENAME,
    MAX_LINE_CHARS,
    CompileTarget,
    compile_content,
    compile_identity,
    compiled_status,
    read_compiled,
    runtime_dir,
)

SOUL_SOURCE = "# Soul\n\n## 核心原则\n\n- 先做再说\n"
SOUL_COMPILED = "# Soul\n## 核心原则\n- 先做再说"


def _target(key: str) -> CompileTarget:
    return next(target for target in COMPILE_TARGETS if target.key == key)


def _seed(workspace: Path, name: str, content: str) -> Path:
    identity_dir = workspace / IDENTITY_DIR_NAME
    identity_dir.mkdir(parents=True, exist_ok=True)
    path = identity_dir / name
    path.write_text(content, encoding="utf-8")
    return path


# ---- 取舍规则 ---------------------------------------------------------------


def test_owned_sections_are_kept_and_other_sections_dropped():
    content = "# Soul\n\n## 核心原则\n\n- 先做再说\n\n## 平台职责\n\n- 不该出现\n"

    out = compile_content(content, _target("identity_core"))

    assert "先做再说" in out
    assert "不该出现" not in out


def test_falls_back_to_all_body_lines_when_no_section_matches():
    """用户的标题未必和出厂模板一致；收窄失败时宁可全收，不要编译出空产物。"""
    content = "# 我的设定\n\n- 规则一\n- 规则二\n"

    out = compile_content(content, _target("agent_behavior"))

    assert "规则一" in out
    assert "规则二" in out
    assert "# 我的设定" not in out


def test_user_placeholders_are_dropped():
    content = "# User Profile\n\n- **称呼**：（待填）\n- **语气**：轻松\n"

    out = compile_content(content, _target("user_profile_core"))

    assert "待填" not in out
    assert "语气" in out


def test_duplicate_lines_and_markdown_noise_are_removed():
    content = "# Soul\n\n## 核心原则\n\n- 重复\n- 重复\n\n---\n\n- 唯一\n"

    out = compile_content(content, _target("identity_core"))

    assert out.count("- 重复") == 1
    assert "---" not in out
    assert "- 唯一" in out


def test_overlong_line_is_truncated():
    content = "# Soul\n\n## 核心原则\n\n- " + "长" * 500 + "\n"

    out = compile_content(content, _target("identity_core"))

    assert max(len(line) for line in out.splitlines()) <= MAX_LINE_CHARS


def test_char_budget_is_enforced():
    body = "\n".join(f"- 规则{i}：" + "字" * 40 for i in range(80))
    target = _target("identity_core")

    out = compile_content(f"# Soul\n\n## 核心原则\n\n{body}\n", target)

    assert 0 < len(out) <= target.max_chars


def test_placeholder_only_source_compiles_to_empty():
    content = "# User Profile\n\n- **称呼**：（待填）\n"

    assert compile_content(content, _target("user_profile_core")) == ""


# ---- 落盘 -------------------------------------------------------------------


def test_compile_identity_writes_products_and_stamp(tmp_path: Path):
    _seed(tmp_path, "SOUL.md", SOUL_SOURCE)

    result = compile_identity(tmp_path)

    assert result["status"] == "ok"
    assert result["modeUsed"] == "rules"
    assert result["compiledFiles"] == ["identity.core.md"]
    # 缺源文件的目标要报 skipped，而不是静默当成成功。
    assert {entry["target"] for entry in result["skipped"]} == {
        "agent_behavior",
        "user_profile_core",
    }
    assert {entry["reason"] for entry in result["skipped"]} == {"source_missing"}

    rdir = runtime_dir(tmp_path)
    assert (rdir / "identity.core.md").read_text(encoding="utf-8") == SOUL_COMPILED
    assert (rdir / COMPILER_VERSION_FILENAME).read_text(encoding="utf-8") == (
        COMPILED_SCHEMA_VERSION
    )
    assert (rdir / COMPILED_AT_FILENAME).is_file()


def test_all_placeholder_workspace_compiles_nothing_but_records_attempt(tmp_path: Path):
    """全是占位符 = 编译过了、但确实没有可注入的规则——时间戳仍要写。"""
    _seed(tmp_path, "USER.md", "# User Profile\n\n- **称呼**：（待填）\n")

    result = compile_identity(tmp_path)

    assert result["compiledFiles"] == []
    assert (runtime_dir(tmp_path) / COMPILED_AT_FILENAME).is_file()
    assert read_compiled(tmp_path) is None


# ---- 新鲜度 -----------------------------------------------------------------


def test_status_reports_not_compiled_for_empty_workspace(tmp_path: Path):
    assert compiled_status(tmp_path) == {
        "compiled": False,
        "fresh": False,
        "reason": "not_compiled",
    }
    assert read_compiled(tmp_path) is None


def test_fresh_products_are_readable_after_compile(tmp_path: Path):
    _seed(tmp_path, "SOUL.md", SOUL_SOURCE)

    compile_identity(tmp_path)

    assert compiled_status(tmp_path) == {"compiled": True, "fresh": True, "reason": "ok"}
    assert read_compiled(tmp_path) == {"identity_core": SOUL_COMPILED}


def test_source_edit_invalidates_compiled_set(tmp_path: Path):
    soul = _seed(tmp_path, "SOUL.md", SOUL_SOURCE)
    compile_identity(tmp_path)

    # 显式把源文件 mtime 推到未来：秒级精度文件系统下同秒写入会误判。
    future = time.time() + 5
    os.utime(soul, (future, future))

    assert compiled_status(tmp_path)["reason"] == "source_newer"
    assert read_compiled(tmp_path) is None


def test_schema_bump_invalidates_compiled_set(tmp_path: Path):
    _seed(tmp_path, "SOUL.md", SOUL_SOURCE)
    compile_identity(tmp_path)

    (runtime_dir(tmp_path) / COMPILER_VERSION_FILENAME).write_text("0", encoding="utf-8")

    assert compiled_status(tmp_path)["reason"] == "schema_mismatch"
    assert read_compiled(tmp_path) is None


def test_missing_products_mark_set_unusable(tmp_path: Path):
    _seed(tmp_path, "SOUL.md", SOUL_SOURCE)
    compile_identity(tmp_path)

    (runtime_dir(tmp_path) / "identity.core.md").unlink()

    assert compiled_status(tmp_path)["reason"] == "no_products"
    assert read_compiled(tmp_path) is None


def test_recompile_clears_source_newer_state(tmp_path: Path):
    soul = _seed(tmp_path, "SOUL.md", SOUL_SOURCE)
    compile_identity(tmp_path)
    future = time.time() + 5
    os.utime(soul, (future, future))
    assert compiled_status(tmp_path)["fresh"] is False

    compile_identity(tmp_path)

    assert compiled_status(tmp_path)["fresh"] is True
