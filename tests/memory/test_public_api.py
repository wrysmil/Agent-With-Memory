"""Tests for public API exports."""

import pytest


class TestPublicApi:
    def test_public_api_exports(self):
        from nanobot.memory import (
            MemoryDatabase, Memory, Episode, ScratchpadEntry,
            MemoryType, MemoryPriority, EpisodeOutcome,
            add_memory, get_memory, list_memories, search_memories, delete_memory,
            add_episode, get_episode, list_episodes_by_session,
            upsert_scratchpad, get_scratchpad,
        )
        assert MemoryDatabase is not None
        assert Memory is not None
        assert Episode is not None
        assert ScratchpadEntry is not None
        assert MemoryType is not None
        assert MemoryPriority is not None
        assert EpisodeOutcome is not None
        assert add_memory is not None
        assert get_memory is not None
        assert list_memories is not None
        assert search_memories is not None
        assert delete_memory is not None
        assert add_episode is not None
        assert get_episode is not None
        assert list_episodes_by_session is not None
        assert upsert_scratchpad is not None
        assert get_scratchpad is not None

    def test_all_exported_from_memory(self):
        from nanobot.memory import __all__
        expected = [
            "MemoryDatabase",
            "Memory", "Episode", "ScratchpadEntry",
            "MemoryType", "MemoryPriority", "EpisodeOutcome", "EpisodeSource",
            "add_memory", "get_memory", "list_memories", "search_memories", "delete_memory",
            "add_episode", "get_episode", "list_episodes_by_session",
            "upsert_scratchpad", "get_scratchpad",
        ]
        assert set(__all__) == set(expected)
