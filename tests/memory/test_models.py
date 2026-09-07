"""Tests for memory dataclass models."""

import pytest

from nanobot.memory.models import (
    EpisodeOutcome,
    EpisodeSource,
    Memory,
    MemoryPriority,
    MemoryType,
    ScratchpadEntry,
    Episode,
)


class TestEnums:
    def test_memory_type_values(self):
        assert MemoryType.FACT.value == 'fact'
        assert MemoryType.PREFERENCE.value == 'preference'
        assert MemoryType.SKILL.value == 'skill'
        assert MemoryType.ERROR.value == 'error'
        assert MemoryType.RULE.value == 'rule'
        assert MemoryType.EXPERIENCE.value == 'experience'

    def test_memory_priority_values(self):
        assert MemoryPriority.SHORT_TERM.value == 'short_term'
        assert MemoryPriority.LONG_TERM.value == 'long_term'

    def test_episode_outcome_values(self):
        assert EpisodeOutcome.COMPLETED.value == 'completed'
        assert EpisodeOutcome.FAILED.value == 'failed'
        assert EpisodeOutcome.PARTIAL.value == 'partial'


class TestMemoryDataclass:
    def test_memory_required_fields(self):
        m = Memory(id="m1", content="x", created_at="2026-09-07")
        assert m.id == "m1"
        assert m.content == "x"

    def test_memory_defaults(self):
        m = Memory(id="m1", content="x", created_at="2026-09-07")
        assert m.type == MemoryType.FACT
        assert m.priority == MemoryPriority.LONG_TERM
        assert m.importance_score == 0.5
        assert m.tags == []

    def test_memory_serializes_to_dict(self):
        m = Memory(id="m1", content="x", created_at="2026-09-07")
        d = m.to_row()
        assert d['id'] == 'm1'
        assert d['type'] == 'fact'
        assert d['tags'] == '[]'


class TestEpisodeDataclass:
    def test_episode_required_fields(self):
        e = Episode(
            id="e1", session_id="s1", summary="task done",
            started_at="2026-09-07", ended_at="2026-09-07",
        )
        assert e.id == "e1"
        assert e.outcome == EpisodeOutcome.COMPLETED

    def test_episode_defaults(self):
        e = Episode(
            id="e1", session_id="s1", summary="task",
            started_at="2026-09-07", ended_at="2026-09-07",
        )
        assert e.goal == ''
        assert e.action_nodes == []
        assert e.tools_used == []
        assert e.linked_memory_ids == []


class TestScratchpadDataclass:
    def test_scratchpad_defaults(self):
        s = ScratchpadEntry(user_id="u1", workspace_id="w1", updated_at="2026-09-07")
        assert s.content == ""
        assert s.active_projects == []
        assert s.open_questions == []
        assert s.next_steps == []

    def test_scratchpad_to_row(self):
        s = ScratchpadEntry(
            user_id="u1", workspace_id="w1", updated_at="2026-09-07",
            content="Working on memory system",
            active_projects=["proj1"],
        )
        d = s.to_row()
        assert d['user_id'] == 'u1'
        assert d['content'] == 'Working on memory system'
        assert d['active_projects'] == '["proj1"]'
