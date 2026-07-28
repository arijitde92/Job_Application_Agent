"""
Golden-dataset loading for the RAGAS harness.

A dataset version lives at
``backend/golden_dataset/agents/<agent>/<version>/`` and contains::

    manifest.json      version metadata + ordered sample list
    task_prompt.txt    the rendered task description (the RAGAS "question")
    samples/<id>/
        context.txt        raw extracted resume text (the agent's true input)
        ground_truth.json  hand-verified ParsedResume JSON
        meta.json          source filename, provenance, verified flag
"""

import json
from dataclasses import dataclass
from pathlib import Path

from app.services.resume_parser import ParsedResume

BACKEND_DIR = Path(__file__).resolve().parents[3]
GOLDEN_DATASET_ROOT = BACKEND_DIR / "golden_dataset" / "agents"


@dataclass
class GoldenSample:
    sample_id: str
    context_text: str
    ground_truth: ParsedResume
    meta: dict
    path: Path


def dataset_dir(agent: str, version: str) -> Path:
    return GOLDEN_DATASET_ROOT / agent / version


def load_manifest(agent: str, version: str) -> dict:
    manifest_path = dataset_dir(agent, version) / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(
            f"No manifest at {manifest_path} — run bootstrap_dataset.py first "
            f"or check the --version value ({version!r})."
        )
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def load_task_prompt(agent: str, version: str) -> str:
    prompt_path = dataset_dir(agent, version) / "task_prompt.txt"
    if not prompt_path.exists():
        raise FileNotFoundError(
            f"No task prompt at {prompt_path} — run bootstrap_dataset.py first."
        )
    return prompt_path.read_text(encoding="utf-8")


def load_samples(agent: str, version: str) -> list[GoldenSample]:
    """Load every sample listed in the manifest, in manifest order."""
    manifest = load_manifest(agent, version)
    samples_root = dataset_dir(agent, version) / "samples"
    samples: list[GoldenSample] = []
    for sample_id in manifest["samples"]:
        sample_path = samples_root / sample_id
        context_path = sample_path / "context.txt"
        ground_truth_path = sample_path / "ground_truth.json"
        meta_path = sample_path / "meta.json"
        if not context_path.exists() or not ground_truth_path.exists():
            raise FileNotFoundError(
                f"Sample '{sample_id}' in the manifest is incomplete under "
                f"{sample_path} (need context.txt and ground_truth.json)."
            )
        ground_truth = ParsedResume.model_validate_json(
            ground_truth_path.read_text(encoding="utf-8")
        )
        meta = (
            json.loads(meta_path.read_text(encoding="utf-8"))
            if meta_path.exists()
            else {}
        )
        samples.append(
            GoldenSample(
                sample_id=sample_id,
                context_text=context_path.read_text(encoding="utf-8"),
                ground_truth=ground_truth,
                meta=meta,
                path=sample_path,
            )
        )
    return samples
