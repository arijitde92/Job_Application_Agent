"""
RAGAS evaluation runner for the resume_analyzer agent.

Flow: load the golden dataset version -> get an answer per sample (live agent
run on Claude, or cached answers from a previous run via --answers-from) ->
score with faithfulness / answer_relevancy / answer_correctness on a Gemini
judge -> write scores.csv + summary.json + run_config.json under
``tests/ragas/agents/resume_analyzer/<dataset_version>/run_<timestamp>/``.

Run from backend/:

    uv run python -m tests.ragas.agents.resume_analyzer.run_eval --version v1
    uv run python -m tests.ragas.agents.resume_analyzer.run_eval --version v1 --limit 1
    uv run python -m tests.ragas.agents.resume_analyzer.run_eval --version v1 \
        --answers-from tests/ragas/agents/resume_analyzer/v1/run_20260728_120000

Requires GEMINI_API_KEY (judge + embeddings) and — unless --answers-from is
given — ANTHROPIC_API_KEY (the agent under test runs on Claude).
"""

import argparse
import json
import math
import os
import sys
from datetime import datetime
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(BACKEND_DIR))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND_DIR / ".env")

from app.services.resume_parser import ParsedResume  # noqa: E402
from tests.ragas.agents.resume_analyzer import AGENT_NAME  # noqa: E402
from tests.ragas.common import compat  # noqa: F401, E402 — before `import ragas`
from tests.ragas.common.dataset import (  # noqa: E402
    GoldenSample,
    load_manifest,
    load_samples,
    load_task_prompt,
)
from tests.ragas.common.gemini import (  # noqa: E402
    EMBEDDING_MODEL,
    JUDGE_MODEL,
    build_evaluator,
)
from tests.ragas.common.results_io import (  # noqa: E402
    create_run_dir,
    git_commit,
    load_answer,
    save_answer,
    write_run_results,
)
from tests.ragas.common.verbalize import (  # noqa: E402
    VERBALIZER_ID,
    verbalize_parsed_resume,
)

# Keep well under the agent/judge rate limits (agent LLM caps at max_rpm=10).
MAX_WORKERS = 4
# answer_correctness chains many judge calls per sample on long resumes; the
# ragas default (180s) times out. Per-job budget, not overall.
JOB_TIMEOUT_SECONDS = 600

METRIC_NAMES = ["faithfulness", "answer_relevancy", "answer_correctness"]


def _collect_answers(
    samples: list[GoldenSample], run_dir: Path, answers_from: Path | None
) -> dict[str, str]:
    """Return {sample_id: normalized parsed-resume JSON} and cache into run_dir."""
    answers: dict[str, str] = {}
    for index, sample in enumerate(samples, start=1):
        if answers_from is not None:
            answer_json = load_answer(answers_from, sample.sample_id)
            print(f"[{index}/{len(samples)}] {sample.sample_id}: reusing cached answer")
        else:
            print(f"[{index}/{len(samples)}] {sample.sample_id}: running resume_analyzer ...")
            from tests.ragas.agents.resume_analyzer.generate_answers import (
                generate_answer,
            )

            answer_json = generate_answer(sample)
        # Validate + cache whatever will be scored.
        ParsedResume.model_validate_json(answer_json)
        save_answer(run_dir, sample.sample_id, answer_json)
        answers[sample.sample_id] = answer_json
    return answers


def run_eval(version: str, limit: int | None, answers_from: Path | None) -> int:
    manifest = load_manifest(AGENT_NAME, version)
    task_prompt = load_task_prompt(AGENT_NAME, version)
    samples = load_samples(AGENT_NAME, version)
    if limit is not None:
        samples = samples[:limit]
    if not samples:
        print("ERROR: no samples to evaluate.")
        return 1

    unverified = [s.sample_id for s in samples if not s.meta.get("verified")]
    if unverified:
        print(
            "WARNING: ground truth not hand-verified for: "
            + ", ".join(unverified)
            + " — answer_correctness scores measure agreement with an "
            "unverified seed, not true correctness."
        )

    run_dir = create_run_dir(AGENT_NAME, version)
    print(f"Run directory: {run_dir}")

    answers = _collect_answers(samples, run_dir, answers_from)

    # Build the RAGAS dataset.
    from ragas import EvaluationDataset, evaluate
    from ragas.dataset_schema import SingleTurnSample
    from ragas.metrics import AnswerCorrectness, Faithfulness, ResponseRelevancy
    from ragas.run_config import RunConfig

    ragas_samples = [
        SingleTurnSample(
            user_input=task_prompt,
            retrieved_contexts=[sample.context_text],  # whole resume text, unchunked
            response=verbalize_parsed_resume(
                ParsedResume.model_validate_json(answers[sample.sample_id]).result
            ),
            reference=verbalize_parsed_resume(sample.ground_truth.result),
        )
        for sample in samples
    ]

    llm, embeddings = build_evaluator()
    metrics = [
        Faithfulness(llm=llm),
        ResponseRelevancy(llm=llm, embeddings=embeddings),
        AnswerCorrectness(llm=llm, embeddings=embeddings),
    ]

    print(f"Scoring {len(ragas_samples)} sample(s) with {len(metrics)} metrics ...")
    result = evaluate(
        EvaluationDataset(samples=ragas_samples),
        metrics=metrics,
        llm=llm,
        embeddings=embeddings,
        run_config=RunConfig(max_workers=MAX_WORKERS, timeout=JOB_TIMEOUT_SECONDS),
    )

    scores_df = result.to_pandas()
    scores_df.insert(0, "sample_id", [sample.sample_id for sample in samples])

    metric_columns = [c for c in METRIC_NAMES if c in scores_df.columns]
    nan_counts = {c: int(scores_df[c].isna().sum()) for c in metric_columns}
    summary = {
        "agent": AGENT_NAME,
        "dataset_version": version,
        "run": run_dir.name,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "num_samples": len(samples),
        "sample_ids": [sample.sample_id for sample in samples],
        "unverified_ground_truths": unverified,
        # None (JSON null) when every sample failed the metric — a bare NaN
        # would make summary.json invalid JSON.
        "metric_means": {
            c: (None if math.isnan(mean) else round(mean, 4))
            for c in metric_columns
            for mean in [float(scores_df[c].mean())]
        },
        "nan_counts": nan_counts,
    }
    run_config = {
        "agent": AGENT_NAME,
        "dataset_version": version,
        "metrics": metric_columns,
        "judge_model": JUDGE_MODEL,
        "embedding_model": EMBEDDING_MODEL,
        "agent_model": _agent_model(),
        "verbalizer_id": VERBALIZER_ID,
        "manifest_verbalizer_id": manifest.get("verbalizer_id"),
        "answers_from": str(answers_from) if answers_from else None,
        "max_workers": MAX_WORKERS,
        "job_timeout_seconds": JOB_TIMEOUT_SECONDS,
        "git_commit": git_commit(),
        "limit": limit,
    }
    write_run_results(run_dir, scores_df, summary, run_config)

    print("\n" + "=" * 70)
    print(f"RESULTS ({run_dir})")
    for metric_name, mean in summary["metric_means"].items():
        flag = f"  ({nan_counts[metric_name]} NaN)" if nan_counts[metric_name] else ""
        shown = "n/a" if mean is None else f"{mean:.4f}"
        print(f"  {metric_name:>20}: {shown}{flag}")
    print("=" * 70)
    return 0


def _agent_model() -> str:
    """The model the agent under test runs on (recorded for reproducibility)."""
    try:
        from app.services.crew.agents import resume_analyzer

        return str(resume_analyzer.llm.model)
    except Exception:  # noqa: BLE001 — recording metadata must never fail the run
        return "unknown"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the RAGAS evaluation for the resume_analyzer agent."
    )
    parser.add_argument("--version", default="v1", help="Dataset version (default v1)")
    parser.add_argument(
        "--limit", type=int, default=None, help="Evaluate only the first N samples"
    )
    parser.add_argument(
        "--answers-from",
        default=None,
        help="Path to a previous run dir whose answers/ should be reused "
        "(skips the live agent runs)",
    )
    args = parser.parse_args()

    if not os.environ.get("GEMINI_API_KEY"):
        print("ERROR: GEMINI_API_KEY is not set (expected in backend/.env).")
        return 1

    answers_from: Path | None = None
    if args.answers_from:
        answers_from = Path(args.answers_from)
        if not answers_from.is_absolute():
            answers_from = BACKEND_DIR / answers_from
        if not (answers_from / "answers").is_dir():
            print(f"ERROR: no answers/ directory under {answers_from}")
            return 1
    elif not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "ERROR: ANTHROPIC_API_KEY is not set (expected in backend/.env) — "
            "the resume_analyzer agent runs on Claude. Use --answers-from to "
            "score cached answers instead."
        )
        return 1

    return run_eval(args.version, args.limit, answers_from)


if __name__ == "__main__":
    sys.exit(main())
