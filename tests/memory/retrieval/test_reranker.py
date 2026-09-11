# tests/memory/retrieval/test_reranker.py
import math
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from nanobot.memory.retrieval.candidate import RetrievalCandidate
from nanobot.memory.retrieval.reranker import Reranker, _compute_recency


def test_recency_exponential_decay():
    now = datetime.now(timezone.utc)
    assert _compute_recency(now) == pytest.approx(1.0, abs=1e-3)
    assert _compute_recency(now - timedelta(days=1)) == pytest.approx(math.exp(-0.1), abs=1e-3)
    assert _compute_recency(now - timedelta(days=7)) == pytest.approx(math.exp(-0.7), abs=1e-3)


def _cand(channel, **kw):
    base = dict(
        memory_id="m", content="x", source_channel=channel,
        relevance=0.5, recency_score=0.5, importance_score=0.5,
        access_frequency_score=0.5,
    )
    base.update(kw)
    return RetrievalCandidate(**base)


def test_rerank_base_score_formula():
    cands = [_cand("semantic", relevance=1.0, recency_score=1.0,
                   importance_score=1.0, access_frequency_score=1.0)]
    out = Reranker().rerank(cands, query="x", persona=None, focus_terms=[])
    assert out[0].composite_score == pytest.approx(1.0, abs=1e-3)


def test_rerank_focus_boost_in_range():
    content = "Python 爬虫实战经验"
    base = _cand("semantic", relevance=0.5, content=content)
    boosted = _cand("semantic", relevance=0.5, content=content)
    out_base = Reranker().rerank([base], query="Python 爬虫", persona=None, focus_terms=[])
    out_boost = Reranker().rerank(
        [boosted], query="Python 爬虫", persona=None, focus_terms=["Python", "爬虫"]
    )
    assert out_boost[0].composite_score > out_base[0].composite_score
    assert out_boost[0].composite_score <= out_base[0].composite_score * 1.35 + 1e-6


def test_rerank_action_penalty_drops_action_phrased_fact():
    """动作词惩罚 ×0.3 后必然低于 0.35 阈值 → 同分对照下被惩罚者遭剔除。"""
    penalized = _cand("semantic", relevance=0.8, importance_score=0.8, recency_score=0.8,
                      content="打开 Chrome 浏览器查看", raw=SimpleNamespace(type="fact"))
    kept = _cand("semantic", relevance=0.8, importance_score=0.8, recency_score=0.8,
                 content="Chrome 浏览器安装在 D 盘", raw=SimpleNamespace(type="fact"))
    out = Reranker(fact_type_names=("fact",)).rerank(
        [penalized, kept], query="x", persona=None, focus_terms=[]
    )
    assert [c.content for c in out] == ["Chrome 浏览器安装在 D 盘"]


def test_rerank_action_penalty_factor_is_0_3():
    """借冷启动豁免让被惩罚候选仍返回，从而直接钉住 0.3 系数。"""
    c = _cand("semantic", relevance=0.8, importance_score=0.8, recency_score=0.995,
              content="打开 Chrome 浏览器查看", raw=SimpleNamespace(type="fact"))
    out = Reranker(fact_type_names=("fact",)).rerank(
        [c], query="x", persona=None, focus_terms=[]
    )
    raw_score = 0.4 * 0.8 + 0.2 * 0.995 + 0.2 * 0.8 + 0.2 * 0.5  # 0.779
    assert out[0].composite_score == pytest.approx(raw_score * 0.3, abs=1e-3)


def test_rerank_cold_start_exemption():
    c = _cand("semantic", relevance=0.05, importance_score=0.05, recency_score=0.995,
              access_frequency_score=0.05)
    out = Reranker().rerank([c], query="x", persona=None, focus_terms=[])
    assert len(out) == 1  # 豁免，未被 0.35 阈值过滤


def test_rerank_min_threshold_drops_low_score():
    c = _cand("semantic", relevance=0.05, importance_score=0.05, recency_score=0.05,
              access_frequency_score=0.05)
    out = Reranker().rerank([c], query="x", persona=None, focus_terms=[])
    assert out == []
