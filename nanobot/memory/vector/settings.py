"""向量层配置载体。

刻意用 ``dataclass`` 而非 pydantic：``VectorStore`` 只需要一组冻结值，
不应依赖 nanobot 的配置体系——这样它能在测试里被直接构造（spec §4.2 边界纪律）。
pydantic ↔ dataclass 的映射在 ``nanobot/config/schema.py::MemoryVectorConfig``。
"""
from __future__ import annotations

from dataclasses import dataclass

DEFAULT_MODEL = "BAAI/bge-small-zh-v1.5"
DEFAULT_DIMENSIONS = 512


@dataclass(frozen=True)
class VectorSettings:
    """``VectorStore`` 的全部输入。"""

    enabled: bool = False
    model: str = DEFAULT_MODEL
    dimensions: int = DEFAULT_DIMENSIONS
    device: str = "cpu"
    download_source: str = "auto"
    local_model_dir: str = ""
    index_path: str = ""
    sync_on_startup: bool = True
    max_candidates: int = 45
