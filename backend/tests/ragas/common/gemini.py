"""
Gemini evaluator (judge LLM + embeddings) for RAGAS metrics.

The judge runs on Gemini via the already-installed ``langchain-google-genai``
package. The repo stores the key as GEMINI_API_KEY (langchain-google-genai
would otherwise look for GOOGLE_API_KEY), so the key is passed explicitly.

NOTE: the embedding model must be ``gemini-embedding-001`` —
``text-embedding-004`` was retired by Google and returns 404.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

from tests.ragas.common import compat  # noqa: F401 — must precede `import ragas`
from ragas.embeddings import LangchainEmbeddingsWrapper
from ragas.llms import LangchainLLMWrapper

BACKEND_DIR = Path(__file__).resolve().parents[3]

JUDGE_MODEL = "gemini-2.5-flash"
EMBEDDING_MODEL = "models/gemini-embedding-001"


def require_gemini_api_key() -> str:
    """Return GEMINI_API_KEY, loading backend/.env first. Raises if unset."""
    load_dotenv(BACKEND_DIR / ".env")
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError(
            "GEMINI_API_KEY is not set (expected in backend/.env) — the RAGAS "
            "judge LLM and embeddings cannot run without it."
        )
    return key


def build_evaluator() -> tuple[LangchainLLMWrapper, LangchainEmbeddingsWrapper]:
    """Build the RAGAS judge LLM and embeddings, both on Gemini."""
    from langchain_google_genai import (
        ChatGoogleGenerativeAI,
        GoogleGenerativeAIEmbeddings,
    )

    key = require_gemini_api_key()
    llm = LangchainLLMWrapper(
        ChatGoogleGenerativeAI(model=JUDGE_MODEL, temperature=0, google_api_key=key)
    )
    embeddings = LangchainEmbeddingsWrapper(
        GoogleGenerativeAIEmbeddings(model=EMBEDDING_MODEL, google_api_key=key)
    )
    return llm, embeddings
