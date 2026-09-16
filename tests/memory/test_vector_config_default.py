"""默认配置回归：D5 + R6 —— 不装 vector extra 时行为与体积零改变。"""
from __future__ import annotations

from pathlib import Path

from nanobot.config.schema import AgentDefaults
from nanobot.memory.vector.settings import VectorSettings
from nanobot.memory.vector.store import VectorStore


def test_default_config_creates_no_vector_artifacts(tmp_path):
    """D5：默认配置下不建 chromadb 目录、不起线程、无网络。"""
    d = AgentDefaults()
    s = d.memory_vector.to_vector_settings(
        enabled=(d.memory_search_backend == "chromadb")
    )
    assert s == VectorSettings(enabled=False) or s.enabled is False

    store = VectorStore(s, workspace=tmp_path)
    assert store.enabled is False
    assert store.search("x") == []
    assert store.count() == 0
    assert not (tmp_path / "memory" / "chromadb").exists()
    assert store.state == "idle"


def test_vector_extra_absent_from_core_dependencies():
    """R6：核心 dependencies 不得出现向量重依赖。"""
    import tomllib

    root = Path(__file__).resolve().parents[2]
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    core = " ".join(data["project"]["dependencies"]).lower()
    for banned in ("chromadb", "sentence-transformers", "torch"):
        assert banned not in core, f"{banned} 不得进入核心 dependencies"
    assert "vector" in data["project"]["optional-dependencies"]


def test_vector_extra_has_cpu_torch_index():
    """R-1：uv index 配置必须存在，否则 Windows 会拉 CUDA wheel。"""
    import tomllib

    root = Path(__file__).resolve().parents[2]
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    assert data["tool"]["uv"]["sources"]["torch"]["index"] == "pytorch-cpu"
    indexes = {i["name"]: i for i in data["tool"]["uv"]["index"]}
    assert indexes["pytorch-cpu"]["url"] == "https://download.pytorch.org/whl/cpu"
    assert indexes["pytorch-cpu"]["explicit"] is True
