"""
Bootstrap a golden-dataset version for the resume_analyzer agent.

For every sample resume in ``backend/sample_data/`` this script:

  1. extracts the raw resume text -> ``samples/<id>/context.txt``
  2. seeds ``samples/<id>/ground_truth.json`` from the newest matching agent
     output in ``backend/tests/output/`` (normalized through ParsedResume,
     id forced to 0) — marked ``"verified": false`` in meta.json until a
     human has corrected it
  3. writes ``manifest.json`` and ``task_prompt.txt``

Existing ground_truth.json files are NEVER overwritten (they may contain
hand-verification work) unless --force is passed. context.txt and
task_prompt.txt are regenerated on every run — they are derived artifacts.

Run from backend/:

    uv run python -m tests.ragas.agents.resume_analyzer.bootstrap_dataset --version v1
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(BACKEND_DIR))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND_DIR / ".env")

from app.services.resume_parser import (  # noqa: E402
    ParsedResume,
    ResumeParsingError,
    extract_resume_text,
)
from tests.ragas.agents.resume_analyzer import AGENT_NAME  # noqa: E402
from tests.ragas.common.dataset import dataset_dir  # noqa: E402
from tests.ragas.common.verbalize import VERBALIZER_ID  # noqa: E402

SAMPLE_DATA_DIR = BACKEND_DIR / "sample_data"
TESTS_OUTPUT_DIR = BACKEND_DIR / "tests" / "output"

# sample_id -> source resume file. Ids match the hand-curated sample dirs in
# golden_dataset/. The Arijit resume exists as .md/.pdf/.docx; the .md is used
# because its text needs no PDF extraction.
SOURCES: dict[str, str] = {
    "anugya_srivastava": "ANUGYA_SRIVASTAVA_resume.pdf",
    "arijit_de": "Arijit De Resume 2026.md",
    "deep_mehta": "Deep_Mehta_resume.pdf",
    "karan_pratap_singh": "Karan_Singh_Resume.pdf",
    "monisha_jagadeesan": "Monisha_Jagadeesan_Resume.pdf",
    "yash_pal": "Yash_Pal_Resume.pdf",
    "yash_raj_singh": "Yash_Raj_Singh_Oct_Resume.pdf",
}


def _sanitize_stem(name: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in name).strip("_")


def _find_seed_output(source_file: str) -> Path | None:
    """Newest tests/output JSON generated from ``source_file``, if any."""
    stem = _sanitize_stem(Path(source_file).stem)
    candidates = sorted(TESTS_OUTPUT_DIR.glob(f"test_parsed_resume_{stem}_*.json"))
    return candidates[-1] if candidates else None


def bootstrap(version: str, force: bool) -> int:
    root = dataset_dir(AGENT_NAME, version)
    samples_root = root / "samples"
    samples_root.mkdir(parents=True, exist_ok=True)

    sample_ids: list[str] = []
    for sample_id, source_file in SOURCES.items():
        source_path = SAMPLE_DATA_DIR / source_file
        if not source_path.exists():
            print(f"SKIP {sample_id}: source not found: {source_path}")
            continue

        sample_path = samples_root / sample_id
        sample_path.mkdir(parents=True, exist_ok=True)

        # 1. Context = extracted resume text (regenerated every run).
        try:
            context_text = extract_resume_text(str(source_path))
        except ResumeParsingError as e:
            print(f"SKIP {sample_id}: text extraction failed: {e}")
            continue
        (sample_path / "context.txt").write_text(context_text, encoding="utf-8")

        # 2. Ground truth seed (never overwritten without --force).
        ground_truth_path = sample_path / "ground_truth.json"
        seed_path = _find_seed_output(source_file)
        seeded_from = None
        if ground_truth_path.exists() and not force:
            print(f"KEEP {sample_id}: ground_truth.json already exists")
        elif seed_path is None:
            print(
                f"WARN {sample_id}: no seed output in tests/output/ — create "
                f"{ground_truth_path} by hand (or run tests/run_resume_analyzer_test.py "
                f"--resume '{source_file}' first)"
            )
        else:
            parsed = ParsedResume.model_validate_json(
                seed_path.read_text(encoding="utf-8")
            )
            parsed.id = 0
            ground_truth_path.write_text(
                parsed.model_dump_json(indent=2), encoding="utf-8"
            )
            seeded_from = seed_path.name
            print(f"SEED {sample_id}: ground truth seeded from {seed_path.name}")

        # 3. Meta (created once; refreshed only when we re-seeded).
        meta_path = sample_path / "meta.json"
        if not meta_path.exists() or seeded_from:
            meta = {
                "source_file": source_file,
                "seeded_from": seeded_from,
                "verified": False,
                "notes": (
                    "Ground truth seeded from an UNVERIFIED agent output — "
                    "hand-check every field, then set verified=true."
                ),
            }
            meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

        sample_ids.append(sample_id)

    if not sample_ids:
        print("ERROR: no samples could be bootstrapped.")
        return 1

    # 4. Task prompt (the RAGAS "question"; identical for every sample).
    from tests.ragas.agents.resume_analyzer.generate_answers import render_task_prompt

    (root / "task_prompt.txt").write_text(render_task_prompt(), encoding="utf-8")

    # 5. Manifest.
    manifest = {
        "agent": AGENT_NAME,
        "dataset_version": version,
        "created": datetime.now().strftime("%Y-%m-%d"),
        "verbalizer_id": VERBALIZER_ID,
        "samples": sample_ids,
        "notes": (
            "Ground truths are seeded from unverified agent outputs; see each "
            "sample's meta.json 'verified' flag."
        ),
    }
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print(f"\nBootstrapped {len(sample_ids)} sample(s) into {root}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--version", default="v1", help="Dataset version (default v1)")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing ground_truth.json files (DESTROYS hand edits)",
    )
    args = parser.parse_args()
    return bootstrap(args.version, args.force)


if __name__ == "__main__":
    sys.exit(main())
