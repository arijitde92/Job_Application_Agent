"""
app.services.pipeline
---------------------
The job-application agent pipeline: LangChain 1.x agents orchestrated as a
LangGraph state graph, traced with LangSmith.

    build_pipeline(...)   compile the graph for one run (``graph.py``)
    analyze_resume(...)   run the Resume Analyzer on its own
    build_run_config(...) run name, tags, metadata, callbacks (``tracing.py``)

The web app drives it through ``app.services.pipeline_runner``; the standalone
CLI is ``python -m app.services.pipeline`` (``uv run pipeline``).
"""

from app.services.pipeline.graph import (
    NODE_NAMES,
    PipelineError,
    PipelineInputs,
    PipelineState,
    analyze_resume,
    build_pipeline,
)
from app.services.pipeline.llms import AgentRole, ModelRegistry
from app.services.pipeline.middleware import ResumeAnalysisError
from app.services.pipeline.tracing import build_run_config

__all__ = [
    "NODE_NAMES",
    "AgentRole",
    "ModelRegistry",
    "PipelineError",
    "PipelineInputs",
    "PipelineState",
    "ResumeAnalysisError",
    "analyze_resume",
    "build_pipeline",
    "build_run_config",
]
