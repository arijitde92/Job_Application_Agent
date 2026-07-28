"""
Standalone quality-check script for the resume_analyzer agent.

Runs the resume analysis agent + task in isolation (no GitHub task, no
profiler, no GCS upload, no database write): the parsed-resume JSON is
written to local temp storage by the save_parsed_resume tool and then copied
to tests/output/test_parsed_resume_<stem>_<timestamp>.json for inspection.

By default it runs against all three sample resumes; pass --resume to run a
single custom file instead.

Not a pytest module — run it directly:

    cd backend
    uv run python tests/run_resume_analyzer_test.py
    uv run python tests/run_resume_analyzer_test.py --resume "sample_data/Arijit De Resume 2026.docx"

Requires GEMINI_API_KEY (loaded from backend/.env).
"""

import argparse
import os
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

load_dotenv(BACKEND_DIR / ".env")

DEFAULT_RESUMES = [
    BACKEND_DIR / "sample_data" / "ANUGYA_SRIVASTAVA_resume.pdf",
    BACKEND_DIR / "sample_data" / "Deep_Mehta_resume.pdf",
    BACKEND_DIR / "sample_data" / "Karan_Singh_Resume.pdf",
    BACKEND_DIR / "sample_data" / "Monisha_Jagadeesan_Resume.pdf",
]
OUTPUT_DIR = Path(__file__).resolve().parent / "output"


def _sanitize_stem(name: str) -> str:
    """Make a filesystem-safe stem for the output filename."""
    return "".join(c if c.isalnum() else "_" for c in name).strip("_") or "resume"


def analyze_resume(resume_file: Path) -> bool:
    """Run the resume_analyzer on one file. Returns True on success."""
    # Imports that need sys.path/.env ready.
    from crewai import Crew

    from app.services.crew.agents import resume_analyzer
    from app.services.crew.resume_tools import ResumeAnalysisContext
    from app.services.crew.tasks import _resume_analysis_task
    from app.services.resume_parser import (
        ParsedResume,
        ResumeParsingError,
        extract_resume_text,
    )

    # 1. Extract text (pypdf → pdfplumber fallback / python-docx / raw md).
    print(f"Extracting text from: {resume_file}")
    try:
        resume_text = extract_resume_text(str(resume_file))
    except ResumeParsingError as e:
        print(f"ERROR: resume parsing failed: {e}")
        return False
    print(f"Extracted {len(resume_text)} characters.")

    tmp_md = tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", prefix="test_resume_", delete=False, encoding="utf-8"
    )
    with tmp_md as f:
        f.write(resume_text)

    try:
        # 2. Run the agent + task in isolation. local_only=True → the save
        #    tool writes to temp storage only (no GCS upload, no DB write).
        ctx = ResumeAnalysisContext(
            user_id=0, resume_id=0, user_name="test_user", local_only=True,
            resume_text=resume_text,
        )
        task = _resume_analysis_task(ctx, run_async=False)
        crew = Crew(agents=[resume_analyzer], tasks=[task], verbose=True)
        crew.kickoff(inputs={"resume_path": tmp_md.name})

        if not ctx.state.get("saved"):
            print("FAILED: the agent never saved the parsed resume.")
            return False

        # 3. Validate and copy to tests/output/.
        parsed = ParsedResume.model_validate_json(ctx.state["json"])
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = (
            OUTPUT_DIR
            / f"test_parsed_resume_{_sanitize_stem(resume_file.stem)}_{timestamp}.json"
        )
        shutil.copy(ctx.state["local_path"], output_path)

        result = parsed.result
        filled = sum(
            1 for value in result.model_dump().values()
            if value not in ("", None, [], False, 0)
        )
        print("\n" + "=" * 70)
        print(f"SUCCESS — parsed resume copied to: {output_path}")
        print(f"  name:       {result.name!r}")
        print(f"  email:      {result.email!r}")
        print(f"  positions:  {len(result.positions)}")
        print(f"  education:  {len(result.education_qualifications)}")
        print(f"  projects:   {len(result.projects)}")
        print(f"  pubs:       {len(result.publications)}")
        print(f"  years_exp:  {result.years_of_experience}")
        print(f"  filled top-level fields: {filled}/{len(result.model_dump())}")
        print("=" * 70)
        return True
    finally:
        if os.path.exists(tmp_md.name):
            os.unlink(tmp_md.name)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the resume_analyzer agent in isolation.")
    parser.add_argument(
        "--resume",
        default=None,
        help="Path to a single .pdf/.docx/.md resume. Omit to run all three "
             "sample resumes.",
    )
    args = parser.parse_args()

    if not os.environ.get("GEMINI_API_KEY"):
        print("ERROR: GEMINI_API_KEY is not set (expected in backend/.env).")
        return 1

    # Resolve the list of resumes to process.
    if args.resume:
        resume_file = Path(args.resume)
        if not resume_file.is_absolute():
            resume_file = BACKEND_DIR / resume_file
        resumes = [resume_file]
    else:
        resumes = list(DEFAULT_RESUMES)

    results: list[tuple[Path, bool]] = []
    for i, resume_file in enumerate(resumes, start=1):
        print("\n" + "#" * 70)
        print(f"# Resume {i}/{len(resumes)}: {resume_file.name}")
        print("#" * 70)
        if not resume_file.exists():
            print(f"ERROR: resume file not found: {resume_file}")
            results.append((resume_file, False))
            continue
        try:
            ok = analyze_resume(resume_file)
        except Exception as e:  # noqa: BLE001 — one bad resume shouldn't stop the rest
            print(f"ERROR: unexpected failure on {resume_file.name}: {e}")
            ok = False
        results.append((resume_file, ok))

    # Summary.
    print("\n" + "=" * 70)
    print("SUMMARY")
    for resume_file, ok in results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {resume_file.name}")
    passed = sum(1 for _, ok in results if ok)
    print(f"  {passed}/{len(results)} succeeded")
    print("=" * 70)

    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
