"""VectorStore 运行期降级：依赖缺失 / 推理异常（D1/D4）。"""
from __future__ import annotations

import pytest

from nanobot.memory.vector.settings import VectorSettings
from nanobot.memory.vector.store import VectorStore


def _settings(**kw) -> VectorSettings:
    base = {"enabled": True, "dimensions": 4, "device": "cpu"}
    base.update(kw)
    return VectorSettings(**base)


def test_missing_chromadb_is_survivable(tmp_path, monkeypatch):
    """D1：未装 chromadb → 所有方法返回空/false，不抛。"""
    monkeypatch.setattr(VectorStore, "_do_load", lambda self: (_ for _ in ()).throw(ImportError("no chromadb")))
    s = VectorStore(_settings(), workspace=tmp_path)
    s._initialize_now()
    assert s.state == "failed"
    assert s.search("x") == []
    assert s.count() == 0
    assert s.remove("m1") is False


def test_encode_exception_is_swallowed(tmp_path):
    """D4：encode 抛异常 → 吞掉返回 []，主流程不受影响。"""

    class _Boom:
        def get_sentence_embedding_dimension(self):
            return 4

        def encode(self, texts, normalize_embeddings=True):
            raise RuntimeError("cuda oom")

    s = VectorStore(_settings(), workspace=tmp_path)
    s._activate(_Boom(), object())
    assert s.search("x") == []
    assert s.upsert("m1", "内容", {}) is False
