"""启动即编译：sync_workspace_templates 播种后补齐过期的 runtime 产物。"""

from pathlib import Path

from nanobot.identity.compiler import compiled_status, runtime_dir
from nanobot.utils.helpers import sync_workspace_templates


def test_sync_seeds_and_compiles_on_fresh_workspace(tmp_path: Path):
    """全新 workspace 首次 sync：播种后自动编译，产物存在且新鲜。"""
    sync_workspace_templates(tmp_path, silent=True)

    status = compiled_status(tmp_path)
    assert status["compiled"] is True
    assert status["fresh"] is True
    # 出厂模板态：仅 SOUL 产出清洗版，AGENT/USER 被 skip_factory_template 跳过
    assert (runtime_dir(tmp_path) / "identity.core.md").is_file()


def test_sync_does_not_recompile_when_fresh(tmp_path: Path):
    """产物已新鲜时，重复 sync 不重写时间戳（无稳态开销）。"""
    sync_workspace_templates(tmp_path, silent=True)
    stamp = runtime_dir(tmp_path) / ".compiled_at"
    first_mtime = stamp.stat().st_mtime_ns

    sync_workspace_templates(tmp_path, silent=True)
    assert stamp.stat().st_mtime_ns == first_mtime
