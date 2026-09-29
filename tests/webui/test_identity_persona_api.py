from pathlib import Path

import pytest

from nanobot.webui.identity_api import (
    identity_get_active_persona,
    identity_list_files,
    identity_set_active_persona,
)
from nanobot.webui.settings_contracts import WebUISettingsError


def _ws(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    (ws / "identity" / "personas").mkdir(parents=True)
    (ws / "identity" / "personas" / "tech_expert.md").write_text("A", encoding="utf-8")
    (ws / "identity" / "personas" / "mentor.md").write_text("B", encoding="utf-8")
    return ws


def test_get_active_persona_defaults_to_inactive(tmp_path):
    assert identity_get_active_persona(_ws(tmp_path)) == {
        "active": "",
        "options": ["mentor", "tech_expert"],
    }


def test_set_then_get_roundtrip(tmp_path):
    ws = _ws(tmp_path)
    assert identity_set_active_persona(ws, "mentor") == {"active": "mentor"}
    assert identity_get_active_persona(ws)["active"] == "mentor"


def test_set_empty_clears_activation(tmp_path):
    ws = _ws(tmp_path)
    identity_set_active_persona(ws, "mentor")
    assert identity_set_active_persona(ws, "")["active"] == ""


def test_set_unknown_persona_is_404(tmp_path):
    with pytest.raises(WebUISettingsError) as exc:
        identity_set_active_persona(_ws(tmp_path), "nope")
    assert exc.value.status == 404


def test_set_rejects_path_traversal(tmp_path):
    with pytest.raises(WebUISettingsError) as exc:
        identity_set_active_persona(_ws(tmp_path), "../../secrets")
    assert exc.value.status == 400


def test_get_treats_dangling_activation_as_inactive(tmp_path):
    ws = _ws(tmp_path)
    identity_set_active_persona(ws, "mentor")
    (ws / "identity" / "personas" / "mentor.md").unlink()
    assert identity_get_active_persona(ws)["active"] == ""


def test_list_files_reports_token_estimate(tmp_path):
    ws = _ws(tmp_path)
    (ws / "identity" / "personas" / "tech_expert.md").write_text(
        "技术搭档" * 100, encoding="utf-8"
    )
    items = {f["name"]: f for f in identity_list_files(ws)["files"]}
    assert items["tech_expert.md"]["tokens"] > 0


def test_list_files_survives_undecodable_file(tmp_path):
    """计量是展示用的：一个非 UTF-8 文件不能把整个清单打成 500。"""
    ws = _ws(tmp_path)
    (ws / "identity" / "personas" / "tech_expert.md").write_text(
        "技术搭档" * 100, encoding="utf-8"
    )
    (ws / "identity" / "personas" / "mentor.md").write_bytes(b"\xff\xfe\x00bad")
    items = {f["name"]: f for f in identity_list_files(ws)["files"]}
    assert items["mentor.md"]["tokens"] == 0
    assert items["tech_expert.md"]["tokens"] > 0


def test_dotted_stem_can_be_activated(tmp_path):
    """``foo.bar.md`` 是合法 persona：isidentifier 会误伤它，不能用。"""
    ws = _ws(tmp_path)
    (ws / "identity" / "personas" / "foo.bar.md").write_text("C", encoding="utf-8")
    assert identity_set_active_persona(ws, "foo.bar") == {"active": "foo.bar"}
    assert identity_get_active_persona(ws)["active"] == "foo.bar"


@pytest.mark.parametrize("stem", ["a/b", "a\\b", "..", ".", "\x00"])
def test_set_rejects_escaping_stems(tmp_path, stem):
    ws = _ws(tmp_path)
    with pytest.raises(WebUISettingsError) as exc:
        identity_set_active_persona(ws, stem)
    assert exc.value.status == 400


def test_set_traversal_does_not_touch_disk(tmp_path):
    """被拒的写入不能留下任何痕迹——包括把 workspace 外的文件读进来。"""
    ws = _ws(tmp_path)
    with pytest.raises(WebUISettingsError):
        identity_set_active_persona(ws, "../../secrets")
    assert not (tmp_path / "secrets").exists()
    assert not (ws / "secrets").exists()
    assert not (ws / "identity" / "secrets").exists()
