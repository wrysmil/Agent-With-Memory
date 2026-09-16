"""VectorStore 状态机、冷却与「绝不阻塞」契约（D2/D3/D6）。"""
from __future__ import annotations

import time

import pytest

from nanobot.memory.vector.settings import VectorSettings
from nanobot.memory.vector.store import VectorStore


class _FakeModel:
    """最小 sentence-transformers 替身：返回固定维度向量。"""

    def __init__(self, dim: int = 4) -> None:
        self._dim = dim
        self.encode_calls = 0

    def get_sentence_embedding_dimension(self) -> int:
        return self._dim

    def encode(self, texts, normalize_embeddings=True):
        self.encode_calls += 1
        import numpy as np

        return np.ones((len(texts), self._dim), dtype="float32")


def _settings(**kw) -> VectorSettings:
    base = {"enabled": True, "dimensions": 4, "device": "cpu"}
    base.update(kw)
    return VectorSettings(**base)


def test_disabled_store_is_inert(tmp_path):
    """D5：enabled=False 时不建目录、不起线程、不联网。"""
    s = VectorStore(_settings(enabled=False), workspace=tmp_path)
    assert s.enabled is False
    assert s.search("x") == []
    assert not (tmp_path / "memory" / "chromadb").exists()


def test_loading_state_never_blocks(tmp_path, monkeypatch):
    """D3/D6：加载中查询立即返回空，不等模型。"""
    s = VectorStore(_settings(), workspace=tmp_path)
    monkeypatch.setattr(s, "_state", "loading")
    t0 = time.perf_counter()
    assert s.search("慢查询") == []
    assert time.perf_counter() - t0 < 0.05


def test_constructor_returns_immediately(tmp_path):
    """D6：向量层对启动耗时的贡献 < 100 ms。"""
    t0 = time.perf_counter()
    VectorStore(_settings(), workspace=tmp_path)
    assert time.perf_counter() - t0 < 0.1


def test_failed_state_sets_fixed_cooldown(tmp_path):
    """D2：普通失败固定 300 s 冷却。"""
    s = VectorStore(_settings(), workspace=tmp_path)
    s._mark_failed(RuntimeError("boom"), is_import_error=False)
    assert s.state == "failed"
    assert s.error and "boom" in s.error
    assert s._cooldown_seconds == pytest.approx(300.0)


def test_import_error_uses_exponential_backoff_capped(tmp_path):
    """D2：ImportError 指数退避，3600 s 封顶。"""
    s = VectorStore(_settings(), workspace=tmp_path)
    s._mark_failed(ImportError("no chromadb"), is_import_error=True)
    assert s._cooldown_seconds == pytest.approx(600.0)
    s._mark_failed(ImportError("no chromadb"), is_import_error=True)
    assert s._cooldown_seconds == pytest.approx(1200.0)
    for _ in range(10):
        s._mark_failed(ImportError("no chromadb"), is_import_error=True)
    assert s._cooldown_seconds == pytest.approx(3600.0)


def test_dimension_mismatch_rejects_init(tmp_path):
    """R-3：模型实际维度与配置不符 → 拒绝启用并写 vector_error。"""
    s = VectorStore(_settings(), workspace=tmp_path)
    s._activate(_FakeModel(dim=8), object())
    assert s.state == "failed"
    assert s.error and "dimension" in s.error.lower()


def test_matching_dimension_reaches_ready(tmp_path):
    s = VectorStore(_settings(), workspace=tmp_path)
    s._activate(_FakeModel(dim=4), object())
    assert s.state == "ready"
    assert s.error is None
