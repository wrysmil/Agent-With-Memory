"""nanobot.memory 公共 API 导出单测（Task 9）。"""


def test_new_public_api_exports():
    import nanobot.memory as m

    assert hasattr(m, "SessionEndEvent")
    assert hasattr(m, "SessionEndReason")
    assert hasattr(m, "SessionEndOrchestrator")
    assert hasattr(m, "ProfileExtractor")
    assert hasattr(m, "ExperienceExtractor")
    assert hasattr(m, "TopicChangeGate")
    assert hasattr(m, "compute_topic_hash")