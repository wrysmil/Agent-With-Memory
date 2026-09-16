"""模型获取：三源探测 + endpoint 双写 + 缓存命中 + 分层重试。

本模块**不**在顶层 import ``huggingface_hub`` / ``modelscope``——它们是
``vector`` extra 的依赖，核心安装下不存在。所有第三方 import 都延迟到调用点。
"""
from __future__ import annotations

import os
from pathlib import Path

from loguru import logger


class ModelUnavailableError(RuntimeError):
    """所有下载源均失败。由 ``VectorStore`` 接住并转入 ``failed`` 状态。"""


# 三源 endpoint（spec §2.3 已实测 HTTP 200）
_SOURCES: dict[str, str] = {
    "huggingface": "https://huggingface.co",
    "hf-mirror": "https://hf-mirror.com",
    "modelscope": "https://modelscope.cn",
}

# auto 模式的探测顺序：国内环境优先镜像，避免先打原站等超时
_PROBE_ORDER: tuple[str, ...] = ("hf-mirror", "huggingface", "modelscope")

_DOWNLOAD_TIMEOUT_SECONDS = 60.0


def _sync_hf_hub_endpoint(endpoint: str) -> None:
    """**双写** HF endpoint。

    R-2：``huggingface_hub`` 在**模块导入时**就把 ``HF_ENDPOINT`` 读成
    ``constants.ENDPOINT`` 常量，之后改 ``os.environ`` 完全无效。
    ``ENDPOINT`` 覆盖 ``>=0.25``；旧版的 ``HF_ENDPOINT`` 由 env 分支覆盖。
    """
    os.environ["HF_ENDPOINT"] = endpoint
    try:
        from huggingface_hub import constants as hf_constants
    except ImportError:
        return
    hf_constants.ENDPOINT = endpoint
    hf_constants.HUGGINGFACE_CO_URL_TEMPLATE = (
        endpoint + "/{repo_id}/resolve/{revision}/{filename}"
    )


def apply_source_env(source: str) -> str | None:
    """按配置写入 endpoint 环境变量。"""
    if source == "auto":
        return None
    endpoint = _SOURCES.get(source)
    if endpoint is None:
        logger.warning("unknown vector download_source: {}", source)
        return None
    _sync_hf_hub_endpoint(endpoint)
    return endpoint


def ensure_model(
    model_name: str,
    *,
    source: str = "auto",
    local_dir: str = "",
) -> str:
    """确保模型可用，返回可喂给 ``SentenceTransformer`` 的路径。"""
    if local_dir:
        path = Path(local_dir)
        if path.is_dir():
            return str(path)
        logger.warning("vector local_model_dir missing, falling back to download: {}", local_dir)

    if source != "auto":
        return _download(model_name, source)

    last_error: Exception | None = None
    for candidate in _PROBE_ORDER:
        try:
            return _download(model_name, candidate)
        except Exception as exc:
            last_error = exc
            logger.debug("vector model source {} failed: {}", candidate, exc)
    raise ModelUnavailableError(
        f"all model sources failed for {model_name}: {type(last_error).__name__}: {last_error}"
    )


def _download(model_name: str, source: str) -> str:
    """从单一源下载并返回本地路径。"""
    _sync_hf_hub_endpoint(_SOURCES[source])
    if source == "modelscope":
        import modelscope  # 延迟 import

        return modelscope.snapshot_download(model_name)
    from huggingface_hub import snapshot_download  # 延迟 import

    # snapshot_download 没有 timeout 形参（只有 etag_timeout）；下载超时须经
    # HF_HUB_DOWNLOAD_TIMEOUT 环境变量设置。此前直接传 timeout= 会抛
    # TypeError: unexpected keyword argument 'timeout'，导致 hf-mirror /
    # huggingface 两源恒失败（只剩 modelscope），自动下载实际不可用。
    os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", str(_DOWNLOAD_TIMEOUT_SECONDS))
    return snapshot_download(repo_id=model_name)


def is_cached(model_name: str) -> bool:
    """检测 HF 缓存里是否已有必需的 snapshot 文件。"""
    required = ("config.json", "model.safetensors", "tokenizer_config.json")
    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        return False
    try:
        path = snapshot_download(model_name, local_files_only=True)
    except Exception:
        return False
    base = Path(path)
    return all((base / name).exists() for name in required)
