"""Local-only SearXNG entry point. No query-bearing log messages."""
import logging

logging.disable(logging.CRITICAL)
from searx.webapp import app  # noqa: E402,F401
