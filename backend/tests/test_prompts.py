"""Tests for prompt interpolation and task-message rendering."""

import json
import re

import pytest

from app.services.pipeline import prompts

INPUTS = {"job_details_json": '{"job_name": "Engineer"}', "company_name": "Acme"}
_BARE_TOKEN = re.compile(r"\{[A-Za-z_][A-Za-z0-9_]*\}")


def test_interpolate_replaces_known_tokens_only():
    text = "Job: {job_details_json} | unknown: {not_an_input} | json: {\"a\": 1}"
    assert prompts.interpolate(text, INPUTS) == (
        'Job: {"job_name": "Engineer"} | unknown: {not_an_input} | json: {"a": 1}'
    )


def test_interpolate_does_not_recurse_into_substituted_values():
    assert prompts.interpolate("{a}", {"a": "{b}", "b": "x"}) == "{b}"


def _all_rendered_descriptions() -> list[str]:
    return [
        prompts.resume_analysis_description(42),
        prompts.GITHUB_SUMMARY_DESCRIPTION,
        prompts.profile_description(True),
        prompts.profile_description(False),
        prompts.resume_strategy_description(True, True),
        prompts.resume_strategy_description(False, False),
        prompts.INTERVIEW_PREPARATION_DESCRIPTION,
    ]


@pytest.mark.parametrize("description", _all_rendered_descriptions())
def test_no_unresolved_tokens_after_interpolation(description):
    """Every bare {identifier} in a description must be a real pipeline input."""
    rendered = prompts.interpolate(description, INPUTS)
    assert _BARE_TOKEN.findall(rendered) == []


def test_json_templates_survive_interpolation():
    rendered = prompts.interpolate(prompts.resume_strategy_description(False, True), INPUTS)
    assert '"personal_information": {"name": ""' in rendered
    assert '"chosen_github_repos"' in prompts.interpolate(prompts.GITHUB_SUMMARY_DESCRIPTION, INPUTS)


def test_resume_analysis_prompt_embeds_the_resume_id_and_tool_steps():
    description = prompts.resume_analysis_description(42)
    assert '"id" MUST be 42' in description
    assert "read_resume tool" in description
    assert "calculate_yoe" in description and "save_parsed_resume" in description
    assert "<resume_id>" not in description


def test_docx_step_only_with_docx():
    assert "generate_resume_docx" in prompts.resume_strategy_description(True, True)
    assert "generate_resume_docx" not in prompts.resume_strategy_description(True, False)
    assert ".docx" in prompts.resume_strategy_expected_output(True)
    assert ".docx" not in prompts.resume_strategy_expected_output(False)


def test_no_github_profile_prompt_carries_grounding_rules():
    assert "No GitHub profile was provided" in prompts.profile_description(False)
    assert "GitHub projects summary" in prompts.profile_description(True)


def test_render_task_message_layout():
    message = prompts.render_task_message(
        "Do the thing.", "A thing.",
        context=[("Profile", "P"), ("Resume", json.dumps({"a": 1}))],
        markdown=True,
    )
    assert message.startswith("Current Task: Do the thing.")
    assert "expected criteria for your final answer: A thing." in message
    assert "Markdown" in message
    assert "### Profile\nP" in message
    assert message.index("### Profile") < message.index("### Resume")


def test_render_task_message_without_context_or_markdown():
    message = prompts.render_task_message("Task.", "Output.")
    assert "context you're working with" not in message
    assert "Markdown syntax" not in message
