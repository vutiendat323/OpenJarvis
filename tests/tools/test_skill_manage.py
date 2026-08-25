from __future__ import annotations

from pathlib import Path

import pytest
import tomllib

from openjarvis.skills.loader import load_skill
from openjarvis.tools.skill_manage import SkillManageTool


@pytest.fixture
def skills_dir(tmp_path: Path) -> Path:
    d = tmp_path / "skills"
    d.mkdir()
    return d


def test_skill_create(skills_dir: Path):
    from openjarvis.tools.skill_manage import SkillManageTool

    tool = SkillManageTool(skills_dir=skills_dir)
    result = tool.execute(
        action="create",
        name="api_health",
        description="Check API health",
        steps=[
            {
                "tool_name": "http_request",
                "arguments_template": '{"url": "{endpoint}/health"}',
            }
        ],
    )
    assert result.success
    assert (skills_dir / "api_health.toml").exists()


def test_skill_list(skills_dir: Path):
    from openjarvis.tools.skill_manage import SkillManageTool

    tool = SkillManageTool(skills_dir=skills_dir)
    tool.execute(
        action="create",
        name="skill_a",
        description="Skill A",
        steps=[{"tool_name": "calculator"}],
    )
    tool.execute(
        action="create",
        name="skill_b",
        description="Skill B",
        steps=[{"tool_name": "calculator"}],
    )
    result = tool.execute(action="list")
    assert "skill_a" in result.content
    assert "skill_b" in result.content


def test_skill_delete(skills_dir: Path):
    from openjarvis.tools.skill_manage import SkillManageTool

    tool = SkillManageTool(skills_dir=skills_dir)
    tool.execute(
        action="create",
        name="temp_skill",
        description="Temp",
        steps=[{"tool_name": "calculator"}],
    )
    assert (skills_dir / "temp_skill.toml").exists()
    result = tool.execute(action="delete", name="temp_skill")
    assert result.success
    assert not (skills_dir / "temp_skill.toml").exists()


def test_skill_load(skills_dir: Path):
    from openjarvis.tools.skill_manage import SkillManageTool

    tool = SkillManageTool(skills_dir=skills_dir)
    tool.execute(
        action="create",
        name="my_skill",
        description="My skill desc",
        steps=[
            {
                "tool_name": "web_search",
                "arguments_template": '{"q": "test"}',
            }
        ],
    )
    result = tool.execute(action="load", name="my_skill")
    assert "web_search" in result.content
    assert "My skill desc" in result.content


@pytest.mark.parametrize("action", ["create", "load", "delete"])
def test_skill_name_traversal_is_rejected_before_file_io(
    skills_dir: Path, action: str
) -> None:
    outside = skills_dir.parent / "config.toml"
    outside.write_text("owner-data")
    tool = SkillManageTool(skills_dir=skills_dir)

    result = tool.execute(
        action=action,
        name="../config",
        description="attacker",
        steps=[{"tool_name": "http_request"}],
    )

    assert result.success is False
    assert outside.read_text() == "owner-data"
    assert "owner-data" not in result.content


def test_absolute_skill_name_is_rejected_before_file_io(
    skills_dir: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "absolute-target.toml"
    tool = SkillManageTool(skills_dir=skills_dir)

    result = tool.execute(
        action="create",
        name=str(outside.with_suffix("")),
        description="attacker",
        steps=[],
    )

    assert result.success is False
    assert not outside.exists()


@pytest.mark.parametrize("action", ["create", "load", "delete"])
def test_resolved_skill_target_must_be_a_direct_child(
    skills_dir: Path, tmp_path: Path, action: str
) -> None:
    outside = tmp_path / "owner.toml"
    outside.write_text("owner-data")
    link = skills_dir / "safe.toml"
    link.symlink_to(outside)
    tool = SkillManageTool(skills_dir=skills_dir)

    result = tool.execute(
        action=action,
        name="safe",
        description="attacker",
        steps=[{"tool_name": "http_request"}],
    )

    assert result.success is False
    assert outside.read_text() == "owner-data"
    assert link.is_symlink()
    assert "owner-data" not in result.content


def test_created_skill_quotes_untrusted_values_as_toml(skills_dir: Path) -> None:
    tool = SkillManageTool(skills_dir=skills_dir)
    description = 'legit"\n[attacker]\nowned = "yes'
    tool_name = 'http_request"\n[[attacker.steps]]\nname = "owned'
    arguments_template = '{"query": "customer\'s \\"coffee\\"\nsize"}'
    output_key = 'result"\n[attacker.output]\nowned = "yes'

    result = tool.execute(
        action="create",
        name="safe_skill",
        description=description,
        steps=[
            {
                "tool_name": tool_name,
                "arguments_template": arguments_template,
                "output_key": output_key,
            }
        ],
    )

    assert result.success is True
    path = skills_dir / "safe_skill.toml"
    raw = tomllib.loads(path.read_text())
    manifest = load_skill(path)
    assert set(raw) == {"skill"}
    assert manifest.name == "safe_skill"
    assert manifest.description == description
    assert len(manifest.steps) == 1
    assert manifest.steps[0].tool_name == tool_name
    assert manifest.steps[0].arguments_template == arguments_template
    assert manifest.steps[0].output_key == output_key
