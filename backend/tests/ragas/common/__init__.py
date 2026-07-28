"""Shared helpers for the RAGAS evaluation harness."""

# Installs the langchain-1.x shim so any module under this package can
# `import ragas` safely.
from tests.ragas.common import compat  # noqa: F401
