"""
logger.py
---------
Centralized logging configuration for the Job Application Agent.

Usage:
    from logger import get_logger
    logger = get_logger(__name__)
    logger.info("Something happened")

Log levels (from most to least verbose):
    DEBUG    → fine-grained diagnostics
    INFO     → normal operational events
    WARNING  → something unexpected but recoverable
    ERROR    → a failure that needs attention
    CRITICAL → fatal errors

Logs go to:
    - Console  (INFO and above, colourised)
    - File     (DEBUG and above, plain text → logs/job_agent.log with rotation)
"""

import logging
import logging.handlers
import os
import sys
import datetime
from pathlib import Path

# ── Constants ─────────────────────────────────────────────────────────────────
LOG_DIR = Path(__file__).parent / "logs"
timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
LOG_FILE = LOG_DIR / f"job_agent_{timestamp}.log"
LOG_LEVEL_ENV = os.environ.get("LOG_LEVEL", "INFO").upper()  # Override via .env
MAX_BYTES = 5 * 1024 * 1024   # 5 MB per log file
BACKUP_COUNT = 3               # Keep 3 rotated files


# ── ANSI colour formatter (console only) ─────────────────────────────────────
class _ColouredFormatter(logging.Formatter):
    """Add ANSI colour codes to console log records by level."""

    _COLOURS = {
        logging.DEBUG:    "\033[36m",   # Cyan
        logging.INFO:     "\033[32m",   # Green
        logging.WARNING:  "\033[33m",   # Yellow
        logging.ERROR:    "\033[31m",   # Red
        logging.CRITICAL: "\033[35m",   # Magenta
    }
    _RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        colour = self._COLOURS.get(record.levelno, "")
        record.levelname = f"{colour}{record.levelname:<8}{self._RESET}"
        return super().format(record)


# ── Module-level setup (runs once on first import) ────────────────────────────
def _setup_root_logger() -> None:
    """Configure the root logger once."""
    root = logging.getLogger()

    # Avoid adding duplicate handlers if already configured
    if root.handlers:
        return

    root.setLevel(logging.DEBUG)  # Allow all levels; handlers filter further

    # ── File handler ──────────────────────────────────────────────────────────
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    file_fmt = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(filename)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler = logging.handlers.RotatingFileHandler(
        filename=LOG_FILE,
        maxBytes=MAX_BYTES,
        backupCount=BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(file_fmt)

    # ── Console handler ───────────────────────────────────────────────────────
    console_fmt = _ColouredFormatter(
        fmt="%(asctime)s | %(levelname)s | %(filename)s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(getattr(logging, LOG_LEVEL_ENV, logging.INFO))
    console_handler.setFormatter(console_fmt)

    root.addHandler(file_handler)
    root.addHandler(console_handler)


_setup_root_logger()


def get_logger(name: str) -> logging.Logger:
    """
    Return a named logger for the given module.

    Args:
        name: Typically ``__name__`` of the calling module.

    Returns:
        A configured :class:`logging.Logger` instance.
    """
    return logging.getLogger(name)


# ── Token usage helper ────────────────────────────────────────────────────────
def log_token_usage(usage, logger: logging.Logger | None = None) -> None:
    """
    Log LLM token usage statistics from a CrewAI kickoff result.

    Args:
        usage: The ``UsageMetrics`` object returned by ``crew.kickoff()``,
               accessible as ``result.token_usage``.
        logger: Logger to use. Defaults to the module-level logger.
    """
    _log = logger or get_logger(__name__)

    if usage is None:
        _log.warning("Token usage metrics not available (None returned).")
        return

    # CrewAI UsageMetrics fields (all are integers)
    prompt_tokens      = getattr(usage, "prompt_tokens",      0)
    completion_tokens  = getattr(usage, "completion_tokens",  0)
    total_tokens       = getattr(usage, "total_tokens",        0)
    successful_requests = getattr(usage, "successful_requests", 0)
    cached_prompt_tokens = getattr(usage, "cached_prompt_tokens", 0)

    _log.info("=" * 55)
    _log.info("LLM TOKEN USAGE SUMMARY")
    _log.info("=" * 55)
    _log.info("  Prompt tokens      : %d", prompt_tokens)
    _log.info("  Completion tokens  : %d", completion_tokens)
    _log.info("  Cached prompt tkns : %d", cached_prompt_tokens)
    _log.info("  Total tokens       : %d", total_tokens)
    _log.info("  Successful requests: %d", successful_requests)
    _log.info("=" * 55)
