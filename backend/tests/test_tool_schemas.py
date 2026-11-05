"""
Tests for the LangChain tools as the LLM sees them: names, argument schemas,
and the per-run identity closured into the factories.
"""

import pytest
from langchain_core.utils.function_calling import convert_to_openai_tool

import app.services.pipeline.tools.github_tools as github_tools
from app.services.pipeline.tools import (
    GithubIndexContext,
    ResumeAnalysisContext,
    ResumeDocxContext,
    calculate_yoe,
    make_extract_github_repos_tool,
    make_generate_resume_docx_tool,
    make_read_resume_tool,
    make_repo_content_searcher_tool,
    make_save_parsed_resume_tool,
    scrape_website,
    web_search,
)


def _parameters(tool) -> dict:
    return convert_to_openai_tool(tool)["function"]["parameters"]


@pytest.fixture
def github_ctx():
    return GithubIndexContext(github_url="https://github.com/ada", github_username="ada")


def test_tool_names_match_the_prompts(tmp_path, github_ctx):
    ctx = ResumeAnalysisContext(user_id=1, resume_id=1, user_name="u", local_only=True)
    docx_ctx = ResumeDocxContext("a", "c", "j", 1, str(tmp_path / "x.docx"))
    tools = [
        calculate_yoe, web_search, scrape_website,
        make_read_resume_tool("r.md"), make_save_parsed_resume_tool(ctx),
        make_generate_resume_docx_tool(docx_ctx),
        make_extract_github_repos_tool(github_ctx), make_repo_content_searcher_tool(github_ctx),
    ]
    assert [t.name for t in tools] == [
        "calculate_yoe", "web_search", "scrape_website", "read_resume",
        "save_parsed_resume", "generate_resume_docx", "github_repos_extractor",
        "repo_content_searcher",
    ]
    for t in tools:
        assert t.description.strip(), f"{t.name} has no description"


def test_calculate_yoe_takes_an_array():
    params = _parameters(calculate_yoe)
    assert params["properties"]["date_ranges"]["type"] == "array"
    assert params["required"] == ["date_ranges"]


@pytest.mark.parametrize("factory_name", ["read_resume", "github_repos_extractor"])
def test_identity_bound_tools_take_no_arguments(factory_name, github_ctx):
    tool = (
        make_read_resume_tool("r.md") if factory_name == "read_resume"
        else make_extract_github_repos_tool(github_ctx)
    )
    assert _parameters(tool).get("properties", {}) == {}


def test_repo_search_has_no_username_argument(github_ctx):
    """The account to search is closured in — the model cannot choose it."""
    params = _parameters(make_repo_content_searcher_tool(github_ctx))
    assert set(params["properties"]) == {"query", "job_description", "top_k"}
    assert params["required"] == ["query"]


def test_repo_search_is_scoped_and_caps_the_job_description(monkeypatch, github_ctx):
    calls = []

    def fake_query(query, top_k=5, repo_username=None):
        calls.append({"query": query, "top_k": top_k, "repo_username": repo_username})
        return [("chunk", {"repo": "engine"})]

    monkeypatch.setattr(github_tools, "query_github_vector_store", fake_query)
    tool = make_repo_content_searcher_tool(github_ctx)

    result = tool.invoke({"query": "fastapi", "job_description": "x" * 5000, "top_k": 3})

    assert result == [{"content": "chunk", "metadata": {"repo": "engine"}}]
    assert calls[0]["repo_username"] == "ada"
    assert calls[0]["top_k"] == 3
    assert calls[0]["query"].count("x") == github_tools.MAX_JOB_DESCRIPTION_CHARS


def test_repo_extractor_reports_no_repositories(monkeypatch, github_ctx):
    monkeypatch.setattr(github_tools, "_get_repository_links", lambda url, acc: [])
    result = make_extract_github_repos_tool(github_ctx).invoke({})
    assert result.startswith("ERROR:")


def test_repo_extractor_indexes_up_to_the_limit(monkeypatch):
    ctx = GithubIndexContext("https://github.com/ada", "ada", repo_limit=2)
    indexed = []
    monkeypatch.setattr(
        github_tools, "_get_repository_links",
        lambda url, acc: [f"https://github.com/ada/r{i}" for i in range(5)],
    )
    monkeypatch.setattr(
        github_tools, "process_github_repo_to_vector_store",
        lambda url, **kwargs: indexed.append(url),
    )
    result = make_extract_github_repos_tool(ctx).invoke({})
    assert result == indexed == ["https://github.com/ada/r0", "https://github.com/ada/r1"]


def test_read_resume_reads_only_the_bound_file(tmp_path):
    path = tmp_path / "resume.md"
    path.write_text("Ada Lovelace — Engineer", encoding="utf-8")
    assert make_read_resume_tool(str(path)).invoke({}) == "Ada Lovelace — Engineer"


def test_read_resume_missing_file_is_an_error_string(tmp_path):
    result = make_read_resume_tool(str(tmp_path / "missing.md")).invoke({})
    assert result.startswith("ERROR:")


def test_web_search_without_key_degrades(monkeypatch):
    import app.services.pipeline.tools.search_tools as search_tools

    monkeypatch.setattr(search_tools, "_serper_api_key", lambda: "")
    assert web_search.invoke({"query": "acme interview"}).startswith("ERROR:")
