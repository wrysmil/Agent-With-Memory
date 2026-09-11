# tests/memory/retrieval/test_decomposer.py
import pytest

from nanobot.memory.retrieval.decomposer import QueryDecomposer


class _FakeBrain:
    def __init__(self, payload): self._payload = payload
    async def call_compiler(self, prompt: str) -> str:
        return self._payload


@pytest.mark.asyncio
async def test_llm_path_returns_keywords_and_intent():
    brain = _FakeBrain('{"keywords": ["Python", "爬虫"], "intent": "general"}')
    decomp = QueryDecomposer(brain=brain)
    result = await decomp.decompose("我之前写的那个 Python 爬虫脚本还能用吗", recent=[])
    assert result.keywords == ["Python", "爬虫"]
    assert result.intent == "general"
    assert result.used_llm is True


@pytest.mark.asyncio
async def test_rule_fallback_when_no_brain():
    decomp = QueryDecomposer(brain=None)
    result = await decomp.decompose("我之前写的 Python 爬虫脚本还能用吗", recent=[])
    assert "Python" in result.keywords or "爬虫" in result.keywords
    assert result.used_llm is False


@pytest.mark.asyncio
async def test_rule_fallback_when_llm_fails():
    class _BadBrain:
        async def call_compiler(self, prompt: str) -> str:
            raise RuntimeError("LLM down")
    decomp = QueryDecomposer(brain=_BadBrain())
    result = await decomp.decompose("找到那个 Python 脚本", recent=[])
    assert result.used_llm is False
    assert result.keywords  # 至少提取了一个关键词


@pytest.mark.asyncio
async def test_search_file_intent_from_keywords():
    decomp = QueryDecomposer(brain=None)
    result = await decomp.decompose("之前的 图片 在哪", recent=[])
    assert result.intent == "search_file"


@pytest.mark.asyncio
async def test_cache_hit_skips_llm():
    brain = _FakeBrain('{"keywords": ["X"], "intent": "general"}')
    decomp = QueryDecomposer(brain=brain)
    r1 = await decomp.decompose("something", recent=[])
    r2 = await decomp.decompose("something", recent=[])
    # 第二次 cache hit，used_llm 仍为 True（同一结果），但内部 _decompose_cache 命中
    assert r1.used_llm is True and r2.used_llm is True


@pytest.mark.asyncio
async def test_cache_whitespace_normalized():
    """相同的 query（忽略首尾空白）应该命中缓存。"""
    brain = _FakeBrain('{"keywords": ["test"], "intent": "general"}')
    decomp = QueryDecomposer(brain=brain)
    r1 = await decomp.decompose("query", recent=[])
    r2 = await decomp.decompose("  query  ", recent=[])
    assert r1.keywords == r2.keywords


@pytest.mark.asyncio
async def test_media_hints_trigger_search_file():
    hints = ["图片", "照片", "视频", "文件", "音频", "pdf", "PDF"]
    for hint in hints:
        decomp = QueryDecomposer(brain=None)
        result = await decomp.decompose(f"找一下之前的{hint}", recent=[])
        assert result.intent == "search_file", f"{hint} should trigger search_file intent"
