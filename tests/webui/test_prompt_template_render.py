"""档案 prompt 模板渲染：单次替换语义。"""

from __future__ import annotations

from pathlib import Path

from nanobot.agents.models import AgentProfile
from nanobot.agents.runtime import render_profile_prompt


def _render(prompt: str, *, name: str = "档案", description: str = "") -> str:
    profile = AgentProfile(id="p", name=name, description=description, prompt=prompt)
    return render_profile_prompt(profile, workspace=Path("/tmp/ws"))


def test_unknown_variable_kept_verbatim() -> None:
    assert "{{unknown}}" in _render("见 {{unknown}}")


def test_name_containing_token_is_not_substituted() -> None:
    """档案名本身含 {{...}} 时不能被二次替换。

    失败场景：用户把档案命名为「{{tools}}」，单趟 replace 会先替换 {{name}}
    得到「{{tools}}」，再替换 {{tools}} 把它展开成工具清单——档案名被静默改写。
    """
    out = _render("名字={{name}}", name="{{tools}}")
    assert out == "名字={{tools}}"


def test_description_containing_token_is_not_substituted() -> None:
    out = _render("描述={{description}}", description="见 {{date}}")
    assert out == "描述=见 {{date}}"


def test_jinja_escape_four_braces_is_consumed_as_one() -> None:
    """{{{{name}}}} 是「字面量 {{name}}」的写法。

    单趟 str.replace 会得到 '{{正常}}' 这种半截 token，用户看不出出错了。
    改成单趟正则后，四个花括号应被识别为一个转义 token 并原样输出。
    """
    out = _render("转义 {{{{name}}}} 应保持")
    assert out == "转义 {{name}} 应保持"


def test_known_variables_still_render() -> None:
    out = _render(
        "{{name}} / {{description}} / {{workspace}} / {{date}}", name="甲", description="乙"
    )
    assert out.startswith("甲 / 乙 / ws / ")


def test_empty_prompt_returns_empty_string() -> None:
    assert _render("") == ""
