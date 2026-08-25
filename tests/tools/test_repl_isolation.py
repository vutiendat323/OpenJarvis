"""A repl session belongs to one conversation, whatever id the model supplies."""

from __future__ import annotations

from openjarvis.core.conversation import conversation_scope
from openjarvis.tools.repl import ReplTool


def test_same_session_id_in_two_conversations_sees_different_namespaces():
    tool = ReplTool()

    with conversation_scope("conversation-a"):
        tool.execute(code="secret = 'A'", session_id="shared")
    with conversation_scope("conversation-b"):
        result = tool.execute(code="print(secret)", session_id="shared")

    assert result.success is False
    assert "secret" in result.content


def test_a_conversation_still_keeps_its_own_state_across_calls():
    tool = ReplTool()

    with conversation_scope("conversation-a"):
        tool.execute(code="counter = 7", session_id="shared")
        result = tool.execute(code="print(counter + 1)", session_id="shared")

    assert result.success is True
    assert "8" in result.content


def test_omitting_session_id_still_persists_within_one_conversation():
    """The prompt promises this, so it has to be true.

    ``session_id or uuid4()`` gave every call its own namespace, so a model
    that simply wrote code -- as the prompt's own examples do -- lost every
    variable between calls.
    """
    tool = ReplTool()

    with conversation_scope("conversation-a"):
        tool.execute(code="carried = 3")
        result = tool.execute(code="print(carried * 2)")

    assert result.success is True
    assert "6" in result.content


def test_the_default_session_is_not_shared_between_conversations():
    tool = ReplTool()

    with conversation_scope("conversation-a"):
        tool.execute(code="carried = 3")
    with conversation_scope("conversation-b"):
        result = tool.execute(code="print(carried)")

    assert result.success is False


def test_unset_conversation_is_its_own_scope():
    tool = ReplTool()

    tool.execute(code="loose = 1", session_id="shared")
    with conversation_scope("conversation-a"):
        result = tool.execute(code="print(loose)", session_id="shared")

    assert result.success is False
