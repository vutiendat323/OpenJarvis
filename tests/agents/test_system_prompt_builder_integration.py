from __future__ import annotations

from pathlib import Path

from openjarvis.core.config import MemoryFilesConfig, SystemPromptConfig


def test_base_agent_uses_builder(tmp_path: Path):
    soul = tmp_path / "SOUL.md"
    soul.write_text("I am Jarvis.")
    memory = tmp_path / "MEMORY.md"
    memory.write_text("- User likes Python")

    from openjarvis.prompt.builder import SystemPromptBuilder

    builder = SystemPromptBuilder(
        agent_template="You are a helpful assistant.",
        memory_files_config=MemoryFilesConfig(
            soul_path=str(soul),
            memory_path=str(memory),
            user_path=str(tmp_path / "USER.md"),
        ),
        system_prompt_config=SystemPromptConfig(),
    )
    prompt = builder.build()
    assert "Jarvis" in prompt
    assert "Python" in prompt
    assert "helpful assistant" in prompt


def test_configured_ordering_prompt_is_not_replaced_by_generic_persona_builder(
    tmp_path,
):
    from openjarvis.agents.orchestrator import OrchestratorAgent
    from openjarvis.prompt.builder import SystemPromptBuilder

    soul = tmp_path / "SOUL.md"
    soul.write_text("Use a friendly tone.")
    builder = SystemPromptBuilder(
        agent_template="Generic assistant instructions.",
        memory_files_config=MemoryFilesConfig(soul_path=str(soul)),
    )
    configured = Path("configs/openjarvis/prompts/ordering-kiosk.md").read_text()
    agent = OrchestratorAgent(
        object(), "test", system_prompt=configured, prompt_builder=builder
    )
    messages = agent._build_messages("Thanh toán", system_prompt=agent._system_prompt)
    assert configured in messages[0].text
    assert "Use a friendly tone." in messages[0].text
    assert "Generic assistant instructions." not in messages[0].text
