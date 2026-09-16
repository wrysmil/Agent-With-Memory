"""model_hub：endpoint 双写与源探测。"""
from __future__ import annotations

import os

import pytest

from nanobot.memory.vector import model_hub


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("HF_ENDPOINT", raising=False)
    yield


def test_sync_endpoint_writes_both_env_and_constants(monkeypatch):
    """R-2：只改 os.environ 无效，必须同时 patch huggingface_hub.constants.ENDPOINT。"""
    fake = type("C", (), {"ENDPOINT": "https://original", "HUGGINGFACE_CO_URL_TEMPLATE": ""})
    monkeypatch.setitem(__import__("sys").modules, "huggingface_hub.constants", fake)

    model_hub._sync_hf_hub_endpoint("https://hf-mirror.com")

    assert os.environ["HF_ENDPOINT"] == "https://hf-mirror.com"
    assert fake.ENDPOINT == "https://hf-mirror.com"
    assert "hf-mirror.com" in fake.HUGGINGFACE_CO_URL_TEMPLATE


def test_sync_endpoint_survives_missing_hub(monkeypatch):
    """huggingface_hub 未装时只写 env，不抛异常。"""
    import sys
    monkeypatch.setitem(sys.modules, "huggingface_hub.constants", None)
    model_hub._sync_hf_hub_endpoint("https://hf-mirror.com")
    assert os.environ["HF_ENDPOINT"] == "https://hf-mirror.com"


def test_apply_source_env_auto_is_noop():
    assert model_hub.apply_source_env("auto") is None
    assert "HF_ENDPOINT" not in os.environ


def test_apply_source_env_unknown_source_returns_none():
    assert model_hub.apply_source_env("nope") is None


def test_probe_order_prefers_mirror():
    """中文环境优先镜像，避免先打原站超时。"""
    assert model_hub._PROBE_ORDER[0] == "hf-mirror"


def test_ensure_model_prefers_local_dir(tmp_path, monkeypatch):
    """配了 local_model_dir 且目录存在 → 直接返回，不触发下载。"""
    def _boom(*a, **k):
        raise AssertionError("should not download")

    monkeypatch.setattr(model_hub, "_download", _boom)
    assert model_hub.ensure_model("m", local_dir=str(tmp_path)) == str(tmp_path)


def test_ensure_model_raises_after_all_sources_fail(monkeypatch):
    monkeypatch.setattr(model_hub, "_download", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("net")))
    with pytest.raises(model_hub.ModelUnavailableError):
        model_hub.ensure_model("m")
