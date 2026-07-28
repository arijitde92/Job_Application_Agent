"""
Result writing for RAGAS evaluation runs.

Each run writes to
``backend/tests/ragas/agents/<agent>/<dataset_version>/run_<timestamp>/``::

    scores.csv        per-sample per-metric scores (result.to_pandas())
    summary.json      aggregate means + run metadata (tracked in git)
    run_config.json   full reproducibility record (tracked in git)
    answers/<id>.json the raw agent output that was scored (gitignored)
"""

import json
import subprocess
from datetime import datetime
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[3]
RESULTS_ROOT = BACKEND_DIR / "tests" / "ragas" / "agents"


def git_commit() -> str:
    """Current git commit hash, or 'unknown' outside a repo."""
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=BACKEND_DIR,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except Exception:  # noqa: BLE001 — results must still be writable without git
        return "unknown"


def create_run_dir(agent: str, dataset_version: str) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = RESULTS_ROOT / agent / dataset_version / f"run_{timestamp}"
    (run_dir / "answers").mkdir(parents=True, exist_ok=False)
    return run_dir


def save_answer(run_dir: Path, sample_id: str, answer_json: str) -> None:
    (run_dir / "answers" / f"{sample_id}.json").write_text(
        answer_json, encoding="utf-8"
    )


def load_answer(run_dir: Path, sample_id: str) -> str:
    answer_path = Path(run_dir) / "answers" / f"{sample_id}.json"
    if not answer_path.exists():
        raise FileNotFoundError(
            f"No cached answer for sample '{sample_id}' at {answer_path}."
        )
    return answer_path.read_text(encoding="utf-8")


def write_run_results(run_dir: Path, scores_df, summary: dict, run_config: dict) -> None:
    scores_df.to_csv(run_dir / "scores.csv", index=False)
    (run_dir / "summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    (run_dir / "run_config.json").write_text(
        json.dumps(run_config, indent=2), encoding="utf-8"
    )
