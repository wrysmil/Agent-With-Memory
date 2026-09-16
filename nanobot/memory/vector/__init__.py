"""向量检索子包。

边界纪律（spec §4.2）：本包的模块**不知道** ``Memory`` 领域模型，
只处理 ``(id, content, metadata)`` 三元组，因此可脱离 nanobot 独立单测。
``MemoryIndexer`` 是唯一一层把 ``Memory`` 转成三元组的地方。

**本文件刻意不做 re-export。** 若在此 ``from ...store import VectorStore``，
则任何 ``from nanobot.memory.vector import model_hub`` 都会连带 import
``store``（进而在模块顶层沾上 chromadb / sentence-transformers 相关符号），
使 `model_hub` 无法脱离重依赖单测，也让「核心安装不拉重依赖」这条纪律
在 import 层面失效。请统一使用**全路径** import：

    from nanobot.memory.vector.settings import VectorSettings
    from nanobot.memory.vector.store import VectorStore
    from nanobot.memory.vector.indexer import MemoryIndexer
"""
